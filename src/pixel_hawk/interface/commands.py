"""Project management service layer for pixel-hawk.

Discord-agnostic functions that implement the business logic behind slash commands:
creating projects, editing projects, deleting projects, and listing projects.
Parsing filenames/coordinates.
"""

import asyncio
import base64
import json
import os
import re
import time
import uuid

from loguru import logger
from PIL import Image

from ..models.config import get_config
from ..models.person import Person
from ..models.project import DiffStatus, HistoryChange, ProjectInfo, ProjectState
from ..models.geometry import GeoPoint, Point, Rectangle, Size
from ..models.palette import PALETTE, ColorsNotInPalette
from ..watcher.projects import Project, count_cached_tiles
from .access import ErrorMsg
from .watch import delete_watches_for_project

_COORDS_FRAGMENT = r"(?P<tx>\d{1,4})(?P<sep>[ .-])(?P<ty>\d{1,4})(?P=sep)(?P<px>\d{1,3})(?P=sep)(?P<py>\d{1,3})"
_COORDS_RES = (
    re.compile(rf"^(?P<name>.+)[ .-]{_COORDS_FRAGMENT}$"),
    re.compile(rf"^{_COORDS_FRAGMENT}[ .-](?P<name>.+)$"),
    re.compile(rf"^{_COORDS_FRAGMENT}$"),
)
_POSITIVE_INT_RE = re.compile(r"\d+")
_LINKED_STATES = (ProjectState.ACTIVE, ProjectState.PASSIVE)
_PROJECT_NAMESPACE = uuid.UUID("07e7e79e-a311-5c4c-bda2-f70758b10d6e")


_command_prefix: str | None = None


def get_command_prefix() -> str:
    global _command_prefix
    if _command_prefix is None:
        _command_prefix = os.environ.get("HAWK_COMMAND_PREFIX", "hawk")
    return _command_prefix


def parse_filename(filename: str) -> tuple[str | None, tuple[int, int, int, int] | None]:
    """Extract coords (tx, ty, px, py) and optional project name from a filename."""
    # Discord converts spaces to underscores. If we're going to
    # have to conflate them, then we prefer keeping the spaces.
    filename = filename.replace("_", " ")
    stem = filename[:-4] if filename.lower().endswith(".png") else filename
    for pattern in _COORDS_RES:
        m = pattern.match(stem)
        if not m:
            continue
        tx, ty, px, py = int(m["tx"]), int(m["ty"]), int(m["px"]), int(m["py"])
        if 0 <= tx < 2048 and 0 <= ty < 2048 and 0 <= px < 1000 and 0 <= py < 1000:
            name = m.groupdict().get("name")
            return name, (tx, ty, px, py)
    return stem or None, None


KNOWN_WPLACE_VERSIONS = ("1",)


def parse_wplace(data: bytes) -> tuple[str, bytes, Point, Size]:
    """Parse a .wplace project file. Returns (name, png_bytes, top_left_point, bounds_size)."""
    try:
        doc = json.loads(data)
    except json.JSONDecodeError, UnicodeDecodeError:
        raise ErrorMsg("Invalid .wplace file.")

    version = doc.get("schemaVersion", "")
    if version not in KNOWN_WPLACE_VERSIONS:
        logger.warning(
            f".wplace schemaVersion '{version}' is not recognized (known: {', '.join(KNOWN_WPLACE_VERSIONS)})"
        )

    name = doc.get("name", "")
    if not name:
        raise ErrorMsg("Missing project name in .wplace file.")

    image_obj = doc.get("image")
    if not isinstance(image_obj, dict):
        raise ErrorMsg("Missing image in .wplace file.")
    image_b64 = image_obj.get("dataUrl", "")
    if not image_b64:
        raise ErrorMsg("Missing image data in .wplace file.")

    # Strip data URL prefix if present (e.g. "data:image/png;base64,...")
    if "," in image_b64[:64]:
        image_b64 = image_b64.split(",", 1)[1]

    try:
        image_data = base64.b64decode(image_b64)
    except Exception:
        raise ErrorMsg("Invalid image data in .wplace file.")

    bounds = doc.get("bounds")
    if not isinstance(bounds, dict):
        raise ErrorMsg("Missing bounds in .wplace file.")
    north = bounds.get("north")
    south = bounds.get("south")
    west = bounds.get("west")
    east = bounds.get("east")
    if north is None or west is None:
        raise ErrorMsg("Missing north/west bounds in .wplace file.")

    nw = GeoPoint(north, west).to_pixel()

    if south is not None and east is not None:
        se = GeoPoint(south, east).to_pixel()
        bounds_size = Size(se.x - nw.x, se.y - nw.y)
    else:
        bounds_size = Size(image_obj.get("width", 0), image_obj.get("height", 0))

    return name, image_data, nw, bounds_size


def _parse_coords(coords_str: str) -> tuple[int, int, int, int]:
    """Parse a tx_ty_px_py coordinate string. Accepts any and all separators."""
    parts = _POSITIVE_INT_RE.findall(coords_str)
    if len(parts) != 4:
        raise ErrorMsg("Invalid coordinates: expected tx, ty, px, py (e.g. 1234 567 890 123)")
    tx, ty, px, py = (int(p) for p in parts)
    if tx > 2047 or ty > 2047 or px > 999 or py > 999:
        raise ErrorMsg(f"Coordinates out of range: {tx}_{ty}_{px}_{py} (tile 0-2047, pixel 0-999)")
    return tx, ty, px, py


def _set_coords(info: ProjectInfo, person_id: int, x: int, y: int) -> None:
    """Update info coordinates and rename the project file accordingly.

    Auto-transitions CREATING projects to ACTIVE so that info.filename
    reflects the new coordinate-based name.
    """
    person_dir = get_config().projects_dir / str(person_id)
    old = person_dir / info.filename
    info.x = x
    info.y = y
    if info.state == ProjectState.CREATING:
        info.state = ProjectState.ACTIVE
    new = person_dir / info.filename
    if old != new and old.exists():
        old.rename(new)


async def _try_initial_diff(info: ProjectInfo) -> str | None:
    """Run an initial diff if any tiles are cached. Returns formatted status or None."""
    cached, total = await count_cached_tiles(info.rectangle)
    if cached == 0:
        return None
    await info.fetch_related_owner()
    change = await Project(info).run_diff()
    if change.id == 0:
        await change.save()
    status = _format_project(info, change, 0, 0)
    if cached < total:
        status += f"\n  ({cached}/{total} tiles cached)"
    return status


PNG_HEADER = b"\x89PNG\r\n\x1a\n"


YAWCC_HINT = "You can use [yawcc](https://yawcc.z1x.us) to resize and convert images."


async def _validate_image(image_data: bytes, *, wplace_size: Size = Size()) -> tuple[int, int]:
    """Validate PNG data against palette and size limits. Returns (width, height)."""
    if not image_data.startswith(PNG_HEADER):
        raise ErrorMsg("Not a PNG file.")
    try:
        async with PALETTE.aopen_bytes(image_data) as image:
            width, height = image.size
    except ColorsNotInPalette as e:
        if wplace_size:
            raise ErrorMsg(
                f"Sorry, .wplace files store the original image before color conversion. {e}\n"
                f"Target size: **{wplace_size}**\n\n{YAWCC_HINT}"
            )
        raise ErrorMsg(f"{e}\n\n{YAWCC_HINT}")
    except Image.DecompressionBombError:
        raise ErrorMsg(f"Image too large. Maximum 1000px.\n\n{YAWCC_HINT}")
    if width > 1000 or height > 1000:
        raise ErrorMsg(f"Image too large ({width}x{height}). Maximum 1000px.\n\n{YAWCC_HINT}")
    return width, height


async def _check_coord_conflict(owner_id: int, x: int, y: int, *, exclude_id: int | None = None) -> None:
    """Raise ErrorMsg if another non-INACTIVE project exists at these coordinates for this owner."""
    existing = await ProjectInfo.filter_by_coords(
        owner_id, x, y, exclude_id=exclude_id or 0, exclude_state=ProjectState.INACTIVE
    )
    if existing:
        prefix = get_command_prefix()
        raise ErrorMsg(
            f"You already have project {existing.id:04} ('{existing.name}') at those coordinates.\n"
            f"Use `/{prefix} edit {existing.id}` with an image to replace it."
        )


async def _check_quotas(
    person: Person,
    rect: Rectangle | None = None,
    *,
    is_new_project: bool = False,
    exclude_project_id: int = 0,
) -> None:
    """Pre-check per-user quotas. Raises ErrorMsg if the operation would exceed limits."""
    if is_new_project:
        total = await ProjectInfo.count_by_owner(person.id)
        if total >= person.max_active_projects:
            raise ErrorMsg(f"You've reached your limit of {person.max_active_projects} projects.")

    if rect is not None:
        tiles: set = set()
        for project in await ProjectInfo.filter_by_owner(person.id, state=ProjectState.ACTIVE):
            if project.id == exclude_project_id:
                continue
            tiles.update(project.rectangle.tiles)
        tiles.update(rect.tiles)
        if len(tiles) > person.max_watched_tiles:
            raise ErrorMsg(
                f"This would use {len(tiles)} watched tiles, exceeding your limit of {person.max_watched_tiles}."
            )


async def new_project(discord_id: int, image_data: bytes, filename: str, *, wplace_size: Size = Size()) -> str | None:
    """Create a new project from an uploaded image. Returns None if no Person linked."""
    person = await Person.get_or_none_by_discord_id(discord_id)
    if person is None:
        return None

    width, height = await _validate_image(image_data, wplace_size=wplace_size)
    inferred_name, inferred_coords = parse_filename(filename)

    if inferred_coords:
        point = Point.from4(*inferred_coords)
        await _check_coord_conflict(person.id, point.x, point.y)
        await _check_quotas(person, Rectangle.from_point_size(point, Size(width, height)), is_new_project=True)
        state = ProjectState.ACTIVE
    else:
        point = Point(0, 0)
        state = ProjectState.CREATING
        await _check_quotas(person, is_new_project=True)

    now = round(time.time())
    info = ProjectInfo(
        owner_id=person.id,
        name="pending",
        state=state,
        x=point.x,
        y=point.y,
        width=width,
        height=height,
        first_seen=now,
        last_check=0,
    )
    await info.save_as_new()

    resolved_name = inferred_name or f"Project {info.id:04}"
    existing = await ProjectInfo.filter_by_owner_name(person.id, resolved_name, exclude_id=info.id)
    if existing:
        await info.delete()
        raise ErrorMsg(f"You already have a project named '{resolved_name}' (project {existing.id:04}).")
    info.name = resolved_name
    await info.save()

    person_dir = get_config().projects_dir / str(person.id)
    await asyncio.to_thread(person_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread((person_dir / info.filename).write_bytes, image_data)

    if inferred_coords:
        linked = await info.link_tiles()
        await person.update_totals()
        logger.info(f"{person.name}: Created project {info.id:04} '{info.name}' ({width}x{height}, {linked} tiles)")
        result = f"Project **{info.id:04}** activated ({width}x{height} px, {linked} tiles).\nName: {info.name} · Coords: {point}"
        status = await _try_initial_diff(info)
        if status:
            result += "\n" + status
        return result

    logger.info(f"{person.name}: Created project {info.id:04} '{info.name}' ({width}x{height}, awaiting coords)")
    return (
        f"Project **{info.id:04}** created ({width}x{height} px).\n"
        f"Name: {info.name}\n"
        f"Use `/{get_command_prefix()} edit {info.id}` to set coordinates and name, then activate."
    )


async def edit_project(
    discord_id: int,
    project_id: int,
    *,
    image_data: bytes | None = None,
    image_filename: str | None = None,
    name: str | None = None,
    coords: str | None = None,
    state: ProjectState | None = None,
    wplace_size: Size = Size(),
) -> str | None:
    """Edit an existing project. Returns None if no Person linked."""
    person = await Person.get_or_none_by_discord_id(discord_id)
    if person is None:
        return None

    info = await ProjectInfo.get_by_id_with_owner(project_id)
    if info is None:
        raise ErrorMsg(f"Project {project_id:04} not found.")
    assert info.owner is not None
    if info.owner.id != person.id:
        raise ErrorMsg(f"Project {project_id:04} is not yours.")

    original_state = info.state
    changes: list[str] = []
    needs_relink = False

    # --- Determine effective coord change (explicit > filename-inferred) ---
    new_point = None
    if coords is not None:
        tx, ty, px, py = _parse_coords(coords)
        new_point = Point.from4(tx, ty, px, py)
    elif image_data is not None and image_filename:
        _, filename_coords = parse_filename(image_filename)
        if filename_coords:
            new_point = Point.from4(*filename_coords)
    if new_point is not None and info.state != ProjectState.CREATING:
        if new_point.x == info.x and new_point.y == info.y:
            new_point = None  # Same coords, not a change

    # --- Image replacement ---
    if image_data is not None:
        width, height = await _validate_image(image_data, wplace_size=wplace_size)

        if new_point is not None:
            await _check_coord_conflict(person.id, new_point.x, new_point.y, exclude_id=info.id)
            _set_coords(info, person.id, new_point.x, new_point.y)
            changes.append(f"Coords: {new_point}")

        dims_changed = width != info.width or height != info.height
        if dims_changed:
            info.width = width
            info.height = height

        needs_relink = (new_point is not None or dims_changed) and info.state in _LINKED_STATES
        changes.append(f"Image: {width}x{height}")

        # Write new image and clean up snapshot (inside narrowed block for type safety)
        person_dir = get_config().projects_dir / str(person.id)
        await asyncio.to_thread(person_dir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread((person_dir / info.filename).write_bytes, image_data)
        snapshot = get_config().snapshots_dir / str(person.id) / info.filename
        await asyncio.to_thread(lambda: snapshot.unlink(missing_ok=True))
        info.reset_tracking()

    # --- Coords-only change (no image) ---
    elif new_point is not None:
        await _check_coord_conflict(person.id, new_point.x, new_point.y, exclude_id=info.id)
        _set_coords(info, person.id, new_point.x, new_point.y)
        needs_relink = info.state in _LINKED_STATES
        changes.append(f"Coords: {new_point}")

    if needs_relink:
        exclude_id = info.id if original_state == ProjectState.ACTIVE else 0
        await _check_quotas(person, info.rectangle, exclude_project_id=exclude_id)
        await info.unlink_tiles()
        await info.link_tiles()
        await person.update_totals()

    # --- Name change ---
    if name is not None:
        existing = await ProjectInfo.filter_by_owner_name(person.id, name, exclude_id=project_id)
        if existing:
            raise ErrorMsg(f"You already have a project named '{name}'.")
        info.name = name
        changes.append(f"Name: {name}")

    # --- State change ---
    if state is not None:
        if state in _LINKED_STATES and info.state == ProjectState.CREATING:
            raise ErrorMsg(f"Cannot activate: set coordinates first with `/{get_command_prefix()} edit`.")
        if state == ProjectState.ACTIVE and original_state != ProjectState.ACTIVE and not needs_relink:
            await _check_quotas(person, info.rectangle)
        info.state = state
        changes.append(f"State: {state.name}")

    if not changes:
        raise ErrorMsg("No changes specified.")

    await info.save()

    if state is not None and state != original_state:
        was_linked = original_state in _LINKED_STATES
        now_linked = state in _LINKED_STATES
        if was_linked and not now_linked:
            await info.unlink_tiles()
        elif not was_linked and now_linked:
            await info.link_tiles()
        elif was_linked and now_linked:
            await info.adjust_linked_tiles_heat()
        if not needs_relink:
            await person.update_totals()

    if (needs_relink or image_data is not None) and info.state == ProjectState.ACTIVE:
        status = await _try_initial_diff(info)
        if status:
            changes.append(status)

    logger.info(f"{person.name}: Edited project {info.id:04}: {', '.join(changes)}")
    return f"Project **{info.id:04}** updated:\n" + "\n".join(f"  {c}" for c in changes)


async def delete_project(discord_id: int, project_id: int) -> str | None:
    """Delete a project and all associated files. Returns None if no Person linked."""
    person = await Person.get_or_none_by_discord_id(discord_id)
    if person is None:
        return None

    info = await ProjectInfo.get_by_id_with_owner(project_id)
    if info is None:
        raise ErrorMsg(f"Project {project_id:04} not found.")
    assert info.owner is not None
    if info.owner.id != person.id:
        raise ErrorMsg(f"Project {project_id:04} is not yours.")

    project_name = info.name
    person_dir = get_config().projects_dir / str(person.id)
    snapshot_dir = get_config().snapshots_dir / str(person.id)

    await info.unlink_tiles()
    await delete_watches_for_project(info.id)
    await asyncio.to_thread(lambda: (person_dir / info.filename).unlink(missing_ok=True))
    await asyncio.to_thread(lambda: (snapshot_dir / info.filename).unlink(missing_ok=True))
    await info.delete()
    await person.update_totals()

    logger.info(f"{person.name}: Deleted project {project_id:04} '{project_name}'")
    return f"Project **{project_id:04}** ('{project_name}') deleted."


async def export_wplace(discord_id: int, project_id: int) -> tuple[bytes, str]:
    """Export a project as a .wplace file. Returns (wplace_bytes, filename)."""
    person = await Person.get_or_none_by_discord_id(discord_id)
    if person is None:
        raise ErrorMsg("No linked account found.")

    info = await ProjectInfo.get_by_id_with_owner(project_id)
    if info is None:
        raise ErrorMsg(f"Project {project_id:04} not found.")
    assert info.owner is not None
    if info.owner.id != person.id:
        raise ErrorMsg(f"Project {project_id:04} is not yours.")
    if info.state == ProjectState.CREATING:
        raise ErrorMsg("Cannot export a project that has no coordinates yet.")

    project_path = get_config().projects_dir / str(person.id) / info.filename
    png_data = await asyncio.to_thread(project_path.read_bytes)
    b64 = base64.b64encode(png_data).decode()

    rect = info.rectangle
    nw = GeoPoint.from_pixel(rect.left, rect.top)
    se = GeoPoint.from_pixel(rect.right, rect.bottom)

    doc = {
        "id": str(uuid.uuid5(_PROJECT_NAMESPACE, project_id.to_bytes(16))),
        "schemaVersion": "1",
        "name": info.name,
        "image": {"dataUrl": f"data:image/png;base64,{b64}", "width": rect.size.w, "height": rect.size.h},
        "bounds": {
            "north": nw.latitude,
            "south": se.latitude,
            "west": nw.longitude,
            "east": se.longitude,
        },
    }
    wplace_bytes = json.dumps(doc, indent=2).encode()
    safe_name = re.sub(r"[^\w\s-]", "", info.name).strip().replace(" ", "-") or f"project-{info.id:04}"
    filename = f"{safe_name}.wplace"
    return wplace_bytes, filename


DISCORD_MESSAGE_LIMIT = 2000


def _format_project(
    info: ProjectInfo,
    latest: HistoryChange | None,
    progress_24h: int,
    regress_24h: int,
) -> str:
    """Format a single project entry for the /hawk list response."""
    state = ProjectState(info.state)
    if state == ProjectState.CREATING:
        return f"**{info.id:04}** [CREATING] {info.name}"

    header = f"**{info.id:04}** [{state.name}] {info.name} <{info.rectangle.to_link()}>"

    if state == ProjectState.INACTIVE:
        return header

    if info.last_check == 0:
        return f"{header}\n  🤔 Not yet checked"

    if latest and latest.status == DiffStatus.COMPLETE:
        return f"{header}\n  ✅ Complete since <t:{info.max_completion_time}:R>! · {latest.num_target:,} px total"

    # In progress (or not-started with last_check > 0)
    if not latest:
        parts = []
    else:
        emoji = "⏳" if latest.completion_percent < 0.5 else "⌛"
        parts = [f"{emoji} {latest.completion_percent:.1f}% complete", f"{latest.num_remaining:,} px remaining"]

    if progress_24h or regress_24h:
        parts.append(f"Last 24h +{progress_24h}-{regress_24h}")

    if not parts:
        return header
    return f"{header}\n  {' · '.join(parts)}"


async def list_projects(discord_id: int) -> str | None:
    """Core list logic, separated for testability.

    Returns a formatted string of projects, or None if no Person is linked.
    """
    person = await Person.get_or_none_by_discord_id(discord_id)
    if person is None:
        return None

    projects = await ProjectInfo.filter_by_owner(person.id, order_by="last_snapshot DESC")
    if not projects:
        return "You have no projects."

    cutoff = round(time.time()) - 86400
    entries: list[str] = []

    for i, info in enumerate(projects):
        changes_24h = await HistoryChange.filter_by_project(info.id, since=cutoff)
        if changes_24h:
            latest = changes_24h[0]
        else:
            latest_list = await HistoryChange.filter_by_project(info.id, limit=1)
            latest = latest_list[0] if latest_list else None
        progress_24h = sum(c.progress_pixels for c in changes_24h)
        regress_24h = sum(c.regress_pixels for c in changes_24h)
        entry = _format_project(info, latest, progress_24h, regress_24h)

        # Check if adding this entry would exceed the Discord message limit
        remaining = len(projects) - i - 1
        suffix = f"\n\n... and {remaining} more" if remaining else ""
        candidate = "\n\n".join(entries + [entry]) + suffix
        if len(candidate) > DISCORD_MESSAGE_LIMIT:
            remaining = len(projects) - i
            return "\n\n".join(entries) + f"\n\n... and {remaining} more"
        entries.append(entry)

    return "\n\n".join(entries)

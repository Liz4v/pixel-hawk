"""Project management service layer for pixel-hawk.

Discord-agnostic functions that implement the business logic behind slash commands:
creating projects, editing projects, deleting projects, and listing projects.
Parsing filenames/coordinates.
"""

import asyncio
import re
import time

from loguru import logger
from PIL import Image

from ..models.config import get_config
from ..models.entities import DiffStatus, HistoryChange, Person, ProjectInfo, ProjectState
from ..models.geometry import Point, Rectangle, Size
from ..models.palette import PALETTE
from ..watcher.projects import Project, count_cached_tiles
from .access import ErrorMsg, get_command_prefix
from .watch import delete_watches_for_project

_ENTIRELY_RE = re.compile(r"^(?P<tx>\d+)(?P<sep>[ ._-])(?P<ty>\d+)(?P=sep)(?P<px>\d+)(?P=sep)(?P<py>\d+)$")
_ENDS_WITH_RE = re.compile(
    r"^(?P<name>.+)[ ._-](?P<tx>\d+)(?P<sep>[ ._-])(?P<ty>\d+)(?P=sep)(?P<px>\d+)(?P=sep)(?P<py>\d+)$"
)
_BEGINS_WITH_RE = re.compile(
    r"^(?P<tx>\d+)(?P<sep>[ ._-])(?P<ty>\d+)(?P=sep)(?P<px>\d+)(?P=sep)(?P<py>\d+)[ ._-](?P<name>.+)$"
)
_POSITIVE_INT_RE = re.compile(r"\d+")


def parse_filename(filename: str) -> tuple[str | None, tuple[int, int, int, int] | None]:
    """Extract coords (tx, ty, px, py) and optional project name from a filename."""
    stem = filename[:-4] if filename.lower().endswith(".png") else filename
    for pattern in (_ENTIRELY_RE, _ENDS_WITH_RE, _BEGINS_WITH_RE):
        m = pattern.match(stem)
        if not m:
            continue
        tx, ty, px, py = int(m["tx"]), int(m["ty"]), int(m["px"]), int(m["py"])
        if 0 <= tx < 2048 and 0 <= ty < 2048 and 0 <= px < 1000 and 0 <= py < 1000:
            name = m.groupdict().get("name")
            return name, (tx, ty, px, py)
    return stem or None, None


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
    await info.fetch_related("owner")
    change = await Project(info).run_diff()
    if not change.pk:
        await change.save()
    status = _format_project(info, change, 0, 0)
    if cached < total:
        status += f"\n  ({cached}/{total} tiles cached)"
    return status


PNG_HEADER = b"\x89PNG\r\n\x1a\n"


async def _validate_image(image_data: bytes) -> tuple[int, int]:
    """Validate PNG data against palette and size limits. Returns (width, height)."""
    if not image_data.startswith(PNG_HEADER):
        raise ErrorMsg("Not a PNG file.")
    try:
        async with PALETTE.aopen_bytes(image_data) as image:
            width, height = image.size
    except Image.DecompressionBombError:
        raise ErrorMsg("Image too large. Maximum 1000px.")
    if width > 1000 or height > 1000:
        raise ErrorMsg(f"Image too large ({width}x{height}). Maximum 1000px.")
    return width, height


async def _check_coord_conflict(owner_id: int, x: int, y: int, *, exclude_id: int | None = None) -> None:
    """Raise ErrorMsg if another non-INACTIVE project exists at these coordinates for this owner."""
    q = ProjectInfo.filter(owner_id=owner_id, x=x, y=y).exclude(state=ProjectState.INACTIVE)
    if exclude_id is not None:
        q = q.exclude(id=exclude_id)
    existing = await q.first()
    if existing:
        prefix = get_command_prefix()
        raise ErrorMsg(
            f"You already have project {existing.id:04} ('{existing.name}') at those coordinates.\n"
            f"Use `/{prefix} edit {existing.id}` with an image to replace it."
        )


async def _check_quotas(
    person: Person, rect: Rectangle | None = None, *, is_new_project: bool = False, exclude_project_id: int = 0,
) -> None:
    """Pre-check per-user quotas. Raises ErrorMsg if the operation would exceed limits."""
    if is_new_project:
        total = await person.projects.all().count()
        if total >= person.max_active_projects:
            raise ErrorMsg(f"You've reached your limit of {person.max_active_projects} projects.")

    if rect is not None:
        tiles = set()
        for project in await person.projects.filter(state=ProjectState.ACTIVE).all():
            if project.id == exclude_project_id:
                continue
            tiles.update(project.rectangle.tiles)
        tiles.update(rect.tiles)
        if len(tiles) > person.max_watched_tiles:
            raise ErrorMsg(
                f"This would use {len(tiles)} watched tiles, exceeding your limit of {person.max_watched_tiles}."
            )


async def new_project(discord_id: int, image_data: bytes, filename: str) -> str | None:
    """Create a new project from an uploaded image. Returns None if no Person linked."""
    person = await Person.filter(discord_id=discord_id).first()
    if person is None:
        return None

    width, height = await _validate_image(image_data)
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
    existing = await ProjectInfo.filter(owner_id=person.id, name=resolved_name).exclude(id=info.id).first()
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
) -> str | None:
    """Edit an existing project. Returns None if no Person linked."""
    person = await Person.filter(discord_id=discord_id).first()
    if person is None:
        return None

    info = await ProjectInfo.filter(id=project_id).prefetch_related("owner").first()
    if info is None:
        raise ErrorMsg(f"Project {project_id:04} not found.")
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
        width, height = await _validate_image(image_data)

        if new_point is not None:
            await _check_coord_conflict(person.id, new_point.x, new_point.y, exclude_id=info.id)
            _set_coords(info, person.id, new_point.x, new_point.y)
            changes.append(f"Coords: {new_point}")

        dims_changed = width != info.width or height != info.height
        if dims_changed:
            info.width = width
            info.height = height

        needs_relink = (new_point is not None or dims_changed) and info.state != ProjectState.CREATING
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
        needs_relink = True
        changes.append(f"Coords: {new_point}")

    if needs_relink:
        exclude_id = info.id if original_state == ProjectState.ACTIVE else 0
        await _check_quotas(person, info.rectangle, exclude_project_id=exclude_id)
        await info.unlink_tiles()
        await info.link_tiles()
        await person.update_totals()

    # --- Name change ---
    if name is not None:
        existing = await ProjectInfo.filter(owner_id=person.id, name=name).exclude(id=project_id).first()
        if existing:
            raise ErrorMsg(f"You already have a project named '{name}'.")
        info.name = name
        changes.append(f"Name: {name}")

    # --- State change ---
    if state is not None:
        if state in (ProjectState.ACTIVE, ProjectState.PASSIVE) and info.state == ProjectState.CREATING:
            raise ErrorMsg(f"Cannot activate: set coordinates first with `/{get_command_prefix()} edit`.")
        if state == ProjectState.ACTIVE and original_state != ProjectState.ACTIVE and not needs_relink:
            await _check_quotas(person, info.rectangle)
        info.state = state
        changes.append(f"State: {state.name}")

    if not changes:
        raise ErrorMsg("No changes specified.")

    await info.save()

    if state is not None and state != original_state and not needs_relink:
        await person.update_totals()

    if (needs_relink or image_data is not None) and info.state == ProjectState.ACTIVE:
        status = await _try_initial_diff(info)
        if status:
            changes.append(status)

    logger.info(f"{person.name}: Edited project {info.id:04}: {', '.join(changes)}")
    return f"Project **{info.id:04}** updated:\n" + "\n".join(f"  {c}" for c in changes)


async def delete_project(discord_id: int, project_id: int) -> str | None:
    """Delete a project and all associated files. Returns None if no Person linked."""
    person = await Person.filter(discord_id=discord_id).first()
    if person is None:
        return None

    info = await ProjectInfo.filter(id=project_id).prefetch_related("owner").first()
    if info is None:
        raise ErrorMsg(f"Project {project_id:04} not found.")
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
    person = await Person.filter(discord_id=discord_id).first()
    if person is None:
        return None

    projects = await ProjectInfo.filter(owner=person).order_by("-last_snapshot").all()
    if not projects:
        return "You have no projects."

    cutoff = round(time.time()) - 86400
    entries: list[str] = []

    for i, info in enumerate(projects):
        changes_24h = await HistoryChange.filter(project=info, timestamp__gte=cutoff).order_by("-timestamp").all()
        if changes_24h:
            latest = changes_24h[0]
        else:
            latest = await HistoryChange.filter(project=info).order_by("-timestamp").first()
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

"""Project management service layer for pixel-hawk.

Discord-agnostic functions that implement the business logic behind slash commands:
creating projects, editing projects, listing projects, granting admin access, and
parsing filenames/coordinates.
"""

import asyncio
import time
import uuid

from loguru import logger

from .config import get_config
from .geometry import Point
from .models import BotAccess, DiffStatus, HistoryChange, Person, ProjectInfo, ProjectState
from .palette import PALETTE

_command_prefix: str | None = None


def get_command_prefix() -> str:
    global _command_prefix
    if _command_prefix is None:
        _command_prefix = get_config().discord.command_prefix
    return _command_prefix


def generate_admin_token() -> str:
    """Generate a fresh admin UUID and write it to nest/data/admin-me.txt.

    A new UUID is generated on every startup so old tokens cannot be reused.
    """
    path = get_config().data_dir / "admin-me.txt"
    token = str(uuid.uuid4())
    path.write_text(f"/{get_command_prefix()} sa myself {token}")
    return token


async def grant_admin(discord_id: int, display_name: str, token: str, expected_token: str) -> str | None:
    """Core admin-me logic, separated for testability.

    Returns a success message string, or None on invalid token.
    """
    if token != expected_token:
        return None

    person = await Person.filter(discord_id=discord_id).first()
    if person is None:
        person = await Person.create(name=display_name, discord_id=discord_id)
        logger.info(f"Created new person '{display_name}' (discord_id={discord_id})")

    person.access = person.access | BotAccess.ADMIN
    await person.save()

    logger.info(f"Admin access granted to '{person.name}' (discord_id={discord_id})")
    return f"Admin access granted to {person.name}."


def _parse_filename(filename: str) -> tuple[str | None, tuple[int, int, int, int] | None]:
    """Extract trailing tx_ty_px_py coords and optional name prefix from a filename."""
    stem = filename.rsplit(".", maxsplit=1)[0] if "." in filename else filename
    parts = stem.split("_")
    if len(parts) >= 4:
        try:
            tx, ty, px, py = (int(p) for p in parts[-4:])
        except ValueError:
            return None, None
        if 0 <= tx < 2048 and 0 <= ty < 2048 and 0 <= px < 1000 and 0 <= py < 1000:
            name = "_".join(parts[:-4]) or None
            return name, (tx, ty, px, py)
    return None, None


def _parse_coords(coords_str: str) -> tuple[int, int, int, int]:
    """Parse a tx_ty_px_py coordinate string. Accepts ``_``, ``,`` or space as separators."""
    parts = coords_str.replace(",", " ").replace("_", " ").split()
    if len(parts) != 4:
        raise ValueError("Invalid coordinates: expected tx_ty_px_py (e.g. 5_7_0_0)")
    try:
        tx, ty, px, py = (int(p) for p in parts)
    except ValueError:
        raise ValueError("Invalid coordinates: all values must be integers")
    if not (0 <= tx < 2048 and 0 <= ty < 2048 and 0 <= px < 1000 and 0 <= py < 1000):
        raise ValueError(f"Coordinates out of range: {tx}_{ty}_{px}_{py} (tile 0-2047, pixel 0-999)")
    return tx, ty, px, py


def _set_coords(info: ProjectInfo, person_id: int, x: int, y: int) -> None:
    """Update info.x/y and rename the file from pending to canonical (or between canonicals)."""
    person_dir = get_config().projects_dir / str(person_id)
    pending = person_dir / f"new_{info.id}.png"
    old_canonical = person_dir / info.filename

    info.x = x
    info.y = y
    new_canonical = person_dir / info.filename

    if pending.exists():
        pending.rename(new_canonical)
    elif old_canonical != new_canonical and old_canonical.exists():
        old_canonical.rename(new_canonical)


PNG_HEADER = b"\x89PNG\r\n\x1a\n"


async def new_project(discord_id: int, image_data: bytes, filename: str) -> str | None:
    """Create a new project from an uploaded image. Returns None if no Person linked."""
    person = await Person.filter(discord_id=discord_id).first()
    if person is None:
        return None

    if not image_data.startswith(PNG_HEADER):
        raise ValueError("Not a PNG file.")

    async with PALETTE.aopen_bytes(image_data) as image:
        width, height = image.size

    if width > 1000 or height > 1000:
        raise ValueError(f"Image too large ({width}x{height}). Maximum 1000x1000 px.")

    inferred_name, inferred_coords = _parse_filename(filename)

    if inferred_coords:
        point = Point.from4(*inferred_coords)
        state = ProjectState.ACTIVE
    else:
        point = Point(0, 0)
        state = ProjectState.CREATING

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
    info.name = inferred_name or f"Project {info.id:04}"
    await info.save()

    person_dir = get_config().projects_dir / str(person.id)
    await asyncio.to_thread(person_dir.mkdir, parents=True, exist_ok=True)

    if inferred_coords:
        await asyncio.to_thread((person_dir / info.filename).write_bytes, image_data)
        linked = await info.link_tiles()
        await person.update_totals()
        logger.info(f"{person.name}: Created project {info.id:04} '{info.name}' ({width}x{height}, {linked} tiles)")
        return (
            f"Project **{info.id:04}** activated ({width}x{height} px, {linked} tiles).\n"
            f"Name: {info.name} · Coords: {point}"
        )

    await asyncio.to_thread((person_dir / f"new_{info.id}.png").write_bytes, image_data)
    logger.info(f"{person.name}: Created project {info.id:04} ({width}x{height}, awaiting coords)")
    return (
        f"Project **{info.id:04}** created ({width}x{height} px).\n"
        f"Use `/{get_command_prefix()} edit {info.id}` to set coordinates and name, then activate."
    )


async def edit_project(
    discord_id: int,
    project_id: int,
    *,
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
        raise ValueError(f"Project {project_id:04} not found.")
    if info.owner.id != person.id:
        raise ValueError(f"Project {project_id:04} is not yours.")

    changes: list[str] = []

    if name is not None:
        existing = await ProjectInfo.filter(owner_id=person.id, name=name).exclude(id=project_id).first()
        if existing:
            raise ValueError(f"You already have a project named '{name}'.")
        info.name = name
        changes.append(f"Name: {name}")

    if coords is not None:
        tx, ty, px, py = _parse_coords(coords)
        point = Point.from4(tx, ty, px, py)
        _set_coords(info, person.id, point.x, point.y)
        await info.unlink_tiles()
        linked = await info.link_tiles()
        await person.update_totals()
        changes.append(f"Coords: {tx}_{ty}_{px}_{py} ({linked} tiles)")

    if state is not None:
        if state in (ProjectState.ACTIVE, ProjectState.PASSIVE):
            canonical = get_config().projects_dir / str(person.id) / info.filename
            if not canonical.exists():
                raise ValueError(f"Cannot activate: set coordinates first with `/{get_command_prefix()} edit`.")
        info.state = state
        changes.append(f"State: {state.name}")

    if not changes:
        raise ValueError("No changes specified.")

    await info.save()
    logger.info(f"{person.name}: Edited project {info.id:04}: {', '.join(changes)}")
    return f"Project **{info.id:04}** updated:\n" + "\n".join(f"  {c}" for c in changes)


DISCORD_MESSAGE_LIMIT = 2000


def _format_project(
    info: ProjectInfo,
    latest: HistoryChange | None,
    progress_24h: int,
    regress_24h: int,
) -> str:
    """Format a single project entry for the /hawk list response."""
    state = ProjectState(info.state)
    header = f"**{info.id:04}** [{state.name}] {info.name} <{info.rectangle.to_link()}>"

    if state == ProjectState.CREATING:
        return f"**{info.id:04}** [CREATING] {info.name}"

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

"""Discord bot integration for pixel-hawk.

Optional Discord bot that runs alongside the polling loop. Reads credentials
from config.toml at the nest root. If config.toml is missing or has no bot_token,
the bot is silently skipped.

Provides slash commands under the /hawk command group.
"""

import asyncio
import contextlib
import time
import uuid

import discord
from discord import app_commands
from loguru import logger

from .config import get_config
from .geometry import Point
from .models import BotAccess, DiffStatus, HistoryChange, Person, ProjectInfo, ProjectState
from .palette import PALETTE, ColorsNotInPalette


def load_bot_token() -> str | None:
    """Read bot_token from config.toml via Config.config_toml. Returns None if unavailable."""
    token = get_config().config_toml.get("discord", {}).get("bot_token")
    if not token:
        return None
    return token


def generate_admin_token() -> str:
    """Generate a fresh admin UUID and write it to nest/data/admin-me.txt.

    A new UUID is generated on every startup so old tokens cannot be reused.
    """
    path = get_config().data_dir / "admin-me.txt"
    token = str(uuid.uuid4())
    path.write_text(f"/hawk sa myself {token}")
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
        owner_id=person.id, name="pending", state=state,
        x=point.x, y=point.y, width=width, height=height,
        first_seen=now, last_check=0,
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
        return f"Project **{info.id:04}** activated ({width}x{height} px, {linked} tiles).\n" \
               f"Name: {info.name} · Coords: {point}"

    await asyncio.to_thread((person_dir / f"new_{info.id}.png").write_bytes, image_data)
    logger.info(f"{person.name}: Created project {info.id:04} ({width}x{height}, awaiting coords)")
    return f"Project **{info.id:04}** created ({width}x{height} px).\n" \
           f"Use `/hawk edit {info.id}` to set coordinates and name, then activate."


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
                raise ValueError("Cannot activate: set coordinates first with `/hawk edit`.")
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


class HawkBot(discord.Client):
    """Discord client for pixel-hawk with slash command support."""

    def __init__(self, admin_token: str):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.admin_token = admin_token
        self._register_commands()

    def _register_commands(self) -> None:
        """Register all slash commands under the /hawk group."""
        hawk_group = app_commands.Group(name="hawk", description="Pixel Hawk commands")
        hawk_group.command(name="sa", description="Admin commands")(self._sa)
        hawk_group.command(name="list", description="List your projects")(self._list)
        hawk_group.command(name="new", description="Upload a new project image")(self._new)
        hawk_group.command(name="edit", description="Edit an existing project")(self._edit)
        self.tree.add_command(hawk_group)

    @app_commands.describe(args="Subcommand and arguments")
    async def _sa(self, interaction: discord.Interaction, args: str) -> None:
        """Dispatch /hawk sa subcommands."""
        parts = args.split()
        if not parts:
            await interaction.response.send_message("No.", ephemeral=True)
            return
        cmd, *params = parts
        user = interaction.user
        if cmd == "myself" and len(params) == 1:
            msg = await grant_admin(user.id, user.name, params[0], self.admin_token)
            await interaction.response.send_message(msg or "No.", ephemeral=True)
        else:
            logger.debug(f"Failed sa dispatch from {user.name} https://discord.com/users/{user.id}")
            await interaction.response.send_message("No.", ephemeral=True)

    async def _list(self, interaction: discord.Interaction) -> None:
        """Handle /hawk list — show the calling user's projects."""
        msg = await list_projects(interaction.user.id)
        await interaction.response.send_message(msg or "No linked account found.", ephemeral=True)

    @app_commands.describe(image="Project PNG image (must use WPlace palette, max 1000x1000)")
    async def _new(self, interaction: discord.Interaction, image: discord.Attachment) -> None:
        """Handle /hawk new — upload a new project image."""
        await interaction.response.defer(ephemeral=True)
        try:
            image_data = await image.read()
            msg = await new_project(interaction.user.id, image_data, image.filename)
        except (ValueError, ColorsNotInPalette) as e:
            msg = str(e)
        except Exception as e:
            logger.error(f"Error in /hawk new: {e}")
            msg = "An error occurred while creating the project."
        await interaction.followup.send(msg or "No linked account found.", ephemeral=True)

    @app_commands.describe(
        project_id="Project ID (4-digit number)", name="New project name",
        coords="Coordinates as tx_ty_px_py (e.g. 5_7_0_0)", state="Project state",
    )
    @app_commands.choices(state=[
        app_commands.Choice(name="Active", value=int(ProjectState.ACTIVE)),
        app_commands.Choice(name="Passive", value=int(ProjectState.PASSIVE)),
        app_commands.Choice(name="Inactive", value=int(ProjectState.INACTIVE)),
    ])
    async def _edit(
        self,
        interaction: discord.Interaction,
        project_id: int,
        name: str | None = None,
        coords: str | None = None,
        state: app_commands.Choice[int] | None = None,
    ) -> None:
        """Handle /hawk edit — modify an existing project."""
        await interaction.response.defer(ephemeral=True)
        try:
            state_value = ProjectState(state.value) if state else None
            msg = await edit_project(interaction.user.id, project_id, name=name, coords=coords, state=state_value)
        except ValueError as e:
            msg = str(e)
        except Exception as e:
            logger.error(f"Error in /hawk edit: {e}")
            msg = "An error occurred while editing the project."
        await interaction.followup.send(msg or "No linked account found.", ephemeral=True)

    async def setup_hook(self) -> None:
        await self.tree.sync()
        logger.info("Discord bot command tree synced")

    async def on_ready(self) -> None:
        logger.info(f"Discord bot connected as {self.user}")


@contextlib.asynccontextmanager
async def maybe_bot():
    """Start the Discord bot if a token is configured, otherwise silently skip."""
    token = load_bot_token()
    if token is None:
        logger.debug("No Discord bot token in config.toml, skipping bot")
        yield
        return

    admin_token = generate_admin_token()
    logger.info(f"Admin token: {admin_token} (see nest/data/admin-me.txt)")

    bot = HawkBot(admin_token)
    asyncio.create_task(bot.start(token))
    yield
    await bot.close()

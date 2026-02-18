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
from .models import BotAccess, DiffStatus, HistoryChange, Person, ProjectInfo, ProjectState


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


DISCORD_MESSAGE_LIMIT = 2000


def _format_project(
    info: ProjectInfo,
    latest: HistoryChange | None,
    progress_24h: int,
    regress_24h: int,
) -> str:
    """Format a single project entry for the /hawk list response."""
    header = f"**{info.id}** [{ProjectState(info.state).name}] {info.name}"
    link = f"  {info.rectangle.to_link()}"

    if info.state == ProjectState.INACTIVE:
        return f"{header}\n{link}"

    if info.last_check == 0:
        return f"{header}\n  \U0001fae3 Not yet checked\n{link}"

    if latest and latest.status == DiffStatus.COMPLETE:
        return (
            f"{header}\n"
            f"  \u2705 Complete since <t:{info.max_completion_time}:R>!"
            f" \u00b7 {latest.num_target:,} px total\n{link}"
        )

    # In progress (or not-started with last_check > 0)
    if latest:
        parts = [f"{latest.completion_percent:.1f}% complete", f"{latest.num_remaining:,} px remaining"]
    else:
        parts = []

    if progress_24h or regress_24h:
        parts.append(f"Last 24h +{progress_24h}-{regress_24h}")

    stats = " \u00b7 ".join(parts)
    if stats:
        return f"{header}\n  \u231b {stats}\n{link}"
    return f"{header}\n{link}"


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

    async def setup_hook(self) -> None:
        """Sync command tree with Discord on ready."""
        synced = await self.tree.sync()
        logger.debug(f"Discord bot command tree synced: {synced}")
        logger.info("Discord bot command tree synced")

    async def on_ready(self) -> None:
        logger.info(f"Discord bot connected as {self.user}")


@contextlib.asynccontextmanager
async def maybe_bot():
    """Attempt to start the Discord bot.

    Reads bot_token from config.toml, generates the admin UUID, and launches
    the bot as a background asyncio task in the current event loop.
    """
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

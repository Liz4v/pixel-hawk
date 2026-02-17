"""Discord bot integration for pixel-hawk.

Optional Discord bot that runs alongside the polling loop. Reads credentials
from config.toml at the nest root. If config.toml is missing or has no bot_token,
the bot is silently skipped.

Provides slash commands under the /hawk command group.
"""

import asyncio
import uuid

import discord
from discord import app_commands
from loguru import logger
from .config import get_config
from .models import BotAccess, Person


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
    path.write_text(token)
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

        @hawk_group.command(name="admin-me", description="Claim admin access with the startup token")
        @app_commands.describe(token="The UUID4 token from admin-me.txt")
        async def admin_me(interaction: discord.Interaction, token: str):
            result = await grant_admin(interaction.user.id, interaction.user.name, token, self.admin_token)
            if result is None:
                await interaction.response.send_message("Invalid token.", ephemeral=True)
                return
            await interaction.response.send_message(result, ephemeral=True)

        self.tree.add_command(hawk_group)

    async def setup_hook(self) -> None:
        """Sync command tree with Discord on ready."""
        await self.tree.sync()
        logger.info("Discord bot command tree synced")

    async def on_ready(self) -> None:
        logger.info(f"Discord bot connected as {self.user}")


async def start_bot() -> HawkBot | None:
    """Attempt to start the Discord bot. Returns HawkBot if started, None if skipped.

    Reads bot_token from config.toml, generates the admin UUID, and launches
    the bot as a background asyncio task in the current event loop.
    """
    token = load_bot_token()
    if token is None:
        logger.debug("No Discord bot token in config.toml, skipping bot")
        return None

    admin_token = generate_admin_token()
    logger.info(f"Admin token: {admin_token} (see nest/data/admin-me.txt)")

    bot = HawkBot(admin_token)
    asyncio.create_task(bot.start(token))
    return bot

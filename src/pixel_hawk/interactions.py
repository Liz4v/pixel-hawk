"""Discord bot wiring for pixel-hawk.

Optional Discord bot that runs alongside the polling loop. Reads credentials
from config.toml at the nest root. If config.toml is missing or has no bot_token,
the bot is silently skipped.

Dispatches slash commands to service functions in commands.py.
"""

import asyncio
import contextlib

import discord
from discord import app_commands
from loguru import logger

from .commands import edit_project, generate_admin_token, grant_admin, list_projects, new_project
from .config import get_config
from .models import ProjectState
from .palette import ColorsNotInPalette


class HawkBot(discord.Client):
    """Discord client for pixel-hawk with slash command support."""

    def __init__(self, admin_token: str, command_prefix: str):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.admin_token = admin_token
        self.command_prefix = command_prefix
        self._register_commands()

    def _register_commands(self) -> None:
        """Register all slash commands under the command group."""
        hawk_group = app_commands.Group(name=self.command_prefix, description="Pixel Hawk commands")
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
        logger.info(f"SA from {user.name} (https://discord.com/users/{user.id}): {cmd} {params}")
        if cmd == "myself" and len(params) == 1:
            msg = await grant_admin(user.id, user.name, params[0], self.admin_token)
            await interaction.response.send_message(msg or "No.", ephemeral=True)
        else:
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
        project_id="Project ID (4-digit number)",
        name="New project name",
        coords="Coordinates as tx_ty_px_py (e.g. 5_7_0_0)",
        state="Project state",
    )
    @app_commands.choices(
        state=[
            app_commands.Choice(name="Active", value=int(ProjectState.ACTIVE)),
            app_commands.Choice(name="Passive", value=int(ProjectState.PASSIVE)),
            app_commands.Choice(name="Inactive", value=int(ProjectState.INACTIVE)),
        ]
    )
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
    token = get_config().discord.bot_token
    if not token:
        logger.debug("No Discord bot token in config.toml, skipping bot")
        yield
        return

    admin_token = generate_admin_token()
    logger.info(f"Admin token: {admin_token} (see nest/data/admin-me.txt)")

    bot = HawkBot(admin_token, get_config().discord.command_prefix)
    asyncio.create_task(bot.start(token))
    yield
    await bot.close()

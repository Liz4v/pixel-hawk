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

from ..models.config import get_config
from ..models.entities import Person, ProjectState
from ..models.palette import ColorsNotInPalette
from .access import ErrorMsg, check_guild_access, generate_admin_token, grant_admin, set_guild_role
from .commands import delete_project, edit_project, list_projects, new_project


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
        hawk_group.command(name="delete", description="Delete a project")(self._delete)
        self.tree.add_command(hawk_group)

    async def _check_access(self, interaction: discord.Interaction) -> Person | None:
        """Check guild role access. Returns Person on success, sends denial and returns None on failure."""
        guild_id = interaction.guild_id
        assert guild_id is not None, "Commands must be used in a guild"
        member = interaction.user
        assert isinstance(member, discord.Member), "Commands must be used in a guild"
        role_names = [r.name for r in member.roles]
        try:
            return await check_guild_access(guild_id, member.id, member.name, role_names)
        except ErrorMsg as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return None

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
        elif cmd == "role" and len(params) == 1:
            assert interaction.guild_id is not None, "Commands must be used in a guild"
            try:
                msg = await set_guild_role(user.id, interaction.guild_id, params[0])
            except ErrorMsg as e:
                msg = str(e)
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.response.send_message("No.", ephemeral=True)

    async def _list(self, interaction: discord.Interaction) -> None:
        """Handle /hawk list — show the calling user's projects."""
        if await self._check_access(interaction) is None:
            return
        msg = await list_projects(interaction.user.id)
        await interaction.response.send_message(msg or "You have no projects.", ephemeral=True)

    @app_commands.describe(image="Project PNG image (must use WPlace palette, max 1000x1000)")
    async def _new(self, interaction: discord.Interaction, image: discord.Attachment) -> None:
        """Handle /hawk new — upload a new project image."""
        if await self._check_access(interaction) is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            image_data = await image.read()
            msg = await new_project(interaction.user.id, image_data, image.filename)
        except (ErrorMsg, ColorsNotInPalette) as e:
            msg = str(e)
        except Exception as e:
            logger.error(f"Error in /hawk new: {e}")
            msg = "An error occurred while creating the project."
        await interaction.followup.send(msg or "No linked account found.", ephemeral=True)

    @app_commands.describe(
        project_id="Project ID (4-digit number)",
        image="Replacement project PNG image (resets tracking stats)",
        name="New project name",
        coords="Coordinates as Tx,Ty,Px,Py. 4 numbers separated by whatever.",
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
        image: discord.Attachment | None = None,
        name: str | None = None,
        coords: str | None = None,
        state: app_commands.Choice[int] | None = None,
    ) -> None:
        """Handle /hawk edit — modify an existing project."""
        if await self._check_access(interaction) is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            image_data = await image.read() if image else None
            image_filename = image.filename if image else None
            state_value = ProjectState(state.value) if state else None
            msg = await edit_project(
                interaction.user.id,
                project_id,
                image_data=image_data,
                image_filename=image_filename,
                name=name,
                coords=coords,
                state=state_value,
            )
        except (ErrorMsg, ColorsNotInPalette) as e:
            msg = str(e)
        except Exception as e:
            logger.error(f"Error in /hawk edit: {e}")
            msg = "An error occurred while editing the project."
        await interaction.followup.send(msg or "No linked account found.", ephemeral=True)

    @app_commands.describe(project_id="Project ID (4-digit number)")
    async def _delete(self, interaction: discord.Interaction, project_id: int) -> None:
        """Handle /hawk delete — permanently remove a project."""
        if await self._check_access(interaction) is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            msg = await delete_project(interaction.user.id, project_id)
        except ErrorMsg as e:
            msg = str(e)
        except Exception as e:
            logger.error(f"Error in /hawk delete: {e}")
            msg = "An error occurred while deleting the project."
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

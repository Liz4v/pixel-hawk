"""Discord bot wiring for pixel-hawk.

Optional Discord bot that runs alongside the polling loop. Reads credentials
from HAWK_BOT_TOKEN and HAWK_COMMAND_PREFIX environment variables. If no token
is set, the bot is silently skipped.

Dispatches slash commands to service functions in commands.py and watch.py.
"""

import asyncio
import contextlib
import os
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from loguru import logger

from ..models.entities import Person, ProjectState
from ..models.palette import ColorsNotInPalette
from ..watcher.projects import Project
from .access import ErrorMsg, check_dm_access, check_guild_access, set_guild_quotas, set_guild_role, set_user_quotas
from .commands import delete_project, edit_project, list_projects, new_project
from .watch import (
    create_watch,
    format_grief_message,
    format_watch_message,
    get_watches_for_projects,
    remove_watch,
    save_watch_message,
)

if TYPE_CHECKING:
    from ..models.entities import WatchMessage


class HawkBot(discord.Client):
    """Discord client for pixel-hawk with slash command support."""

    def __init__(self, command_prefix: str):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.command_prefix = command_prefix
        self._register_commands()

    def _register_commands(self) -> None:
        """Register all slash commands under the command groups."""
        hawk_group = app_commands.Group(name=self.command_prefix, description="Pixel Hawk commands")
        hawk_group.command(name="list", description="List your projects")(self._list)
        hawk_group.command(name="new", description="Upload a new project image")(self._new)
        hawk_group.command(name="edit", description="Edit an existing project")(self._edit)
        hawk_group.command(name="delete", description="Delete a project")(self._delete)
        hawk_group.command(name="watch", description="Post a live-updating status message for a project")(self._watch)
        hawk_group.command(name="unwatch", description="Stop watching a project in this channel")(self._unwatch)
        hawk_group.command(name="help", description="Learn about Pixel Hawk and its commands")(self._help)
        self.tree.add_command(hawk_group)

        admin_group = app_commands.Group(
            name=f"{self.command_prefix}admin",
            description="Pixel Hawk admin commands",
            default_permissions=discord.Permissions(administrator=True),
            guild_only=True,
        )
        admin_group.command(name="role", description="Set the required role for this server")(self._admin_role)
        admin_group.command(name="quota", description="View or set per-user quotas")(self._admin_quota)
        admin_group.command(name="guildquota", description="View or set guild quota ceilings")(self._admin_guildquota)
        self.tree.add_command(admin_group)

        @self.tree.error
        async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
            if isinstance(error, app_commands.CommandOnCooldown):
                await interaction.response.send_message(f"Try again in {error.retry_after:.0f}s.", ephemeral=True)
            else:
                logger.opt(exception=error).error(f"Unhandled error in /{interaction.command.name if interaction.command else '?'}")

    async def _check_access(self, interaction: discord.Interaction) -> Person | None:
        """Check access (guild role or DM). Returns Person on success, sends denial and returns None on failure."""
        try:
            if interaction.guild_id is not None:
                member = interaction.user
                assert isinstance(member, discord.Member)
                role_ids = [str(r.id) for r in member.roles]
                return await check_guild_access(interaction.guild_id, member.id, member.name, role_ids)
            else:
                return await check_dm_access(interaction.user.id)
        except ErrorMsg as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return None

    @app_commands.describe(role="Role required to use this bot in this server")
    async def _admin_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        """Handle /hawkadmin role — set the required role for this guild."""
        assert interaction.guild_id is not None, "Commands must be used in a guild"
        user = interaction.user
        logger.info(f"Admin role from {user.name} (https://discord.com/users/{user.id}): {role.name} ({role.id})")
        try:
            msg = await set_guild_role(user.id, interaction.guild_id, str(role.id))
        except ErrorMsg as e:
            msg = str(e)
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.describe(
        user="Discord user to view/set quotas for",
        projects="Max active projects",
        tiles="Max watched tiles",
    )
    async def _admin_quota(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        projects: int | None = None,
        tiles: int | None = None,
    ) -> None:
        """Handle /hawkadmin quota — view or set per-user quotas."""
        assert interaction.guild_id is not None, "Commands must be used in a guild"
        caller = interaction.user
        logger.info(f"Admin quota from {caller.name}: user={user.id} projects={projects} tiles={tiles}")
        try:
            msg = await set_user_quotas(
                caller.id, user.id, guild_id=interaction.guild_id, projects=projects, tiles=tiles
            )
        except ErrorMsg as e:
            msg = str(e)
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.describe(
        projects="Max active projects ceiling for this server",
        tiles="Max watched tiles ceiling for this server",
    )
    async def _admin_guildquota(
        self,
        interaction: discord.Interaction,
        projects: int | None = None,
        tiles: int | None = None,
    ) -> None:
        """Handle /hawkadmin guildquota — view or set guild quota ceilings."""
        assert interaction.guild_id is not None, "Commands must be used in a guild"
        caller = interaction.user
        logger.info(f"Admin guildquota from {caller.name}: projects={projects} tiles={tiles}")
        try:
            msg = await set_guild_quotas(caller.id, interaction.guild_id, projects=projects, tiles=tiles)
        except ErrorMsg as e:
            msg = str(e)
        await interaction.response.send_message(msg, ephemeral=True)

    async def _help(self, interaction: discord.Interaction) -> None:
        """Handle /hawk help — show help message explaining commands and concepts."""
        p = self.command_prefix
        msg = (
            "## Pixel Hawk\n"
            "Pixel Hawk tracks changes to your pixel art on WPlace. "
            "It polls the WPlace canvas and compares it against your project images, "
            "so you can see completion progress and spot griefing. "
            "It checks one tile every ~2 minutes, with priority to the most "
            "recently updated tiles, so updates are not instant.\n"
            "\n"
            "### Concepts\n"
            "- **Project** — A PNG image of what you want to build on the canvas, "
            "placed at specific coordinates. Pixel Hawk tracks how closely the canvas matches your project.\n"
            "- **Tile** — A fixed-size section of the WPlace canvas. "
            "Projects span one or more tiles, which Pixel Hawk polls periodically for changes.\n"
            "\n"
            "### Project states\n"
            "- **Creating** — Newly uploaded without coordinates. "
            f"Use `/{p} edit` to set coordinates and activate it.\n"
            "- **Active** — Pixel Hawk actively polls this project's tiles and tracks changes.\n"
            "- **Passive** — Not polled on its own, but piggybacks on tiles polled for other active projects.\n"
            "- **Inactive** — Paused. Tiles are unlinked and no tracking occurs.\n"
            "\n"
            f"### Commands\n"
            f"- **/{p} new** — Upload a new project image (PNG, WPlace palette)\n"
            f"- **/{p} edit** — Edit a project's image, name, coordinates, or state\n"
            f"- **/{p} delete** — Permanently delete a project\n"
            f"- **/{p} list** — List all your projects with current stats\n"
            f"- **/{p} watch** — Post a live-updating status message for a project\n"
            f"- **/{p} unwatch** — Remove a live status message from this channel\n"
            f"- **/{p} help** — Show this message"
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.checks.cooldown(rate=2, per=5.0)
    async def _list(self, interaction: discord.Interaction) -> None:
        """Handle /hawk list — show the calling user's projects."""
        if await self._check_access(interaction) is None:
            return
        msg = await list_projects(interaction.user.id)
        await interaction.response.send_message(msg or "You have no projects.", ephemeral=True)

    @app_commands.checks.cooldown(rate=1, per=10.0)
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
            logger.exception(f"Error in /hawk new: {e}")
            msg = "An error occurred while creating the project."
        await interaction.followup.send(msg or "No linked account found.", ephemeral=True)

    @app_commands.checks.cooldown(rate=1, per=10.0)
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
            logger.exception(f"Error in /hawk edit: {e}")
            msg = "An error occurred while editing the project."
        await interaction.followup.send(msg or "No linked account found.", ephemeral=True)

    @app_commands.checks.cooldown(rate=2, per=5.0)
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
            logger.exception(f"Error in /hawk delete: {e}")
            msg = "An error occurred while deleting the project."
        await interaction.followup.send(msg or "No linked account found.", ephemeral=True)

    @app_commands.checks.cooldown(rate=1, per=10.0)
    @app_commands.describe(project_id="Project ID (4-digit number)")
    async def _watch(self, interaction: discord.Interaction, project_id: int) -> None:
        """Handle /hawk watch — post a live-updating project status message."""
        if await self._check_access(interaction) is None:
            return
        channel_id = interaction.channel_id
        assert channel_id is not None, "Commands must be used in a channel"
        try:
            content, info_id = await create_watch(interaction.user.id, project_id, channel_id, interaction.guild_id or 0)
        except ErrorMsg as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await interaction.response.send_message(content)
        sent = await interaction.original_response()
        await save_watch_message(info_id, channel_id, sent.id)

    @app_commands.checks.cooldown(rate=2, per=5.0)
    @app_commands.describe(project_id="Project ID (4-digit number)")
    async def _unwatch(self, interaction: discord.Interaction, project_id: int) -> None:
        """Handle /hawk unwatch — stop watching a project in this channel."""
        if await self._check_access(interaction) is None:
            return
        channel_id = interaction.channel_id
        assert channel_id is not None, "Commands must be used in a channel"
        try:
            message_id = await remove_watch(interaction.user.id, project_id, channel_id)
        except ErrorMsg as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        channel = interaction.channel
        if isinstance(channel, (discord.TextChannel, discord.DMChannel)):
            try:
                msg = await channel.fetch_message(message_id)
                await msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
        await interaction.response.send_message(
            f"Stopped watching project **{project_id:04}** in this channel.", ephemeral=True
        )

    async def update_watches(self, project_ids: list[int]) -> None:
        """Edit all watch messages for the given diffed projects with fresh stats."""
        watches = await get_watches_for_projects(project_ids)
        for watch in watches:
            try:
                content = await format_watch_message(watch.project)
                channel = self.get_channel(watch.channel_id)
                if not isinstance(channel, (discord.TextChannel, discord.DMChannel)):
                    channel = await self.fetch_channel(watch.channel_id)
                assert isinstance(channel, (discord.TextChannel, discord.DMChannel))
                msg = await channel.fetch_message(watch.message_id)
                await msg.edit(content=content)
                logger.debug(f"Updated watch: project={watch.project.id:04} channel={watch.channel_id}")
            except discord.NotFound:
                logger.info(f"Watch message gone (404): project={watch.project.id:04} channel={watch.channel_id}")
                await watch.delete()
            except discord.Forbidden:
                logger.info(
                    f"Watch message inaccessible (403): project={watch.project.id:04} channel={watch.channel_id}"
                )
                await watch.delete()
            except Exception as e:
                logger.warning(f"Failed to update watch for project {watch.project.id:04}: {e}")

    async def notify_griefs(self, projects: list[Project]) -> None:
        """Send grief alert messages to channels watching projects with grief reports."""
        griefed = [p for p in projects if p.grief_report]
        if not griefed:
            return
        proj_ids = [p.info.id for p in griefed]
        watches = await get_watches_for_projects(proj_ids)
        # Group watches by project ID
        watches_by_project: dict[int, list[WatchMessage]] = {}
        for watch in watches:
            watches_by_project.setdefault(watch.project_id, []).append(watch)
        for proj in griefed:
            content = format_grief_message(proj)
            for watch in watches_by_project.get(proj.info.id, []):
                try:
                    channel = self.get_channel(watch.channel_id)
                    if not isinstance(channel, (discord.TextChannel, discord.DMChannel)):
                        channel = await self.fetch_channel(watch.channel_id)
                    assert isinstance(channel, (discord.TextChannel, discord.DMChannel))
                    await channel.send(content)
                except discord.NotFound:
                    logger.info(f"Grief channel gone (404): project={proj.info.id:04} channel={watch.channel_id}")
                    await watch.delete()
                except discord.Forbidden:
                    logger.info(f"Grief channel forbidden (403): project={proj.info.id:04} channel={watch.channel_id}")
                    await watch.delete()
                except Exception as e:
                    logger.warning(f"Failed to send grief alert for project {proj.info.id:04}: {e}")

    async def setup_hook(self) -> None:
        await self.tree.sync()
        logger.info("Discord bot command tree synced")

    async def on_ready(self) -> None:
        logger.info(f"Discord bot connected as {self.user}")


@contextlib.asynccontextmanager
async def maybe_bot():
    """Start the Discord bot if a token is configured, otherwise silently skip.

    Yields the HawkBot instance (or None if no token). The caller can use the
    bot reference to edit watch messages from the polling loop.
    """
    token = os.environ.get("HAWK_BOT_TOKEN", "")
    if not token:
        logger.debug("No HAWK_BOT_TOKEN set, skipping Discord bot")
        yield None
        return

    bot = HawkBot(os.environ.get("HAWK_COMMAND_PREFIX", "hawk"))
    asyncio.create_task(bot.start(token))
    yield bot
    await bot.close()

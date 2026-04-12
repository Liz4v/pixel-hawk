"""Tests for Discord bot wiring (interactions.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord

from pixel_hawk.interface.access import ErrorMsg
from pixel_hawk.interface.interactions import HawkBot, maybe_bot
from pixel_hawk.models.person import Person
from pixel_hawk.models.project import ProjectState
from pixel_hawk.models.watch import WatchMessage
from pixel_hawk.models.geometry import Size
from pixel_hawk.models.griefing import GriefReport, Painter

from pixel_hawk.watcher.projects import Project


# HawkBot tests


class TestHawkBot:
    def test_construction(self):
        bot = HawkBot("hawk")
        assert bot.command_prefix == "hawk"
        assert bot.tree is not None

    def test_command_tree_has_hawk_group(self):
        bot = HawkBot("hawk")
        commands = bot.tree.get_commands()
        names = [c.name for c in commands]
        assert "hawk" in names

    def test_custom_command_prefix(self):
        bot = HawkBot(command_prefix="testhawk")
        assert bot.command_prefix == "testhawk"
        commands = bot.tree.get_commands()
        names = [c.name for c in commands]
        assert "testhawk" in names
        assert "hawk" not in names

    async def test_on_ready_logs(self):
        bot = HawkBot("hawk")
        # on_ready just logs, should not raise
        bot._connection.user = None  # type: ignore[assignment]
        await bot.on_ready()

    async def test_setup_hook_syncs_tree(self):
        bot = HawkBot("hawk")
        bot.tree.sync = AsyncMock()  # type: ignore[method-assign]
        await bot.setup_hook()
        bot.tree.sync.assert_awaited_once()


# maybe_bot tests


class TestMaybeBot:
    async def test_yields_without_bot_when_no_config(self, setup_config):
        async with maybe_bot():
            pass  # should not raise

    async def test_starts_and_closes_bot_with_config(self, setup_config, monkeypatch):
        monkeypatch.setenv("HAWK_BOT_TOKEN", "fake-token")

        with (
            patch.object(HawkBot, "start", new_callable=AsyncMock),
            patch.object(HawkBot, "close", new_callable=AsyncMock) as mock_close,
        ):
            async with maybe_bot():
                pass
            mock_close.assert_awaited_once()


# HawkBot command tree tests


class TestHawkBotCommands:
    def test_command_tree_has_new(self):
        bot = HawkBot("hawk")
        hawk = next(c for c in bot.tree.get_commands() if c.name == "hawk")
        names = [c.name for c in hawk.commands]
        assert "new" in names

    def test_command_tree_has_edit(self):
        bot = HawkBot("hawk")
        hawk = next(c for c in bot.tree.get_commands() if c.name == "hawk")
        names = [c.name for c in hawk.commands]
        assert "edit" in names

    def test_command_tree_has_delete(self):
        bot = HawkBot("hawk")
        hawk = next(c for c in bot.tree.get_commands() if c.name == "hawk")
        names = [c.name for c in hawk.commands]
        assert "delete" in names

    def test_command_tree_has_no_sa(self):
        bot = HawkBot("hawk")
        hawk = next(c for c in bot.tree.get_commands() if c.name == "hawk")
        names = [c.name for c in hawk.commands]
        assert "sa" not in names

    def test_admin_group_exists(self):
        bot = HawkBot("hawk")
        names = [c.name for c in bot.tree.get_commands()]
        assert "hawkadmin" in names

    def test_admin_group_has_role(self):
        bot = HawkBot("hawk")
        admin = next(c for c in bot.tree.get_commands() if c.name == "hawkadmin")
        names = [c.name for c in admin.commands]
        assert "role" in names

    def test_admin_group_has_quota(self):
        bot = HawkBot("hawk")
        admin = next(c for c in bot.tree.get_commands() if c.name == "hawkadmin")
        names = [c.name for c in admin.commands]
        assert "quota" in names

    def test_admin_group_has_guildquota(self):
        bot = HawkBot("hawk")
        admin = next(c for c in bot.tree.get_commands() if c.name == "hawkadmin")
        names = [c.name for c in admin.commands]
        assert "guildquota" in names

    def test_admin_group_has_admin(self):
        bot = HawkBot("hawk")
        admin = next(c for c in bot.tree.get_commands() if c.name == "hawkadmin")
        names = [c.name for c in admin.commands]
        assert "admin" in names

    def test_admin_group_has_administrator_permissions(self):
        bot = HawkBot("hawk")
        admin = next(c for c in bot.tree.get_commands() if c.name == "hawkadmin")
        assert admin.default_permissions == discord.Permissions(administrator=True)

    def test_custom_prefix_admin_group(self):
        bot = HawkBot("testhawk")
        names = [c.name for c in bot.tree.get_commands()]
        assert "testhawkadmin" in names


def _mock_interaction(*, guild_id=999, user_id=12345, user_name="TestUser", role_ids=None):
    """Create a mock discord.Interaction with a Member user."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id

    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.name = user_name
    roles = []
    for rid in role_ids or []:
        role = MagicMock(spec=discord.Role)
        role.id = rid
        roles.append(role)
    member.roles = roles
    interaction.user = member

    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


# _check_access tests


class TestCheckAccess:
    async def test_success_returns_person(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction(role_ids=[111])
        fake_person = MagicMock(spec=Person)

        with patch(
            "pixel_hawk.interface.interactions.check_guild_access", new_callable=AsyncMock, return_value=fake_person
        ):
            result = await bot._check_access(interaction)

        assert result is fake_person
        interaction.response.send_message.assert_not_awaited()

    async def test_denied_sends_error_and_returns_none(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction(role_ids=[222])

        with patch(
            "pixel_hawk.interface.interactions.check_guild_access",
            new_callable=AsyncMock,
            side_effect=ErrorMsg("You need the **artists** role"),
        ):
            result = await bot._check_access(interaction)

        assert result is None
        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args
        assert "artists" in msg.args[0]
        assert msg.kwargs["ephemeral"] is True


# Admin admin (coadmin) command tests


class TestAdminCoadminCommand:
    async def test_coadmin_success(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction(guild_id=555)
        target_user = MagicMock(spec=discord.User)
        target_user.id = 99999
        target_user.display_name = "TargetUser"

        with patch(
            "pixel_hawk.interface.interactions.coadmin",
            new_callable=AsyncMock,
            return_value="Admin access granted to TargetUser.",
        ):
            await bot._admin_coadmin(interaction, target_user)

        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args
        assert "TargetUser" in msg.args[0]
        assert msg.kwargs["ephemeral"] is True

    async def test_coadmin_error(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction(guild_id=555)
        target_user = MagicMock(spec=discord.User)
        target_user.id = 99999
        target_user.display_name = "TargetUser"

        with patch(
            "pixel_hawk.interface.interactions.coadmin",
            new_callable=AsyncMock,
            side_effect=ErrorMsg("Admin access required."),
        ):
            await bot._admin_coadmin(interaction, target_user)

        msg = interaction.response.send_message.call_args
        assert "Admin access required" in msg.args[0]


# Admin role command tests


class TestAdminRoleCommand:
    async def test_role_success(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction(guild_id=555)
        role = MagicMock(spec=discord.Role)
        role.id = 777
        role.name = "painters"

        with patch(
            "pixel_hawk.interface.interactions.set_guild_role",
            new_callable=AsyncMock,
            return_value="Required role set to <@&777> for this server.",
        ):
            await bot._admin_role(interaction, role)

        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args
        assert "777" in msg.args[0]
        assert msg.kwargs["ephemeral"] is True

    async def test_role_not_admin(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        role = MagicMock(spec=discord.Role)
        role.id = 777
        role.name = "painters"

        with patch(
            "pixel_hawk.interface.interactions.set_guild_role",
            new_callable=AsyncMock,
            side_effect=ErrorMsg("Admin access required."),
        ):
            await bot._admin_role(interaction, role)

        msg = interaction.response.send_message.call_args
        assert "Admin access required" in msg.args[0]


# Admin quota command tests


class TestAdminQuotaCommand:
    async def test_view_quotas(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        target_user = MagicMock(spec=discord.User)
        target_user.id = 99999

        with patch(
            "pixel_hawk.interface.interactions.set_user_quotas",
            new_callable=AsyncMock,
            return_value="**User** quotas:\n  Active projects: 0 / 50",
        ):
            await bot._admin_quota(interaction, target_user)

        msg = interaction.response.send_message.call_args
        assert "50" in msg.args[0]

    async def test_set_quotas(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        target_user = MagicMock(spec=discord.User)
        target_user.id = 99999

        with patch(
            "pixel_hawk.interface.interactions.set_user_quotas",
            new_callable=AsyncMock,
            return_value="Updated quotas for **User**:\n  Active projects limit: 10",
        ):
            await bot._admin_quota(interaction, target_user, projects=10)

        msg = interaction.response.send_message.call_args
        assert "10" in msg.args[0]

    async def test_error_msg(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        target_user = MagicMock(spec=discord.User)
        target_user.id = 99999

        with patch(
            "pixel_hawk.interface.interactions.set_user_quotas",
            new_callable=AsyncMock,
            side_effect=ErrorMsg("User not found."),
        ):
            await bot._admin_quota(interaction, target_user)

        msg = interaction.response.send_message.call_args
        assert "not found" in msg.args[0]


# Admin guildquota command tests


class TestAdminGuildQuotaCommand:
    async def test_view_guild_quotas(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with patch(
            "pixel_hawk.interface.interactions.set_guild_quotas",
            new_callable=AsyncMock,
            return_value="Guild quota ceilings:\n  Max active projects: 50\n  Max watched tiles: 10",
        ):
            await bot._admin_guildquota(interaction)

        msg = interaction.response.send_message.call_args
        assert "50" in msg.args[0]

    async def test_set_guild_quotas(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with patch(
            "pixel_hawk.interface.interactions.set_guild_quotas",
            new_callable=AsyncMock,
            return_value="Updated guild quota ceilings:\n  Max active projects: 100",
        ):
            await bot._admin_guildquota(interaction, projects=100)

        msg = interaction.response.send_message.call_args
        assert "100" in msg.args[0]

    async def test_error_msg(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with patch(
            "pixel_hawk.interface.interactions.set_guild_quotas",
            new_callable=AsyncMock,
            side_effect=ErrorMsg("This server has not been configured."),
        ):
            await bot._admin_guildquota(interaction)

        msg = interaction.response.send_message.call_args
        assert "not been configured" in msg.args[0]


# _new handler tests


class TestNewHandler:
    async def test_denied_returns_early(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=None):
            await bot._new(interaction, MagicMock())

        interaction.response.defer.assert_not_awaited()

    async def test_success(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        attachment = MagicMock(spec=discord.Attachment)
        attachment.read = AsyncMock(return_value=b"png-data")
        attachment.filename = "5_7_0_0.png"

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch("pixel_hawk.interface.interactions.new_project", new_callable=AsyncMock, return_value="Created!"),
        ):
            await bot._new(interaction, attachment)

        interaction.response.defer.assert_awaited_once()
        msg = interaction.followup.send.call_args
        assert msg.args[0] == "Created!"

    async def test_error_msg(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        attachment = MagicMock(spec=discord.Attachment)
        attachment.read = AsyncMock(return_value=b"bad")
        attachment.filename = "test.png"

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.new_project",
                new_callable=AsyncMock,
                side_effect=ErrorMsg("Not a PNG file."),
            ),
        ):
            await bot._new(interaction, attachment)

        msg = interaction.followup.send.call_args
        assert "Not a PNG" in msg.args[0]

    async def test_palette_error(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        attachment = MagicMock(spec=discord.Attachment)
        attachment.read = AsyncMock(return_value=b"data")
        attachment.filename = "test.png"

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.new_project",
                new_callable=AsyncMock,
                side_effect=ErrorMsg("Found 1 pixels not in the palette (#010203)\n\nyawcc"),
            ),
        ):
            await bot._new(interaction, attachment)

        msg = interaction.followup.send.call_args
        assert "not in" in msg.args[0].lower() or "palette" in msg.args[0].lower()

    async def test_unexpected_error(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        attachment = MagicMock(spec=discord.Attachment)
        attachment.read = AsyncMock(return_value=b"data")
        attachment.filename = "test.png"

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.new_project",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
        ):
            await bot._new(interaction, attachment)

        msg = interaction.followup.send.call_args
        assert "error occurred" in msg.args[0].lower()


# _edit handler tests


class TestEditHandler:
    async def test_denied_returns_early(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=None):
            await bot._edit(interaction, 1234)

        interaction.response.defer.assert_not_awaited()

    async def test_name_only(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.edit_project", new_callable=AsyncMock, return_value="Updated!"
            ) as mock_edit,
        ):
            await bot._edit(interaction, 1234, name="new name")

        mock_edit.assert_awaited_once_with(
            12345,
            1234,
            image_data=None,
            image_filename=None,
            name="new name",
            coords=None,
            state=None,
            wplace_size=Size(),
        )
        msg = interaction.followup.send.call_args
        assert msg.args[0] == "Updated!"

    async def test_with_image(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        attachment = MagicMock(spec=discord.Attachment)
        attachment.read = AsyncMock(return_value=b"png-data")
        attachment.filename = "5_7_0_0.png"

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.edit_project", new_callable=AsyncMock, return_value="Image updated!"
            ) as mock_edit,
        ):
            await bot._edit(interaction, 1234, image=attachment)

        mock_edit.assert_awaited_once_with(
            12345,
            1234,
            image_data=b"png-data",
            image_filename="5_7_0_0.png",
            name=None,
            coords=None,
            state=None,
            wplace_size=Size(),
        )

    async def test_with_state_choice(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        state_choice = MagicMock()
        state_choice.value = int(ProjectState.PASSIVE)

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.edit_project", new_callable=AsyncMock, return_value="State changed!"
            ) as mock_edit,
        ):
            await bot._edit(interaction, 1234, state=state_choice)

        mock_edit.assert_awaited_once_with(
            12345,
            1234,
            image_data=None,
            image_filename=None,
            name=None,
            coords=None,
            state=ProjectState.PASSIVE,
            wplace_size=Size(),
        )

    async def test_error_msg(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.edit_project",
                new_callable=AsyncMock,
                side_effect=ErrorMsg("not yours"),
            ),
        ):
            await bot._edit(interaction, 1234, name="x")

        msg = interaction.followup.send.call_args
        assert "not yours" in msg.args[0]

    async def test_unexpected_error(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.edit_project",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
        ):
            await bot._edit(interaction, 1234, name="x")

        msg = interaction.followup.send.call_args
        assert "error occurred" in msg.args[0].lower()


# _delete handler tests


class TestDeleteHandler:
    async def test_denied_returns_early(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=None):
            await bot._delete(interaction, 1234)

        interaction.response.defer.assert_not_awaited()

    async def test_success(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch("pixel_hawk.interface.interactions.delete_project", new_callable=AsyncMock, return_value="Deleted!"),
        ):
            await bot._delete(interaction, 1234)

        interaction.response.defer.assert_awaited_once()
        msg = interaction.followup.send.call_args
        assert msg.args[0] == "Deleted!"

    async def test_error_msg(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.delete_project",
                new_callable=AsyncMock,
                side_effect=ErrorMsg("not found"),
            ),
        ):
            await bot._delete(interaction, 9999)

        msg = interaction.followup.send.call_args
        assert "not found" in msg.args[0]

    async def test_unexpected_error(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.delete_project",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
        ):
            await bot._delete(interaction, 1234)

        msg = interaction.followup.send.call_args
        assert "error occurred" in msg.args[0].lower()


# _list with access check


class TestListWithAccessCheck:
    async def test_denied_returns_early(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=None):
            await bot._list(interaction)

        # Only _check_access should have been called, not send_message again
        interaction.response.send_message.assert_not_awaited()

    async def test_allowed_calls_list_projects(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        fake_person = MagicMock(spec=Person)

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=fake_person),
            patch(
                "pixel_hawk.interface.interactions.list_projects", new_callable=AsyncMock, return_value="Projects here"
            ),
        ):
            await bot._list(interaction)

        interaction.response.send_message.assert_awaited_once()
        assert "Projects here" in interaction.response.send_message.call_args.args[0]


# Command tree: watch/unwatch


class TestWatchCommandTree:
    def test_command_tree_has_watch(self):
        bot = HawkBot("hawk")
        hawk = next(c for c in bot.tree.get_commands() if c.name == "hawk")
        names = [c.name for c in hawk.commands]
        assert "watch" in names

    def test_command_tree_has_unwatch(self):
        bot = HawkBot("hawk")
        hawk = next(c for c in bot.tree.get_commands() if c.name == "hawk")
        names = [c.name for c in hawk.commands]
        assert "unwatch" in names


# _watch handler tests


class TestWatchHandler:
    async def test_denied_returns_early(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=None):
            await bot._watch(interaction, 1234)

        interaction.response.send_message.assert_not_awaited()

    async def test_error_msg(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        interaction.channel_id = 500

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.create_watch",
                new_callable=AsyncMock,
                side_effect=ErrorMsg("not found"),
            ),
        ):
            await bot._watch(interaction, 9999)

        msg = interaction.response.send_message.call_args
        assert "not found" in msg.args[0]
        assert msg.kwargs["ephemeral"] is True

    async def test_success(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        interaction.channel_id = 500
        sent_msg = MagicMock()
        sent_msg.id = 12345
        interaction.original_response = AsyncMock(return_value=sent_msg)

        mock_info = MagicMock()
        mock_info.id = 42

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.create_watch",
                new_callable=AsyncMock,
                return_value=("Stats here", mock_info),
            ),
            patch("pixel_hawk.interface.interactions._make_watch_files", return_value=[]),
            patch("pixel_hawk.interface.interactions.save_watch_message", new_callable=AsyncMock) as mock_save,
        ):
            await bot._watch(interaction, 42)

        # Non-ephemeral message sent
        msg = interaction.response.send_message.call_args
        assert msg.args[0] == "Stats here"
        assert "ephemeral" not in msg.kwargs or msg.kwargs.get("ephemeral") is not True
        # Watch message saved
        mock_save.assert_awaited_once_with(42, 500, 12345)


# _unwatch handler tests


class TestUnwatchHandler:
    async def test_denied_returns_early(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()

        with patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=None):
            await bot._unwatch(interaction, 1234)

        interaction.response.send_message.assert_not_awaited()

    async def test_error_msg(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        interaction.channel_id = 500

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.remove_watch",
                new_callable=AsyncMock,
                side_effect=ErrorMsg("not being watched"),
            ),
        ):
            await bot._unwatch(interaction, 9999)

        msg = interaction.response.send_message.call_args
        assert "not being watched" in msg.args[0]
        assert msg.kwargs["ephemeral"] is True

    async def test_success(self):
        bot = HawkBot("hawk")
        interaction = _mock_interaction()
        interaction.channel_id = 500
        channel = MagicMock(spec=discord.TextChannel)
        fetched_msg = MagicMock()
        fetched_msg.delete = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=fetched_msg)
        interaction.channel = channel

        with (
            patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=MagicMock()),
            patch(
                "pixel_hawk.interface.interactions.remove_watch",
                new_callable=AsyncMock,
                return_value=555,
            ),
        ):
            await bot._unwatch(interaction, 42)

        # Old message deleted
        channel.fetch_message.assert_awaited_once_with(555)
        fetched_msg.delete.assert_awaited_once()
        # Confirmation sent ephemeral
        msg = interaction.response.send_message.call_args
        assert "Stopped watching" in msg.args[0]
        assert msg.kwargs["ephemeral"] is True


# update_watches tests


class TestUpdateWatches:
    async def test_edits_messages(self):
        bot = HawkBot("hawk")
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_msg = MagicMock()
        mock_msg.edit = AsyncMock()
        mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
        bot.get_channel = MagicMock(return_value=mock_channel)

        watch = MagicMock(spec=WatchMessage)
        watch.project = MagicMock()
        watch.project.id = 1
        watch.channel_id = 100
        watch.message_id = 200
        watch.delete = AsyncMock()

        with (
            patch(
                "pixel_hawk.interface.interactions.get_watches_for_projects",
                new_callable=AsyncMock,
                return_value=[watch],
            ),
            patch(
                "pixel_hawk.interface.interactions.format_watch_message",
                new_callable=AsyncMock,
                return_value="Updated stats",
            ),
            patch("pixel_hawk.interface.interactions._make_watch_files", return_value=[]),
        ):
            await bot.update_watches([1])

        mock_msg.edit.assert_awaited_once_with(content="Updated stats", attachments=[])

    async def test_deletes_watch_on_not_found(self):
        bot = HawkBot("hawk")
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(), "gone"))
        bot.get_channel = MagicMock(return_value=mock_channel)

        watch = MagicMock(spec=WatchMessage)
        watch.project = MagicMock()
        watch.project.id = 1
        watch.channel_id = 100
        watch.message_id = 200
        watch.delete = AsyncMock()

        with (
            patch(
                "pixel_hawk.interface.interactions.get_watches_for_projects",
                new_callable=AsyncMock,
                return_value=[watch],
            ),
            patch(
                "pixel_hawk.interface.interactions.format_watch_message",
                new_callable=AsyncMock,
                return_value="x",
            ),
        ):
            await bot.update_watches([1])

        watch.delete.assert_awaited_once()

    async def test_deletes_watch_on_forbidden(self):
        bot = HawkBot("hawk")
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.fetch_message = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "nope"))
        bot.get_channel = MagicMock(return_value=mock_channel)

        watch = MagicMock(spec=WatchMessage)
        watch.project = MagicMock()
        watch.project.id = 1
        watch.channel_id = 100
        watch.message_id = 200
        watch.delete = AsyncMock()

        with (
            patch(
                "pixel_hawk.interface.interactions.get_watches_for_projects",
                new_callable=AsyncMock,
                return_value=[watch],
            ),
            patch(
                "pixel_hawk.interface.interactions.format_watch_message",
                new_callable=AsyncMock,
                return_value="x",
            ),
        ):
            await bot.update_watches([1])

        watch.delete.assert_awaited_once()

    async def test_handles_unexpected_error(self):
        bot = HawkBot("hawk")
        bot.get_channel = MagicMock(side_effect=RuntimeError("boom"))

        watch = MagicMock(spec=WatchMessage)
        watch.project = MagicMock()
        watch.project.id = 1
        watch.channel_id = 100
        watch.message_id = 200
        watch.delete = AsyncMock()

        with (
            patch(
                "pixel_hawk.interface.interactions.get_watches_for_projects",
                new_callable=AsyncMock,
                return_value=[watch],
            ),
            patch(
                "pixel_hawk.interface.interactions.format_watch_message",
                new_callable=AsyncMock,
                return_value="x",
            ),
        ):
            # Should not raise
            await bot.update_watches([1])

        watch.delete.assert_not_awaited()

    async def test_fetches_channel_when_not_cached(self):
        bot = HawkBot("hawk")
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_msg = MagicMock()
        mock_msg.edit = AsyncMock()
        mock_channel.fetch_message = AsyncMock(return_value=mock_msg)
        bot.get_channel = MagicMock(return_value=None)  # Not cached
        bot.fetch_channel = AsyncMock(return_value=mock_channel)

        watch = MagicMock(spec=WatchMessage)
        watch.project = MagicMock()
        watch.project.id = 1
        watch.channel_id = 100
        watch.message_id = 200
        watch.delete = AsyncMock()

        with (
            patch(
                "pixel_hawk.interface.interactions.get_watches_for_projects",
                new_callable=AsyncMock,
                return_value=[watch],
            ),
            patch(
                "pixel_hawk.interface.interactions.format_watch_message",
                new_callable=AsyncMock,
                return_value="Stats",
            ),
            patch("pixel_hawk.interface.interactions._make_watch_files", return_value=[]),
        ):
            await bot.update_watches([1])

        bot.fetch_channel.assert_awaited_once_with(100)
        mock_msg.edit.assert_awaited_once()


# maybe_bot yields bot


class TestMaybeBotYieldsBot:
    async def test_yields_none_without_config(self, setup_config):
        async with maybe_bot() as bot:
            assert bot is None

    async def test_yields_bot_with_config(self, setup_config, monkeypatch):
        monkeypatch.setenv("HAWK_BOT_TOKEN", "fake-token")

        with (
            patch.object(HawkBot, "start", new_callable=AsyncMock),
            patch.object(HawkBot, "close", new_callable=AsyncMock),
        ):
            async with maybe_bot() as bot:
                assert isinstance(bot, HawkBot)


# notify_griefs tests


def _grief_proj(project_id: int = 1, grief: bool = True) -> Project:
    """Build a minimal Project stub for notify_griefs testing."""
    proj = object.__new__(Project)
    info = MagicMock()
    info.id = project_id
    info.owner = MagicMock()
    info.owner.discord_id = 12345
    info.owner.name = "Owner"
    info.name = "test"
    info.rectangle = MagicMock()
    info.rectangle.to_link.return_value = "https://wplace.live/?lat=0&lng=0&zoom=10"
    proj.info = info
    if grief:
        painters = (Painter(user_id=1, user_name="Griefer", alliance_name="", discord_id="", discord_name=""),)
        proj.grief_report = GriefReport(regress_count=100, painters=painters)
    else:
        proj.grief_report = GriefReport()
    return proj


class TestNotifyGriefs:
    async def test_no_grief_reports_skips(self):
        """No projects with grief reports → no watches queried."""
        bot = HawkBot("hawk")
        proj = _grief_proj(grief=False)

        with patch("pixel_hawk.interface.interactions.get_watches_for_projects", new_callable=AsyncMock) as mock_get:
            await bot.notify_griefs([proj])

        mock_get.assert_not_awaited()

    async def test_sends_message_to_channel(self):
        bot = HawkBot("hawk")
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock()
        bot.get_channel = MagicMock(return_value=mock_channel)

        watch = MagicMock(spec=WatchMessage)
        watch.project_id = 1
        watch.channel_id = 100
        watch.delete = AsyncMock()

        proj = _grief_proj(project_id=1)

        with patch(
            "pixel_hawk.interface.interactions.get_watches_for_projects",
            new_callable=AsyncMock,
            return_value=[watch],
        ):
            await bot.notify_griefs([proj])

        mock_channel.send.assert_awaited_once()
        content = mock_channel.send.call_args.args[0]
        assert "Grief alert" in content
        assert "Griefer" in content

    async def test_deletes_watch_on_not_found(self):
        bot = HawkBot("hawk")
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock(side_effect=discord.NotFound(MagicMock(), "gone"))
        bot.get_channel = MagicMock(return_value=mock_channel)

        watch = MagicMock(spec=WatchMessage)
        watch.project_id = 1
        watch.channel_id = 100
        watch.delete = AsyncMock()

        with patch(
            "pixel_hawk.interface.interactions.get_watches_for_projects",
            new_callable=AsyncMock,
            return_value=[watch],
        ):
            await bot.notify_griefs([_grief_proj()])

        watch.delete.assert_awaited_once()

    async def test_deletes_watch_on_forbidden(self):
        bot = HawkBot("hawk")
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(), "nope"))
        bot.get_channel = MagicMock(return_value=mock_channel)

        watch = MagicMock(spec=WatchMessage)
        watch.project_id = 1
        watch.channel_id = 100
        watch.delete = AsyncMock()

        with patch(
            "pixel_hawk.interface.interactions.get_watches_for_projects",
            new_callable=AsyncMock,
            return_value=[watch],
        ):
            await bot.notify_griefs([_grief_proj()])

        watch.delete.assert_awaited_once()

    async def test_handles_unexpected_error(self):
        bot = HawkBot("hawk")
        bot.get_channel = MagicMock(side_effect=RuntimeError("boom"))

        watch = MagicMock(spec=WatchMessage)
        watch.project_id = 1
        watch.channel_id = 100
        watch.delete = AsyncMock()

        with patch(
            "pixel_hawk.interface.interactions.get_watches_for_projects",
            new_callable=AsyncMock,
            return_value=[watch],
        ):
            await bot.notify_griefs([_grief_proj()])

        watch.delete.assert_not_awaited()

    async def test_fetches_channel_when_not_cached(self):
        bot = HawkBot("hawk")
        mock_channel = MagicMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock()
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(return_value=mock_channel)

        watch = MagicMock(spec=WatchMessage)
        watch.project_id = 1
        watch.channel_id = 100
        watch.delete = AsyncMock()

        with patch(
            "pixel_hawk.interface.interactions.get_watches_for_projects",
            new_callable=AsyncMock,
            return_value=[watch],
        ):
            await bot.notify_griefs([_grief_proj()])

        bot.fetch_channel.assert_awaited_once_with(100)
        mock_channel.send.assert_awaited_once()

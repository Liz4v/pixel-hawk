"""Tests for Discord bot wiring (interactions.py)."""

from unittest.mock import AsyncMock, MagicMock, patch

import discord

from pixel_hawk.interface.commands import ErrorMsg
from pixel_hawk.interface.interactions import (
    HawkBot,
    maybe_bot,
)
from pixel_hawk.models.config import get_config
from pixel_hawk.models.entities import Person


def _invalidate_config_toml():
    """Clear the cached_property so config.toml is re-read."""
    cfg = get_config()
    # cached_property stores the value in the instance __dict__
    cfg.__dict__.pop("config_toml", None)
    cfg.__dict__.pop("discord", None)


# HawkBot tests


class TestHawkBot:
    def test_construction(self):
        bot = HawkBot("test-admin-token", "hawk")
        assert bot.admin_token == "test-admin-token"
        assert bot.command_prefix == "hawk"
        assert bot.tree is not None

    def test_command_tree_has_hawk_group(self):
        bot = HawkBot("test-token", "hawk")
        commands = bot.tree.get_commands()
        names = [c.name for c in commands]
        assert "hawk" in names

    def test_custom_command_prefix(self):
        bot = HawkBot("test-token", command_prefix="testhawk")
        assert bot.command_prefix == "testhawk"
        commands = bot.tree.get_commands()
        names = [c.name for c in commands]
        assert "testhawk" in names
        assert "hawk" not in names

    async def test_on_ready_logs(self):
        bot = HawkBot("test-token", "hawk")
        # on_ready just logs, should not raise
        bot._connection.user = None  # type: ignore[assignment]
        await bot.on_ready()

    async def test_setup_hook_syncs_tree(self):
        bot = HawkBot("test-token", "hawk")
        bot.tree.sync = AsyncMock()  # type: ignore[method-assign]
        await bot.setup_hook()
        bot.tree.sync.assert_awaited_once()


# maybe_bot tests


class TestMaybeBot:
    async def test_yields_without_bot_when_no_config(self, setup_config):
        async with maybe_bot():
            pass  # should not raise

    async def test_starts_and_closes_bot_with_config(self, setup_config):
        config_path = get_config().home / "config.toml"
        config_path.write_text('[discord]\nbot_token = "fake-token"\n')
        _invalidate_config_toml()

        with (
            patch.object(HawkBot, "start", new_callable=AsyncMock),
            patch.object(HawkBot, "close", new_callable=AsyncMock) as mock_close,
        ):
            async with maybe_bot():
                pass
            mock_close.assert_awaited_once()


# HawkBot command tree tests


class TestHawkBotNewCommands:
    def test_command_tree_has_new(self):
        bot = HawkBot("test-token", "hawk")
        hawk = next(c for c in bot.tree.get_commands() if c.name == "hawk")
        names = [c.name for c in hawk.commands]
        assert "new" in names

    def test_command_tree_has_edit(self):
        bot = HawkBot("test-token", "hawk")
        hawk = next(c for c in bot.tree.get_commands() if c.name == "hawk")
        names = [c.name for c in hawk.commands]
        assert "edit" in names


def _mock_interaction(*, guild_id=999, user_id=12345, user_name="TestUser", role_names=None):
    """Create a mock discord.Interaction with a Member user."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild_id = guild_id

    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.name = user_name
    roles = []
    for name in role_names or []:
        role = MagicMock(spec=discord.Role)
        role.name = name
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
        bot = HawkBot("test-token", "hawk")
        interaction = _mock_interaction(role_names=["artists"])
        fake_person = MagicMock(spec=Person)

        with patch(
            "pixel_hawk.interface.interactions.check_guild_access", new_callable=AsyncMock, return_value=fake_person
        ):
            result = await bot._check_access(interaction)

        assert result is fake_person
        interaction.response.send_message.assert_not_awaited()

    async def test_denied_sends_error_and_returns_none(self):
        bot = HawkBot("test-token", "hawk")
        interaction = _mock_interaction(role_names=["everyone"])

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


# _sa role subcommand tests


class TestSaRoleCommand:
    async def test_role_success(self):
        bot = HawkBot("test-token", "hawk")
        interaction = _mock_interaction(guild_id=555)

        with patch(
            "pixel_hawk.interface.interactions.set_guild_role",
            new_callable=AsyncMock,
            return_value="Required role set to **painters** for this server.",
        ):
            await bot._sa(interaction, "role painters")

        interaction.response.send_message.assert_awaited_once()
        msg = interaction.response.send_message.call_args
        assert "painters" in msg.args[0]
        assert msg.kwargs["ephemeral"] is True

    async def test_role_not_admin(self):
        bot = HawkBot("test-token", "hawk")
        interaction = _mock_interaction()

        with patch(
            "pixel_hawk.interface.interactions.set_guild_role",
            new_callable=AsyncMock,
            side_effect=ErrorMsg("Admin access required."),
        ):
            await bot._sa(interaction, "role painters")

        msg = interaction.response.send_message.call_args
        assert "Admin access required" in msg.args[0]

    async def test_role_missing_param(self):
        bot = HawkBot("test-token", "hawk")
        interaction = _mock_interaction()
        await bot._sa(interaction, "role")
        msg = interaction.response.send_message.call_args
        assert msg.args[0] == "No."


# _list with access check


class TestListWithAccessCheck:
    async def test_denied_returns_early(self):
        bot = HawkBot("test-token", "hawk")
        interaction = _mock_interaction()

        with patch.object(bot, "_check_access", new_callable=AsyncMock, return_value=None):
            await bot._list(interaction)

        # Only _check_access should have been called, not send_message again
        interaction.response.send_message.assert_not_awaited()

    async def test_allowed_calls_list_projects(self):
        bot = HawkBot("test-token", "hawk")
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

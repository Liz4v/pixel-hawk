"""Tests for Discord bot wiring (interactions.py)."""

from unittest.mock import AsyncMock, patch

from pixel_hawk.models.config import get_config
from pixel_hawk.interface.interactions import (
    HawkBot,
    maybe_bot,
)


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

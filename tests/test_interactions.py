"""Tests for Discord bot integration."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from pixel_hawk.config import get_config
from pixel_hawk.interactions import HawkBot, generate_admin_token, grant_admin, load_bot_token, maybe_bot
from pixel_hawk.models import BotAccess, Person

# BotAccess enum tests


class TestBotAccess:
    def test_admin_value(self):
        assert BotAccess.ADMIN == 0x10000000

    def test_bitmask_set(self):
        access = 0 | BotAccess.ADMIN
        assert access & BotAccess.ADMIN

    def test_bitmask_unset(self):
        assert not (0 & BotAccess.ADMIN)

    def test_bitmask_preserves_other_flags(self):
        access = 0x1 | BotAccess.ADMIN
        assert access & BotAccess.ADMIN
        assert access & 0x1


# load_bot_token tests


class TestLoadBotToken:
    def test_no_config_file(self, setup_config):
        assert load_bot_token() is None

    def test_empty_config(self, setup_config):
        config_path = get_config().home / "config.toml"
        config_path.write_text("")
        _invalidate_config_toml()
        assert load_bot_token() is None

    def test_no_discord_section(self, setup_config):
        config_path = get_config().home / "config.toml"
        config_path.write_text("[other]\nkey = 'value'\n")
        _invalidate_config_toml()
        assert load_bot_token() is None

    def test_no_bot_token_key(self, setup_config):
        config_path = get_config().home / "config.toml"
        config_path.write_text("[discord]\nother_key = 'value'\n")
        _invalidate_config_toml()
        assert load_bot_token() is None

    def test_empty_bot_token(self, setup_config):
        config_path = get_config().home / "config.toml"
        config_path.write_text('[discord]\nbot_token = ""\n')
        _invalidate_config_toml()
        assert load_bot_token() is None

    def test_valid_bot_token(self, setup_config):
        config_path = get_config().home / "config.toml"
        config_path.write_text('[discord]\nbot_token = "my-secret-token"\n')
        _invalidate_config_toml()
        assert load_bot_token() == "my-secret-token"


def _invalidate_config_toml():
    """Clear the cached_property so config.toml is re-read."""
    cfg = get_config()
    # cached_property stores the value in the instance __dict__
    cfg.__dict__.pop("config_toml", None)


# generate_admin_token tests


class TestGenerateAdminToken:
    def test_creates_file(self, setup_config):
        token = generate_admin_token()
        path = get_config().data_dir / "admin-me.txt"
        assert path.exists()
        assert path.read_text() == token

    def test_returns_valid_uuid4(self, setup_config):
        token = generate_admin_token()
        parsed = uuid.UUID(token, version=4)
        assert str(parsed) == token

    def test_overwrites_on_each_call(self, setup_config):
        token1 = generate_admin_token()
        token2 = generate_admin_token()
        assert token1 != token2
        # File should contain the latest token
        path = get_config().data_dir / "admin-me.txt"
        assert path.read_text() == token2


# grant_admin tests


class TestGrantAdmin:
    async def test_invalid_token_returns_none(self):
        result = await grant_admin(12345, "TestUser", "wrong-token", "correct-token")
        assert result is None

    async def test_valid_token_creates_person(self):
        token = "test-token-123"
        result = await grant_admin(99999, "NewUser", token, token)
        assert result is not None
        assert "NewUser" in result

        person = await Person.filter(discord_id=99999).first()
        assert person is not None
        assert person.name == "NewUser"
        assert person.access & BotAccess.ADMIN

    async def test_valid_token_reuses_existing_person(self):
        person = await Person.create(name="Existing", discord_id=88888)
        token = "test-token-456"
        result = await grant_admin(88888, "Existing", token, token)
        assert result is not None

        # Should not create a new person
        count = await Person.filter(discord_id=88888).count()
        assert count == 1

        # Should have admin access
        updated = await Person.get(discord_id=88888)
        assert updated.access & BotAccess.ADMIN

    async def test_idempotent_admin_grant(self):
        token = "test-token-789"
        await grant_admin(77777, "Idempotent", token, token)
        await grant_admin(77777, "Idempotent", token, token)

        person = await Person.get(discord_id=77777)
        assert person.access & BotAccess.ADMIN

    async def test_preserves_existing_access_flags(self):
        person = await Person.create(name="Flagged", discord_id=66666, access=0x1)
        token = "test-token-flags"
        await grant_admin(66666, "Flagged", token, token)

        updated = await Person.get(discord_id=66666)
        assert updated.access & BotAccess.ADMIN
        assert updated.access & 0x1  # Original flag preserved


# Person discord fields tests


class TestPersonDiscordFields:
    async def test_discord_id_nullable(self):
        person = await Person.create(name="NoDiscord")
        assert person.discord_id is None

    async def test_discord_id_set(self):
        person = await Person.create(name="WithDiscord", discord_id=123456789)
        reloaded = await Person.get(id=person.id)
        assert reloaded.discord_id == 123456789

    async def test_discord_id_unique(self):
        await Person.create(name="First", discord_id=111111)
        with pytest.raises(Exception, match=r"(?i)unique|constraint"):
            await Person.create(name="Second", discord_id=111111)

    async def test_access_defaults_to_zero(self):
        person = await Person.create(name="Default")
        assert person.access == 0

    async def test_access_stores_bitmask(self):
        person = await Person.create(name="Admin", access=int(BotAccess.ADMIN))
        reloaded = await Person.get(id=person.id)
        assert reloaded.access & BotAccess.ADMIN


# HawkBot tests


class TestHawkBot:
    def test_construction(self):
        bot = HawkBot("test-admin-token")
        assert bot.admin_token == "test-admin-token"
        assert bot.tree is not None

    def test_command_tree_has_hawk_group(self):
        bot = HawkBot("test-token")
        commands = bot.tree.get_commands()
        names = [c.name for c in commands]
        assert "hawk" in names

    async def test_on_ready_logs(self):
        bot = HawkBot("test-token")
        # on_ready just logs, should not raise
        bot._connection.user = None  # type: ignore[assignment]
        await bot.on_ready()

    async def test_setup_hook_syncs_tree(self):
        bot = HawkBot("test-token")
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

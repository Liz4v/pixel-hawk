"""Tests for Discord bot integration."""

import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from pixel_hawk.config import get_config
from pixel_hawk.geometry import Point, Rectangle, Size
from pixel_hawk.interactions import (
    DISCORD_MESSAGE_LIMIT,
    HawkBot,
    generate_admin_token,
    grant_admin,
    list_projects,
    load_bot_token,
    maybe_bot,
)
from pixel_hawk.models import BotAccess, DiffStatus, HistoryChange, Person, ProjectInfo, ProjectState

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
        assert token in path.read_text()

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
        assert token2 in path.read_text()


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


# list_projects tests

RECT = Rectangle.from_point_size(Point(500_000, 600_000), Size(100, 100))


class TestListProjects:
    async def test_unknown_discord_id_returns_none(self):
        result = await list_projects(99999)
        assert result is None

    async def test_person_with_no_projects(self):
        await Person.create(name="Empty", discord_id=11111)
        result = await list_projects(11111)
        assert result == "You have no projects."

    async def test_active_in_progress(self):
        person = await Person.create(name="Alice", discord_id=22222)
        info = await ProjectInfo.from_rect(RECT, person.id, "sonic the hedgehog")
        info.last_check = round(time.time())
        await info.save()
        now = round(time.time())
        await HistoryChange.create(
            project=info,
            timestamp=now,
            status=DiffStatus.IN_PROGRESS,
            num_remaining=12415,
            num_target=26000,
            completion_percent=52.3,
            progress_pixels=354,
            regress_pixels=12,
        )

        result = await list_projects(22222)
        assert result is not None
        assert f"**{info.id}** [ACTIVE] sonic the hedgehog" in result
        assert "52.3% complete" in result
        assert "12,415 px remaining" in result
        assert "Last 24h +354-12" in result
        assert "https://wplace.live/" in result

    async def test_active_complete(self):
        person = await Person.create(name="Bob", discord_id=33333)
        info = await ProjectInfo.from_rect(RECT, person.id, "twilight sparkle")
        info.last_check = round(time.time())
        info.max_completion_time = 1770550880
        await info.save()
        await HistoryChange.create(
            project=info,
            timestamp=round(time.time()),
            status=DiffStatus.COMPLETE,
            num_remaining=0,
            num_target=35221,
            completion_percent=100.0,
        )

        result = await list_projects(33333)
        assert result is not None
        assert "[ACTIVE] twilight sparkle" in result
        assert "Complete since <t:1770550880:R>!" in result
        assert "35,221 px total" in result

    async def test_never_checked(self):
        person = await Person.create(name="Carol", discord_id=44444)
        info = await ProjectInfo.from_rect(RECT, person.id, "sans undertale")
        info.last_check = 0
        await info.save()

        result = await list_projects(44444)
        assert result is not None
        assert "Not yet checked" in result

    async def test_inactive_shows_no_stats(self):
        person = await Person.create(name="Dave", discord_id=55555)
        await ProjectInfo.from_rect(RECT, person.id, "old project", state=ProjectState.INACTIVE)

        result = await list_projects(55555)
        assert result is not None
        assert "[INACTIVE] old project" in result
        assert "complete" not in result
        assert "Not yet checked" not in result
        assert "https://wplace.live/" in result

    async def test_ordered_by_last_snapshot_descending(self):
        person = await Person.create(name="Eve", discord_id=66666)
        old = await ProjectInfo.from_rect(RECT, person.id, "old one")
        old.last_snapshot = 1000
        old.last_check = 0
        await old.save()
        new = await ProjectInfo.from_rect(RECT, person.id, "new one")
        new.last_snapshot = 2000
        new.last_check = 0
        await new.save()

        result = await list_projects(66666)
        assert result is not None
        assert result.index("new one") < result.index("old one")

    async def test_truncation_at_message_limit(self):
        person = await Person.create(name="Frank", discord_id=77777)
        for i in range(20):
            info = await ProjectInfo.from_rect(RECT, person.id, f"project {'x' * 200} {i}")
            info.last_check = 0
            await info.save()

        result = await list_projects(77777)
        assert result is not None
        assert len(result) <= DISCORD_MESSAGE_LIMIT
        assert "... and" in result
        assert "more" in result

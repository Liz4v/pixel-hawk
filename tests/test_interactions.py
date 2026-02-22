"""Tests for Discord bot integration."""

import time
import uuid
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from pixel_hawk.config import get_config
from pixel_hawk.geometry import Point, Rectangle, Size
from pixel_hawk.interactions import (
    DISCORD_MESSAGE_LIMIT,
    HawkBot,
    _parse_coords,
    _parse_filename,
    edit_project,
    generate_admin_token,
    grant_admin,
    list_projects,
    maybe_bot,
    new_project,
)
from pixel_hawk.models import BotAccess, DiffStatus, HistoryChange, Person, ProjectInfo, ProjectState, TileProject
from pixel_hawk.palette import PALETTE, ColorsNotInPalette

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



def _invalidate_config_toml():
    """Clear the cached_property so config.toml is re-read."""
    cfg = get_config()
    # cached_property stores the value in the instance __dict__
    cfg.__dict__.pop("config_toml", None)
    cfg.__dict__.pop("discord", None)


# generate_admin_token tests


class TestGenerateAdminToken:
    def test_creates_file(self, setup_config):
        token = generate_admin_token("hawk")
        path = get_config().data_dir / "admin-me.txt"
        assert path.exists()
        assert token in path.read_text()

    def test_returns_valid_uuid4(self, setup_config):
        token = generate_admin_token("hawk")
        parsed = uuid.UUID(token, version=4)
        assert str(parsed) == token

    def test_overwrites_on_each_call(self, setup_config):
        token1 = generate_admin_token("hawk")
        token2 = generate_admin_token("hawk")
        assert token1 != token2
        # File should contain the latest token
        path = get_config().data_dir / "admin-me.txt"
        assert token2 in path.read_text()

    def test_custom_command_prefix(self, setup_config):
        token = generate_admin_token(command_prefix="testhawk")
        path = get_config().data_dir / "admin-me.txt"
        content = path.read_text()
        assert content.startswith("/testhawk sa myself ")
        assert token in content


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
        assert f"**{info.id:04}** [ACTIVE] sonic the hedgehog" in result
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

    async def test_checked_no_history_shows_header_only(self):
        person = await Person.create(name="Carol2", discord_id=44555)
        info = await ProjectInfo.from_rect(RECT, person.id, "checked project")
        info.last_check = round(time.time())
        await info.save()

        result = await list_projects(44555)
        assert result is not None
        assert "[ACTIVE] checked project" in result
        assert "complete" not in result
        assert "Not yet checked" not in result

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

    async def test_creating_shows_no_link(self):
        person = await Person.create(name="Grace", discord_id=88888)
        await ProjectInfo.from_rect(RECT, person.id, "wip", state=ProjectState.CREATING)

        result = await list_projects(88888)
        assert result is not None
        assert "[CREATING] wip" in result
        assert "https://wplace.live/" not in result


# Test image helpers


def _make_test_png(width: int = 10, height: int = 10) -> bytes:
    """Create a valid WPlace palette PNG as bytes."""
    image = PALETTE.new((width, height))
    buf = BytesIO()
    image.save(buf, format="PNG")
    image.close()
    return buf.getvalue()


def _make_bad_png(width: int = 10, height: int = 10) -> bytes:
    """Create a PNG with colors not in the WPlace palette."""
    image = Image.new("RGB", (width, height), color=(1, 2, 3))
    buf = BytesIO()
    image.save(buf, format="PNG")
    image.close()
    return buf.getvalue()


# _parse_filename tests


class TestParseFilename:
    def test_coords_only(self):
        name, coords = _parse_filename("5_7_0_0.png")
        assert name is None
        assert coords == (5, 7, 0, 0)

    def test_name_and_coords(self):
        name, coords = _parse_filename("sonic_5_7_0_0.png")
        assert name == "sonic"
        assert coords == (5, 7, 0, 0)

    def test_multi_word_name_and_coords(self):
        name, coords = _parse_filename("my_cool_art_1_2_100_200.png")
        assert name == "my_cool_art"
        assert coords == (1, 2, 100, 200)

    def test_no_coords(self):
        name, coords = _parse_filename("my project.png")
        assert name is None
        assert coords is None

    def test_generic_filename(self):
        name, coords = _parse_filename("image.png")
        assert name is None
        assert coords is None

    def test_out_of_range_tile_ignored(self):
        name, coords = _parse_filename("test_9999_0_0_0.png")
        assert coords is None

    def test_out_of_range_pixel_ignored(self):
        name, coords = _parse_filename("test_0_0_1000_0.png")
        assert coords is None

    def test_no_extension(self):
        name, coords = _parse_filename("5_7_0_0")
        assert coords == (5, 7, 0, 0)

    def test_non_numeric_parts(self):
        name, coords = _parse_filename("a_b_c_d.png")
        assert coords is None


# _parse_coords tests


class TestParseCoords:
    def test_underscore_separator(self):
        assert _parse_coords("5_7_0_0") == (5, 7, 0, 0)

    def test_comma_separator(self):
        assert _parse_coords("5,7,0,0") == (5, 7, 0, 0)

    def test_space_separator(self):
        assert _parse_coords("5 7 0 0") == (5, 7, 0, 0)

    def test_mixed_separators(self):
        assert _parse_coords("5_7,0 0") == (5, 7, 0, 0)

    def test_wrong_count(self):
        with pytest.raises(ValueError, match="expected tx_ty_px_py"):
            _parse_coords("5_7_0")

    def test_non_numeric(self):
        with pytest.raises(ValueError, match="integers"):
            _parse_coords("a_b_c_d")

    def test_tile_out_of_range(self):
        with pytest.raises(ValueError, match="out of range"):
            _parse_coords("2048_0_0_0")

    def test_pixel_out_of_range(self):
        with pytest.raises(ValueError, match="out of range"):
            _parse_coords("0_0_1000_0")

    def test_negative_values(self):
        with pytest.raises(ValueError, match="out of range"):
            _parse_coords("-1_0_0_0")


# new_project tests


class TestNewProject:
    async def test_no_person_returns_none(self):
        result = await new_project(99999, _make_test_png(), "test.png")
        assert result is None

    async def test_not_png_raises(self):
        await Person.create(name="Alice", discord_id=10001)
        with pytest.raises(ValueError, match="Not a PNG"):
            await new_project(10001, b"not a png file", "test.png")

    async def test_too_large_raises(self):
        await Person.create(name="Bob", discord_id=10002)
        with pytest.raises(ValueError, match="too large"):
            await new_project(10002, _make_test_png(1001, 500), "test.png")

    async def test_bad_palette_raises(self):
        await Person.create(name="Carol", discord_id=10003)
        with pytest.raises(ColorsNotInPalette):
            await new_project(10003, _make_bad_png(), "test.png")

    async def test_plain_filename_creates_creating_project(self):
        person = await Person.create(name="Dave", discord_id=10004)
        result = await new_project(10004, _make_test_png(), "image.png")

        assert result is not None
        assert "created" in result
        assert "/hawk edit" in result

        info = await ProjectInfo.filter(owner=person).first()
        assert info is not None
        assert info.state == ProjectState.CREATING
        assert info.name.startswith("Project ")

        # Pending file should exist
        pending = get_config().projects_dir / str(person.id) / f"new_{info.id}.png"
        assert pending.exists()

    async def test_coords_filename_creates_active_project(self):
        person = await Person.create(name="Eve", discord_id=10005)
        result = await new_project(10005, _make_test_png(50, 60), "5_7_0_0.png")

        assert result is not None
        assert "activated" in result

        info = await ProjectInfo.filter(owner=person).first()
        assert info is not None
        assert info.state == ProjectState.ACTIVE
        assert info.x == 5000
        assert info.y == 7000
        assert info.width == 50
        assert info.height == 60

        # Canonical file should exist (not pending)
        canonical = get_config().projects_dir / str(person.id) / info.filename
        assert canonical.exists()
        pending = get_config().projects_dir / str(person.id) / f"new_{info.id}.png"
        assert not pending.exists()

    async def test_name_and_coords_from_filename(self):
        person = await Person.create(name="Fay", discord_id=10006)
        await new_project(10006, _make_test_png(), "sonic_5_7_0_0.png")

        info = await ProjectInfo.filter(owner=person).first()
        assert info is not None
        assert info.name == "sonic"
        assert info.state == ProjectState.ACTIVE

    async def test_coords_filename_creates_tile_links(self):
        person = await Person.create(name="Gina", discord_id=10007)
        await new_project(10007, _make_test_png(), "5_7_0_0.png")

        info = await ProjectInfo.filter(owner=person).first()
        tile_links = await TileProject.filter(project_id=info.id).count()
        assert tile_links > 0

    async def test_plain_filename_no_tile_links(self):
        person = await Person.create(name="Hank", discord_id=10008)
        await new_project(10008, _make_test_png(), "image.png")

        info = await ProjectInfo.filter(owner=person).first()
        tile_links = await TileProject.filter(project_id=info.id).count()
        assert tile_links == 0


# edit_project tests


class TestEditProject:
    async def test_no_person_returns_none(self):
        result = await edit_project(99999, 1, name="test")
        assert result is None

    async def test_project_not_found(self):
        await Person.create(name="Alice", discord_id=20001)
        with pytest.raises(ValueError, match="not found"):
            await edit_project(20001, 9999, name="test")

    async def test_not_owner(self):
        owner = await Person.create(name="Owner", discord_id=20002)
        await Person.create(name="Other", discord_id=20003)
        info = await ProjectInfo.from_rect(RECT, owner.id, "owned project")

        with pytest.raises(ValueError, match="not yours"):
            await edit_project(20003, info.id, name="stolen")

    async def test_set_name(self):
        person = await Person.create(name="Bob", discord_id=20004)
        info = await ProjectInfo.from_rect(RECT, person.id, "old name")

        result = await edit_project(20004, info.id, name="new name")
        assert result is not None
        assert "new name" in result

        reloaded = await ProjectInfo.get(id=info.id)
        assert reloaded.name == "new name"

    async def test_duplicate_name_raises(self):
        person = await Person.create(name="Carol", discord_id=20005)
        await ProjectInfo.from_rect(RECT, person.id, "existing")
        info2 = await ProjectInfo.from_rect(RECT, person.id, "other")

        with pytest.raises(ValueError, match="already have"):
            await edit_project(20005, info2.id, name="existing")

    async def test_set_coords_renames_pending_file(self):
        person = await Person.create(name="Dave", discord_id=20006)
        await new_project(20006, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner=person).first()

        pending = get_config().projects_dir / str(person.id) / f"new_{info.id}.png"
        assert pending.exists()

        result = await edit_project(20006, info.id, coords="5_7_0_0")
        assert result is not None
        assert "5_7_0_0" in result

        assert not pending.exists()
        reloaded = await ProjectInfo.get(id=info.id)
        canonical = get_config().projects_dir / str(person.id) / reloaded.filename
        assert canonical.exists()

    async def test_set_coords_creates_tile_links(self):
        person = await Person.create(name="Eve", discord_id=20007)
        await new_project(20007, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner=person).first()

        await edit_project(20007, info.id, coords="5_7_0_0")

        tile_links = await TileProject.filter(project_id=info.id).count()
        assert tile_links > 0

    async def test_change_coords_relinks_tiles(self):
        person = await Person.create(name="Fay", discord_id=20008)
        await new_project(20008, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner=person).first()

        await edit_project(20008, info.id, coords="5_7_0_0")

        await edit_project(20008, info.id, coords="10_20_0_0")

        # Should have tile links (old ones deleted, new ones created)
        assert await TileProject.filter(project_id=info.id).count() > 0

        reloaded = await ProjectInfo.get(id=info.id)
        assert reloaded.x == 10000
        assert reloaded.y == 20000

    async def test_activate_requires_coords(self):
        person = await Person.create(name="Gina", discord_id=20009)
        await new_project(20009, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner=person).first()

        with pytest.raises(ValueError, match="set coordinates first"):
            await edit_project(20009, info.id, state=ProjectState.ACTIVE)

    async def test_activate_with_coords(self):
        person = await Person.create(name="Hank", discord_id=20010)
        await new_project(20010, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner=person).first()

        await edit_project(20010, info.id, coords="5_7_0_0")
        result = await edit_project(20010, info.id, state=ProjectState.ACTIVE)

        assert result is not None
        assert "ACTIVE" in result
        reloaded = await ProjectInfo.get(id=info.id)
        assert reloaded.state == ProjectState.ACTIVE

    async def test_no_changes_raises(self):
        person = await Person.create(name="Ivy", discord_id=20011)
        info = await ProjectInfo.from_rect(RECT, person.id, "test")

        with pytest.raises(ValueError, match="No changes"):
            await edit_project(20011, info.id)

    async def test_all_at_once(self):
        person = await Person.create(name="Jack", discord_id=20012)
        await new_project(20012, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner=person).first()

        result = await edit_project(
            20012, info.id, name="sonic", coords="5_7_0_0", state=ProjectState.ACTIVE
        )

        assert result is not None
        assert "sonic" in result
        assert "5_7_0_0" in result
        assert "ACTIVE" in result

        reloaded = await ProjectInfo.get(id=info.id)
        assert reloaded.name == "sonic"
        assert reloaded.state == ProjectState.ACTIVE
        assert reloaded.x == 5000


# HawkBot command tree tests


class TestHawkBotNewCommands:
    def test_command_tree_has_new(self):
        bot = HawkBot("test-token")
        hawk = next(c for c in bot.tree.get_commands() if c.name == "hawk")
        names = [c.name for c in hawk.commands]
        assert "new" in names

    def test_command_tree_has_edit(self):
        bot = HawkBot("test-token")
        hawk = next(c for c in bot.tree.get_commands() if c.name == "hawk")
        names = [c.name for c in hawk.commands]
        assert "edit" in names

"""Tests for project management service layer (commands.py)."""

import base64
import json
import time
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from pixel_hawk.interface.access import ErrorMsg
from pixel_hawk.interface.commands import (
    DISCORD_MESSAGE_LIMIT,
    _parse_coords,
    delete_project,
    edit_project,
    get_command_prefix,
    list_projects,
    new_project,
    parse_filename,
    parse_wplace,
)
from pixel_hawk.models.config import get_config
from pixel_hawk.models.entities import DiffStatus, HistoryChange, Person, ProjectInfo, ProjectState, TileProject
from pixel_hawk.models.geometry import Point, Rectangle, Size
from pixel_hawk.models.palette import PALETTE, ColorsNotInPalette
from pixel_hawk.watcher import projects

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


# parse_filename tests


class TestParseFilename:
    def test_coords_only(self):
        name, coords = parse_filename("5_7_0_0.png")
        assert name is None
        assert coords == (5, 7, 0, 0)

    def test_name_and_coords(self):
        name, coords = parse_filename("sonic_5_7_0_0.png")
        assert name == "sonic"
        assert coords == (5, 7, 0, 0)

    def test_multi_word_name_and_coords(self):
        name, coords = parse_filename("my_cool_art_1_2_100_200.png")
        assert name == "my cool art"
        assert coords == (1, 2, 100, 200)

    def test_no_coords(self):
        name, coords = parse_filename("my project.png")
        assert name == "my project"
        assert coords is None

    def test_generic_filename(self):
        name, coords = parse_filename("image.png")
        assert name == "image"
        assert coords is None

    def test_out_of_range_tile_ignored(self):
        name, coords = parse_filename("test_9999_0_0_0.png")
        assert coords is None

    def test_out_of_range_pixel_ignored(self):
        name, coords = parse_filename("test_0_0_1000_0.png")
        assert coords is None

    def test_no_extension(self):
        name, coords = parse_filename("5_7_0_0")
        assert coords == (5, 7, 0, 0)

    def test_non_numeric_parts(self):
        name, coords = parse_filename("a_b_c_d.png")
        assert coords is None

    def test_dot_separator(self):
        name, coords = parse_filename("5.7.0.0.png")
        assert name is None
        assert coords == (5, 7, 0, 0)

    def test_hyphen_separator(self):
        name, coords = parse_filename("5-7-0-0.png")
        assert name is None
        assert coords == (5, 7, 0, 0)

    def test_space_separator(self):
        name, coords = parse_filename("5 7 0 0.png")
        assert name is None
        assert coords == (5, 7, 0, 0)

    def test_name_with_dot_separator(self):
        name, coords = parse_filename("sonic.5.7.0.0.png")
        assert name == "sonic"
        assert coords == (5, 7, 0, 0)

    def test_coords_at_beginning(self):
        name, coords = parse_filename("5_7_0_0_sonic.png")
        assert name == "sonic"
        assert coords == (5, 7, 0, 0)

    def test_mixed_separators_rejected(self):
        name, coords = parse_filename("5_7.0_0.png")
        assert name == "5 7.0 0"
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
        with pytest.raises(ErrorMsg, match="Invalid coordinates"):
            _parse_coords("5_7_0")

    def test_non_numeric(self):
        with pytest.raises(ErrorMsg, match="Invalid coordinates"):
            _parse_coords("a_b_c_d")

    def test_tile_out_of_range(self):
        with pytest.raises(ErrorMsg, match="out of range"):
            _parse_coords("2048_0_0_0")

    def test_pixel_out_of_range(self):
        with pytest.raises(ErrorMsg, match="out of range"):
            _parse_coords("0_0_1000_0")


# new_project tests


class TestNewProject:
    async def test_no_person_returns_none(self):
        result = await new_project(99999, _make_test_png(), "test.png")
        assert result is None

    async def test_not_png_raises(self):
        await Person.create(name="Alice", discord_id=10001)
        with pytest.raises(ErrorMsg, match="Not a PNG"):
            await new_project(10001, b"not a png file", "test.png")

    async def test_too_large_raises(self):
        await Person.create(name="Bob", discord_id=10002)
        with pytest.raises(ErrorMsg, match="too large"):
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
        assert f"/{get_command_prefix()} edit" in result

        info = await ProjectInfo.filter(owner=person).first()
        assert info is not None
        assert info.state == ProjectState.CREATING
        assert info.name == "image"

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
        with pytest.raises(ErrorMsg, match="not found"):
            await edit_project(20001, 9999, name="test")

    async def test_not_owner(self):
        owner = await Person.create(name="Owner", discord_id=20002)
        await Person.create(name="Other", discord_id=20003)
        info = await ProjectInfo.from_rect(RECT, owner.id, "owned project")

        with pytest.raises(ErrorMsg, match="not yours"):
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

        with pytest.raises(ErrorMsg, match="already have"):
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
        assert reloaded.state == ProjectState.ACTIVE  # auto-transitioned from CREATING
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

        with pytest.raises(ErrorMsg, match="set coordinates first"):
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

        with pytest.raises(ErrorMsg, match="No changes"):
            await edit_project(20011, info.id)

    async def test_all_at_once(self):
        person = await Person.create(name="Jack", discord_id=20012)
        await new_project(20012, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner=person).first()

        result = await edit_project(20012, info.id, name="sonic", coords="5_7_0_0", state=ProjectState.ACTIVE)

        assert result is not None
        assert "sonic" in result
        assert "5_7_0_0" in result
        assert "ACTIVE" in result

        reloaded = await ProjectInfo.get(id=info.id)
        assert reloaded.name == "sonic"
        assert reloaded.state == ProjectState.ACTIVE
        assert reloaded.x == 5000


# Conflict detection tests


class TestCoordConflict:
    async def test_new_project_rejects_duplicate_coords(self):
        await Person.create(name="Alice", discord_id=60001)
        await new_project(60001, _make_test_png(), "5_7_0_0.png")

        with pytest.raises(ErrorMsg, match="already have project"):
            await new_project(60001, _make_test_png(), "5_7_0_0.png")

    async def test_new_project_allows_inactive_coords(self):
        person = await Person.create(name="Bob", discord_id=60002)
        await new_project(60002, _make_test_png(), "5_7_0_0.png")
        info = await ProjectInfo.filter(owner=person).first()
        info.state = ProjectState.INACTIVE
        await info.save()

        result = await new_project(60002, _make_test_png(), "5_7_0_0.png")
        assert result is not None
        assert "activated" in result

    async def test_new_project_allows_different_user_same_coords(self):
        await Person.create(name="Carol", discord_id=60003)
        await Person.create(name="Dave", discord_id=60004)
        await new_project(60003, _make_test_png(), "5_7_0_0.png")

        result = await new_project(60004, _make_test_png(), "5_7_0_0.png")
        assert result is not None
        assert "activated" in result

    async def test_edit_coords_rejects_conflict(self):
        person = await Person.create(name="Eve", discord_id=60005)
        await new_project(60005, _make_test_png(), "5_7_0_0.png")
        await new_project(60005, _make_test_png(), "10_20_0_0.png")
        info2 = await ProjectInfo.filter(owner=person, x=10000, y=20000).first()

        with pytest.raises(ErrorMsg, match="already have project"):
            await edit_project(60005, info2.id, coords="5_7_0_0")

    async def test_new_project_rejects_duplicate_name(self):
        await Person.create(name="Fay", discord_id=60006)
        await new_project(60006, _make_test_png(), "image.png")

        with pytest.raises(ErrorMsg, match="already have a project named"):
            await new_project(60006, _make_test_png(), "image.png")


# edit_project with image tests


class TestEditProjectImage:
    async def test_image_replaces_file(self):
        person = await Person.create(name="Alice", discord_id=70001)
        await new_project(70001, _make_test_png(10, 10), "5_7_0_0.png")
        info = await ProjectInfo.filter(owner=person).first()

        # Use a different size so the PNG bytes are guaranteed to differ
        new_png = _make_test_png(10, 8)
        result = await edit_project(70001, info.id, image_data=new_png, image_filename="whatever.png")

        assert result is not None
        assert "Image" in result
        new_data = (get_config().projects_dir / str(person.id) / info.filename).read_bytes()
        assert new_data == new_png

    async def test_image_resets_tracking(self):
        person = await Person.create(name="Bob", discord_id=70002)
        await new_project(70002, _make_test_png(), "5_7_0_0.png")
        info = await ProjectInfo.filter(owner=person).first()

        info.max_completion_percent = 42.0
        info.max_completion_pixels = 100
        info.last_check = round(time.time())
        info.total_progress = 500
        info.total_regress = 50
        await info.save()

        await edit_project(70002, info.id, image_data=_make_test_png(), image_filename="x.png")

        reloaded = await ProjectInfo.get(id=info.id)
        assert reloaded.max_completion_percent == 0.0
        assert reloaded.max_completion_pixels == 0
        assert reloaded.last_check == 0
        # Lifetime totals preserved
        assert reloaded.total_progress == 500
        assert reloaded.total_regress == 50

    async def test_image_with_coord_change(self):
        person = await Person.create(name="Carol", discord_id=70003)
        await new_project(70003, _make_test_png(), "5_7_0_0.png")
        info = await ProjectInfo.filter(owner=person).first()

        result = await edit_project(70003, info.id, image_data=_make_test_png(20, 20), image_filename="10_20_0_0.png")

        assert result is not None
        assert "Coords" in result
        assert "Image" in result

        reloaded = await ProjectInfo.get(id=info.id)
        assert reloaded.x == 10000
        assert reloaded.y == 20000
        assert reloaded.width == 20
        assert reloaded.height == 20

        canonical = get_config().projects_dir / str(person.id) / reloaded.filename
        assert canonical.exists()

    async def test_image_on_creating_with_coords_activates(self):
        person = await Person.create(name="Dave", discord_id=70004)
        await new_project(70004, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner=person).first()
        assert info.state == ProjectState.CREATING

        result = await edit_project(70004, info.id, image_data=_make_test_png(15, 15), image_filename="5_7_0_0.png")

        assert result is not None
        reloaded = await ProjectInfo.get(id=info.id)
        assert reloaded.state == ProjectState.ACTIVE
        assert reloaded.width == 15
        assert reloaded.height == 15

        tile_links = await TileProject.filter(project_id=info.id).count()
        assert tile_links > 0

    async def test_image_dimension_change_relinks_tiles(self):
        person = await Person.create(name="Eve", discord_id=70005)
        await new_project(70005, _make_test_png(10, 10), "5_7_0_0.png")
        info = await ProjectInfo.filter(owner=person).first()

        old_tile_count = await TileProject.filter(project_id=info.id).count()

        # Larger image spans more tiles
        await edit_project(70005, info.id, image_data=_make_test_png(500, 500), image_filename="same.png")

        reloaded = await ProjectInfo.get(id=info.id)
        assert reloaded.width == 500
        assert reloaded.height == 500
        new_tile_count = await TileProject.filter(project_id=info.id).count()
        assert new_tile_count >= old_tile_count

    async def test_image_explicit_coords_override_filename(self):
        person = await Person.create(name="Fay", discord_id=70006)
        await new_project(70006, _make_test_png(), "5_7_0_0.png")
        info = await ProjectInfo.filter(owner=person).first()

        # Filename says 10_20, explicit coords say 30_40
        await edit_project(
            70006, info.id, image_data=_make_test_png(), image_filename="10_20_0_0.png", coords="30_40_0_0"
        )

        reloaded = await ProjectInfo.get(id=info.id)
        assert reloaded.x == 30000
        assert reloaded.y == 40000

    async def test_image_deletes_snapshot(self):
        person = await Person.create(name="Gina", discord_id=70007)
        await new_project(70007, _make_test_png(), "5_7_0_0.png")
        info = await ProjectInfo.filter(owner=person).first()

        snapshot_dir = get_config().snapshots_dir / str(person.id)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot = snapshot_dir / info.filename
        snapshot.write_bytes(b"fake snapshot")
        assert snapshot.exists()

        await edit_project(70007, info.id, image_data=_make_test_png(), image_filename="x.png")
        assert not snapshot.exists()


# delete_project tests


class TestDeleteProject:
    async def test_no_person_returns_none(self):
        result = await delete_project(99999, 1)
        assert result is None

    async def test_project_not_found(self):
        await Person.create(name="Alice", discord_id=80001)
        with pytest.raises(ErrorMsg, match="not found"):
            await delete_project(80001, 9999)

    async def test_not_owner(self):
        owner = await Person.create(name="Owner", discord_id=80002)
        await Person.create(name="Other", discord_id=80003)
        info = await ProjectInfo.from_rect(RECT, owner.id, "owned project")

        with pytest.raises(ErrorMsg, match="not yours"):
            await delete_project(80003, info.id)

    async def test_deletes_active_project(self):
        person = await Person.create(name="Bob", discord_id=80004)
        await new_project(80004, _make_test_png(), "5_7_0_0.png")
        info = await ProjectInfo.filter(owner=person).first()
        project_id = info.id

        project_file = get_config().projects_dir / str(person.id) / info.filename
        assert project_file.exists()

        result = await delete_project(80004, project_id)
        assert result is not None
        assert "deleted" in result.lower()

        # DB records gone
        assert await ProjectInfo.filter(id=project_id).count() == 0
        assert await TileProject.filter(project_id=project_id).count() == 0

        # File gone
        assert not project_file.exists()

    async def test_deletes_creating_project(self):
        person = await Person.create(name="Carol", discord_id=80005)
        await new_project(80005, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner=person).first()
        project_id = info.id

        result = await delete_project(80005, project_id)
        assert result is not None
        assert await ProjectInfo.filter(id=project_id).count() == 0

    async def test_deletes_snapshot(self):
        person = await Person.create(name="Dave", discord_id=80006)
        await new_project(80006, _make_test_png(), "5_7_0_0.png")
        info = await ProjectInfo.filter(owner=person).first()

        snapshot_dir = get_config().snapshots_dir / str(person.id)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot = snapshot_dir / info.filename
        snapshot.write_bytes(b"fake snapshot")

        await delete_project(80006, info.id)
        assert not snapshot.exists()

    async def test_updates_person_totals(self):
        person = await Person.create(name="Eve", discord_id=80007)
        await new_project(80007, _make_test_png(), "5_7_0_0.png")
        info = await ProjectInfo.filter(owner=person).first()

        person = await Person.get(id=person.id)
        assert person.active_projects_count == 1

        await delete_project(80007, info.id)

        person = await Person.get(id=person.id)
        assert person.active_projects_count == 0


# Initial diff tests


class _FakeImage:
    """Minimal fake image for monkeypatching PALETTE.aopen_file and stitch_tiles."""

    def __init__(self, data, size=(10, 10)):
        self._data = data
        self.size = size

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def get_flattened_data(self):
        return self._data

    def save(self, path):
        pass

    def close(self):
        pass


def _patch_diff(monkeypatch, size=(10, 10), target_value=1, current_value=1):
    """Patch stitch_tiles and PALETTE.aopen_file so run_diff can execute."""
    n = size[0] * size[1]
    target = _FakeImage(bytes([target_value] * n), size)
    current = _FakeImage(bytes([current_value] * n), size)

    monkeypatch.setattr(PALETTE, "aopen_file", lambda path: target)

    async def fake_stitch(rect):
        return current

    monkeypatch.setattr(projects, "stitch_tiles", fake_stitch)


class TestInitialDiffNewProject:
    async def test_no_tiles_skips_diff(self, monkeypatch):
        """No tiles cached -> no initial diff in response."""
        await Person.create(name="NoDiff", discord_id=30001)
        result = await new_project(30001, _make_test_png(), "5_7_0_0.png")

        assert result is not None
        assert "activated" in result
        # No completion stats since no tiles cached
        assert "complete" not in result

    async def test_all_tiles_runs_diff(self, setup_config, monkeypatch):
        """All tiles cached -> initial diff included in response."""
        _patch_diff(monkeypatch)
        await Person.create(name="AllTiles", discord_id=30002)

        # Pre-create the tile cache file
        (setup_config.tiles_dir / "tile-5_7.png").touch()

        result = await new_project(30002, _make_test_png(), "5_7_0_0.png")

        assert result is not None
        assert "activated" in result
        assert "complete" in result.lower() or "%" in result

    async def test_some_tiles_shows_count(self, setup_config, monkeypatch):
        """Partial tiles cached -> diff runs with tile count note."""
        _patch_diff(monkeypatch, size=(20, 10), target_value=1, current_value=0)
        await Person.create(name="SomeTiles", discord_id=30003)

        # 20px wide starting at px=990 spans tiles 5 and 6
        png = _make_test_png(20, 10)
        (setup_config.tiles_dir / "tile-5_7.png").touch()

        result = await new_project(30003, png, "5_7_990_0.png")

        assert result is not None
        assert "1/2 tiles cached" in result


class TestInitialDiffEditProject:
    async def test_coords_change_with_tiles_runs_diff(self, setup_config, monkeypatch):
        """Editing coords with tiles cached -> initial diff included."""
        _patch_diff(monkeypatch)
        person = await Person.create(name="EditDiff", discord_id=30004)
        await new_project(30004, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner=person).first()

        (setup_config.tiles_dir / "tile-10_20.png").touch()

        result = await edit_project(30004, info.id, coords="10_20_0_0")

        assert result is not None
        assert "10_20_0_0" in result
        assert "complete" in result.lower() or "%" in result

    async def test_coords_change_no_tiles_skips_diff(self):
        """Editing coords without tiles cached -> no diff."""
        person = await Person.create(name="EditNoDiff", discord_id=30005)
        await new_project(30005, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner=person).first()

        result = await edit_project(30005, info.id, coords="10_20_0_0")

        assert result is not None
        assert "10_20_0_0" in result
        assert "complete" not in result.lower()

    async def test_name_only_no_diff(self):
        """Editing only the name -> no diff attempted."""
        person = await Person.create(name="NameOnly", discord_id=30006)
        info = await ProjectInfo.from_rect(RECT, person.id, "old")

        result = await edit_project(30006, info.id, name="new")

        assert result is not None
        assert "new" in result
        assert "complete" not in result.lower()


# Quota enforcement tests


class TestQuotaEnforcement:
    # --- new_project enforcement ---

    async def test_new_active_exceeds_project_limit(self):
        await Person.create(name="Limited", discord_id=90001, max_active_projects=1)
        await new_project(90001, _make_test_png(), "5_7_0_0.png")

        with pytest.raises(ErrorMsg, match="limit of 1 projects"):
            await new_project(90001, _make_test_png(), "10_20_0_0.png")

    async def test_new_creating_exceeds_project_limit(self):
        await Person.create(name="Limited", discord_id=90002, max_active_projects=1)
        await new_project(90002, _make_test_png(), "image.png")

        with pytest.raises(ErrorMsg, match="limit of 1 projects"):
            await new_project(90002, _make_test_png(), "image2.png")

    async def test_new_active_exceeds_tile_limit(self):
        await Person.create(name="NoTiles", discord_id=90003, max_watched_tiles=0)

        with pytest.raises(ErrorMsg, match="watched tiles"):
            await new_project(90003, _make_test_png(), "5_7_0_0.png")

    async def test_new_creating_skips_tile_check(self):
        await Person.create(name="NoTiles", discord_id=90004, max_watched_tiles=0, max_active_projects=50)
        result = await new_project(90004, _make_test_png(), "image.png")
        assert result is not None
        assert "created" in result

    async def test_new_active_within_limits(self):
        await Person.create(name="Plenty", discord_id=90005, max_active_projects=50, max_watched_tiles=100)
        result = await new_project(90005, _make_test_png(), "5_7_0_0.png")
        assert result is not None
        assert "activated" in result

    # --- edit_project enforcement ---

    async def test_edit_creating_to_active_exceeds_tile_limit(self):
        await Person.create(name="Limited", discord_id=90006, max_active_projects=50, max_watched_tiles=0)
        await new_project(90006, _make_test_png(), "image.png")
        info = await ProjectInfo.filter(owner__discord_id=90006).first()

        with pytest.raises(ErrorMsg, match="watched tiles"):
            await edit_project(90006, info.id, coords="5_7_0_0")

    async def test_edit_coords_change_exceeds_tile_limit(self):
        person = await Person.create(name="Limited", discord_id=90007, max_active_projects=50, max_watched_tiles=1)
        await new_project(90007, _make_test_png(), "5_7_0_0.png")
        info = await ProjectInfo.filter(owner=person).first()

        # Change to coords spanning more tiles than allowed
        with pytest.raises(ErrorMsg, match="watched tiles"):
            await edit_project(90007, info.id, coords="5_7_990_0", image_data=_make_test_png(100, 10))

    async def test_edit_reactivate_passive_exceeds_tile_limit(self):
        person = await Person.create(name="Limited", discord_id=90008, max_active_projects=50, max_watched_tiles=0)
        info = await ProjectInfo.from_rect(RECT, person.id, "test", state=ProjectState.PASSIVE)

        with pytest.raises(ErrorMsg, match="watched tiles"):
            await edit_project(90008, info.id, state=ProjectState.ACTIVE)

    async def test_edit_state_change_updates_totals(self):
        person = await Person.create(name="User", discord_id=90009)
        await new_project(90009, _make_test_png(), "5_7_0_0.png")

        person = await Person.get(id=person.id)
        assert person.active_projects_count == 1

        info = await ProjectInfo.filter(owner=person).first()
        await edit_project(90009, info.id, state=ProjectState.INACTIVE)

        person = await Person.get(id=person.id)
        assert person.active_projects_count == 0


# parse_wplace tests


def _make_wplace(
    name: str = "Test Project",
    png_data: bytes | None = None,
    bounds: dict | None = None,
    width: int = 10,
    height: int = 10,
    schema_version: str = "1",
    **overrides,
) -> bytes:
    """Build a .wplace JSON file as bytes."""
    if png_data is None:
        png_data = _make_test_png(width, height)
    if bounds is None:
        from pixel_hawk.models.geometry import GeoPoint

        nw = GeoPoint.from_pixel(500_000, 600_000)
        se = GeoPoint.from_pixel(500_000 + width, 600_000 + height)
        bounds = {"north": nw.latitude, "south": se.latitude, "west": nw.longitude, "east": se.longitude}
    doc = {
        "id": "test-uuid",
        "schemaVersion": schema_version,
        "name": name,
        "image": {"dataUrl": base64.b64encode(png_data).decode(), "width": width, "height": height},
        "bounds": bounds,
        **overrides,
    }
    return json.dumps(doc).encode()


class TestParseWplace:
    def test_valid_wplace(self):
        data = _make_wplace()
        name, image_data, point = parse_wplace(data)
        assert name == "Test Project"
        assert image_data.startswith(b"\x89PNG")
        assert point.x == 500_000
        assert point.y == 600_000

    def test_rue_portrait(self):
        data = (Path(__file__).parent / "rue.wplace").read_bytes()
        name, image_data, point = parse_wplace(data)
        assert name == "Rue portrait"
        assert point.x == 574_678
        assert point.y == 747_319

    def test_extracts_name(self):
        data = _make_wplace(name="Niko's Dream")
        name, _, _ = parse_wplace(data)
        assert name == "Niko's Dream"

    def test_invalid_json(self):
        with pytest.raises(ErrorMsg, match="Invalid .wplace file"):
            parse_wplace(b"not json")

    def test_missing_name(self):
        data = _make_wplace(name="")
        with pytest.raises(ErrorMsg, match="Missing project name"):
            parse_wplace(data)

    def test_missing_image(self):
        doc = json.dumps({"name": "test", "bounds": {"north": 0, "west": 0}}).encode()
        with pytest.raises(ErrorMsg, match="Missing image"):
            parse_wplace(doc)

    def test_missing_image_data(self):
        doc = json.dumps({"name": "test", "image": {"width": 10}, "bounds": {"north": 0, "west": 0}}).encode()
        with pytest.raises(ErrorMsg, match="Missing image data"):
            parse_wplace(doc)

    def test_missing_bounds(self):
        png = _make_test_png()
        doc = json.dumps({"name": "test", "image": {"dataUrl": base64.b64encode(png).decode()}}).encode()
        with pytest.raises(ErrorMsg, match="Missing bounds"):
            parse_wplace(doc)

    def test_missing_north(self):
        png = _make_test_png()
        doc = json.dumps(
            {
                "name": "test",
                "image": {"dataUrl": base64.b64encode(png).decode()},
                "bounds": {"south": 0, "west": 0},
            }
        ).encode()
        with pytest.raises(ErrorMsg, match="Missing north/west"):
            parse_wplace(doc)

    def test_data_url_prefix_stripped(self):
        from pixel_hawk.models.geometry import GeoPoint

        png = _make_test_png()
        b64 = "data:image/png;base64," + base64.b64encode(png).decode()
        nw = GeoPoint.from_pixel(500_000, 600_000)
        se = GeoPoint.from_pixel(500_010, 600_010)
        doc = json.dumps(
            {
                "name": "test",
                "image": {"dataUrl": b64, "width": 10, "height": 10},
                "bounds": {"north": nw.latitude, "south": se.latitude, "west": nw.longitude, "east": se.longitude},
            }
        ).encode()
        name, image_data, point = parse_wplace(doc)
        assert image_data.startswith(b"\x89PNG")

    def test_invalid_base64(self):
        doc = json.dumps(
            {
                "name": "test",
                "image": {"dataUrl": "!!!not-base64!!!"},
                "bounds": {"north": 0, "west": 0},
            }
        ).encode()
        with pytest.raises(ErrorMsg, match="Invalid image data"):
            parse_wplace(doc)

    def test_unknown_schema_version_warns(self, caplog):
        data = _make_wplace(schema_version="99")
        # Should still parse successfully, just log a warning
        name, image_data, point = parse_wplace(data)
        assert name == "Test Project"

    def test_bounds_image_size_mismatch(self):
        from pixel_hawk.models.geometry import GeoPoint

        # Bounds say 10x10 but declared image size is 20x20
        nw = GeoPoint.from_pixel(500_000, 600_000)
        se = GeoPoint.from_pixel(500_010, 600_010)
        bounds = {"north": nw.latitude, "south": se.latitude, "west": nw.longitude, "east": se.longitude}
        data = _make_wplace(bounds=bounds, width=20, height=20)
        with pytest.raises(ErrorMsg, match="Bounds size.*doesn't match.*declared image size"):
            parse_wplace(data)

    def test_bounds_image_size_match(self):
        # Should pass without error when bounds and declared size agree
        data = _make_wplace(width=50, height=30)
        name, image_data, point = parse_wplace(data)
        assert name == "Test Project"

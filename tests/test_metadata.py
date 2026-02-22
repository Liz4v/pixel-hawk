"""Tests for project metadata computation and ProjectInfo model."""

import random
import time

import pytest

from pixel_hawk import metadata
from pixel_hawk.geometry import Point, Rectangle, Size
from pixel_hawk.models import Person, ProjectInfo


@pytest.fixture
async def test_person():
    """Create a test person for use in tests."""
    return await Person.create(name="TestPerson")


async def test_project_info_default_initialization(test_person):
    """Test ProjectInfo can be created with defaults via DB."""
    info = ProjectInfo(owner=test_person, name="test")
    await info.save_as_new()

    assert info.x == 0
    assert info.y == 0
    assert info.width == 0
    assert info.height == 0
    assert info.first_seen == 0
    assert info.last_check == 0
    assert info.last_snapshot == 0
    assert info.max_completion_pixels == 0
    assert info.max_completion_percent == 0.0
    assert info.max_completion_time == 0
    assert info.total_progress == 0
    assert info.total_regress == 0
    assert info.largest_regress_pixels == 0
    assert info.largest_regress_time == 0
    assert info.recent_rate_pixels_per_hour == 0.0
    assert info.recent_rate_window_start == 0


async def test_from_rect(test_person):
    """Test ProjectInfo.from_rect creates correct initial state."""
    rect = Rectangle.from_point_size(Point(100, 200), Size(50, 60))

    before_time = round(time.time())
    info = await ProjectInfo.from_rect(rect, test_person.id, "test_project")
    after_time = round(time.time())

    assert info.name == "test_project"
    assert info.x == 100
    assert info.y == 200
    assert info.width == 50
    assert info.height == 60
    assert before_time <= info.first_seen <= after_time
    assert before_time <= info.last_check <= after_time
    assert info.first_seen == info.last_check
    assert info.max_completion_pixels == 0
    assert info.total_progress == 0
    assert info.total_regress == 0


async def test_from_rect_with_offset(test_person):
    """Test ProjectInfo.from_rect with non-zero origin."""
    rect = Rectangle.from_point_size(Point.from4(5, 7, 250, 380), Size(120, 80))
    info = await ProjectInfo.from_rect(rect, test_person.id, "offset_project")

    assert info.name == "offset_project"
    assert info.x == 5250  # 5 * 1000 + 250
    assert info.y == 7380  # 7 * 1000 + 380
    assert info.width == 120
    assert info.height == 80


async def test_get_or_create_from_rect_creates_new(test_person):
    """Test get_or_create_from_rect creates when not in DB."""
    rect = Rectangle.from_point_size(Point(10, 20), Size(30, 40))
    info = await ProjectInfo.get_or_create_from_rect(rect, test_person.id, "new_project")

    assert info.name == "new_project"
    assert info.x == 10
    assert info.width == 30


async def test_get_or_create_from_rect_returns_existing(test_person):
    """Test get_or_create_from_rect returns existing record."""
    rect = Rectangle.from_point_size(Point(10, 20), Size(30, 40))
    info1 = await ProjectInfo.from_rect(rect, test_person.id, "existing_project")
    info1.total_progress = 42
    await info1.save()

    info2 = await ProjectInfo.get_or_create_from_rect(rect, test_person.id, "existing_project")
    assert info2.total_progress == 42


async def test_db_persistence_round_trip(test_person):
    """Test ProjectInfo saves to and loads from DB correctly."""
    info = ProjectInfo(
        owner=test_person,
        name="roundtrip",
        x=10,
        y=20,
        width=30,
        height=40,
        first_seen=1000,
        last_check=2000,
        max_completion_pixels=100,
        max_completion_percent=75.5,
        total_progress=50,
        total_regress=5,
    )
    await info.save_as_new()

    loaded = await ProjectInfo.get(owner=test_person, name="roundtrip")
    assert loaded.x == 10
    assert loaded.y == 20
    assert loaded.width == 30
    assert loaded.height == 40
    assert loaded.first_seen == 1000
    assert loaded.max_completion_pixels == 100
    assert loaded.max_completion_percent == 75.5
    assert loaded.total_progress == 50
    assert loaded.total_regress == 5


async def test_numeric_fields_precision(test_person):
    """Test floating point precision in DB round-trip."""
    info = ProjectInfo(
        owner=test_person,
        name="precision",
        max_completion_percent=99.99999,
        recent_rate_pixels_per_hour=123.456789,
    )
    await info.save_as_new()

    loaded = await ProjectInfo.get(owner=test_person, name="precision")
    assert loaded.max_completion_percent == info.max_completion_percent
    assert loaded.recent_rate_pixels_per_hour == info.recent_rate_pixels_per_hour


# Pixel counting tests


async def test_compare_snapshots_progress():
    """Test snapshot comparison detecting progress."""
    target = bytes([0, 1, 2, 0])
    prev = bytes([0, 1, 0, 0])
    current = bytes([0, 1, 2, 0])

    progress, regress = metadata.compare_snapshots(current, prev, target)

    assert progress == 1
    assert regress == 0


async def test_compare_snapshots_regress():
    """Test snapshot comparison detecting regress."""
    target = bytes([0, 1, 2, 0])
    prev = bytes([0, 1, 2, 0])
    current = bytes([0, 1, 0, 0])

    progress, regress = metadata.compare_snapshots(current, prev, target)

    assert progress == 0
    assert regress == 1


async def test_compare_snapshots_mixed():
    """Test snapshot comparison with both progress and regress."""
    target = bytes([0, 1, 2, 3, 0])
    prev = bytes([0, 1, 0, 0, 0])
    current = bytes([0, 1, 2, 0, 0])

    progress, regress = metadata.compare_snapshots(current, prev, target)

    assert progress == 1
    assert regress == 0


async def test_compare_snapshots_no_change():
    """Test snapshot comparison with no changes."""
    target = bytes([0, 1, 2, 0])
    prev = bytes([0, 1, 0, 0])
    current = bytes([0, 1, 0, 0])

    progress, regress = metadata.compare_snapshots(current, prev, target)

    assert progress == 0
    assert regress == 0


async def test_compare_snapshots_skips_transparent():
    """Test that transparent pixels are skipped in comparison."""
    target = bytes([0, 1, 0, 2])
    prev = bytes([5, 1, 5, 0])
    current = bytes([9, 1, 9, 2])

    progress, regress = metadata.compare_snapshots(current, prev, target)

    assert progress == 1
    assert regress == 0


async def test_update_completion_new_record(test_person):
    """Test updating max completion when improved."""
    info = ProjectInfo(owner=test_person, name="comp_new")
    await info.save_as_new()

    metadata.update_completion(info, 100, 50.0, 1000)
    assert info.max_completion_pixels == 100
    assert info.max_completion_percent == 50.0
    assert info.max_completion_time == 1000

    metadata.update_completion(info, 50, 75.0, 2000)
    assert info.max_completion_pixels == 50
    assert info.max_completion_percent == 75.0
    assert info.max_completion_time == 2000


async def test_update_completion_no_improvement(test_person):
    """Test that completion doesn't downgrade."""
    info = ProjectInfo(owner=test_person, name="comp_noimpr")
    await info.save_as_new()

    metadata.update_completion(info, 50, 75.0, 1000)

    metadata.update_completion(info, 100, 50.0, 2000)
    assert info.max_completion_pixels == 50
    assert info.max_completion_percent == 75.0
    assert info.max_completion_time == 1000


async def test_update_regress_new_record(test_person):
    """Test updating largest regress event."""
    info = ProjectInfo(owner=test_person, name="reg_new")
    await info.save_as_new()

    metadata.update_regress(info, 10, 1000)
    assert info.largest_regress_pixels == 10
    assert info.largest_regress_time == 1000

    metadata.update_regress(info, 20, 2000)
    assert info.largest_regress_pixels == 20
    assert info.largest_regress_time == 2000


async def test_update_regress_not_larger(test_person):
    """Test that smaller regress doesn't update record."""
    info = ProjectInfo(owner=test_person, name="reg_smaller")
    await info.save_as_new()

    metadata.update_regress(info, 20, 1000)
    metadata.update_regress(info, 5, 2000)

    assert info.largest_regress_pixels == 20
    assert info.largest_regress_time == 1000


async def test_update_rate_new_window(test_person):
    """Test rate calculation starting new window."""
    info = ProjectInfo(owner=test_person, name="rate_new")
    await info.save_as_new()

    metadata.update_rate(info, 10, 2, 1000)

    assert info.recent_rate_window_start == 1000
    assert info.recent_rate_pixels_per_hour == 0.0


async def test_update_rate_with_elapsed_time(test_person):
    """Test rate calculation with elapsed time."""
    info = ProjectInfo(owner=test_person, name="rate_elapsed")
    await info.save_as_new()

    info.recent_rate_window_start = 1000
    metadata.update_rate(info, 10, 2, 1000 + 3600)

    assert info.recent_rate_pixels_per_hour == 8.0


async def test_update_rate_window_reset(test_person):
    """Test rate window resets after 24 hours."""
    info = ProjectInfo(owner=test_person, name="rate_reset")
    await info.save_as_new()

    info.recent_rate_window_start = 1000
    info.recent_rate_pixels_per_hour = 100.0

    metadata.update_rate(info, 5, 0, 1000 + 86401)

    assert info.recent_rate_window_start == 1000 + 86401
    assert info.recent_rate_pixels_per_hour == 0.0


async def test_update_rate_negative_net_change(test_person):
    """Test rate calculation with net regress."""
    info = ProjectInfo(owner=test_person, name="rate_neg")
    await info.save_as_new()

    info.recent_rate_window_start = 1000
    metadata.update_rate(info, 2, 10, 1000 + 3600)

    assert info.recent_rate_pixels_per_hour == -8.0


async def test_has_missing_tiles_default(test_person):
    """Test has_missing_tiles defaults to True."""
    info = ProjectInfo(owner=test_person, name="miss_default")
    await info.save_as_new()
    assert info.has_missing_tiles is True


async def test_has_missing_tiles_persistence(test_person):
    """Test has_missing_tiles persists through DB round-trip."""
    info = ProjectInfo(owner=test_person, name="miss_persist", has_missing_tiles=False)
    await info.save_as_new()
    loaded = await ProjectInfo.get(owner=test_person, name="miss_persist")
    assert loaded.has_missing_tiles is False

    info2 = ProjectInfo(owner=test_person, name="miss_persist2", has_missing_tiles=True)
    await info2.save_as_new()
    loaded2 = await ProjectInfo.get(owner=test_person, name="miss_persist2")
    assert loaded2.has_missing_tiles is True


# save_as_new tests


async def test_save_as_new_assigns_random_id(test_person):
    """Test that save_as_new assigns an ID in the valid range."""
    info = ProjectInfo(owner=test_person, name="random_id")
    await info.save_as_new()

    assert 1 <= info.id <= 9999
    loaded = await ProjectInfo.get(id=info.id)
    assert loaded.name == "random_id"


async def test_save_as_new_retries_on_collision(test_person, monkeypatch):
    """Test that save_as_new retries when a random ID collides."""
    # Create a project that occupies ID 42
    first = ProjectInfo(owner=test_person, name="occupier")
    await first.save_as_new()
    occupied_id = first.id

    # Monkeypatch randint to return the occupied ID first, then a fresh one
    call_count = 0
    original_randint = random.randint

    def rigged_randint(a, b):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return occupied_id
        return original_randint(a, b)

    monkeypatch.setattr(random, "randint", rigged_randint)

    second = ProjectInfo(owner=test_person, name="retried")
    await second.save_as_new()

    assert second.id != occupied_id
    assert 1 <= second.id <= 9999
    assert call_count >= 2


async def test_save_as_new_exhaustion(test_person, monkeypatch):
    """Test that save_as_new asserts on exhaustion."""
    # Create a project at ID 42
    first = ProjectInfo(owner=test_person, name="blocker")
    await first.save_as_new()
    occupied_id = first.id

    # Always return the same occupied ID
    monkeypatch.setattr(random, "randint", lambda a, b: occupied_id)

    second = ProjectInfo(owner=test_person, name="exhausted")
    with pytest.raises(AssertionError, match="Failed to save project with unique ID"):
        await second.save_as_new(max_attempts=5)


# link_tiles / unlink_tiles tests


TILE_RECT = Rectangle.from_point_size(Point(5000, 7000), Size(100, 100))


class TestLinkTiles:
    async def test_creates_tile_records(self, test_person):
        info = await ProjectInfo.from_rect(TILE_RECT, test_person.id, "link-test")
        linked = await info.link_tiles()
        assert linked > 0

        from pixel_hawk.models import TileProject

        count = await TileProject.filter(project_id=info.id).count()
        assert count == linked

    async def test_idempotent(self, test_person):
        info = await ProjectInfo.from_rect(TILE_RECT, test_person.id, "idem-test")
        first = await info.link_tiles()
        second = await info.link_tiles()
        assert first > 0
        assert second == 0

    async def test_creates_tile_info(self, test_person):
        info = await ProjectInfo.from_rect(TILE_RECT, test_person.id, "tileinfo-test")
        await info.link_tiles()

        from pixel_hawk.models import TileInfo

        tile_info = await TileInfo.get(id=TileInfo.tile_id(5, 7))
        assert tile_info.x == 5
        assert tile_info.y == 7


class TestUnlinkTiles:
    async def test_deletes_tile_projects(self, test_person):
        info = await ProjectInfo.from_rect(TILE_RECT, test_person.id, "unlink-test")
        await info.link_tiles()

        deleted = await info.unlink_tiles()
        assert deleted > 0

        from pixel_hawk.models import TileProject

        count = await TileProject.filter(project_id=info.id).count()
        assert count == 0

    async def test_no_records_returns_zero(self, test_person):
        info = await ProjectInfo.from_rect(TILE_RECT, test_person.id, "empty-test")
        deleted = await info.unlink_tiles()
        assert deleted == 0

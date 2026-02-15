"""Tests for project metadata computation and ProjectInfo model."""

import time

import pytest

from pixel_hawk import metadata
from pixel_hawk.geometry import Point, Rectangle, Size, Tile
from pixel_hawk.models import Person, ProjectInfo, ProjectState


@pytest.fixture
async def test_person():
    """Create a test person for use in tests."""
    return await Person.create(name="TestPerson")


async def test_project_info_default_initialization(test_person):
    """Test ProjectInfo can be created with defaults via DB."""
    info = await ProjectInfo.create(owner=test_person, name="test")

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
    assert info.tile_last_update == {}
    assert info.tile_updates_24h == []


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
    await ProjectInfo.create(
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
        tile_last_update={"1_2": 7000},
        tile_updates_24h=[["1_2", 7000]],
    )

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
    assert loaded.tile_last_update == {"1_2": 7000}
    assert loaded.tile_updates_24h == [["1_2", 7000]]


async def test_prune_old_tile_updates(test_person):
    """Test pruning of old tile updates from 24h list."""
    info = await ProjectInfo.create(owner=test_person, name="prune_test")
    now = round(time.time())
    old_time = now - 100000  # more than 24h ago
    recent_time = now - 1000  # within 24h

    info.tile_updates_24h = [
        ["old_tile_1", old_time],
        ["recent_tile", recent_time],
        ["old_tile_2", old_time - 5000],
        ["recent_tile_2", now],
    ]

    info.last_check = now
    metadata.prune_old_tile_updates(info)

    assert len(info.tile_updates_24h) == 2
    assert ["recent_tile", recent_time] in info.tile_updates_24h
    assert ["recent_tile_2", now] in info.tile_updates_24h
    assert ["old_tile_1", old_time] not in info.tile_updates_24h
    assert ["old_tile_2", old_time - 5000] not in info.tile_updates_24h


async def test_prune_empty_list(test_person):
    """Test pruning on empty tile updates list."""
    info = await ProjectInfo.create(owner=test_person, name="prune_empty")
    info.tile_updates_24h = []

    info.last_check = round(time.time())
    metadata.prune_old_tile_updates(info)

    assert info.tile_updates_24h == []


async def test_prune_all_old(test_person):
    """Test pruning when all updates are old."""
    info = await ProjectInfo.create(owner=test_person, name="prune_all_old")
    old_time = round(time.time()) - 200000
    info.tile_updates_24h = [
        ["tile_1", old_time],
        ["tile_2", old_time + 1000],
    ]

    info.last_check = round(time.time())
    metadata.prune_old_tile_updates(info)

    assert info.tile_updates_24h == []


async def test_update_tile(test_person):
    """Test tile update recording."""
    info = await ProjectInfo.create(owner=test_person, name="update_tile")
    tile = Tile(1, 2)
    timestamp = 12345

    metadata.update_tile(info, tile, timestamp)

    assert info.tile_last_update["1_2"] == timestamp
    assert ["1_2", timestamp] in info.tile_updates_24h


async def test_update_tile_multiple_times(test_person):
    """Test updating the same tile multiple times."""
    info = await ProjectInfo.create(owner=test_person, name="update_multi")
    tile = Tile(5, 10)

    metadata.update_tile(info, tile, 1000)
    assert info.tile_last_update["5_10"] == 1000
    assert len(info.tile_updates_24h) == 1

    metadata.update_tile(info, tile, 2000)
    assert info.tile_last_update["5_10"] == 2000
    assert len(info.tile_updates_24h) == 2
    assert ["5_10", 1000] in info.tile_updates_24h
    assert ["5_10", 2000] in info.tile_updates_24h


async def test_update_tile_duplicate_prevention(test_person):
    """Test that duplicate tile updates are not added."""
    info = await ProjectInfo.create(owner=test_person, name="update_dup")
    tile = Tile(3, 7)
    timestamp = 5000

    metadata.update_tile(info, tile, timestamp)
    metadata.update_tile(info, tile, timestamp)
    metadata.update_tile(info, tile, timestamp)

    assert info.tile_last_update["3_7"] == timestamp
    assert len(info.tile_updates_24h) == 1
    assert info.tile_updates_24h[0] == ["3_7", timestamp]


async def test_update_multiple_tiles(test_person):
    """Test updating multiple different tiles."""
    info = await ProjectInfo.create(owner=test_person, name="update_many")

    tiles_and_times = [
        (Tile(1, 2), 1000),
        (Tile(3, 4), 2000),
        (Tile(5, 6), 3000),
    ]

    for tile, timestamp in tiles_and_times:
        metadata.update_tile(info, tile, timestamp)

    assert len(info.tile_last_update) == 3
    assert info.tile_last_update["1_2"] == 1000
    assert info.tile_last_update["3_4"] == 2000
    assert info.tile_last_update["5_6"] == 3000

    assert len(info.tile_updates_24h) == 3
    assert ["1_2", 1000] in info.tile_updates_24h
    assert ["3_4", 2000] in info.tile_updates_24h
    assert ["5_6", 3000] in info.tile_updates_24h


async def test_tile_tracking_integrated(test_person):
    """Test integrated tile tracking with updates and pruning."""
    info = await ProjectInfo.create(owner=test_person, name="integrated")
    now = round(time.time())

    old_time = now - 100000
    metadata.update_tile(info, Tile(1, 1), old_time)
    metadata.update_tile(info, Tile(2, 2), old_time + 1000)

    recent_time = now - 1000
    metadata.update_tile(info, Tile(3, 3), recent_time)
    metadata.update_tile(info, Tile(4, 4), now)

    assert len(info.tile_last_update) == 4
    assert len(info.tile_updates_24h) == 4

    info.last_check = now
    metadata.prune_old_tile_updates(info)

    assert len(info.tile_updates_24h) == 2
    assert ["3_3", recent_time] in info.tile_updates_24h
    assert ["4_4", now] in info.tile_updates_24h

    assert len(info.tile_last_update) == 4
    assert "1_1" in info.tile_last_update
    assert "2_2" in info.tile_last_update


async def test_numeric_fields_precision(test_person):
    """Test floating point precision in DB round-trip."""
    info = await ProjectInfo.create(
        owner=test_person,
        name="precision",
        max_completion_percent=99.99999,
        recent_rate_pixels_per_hour=123.456789,
    )

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
    info = await ProjectInfo.create(owner=test_person, name="comp_new")

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
    info = await ProjectInfo.create(owner=test_person, name="comp_noimpr")

    metadata.update_completion(info, 50, 75.0, 1000)

    metadata.update_completion(info, 100, 50.0, 2000)
    assert info.max_completion_pixels == 50
    assert info.max_completion_percent == 75.0
    assert info.max_completion_time == 1000


async def test_update_regress_new_record(test_person):
    """Test updating largest regress event."""
    info = await ProjectInfo.create(owner=test_person, name="reg_new")

    metadata.update_regress(info, 10, 1000)
    assert info.largest_regress_pixels == 10
    assert info.largest_regress_time == 1000

    metadata.update_regress(info, 20, 2000)
    assert info.largest_regress_pixels == 20
    assert info.largest_regress_time == 2000


async def test_update_regress_not_larger(test_person):
    """Test that smaller regress doesn't update record."""
    info = await ProjectInfo.create(owner=test_person, name="reg_smaller")

    metadata.update_regress(info, 20, 1000)
    metadata.update_regress(info, 5, 2000)

    assert info.largest_regress_pixels == 20
    assert info.largest_regress_time == 1000


async def test_update_rate_new_window(test_person):
    """Test rate calculation starting new window."""
    info = await ProjectInfo.create(owner=test_person, name="rate_new")

    metadata.update_rate(info, 10, 2, 1000)

    assert info.recent_rate_window_start == 1000
    assert info.recent_rate_pixels_per_hour == 0.0


async def test_update_rate_with_elapsed_time(test_person):
    """Test rate calculation with elapsed time."""
    info = await ProjectInfo.create(owner=test_person, name="rate_elapsed")

    info.recent_rate_window_start = 1000
    metadata.update_rate(info, 10, 2, 1000 + 3600)

    assert info.recent_rate_pixels_per_hour == 8.0


async def test_update_rate_window_reset(test_person):
    """Test rate window resets after 24 hours."""
    info = await ProjectInfo.create(owner=test_person, name="rate_reset")

    info.recent_rate_window_start = 1000
    info.recent_rate_pixels_per_hour = 100.0

    metadata.update_rate(info, 5, 0, 1000 + 86401)

    assert info.recent_rate_window_start == 1000 + 86401
    assert info.recent_rate_pixels_per_hour == 0.0


async def test_update_rate_negative_net_change(test_person):
    """Test rate calculation with net regress."""
    info = await ProjectInfo.create(owner=test_person, name="rate_neg")

    info.recent_rate_window_start = 1000
    metadata.update_rate(info, 2, 10, 1000 + 3600)

    assert info.recent_rate_pixels_per_hour == -8.0


async def test_has_missing_tiles_default(test_person):
    """Test has_missing_tiles defaults to True."""
    info = await ProjectInfo.create(owner=test_person, name="miss_default")
    assert info.has_missing_tiles is True


async def test_has_missing_tiles_persistence(test_person):
    """Test has_missing_tiles persists through DB round-trip."""
    await ProjectInfo.create(owner=test_person, name="miss_persist", has_missing_tiles=False)
    loaded = await ProjectInfo.get(owner=test_person, name="miss_persist")
    assert loaded.has_missing_tiles is False

    await ProjectInfo.create(owner=test_person, name="miss_persist2", has_missing_tiles=True)
    loaded2 = await ProjectInfo.get(owner=test_person, name="miss_persist2")
    assert loaded2.has_missing_tiles is True

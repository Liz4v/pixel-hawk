"""Tests for project metadata computation and ProjectInfo model."""

import random
import time

import pytest

from pixel_hawk.watcher import metadata
from pixel_hawk.models.geometry import Point, Rectangle, Size
from pixel_hawk.models.person import Person
from pixel_hawk.models.project import HistoryChange, ProjectInfo


@pytest.fixture
async def test_person():
    """Create a test person for use in tests."""
    return await Person.create(name="TestPerson")


async def test_project_info_default_initialization(test_person):
    """Test ProjectInfo can be created with defaults via DB."""
    info = ProjectInfo(owner_id=test_person.id, owner=test_person, name="test")
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
        owner_id=test_person.id,
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

    loaded = await ProjectInfo.get_by_owner_name(test_person.id, "roundtrip")
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
        owner_id=test_person.id,
        owner=test_person,
        name="precision",
        max_completion_percent=99.99999,
        recent_rate_pixels_per_hour=123.456789,
    )
    await info.save_as_new()

    loaded = await ProjectInfo.get_by_owner_name(test_person.id, "precision")
    assert loaded.max_completion_percent == info.max_completion_percent
    assert loaded.recent_rate_pixels_per_hour == info.recent_rate_pixels_per_hour


# Pixel counting tests


async def test_find_regressed_indices_basic():
    """Test finding flat indices of regressed pixels."""
    target = bytes([0, 1, 2, 3, 0])
    prev = bytes([0, 1, 2, 3, 0])
    current = bytes([0, 1, 0, 0, 0])  # pixels 2 and 3 regressed

    indices = metadata.find_regressed_indices(current, prev, target)

    assert indices == [2, 3]


async def test_find_regressed_indices_skips_transparent():
    """Transparent target pixels are never counted as regressed."""
    target = bytes([0, 1, 0, 2])
    prev = bytes([5, 1, 5, 2])
    current = bytes([9, 1, 9, 0])  # pixel 3 regressed, pixel 0 and 2 are transparent

    indices = metadata.find_regressed_indices(current, prev, target)

    assert indices == [3]


async def test_find_regressed_indices_no_regression():
    """No regressed pixels returns empty list."""
    target = bytes([0, 1, 2, 0])
    prev = bytes([0, 0, 0, 0])
    current = bytes([0, 1, 2, 0])  # all progress, no regress

    indices = metadata.find_regressed_indices(current, prev, target)

    assert indices == []


async def test_find_regressed_indices_ignores_progress():
    """Progress pixels are not included in regressed indices."""
    target = bytes([1, 2, 3])
    prev = bytes([0, 2, 0])
    current = bytes([1, 0, 3])  # pixel 0: progress, pixel 1: regress, pixel 2: progress

    indices = metadata.find_regressed_indices(current, prev, target)

    assert indices == [1]


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
    info = ProjectInfo(owner_id=test_person.id, owner=test_person, name="comp_new")
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
    info = ProjectInfo(owner_id=test_person.id, owner=test_person, name="comp_noimpr")
    await info.save_as_new()

    metadata.update_completion(info, 50, 75.0, 1000)

    metadata.update_completion(info, 100, 50.0, 2000)
    assert info.max_completion_pixels == 50
    assert info.max_completion_percent == 75.0
    assert info.max_completion_time == 1000


async def test_update_completion_stays_locked_at_zero(test_person):
    """Test that completion time doesn't update once project reaches 0 remaining."""
    info = ProjectInfo(owner_id=test_person.id, owner=test_person, name="comp_lock")
    await info.save_as_new()

    metadata.update_completion(info, 0, 100.0, 1000)
    assert info.max_completion_pixels == 0
    assert info.max_completion_time == 1000

    metadata.update_completion(info, 0, 100.0, 9999)
    assert info.max_completion_time == 1000


async def test_update_regress_new_record(test_person):
    """Test updating largest regress event."""
    info = ProjectInfo(owner_id=test_person.id, owner=test_person, name="reg_new")
    await info.save_as_new()

    metadata.update_regress(info, 10, 1000)
    assert info.largest_regress_pixels == 10
    assert info.largest_regress_time == 1000

    metadata.update_regress(info, 20, 2000)
    assert info.largest_regress_pixels == 20
    assert info.largest_regress_time == 2000


async def test_update_regress_not_larger(test_person):
    """Test that smaller regress doesn't update record."""
    info = ProjectInfo(owner_id=test_person.id, owner=test_person, name="reg_smaller")
    await info.save_as_new()

    metadata.update_regress(info, 20, 1000)
    metadata.update_regress(info, 5, 2000)

    assert info.largest_regress_pixels == 20
    assert info.largest_regress_time == 1000


def _make_change(timestamp: int, progress: int = 0, regress: int = 0) -> HistoryChange:
    """Build a minimal HistoryChange for rate tests."""
    return HistoryChange(timestamp=timestamp, progress_pixels=progress, regress_pixels=regress)


def test_compute_rate_basic():
    """Steady painting over multiple intervals."""
    changes = [
        _make_change(1000),
        _make_change(1000 + 3600, progress=132),
        _make_change(1000 + 7200, progress=132),
    ]
    assert metadata.compute_rate(changes) == pytest.approx(132.0)


def test_compute_rate_includes_idle_time():
    """Idle gaps are included in the denominator (calendar rate)."""
    changes = [
        _make_change(1000),
        _make_change(1000 + 3600, progress=100),  # 1h active
        _make_change(1000 + 3600 + 50000, progress=5),  # ~14h idle
        _make_change(1000 + 3600 + 50000 + 3600, progress=100),  # 1h active
    ]
    rate = metadata.compute_rate(changes)
    # 205 net over full span of ~15.2 hours
    total_hours = (3600 + 50000 + 3600) / 3600.0
    assert rate == pytest.approx(205 / total_hours)


def test_compute_rate_handles_regress():
    """Grief pixels are subtracted from the rate."""
    changes = [
        _make_change(1000),
        _make_change(1000 + 3600, progress=100, regress=20),
    ]
    assert metadata.compute_rate(changes) == pytest.approx(80.0)


def test_compute_rate_too_few_entries():
    """Returns 0 with fewer than 2 entries."""
    assert metadata.compute_rate([]) == 0.0
    assert metadata.compute_rate([_make_change(1000)]) == 0.0


def test_compute_rate_unsorted_input():
    """Works regardless of input order."""
    changes = [
        _make_change(1000 + 7200, progress=132),
        _make_change(1000),
        _make_change(1000 + 3600, progress=132),
    ]
    assert metadata.compute_rate(changes) == pytest.approx(132.0)


async def test_has_missing_tiles_default(test_person):
    """Test has_missing_tiles defaults to True."""
    info = ProjectInfo(owner_id=test_person.id, owner=test_person, name="miss_default")
    await info.save_as_new()
    assert info.has_missing_tiles is True


async def test_has_missing_tiles_persistence(test_person):
    """Test has_missing_tiles persists through DB round-trip."""
    info = ProjectInfo(owner_id=test_person.id, owner=test_person, name="miss_persist", has_missing_tiles=False)
    await info.save_as_new()
    loaded = await ProjectInfo.get_by_owner_name(test_person.id, "miss_persist")
    assert loaded.has_missing_tiles is False

    info2 = ProjectInfo(owner_id=test_person.id, owner=test_person, name="miss_persist2", has_missing_tiles=True)
    await info2.save_as_new()
    loaded2 = await ProjectInfo.get_by_owner_name(test_person.id, "miss_persist2")
    assert loaded2.has_missing_tiles is True


# save_as_new tests


async def test_save_as_new_assigns_random_id(test_person):
    """Test that save_as_new assigns an ID in the valid range."""
    info = ProjectInfo(owner_id=test_person.id, owner=test_person, name="random_id")
    await info.save_as_new()

    assert 1 <= info.id <= 9999
    loaded = await ProjectInfo.get_by_id(info.id)
    assert loaded is not None
    assert loaded.name == "random_id"


async def test_save_as_new_retries_on_collision(test_person, monkeypatch):
    """Test that save_as_new retries when a random ID collides."""
    # Create a project that occupies ID 42
    first = ProjectInfo(owner_id=test_person.id, owner=test_person, name="occupier")
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

    second = ProjectInfo(owner_id=test_person.id, owner=test_person, name="retried")
    await second.save_as_new()

    assert second.id != occupied_id
    assert 1 <= second.id <= 9999
    assert call_count >= 2


async def test_save_as_new_exhaustion(test_person, monkeypatch):
    """Test that save_as_new raises RuntimeError on exhaustion."""
    # Create a project at ID 42
    first = ProjectInfo(owner_id=test_person.id, owner=test_person, name="blocker")
    await first.save_as_new()
    occupied_id = first.id

    # Always return the same occupied ID
    monkeypatch.setattr(random, "randint", lambda a, b: occupied_id)

    second = ProjectInfo(owner_id=test_person.id, owner=test_person, name="exhausted")
    with pytest.raises(RuntimeError, match="Failed to save project with unique ID"):
        await second.save_as_new(max_attempts=5)


# link_tiles / unlink_tiles tests


TILE_RECT = Rectangle.from_point_size(Point(5000, 7000), Size(100, 100))


class TestLinkTiles:
    async def test_creates_tile_records(self, test_person):
        info = await ProjectInfo.from_rect(TILE_RECT, test_person.id, "link-test")
        linked = await info.link_tiles()
        assert linked > 0

        from pixel_hawk.models.tile import TileProject

        count = len(await TileProject.filter_by_project(info.id))
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

        from pixel_hawk.models.tile import TileInfo

        tile_info = await TileInfo.get_by_id(TileInfo.tile_id(5, 7))
        assert tile_info.x == 5
        assert tile_info.y == 7


class TestUnlinkTiles:
    async def test_deletes_tile_projects(self, test_person):
        info = await ProjectInfo.from_rect(TILE_RECT, test_person.id, "unlink-test")
        await info.link_tiles()

        deleted = await info.unlink_tiles()
        assert deleted > 0

        from pixel_hawk.models.tile import TileProject

        count = len(await TileProject.filter_by_project(info.id))
        assert count == 0

    async def test_no_records_returns_zero(self, test_person):
        info = await ProjectInfo.from_rect(TILE_RECT, test_person.id, "empty-test")
        deleted = await info.unlink_tiles()
        assert deleted == 0

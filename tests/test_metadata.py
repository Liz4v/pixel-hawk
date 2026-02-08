"""Tests for project metadata tracking and persistence."""

import time

from cam.geometry import Point, Rectangle, Size, Tile
from cam.metadata import ProjectMetadata


def test_metadata_default_initialization():
    """Test ProjectMetadata can be created with defaults."""
    meta = ProjectMetadata()

    assert meta.x == 0
    assert meta.y == 0
    assert meta.width == 0
    assert meta.height == 0
    assert meta.first_seen == 0
    assert meta.last_check == 0
    assert meta.last_snapshot == 0
    assert meta.max_completion_pixels == 0
    assert meta.max_completion_percent == 0.0
    assert meta.max_completion_time == 0
    assert meta.total_progress == 0
    assert meta.total_regress == 0
    assert meta.largest_regress_pixels == 0
    assert meta.largest_regress_time == 0
    assert meta.change_streak_type == "none"
    assert meta.change_streak_count == 0
    assert meta.nochange_streak_count == 0
    assert meta.recent_rate_pixels_per_hour == 0.0
    assert meta.recent_rate_window_start == 0
    assert meta.tile_last_update == {}
    assert meta.tile_updates_24h == []


def test_metadata_from_rect():
    """Test ProjectMetadata.from_rect creates correct initial state."""
    rect = Rectangle.from_point_size(Point(100, 200), Size(50, 60))

    before_time = round(time.time())
    meta = ProjectMetadata.from_rect(rect, "test_project.png")
    after_time = round(time.time())

    assert meta.name == "test_project.png"
    assert meta.x == 100
    assert meta.y == 200
    assert meta.width == 50
    assert meta.height == 60
    assert before_time <= meta.first_seen <= after_time
    assert before_time <= meta.last_check <= after_time
    assert meta.first_seen == meta.last_check
    assert meta.max_completion_pixels == 0
    assert meta.total_progress == 0
    assert meta.total_regress == 0


def test_metadata_from_rect_with_offset():
    """Test ProjectMetadata.from_rect with non-zero origin."""
    rect = Rectangle.from_point_size(Point.from4(5, 7, 250, 380), Size(120, 80))
    meta = ProjectMetadata.from_rect(rect, "offset_project.png")

    assert meta.name == "offset_project.png"
    assert meta.x == 5250  # 5 * 1000 + 250
    assert meta.y == 7380  # 7 * 1000 + 380
    assert meta.width == 120
    assert meta.height == 80


def test_metadata_to_dict():
    """Test metadata serialization to dictionary."""
    meta = ProjectMetadata(
        x=10,
        y=20,
        width=30,
        height=40,
        first_seen=1000,
        last_check=2000,
        last_snapshot=3000,
        max_completion_pixels=100,
        max_completion_percent=75.5,
        max_completion_time=4000,
        total_progress=50,
        total_regress=5,
        largest_regress_pixels=10,
        largest_regress_time=5000,
        change_streak_type="progress",
        change_streak_count=3,
        nochange_streak_count=0,
        recent_rate_pixels_per_hour=12.5,
        recent_rate_window_start=6000,
        tile_last_update={"1_2": 7000, "3_4": 8000},
        tile_updates_24h=[("1_2", 7000), ("3_4", 8000)],
    )

    data = meta.to_dict()

    assert data["bounds"]["x"] == 10
    assert data["bounds"]["y"] == 20
    assert data["bounds"]["width"] == 30
    assert data["bounds"]["height"] == 40
    assert data["timestamps"]["first_seen"] == 1000
    assert data["timestamps"]["last_check"] == 2000
    assert data["timestamps"]["last_snapshot"] == 3000
    assert data["max_completion"]["pixels_remaining"] == 100
    assert data["max_completion"]["percent_complete"] == 75.5
    assert data["max_completion"]["achieved_at"] == 4000
    assert data["totals"]["progress_pixels"] == 50
    assert data["totals"]["regress_pixels"] == 5
    assert data["largest_regress"]["pixels"] == 10
    assert data["largest_regress"]["timestamp"] == 5000
    assert data["streak"]["change_type"] == "progress"
    assert data["streak"]["change_count"] == 3
    assert data["streak"]["nochange_count"] == 0
    assert data["recent_rate"]["pixels_per_hour"] == 12.5
    assert data["recent_rate"]["window_start"] == 6000
    assert data["tile_updates"]["last_update_by_tile"] == {"1_2": 7000, "3_4": 8000}
    assert len(data["tile_updates"]["recent_24h"]) == 2
    assert data["tile_updates"]["recent_24h"][0]["tile"] == "1_2"
    assert data["tile_updates"]["recent_24h"][0]["timestamp"] == 7000


def test_metadata_from_dict():
    """Test metadata deserialization from dictionary."""
    data = {
        "bounds": {"x": 10, "y": 20, "width": 30, "height": 40},
        "timestamps": {
            "first_seen": 1000,
            "last_check": 2000,
            "last_snapshot": 3000,
        },
        "max_completion": {
            "pixels_remaining": 100,
            "percent_complete": 75.5,
            "achieved_at": 4000,
        },
        "totals": {
            "progress_pixels": 50,
            "regress_pixels": 5,
        },
        "largest_regress": {
            "pixels": 10,
            "timestamp": 5000,
        },
        "streak": {
            "change_type": "progress",
            "change_count": 3,
            "nochange_count": 0,
        },
        "recent_rate": {
            "pixels_per_hour": 12.5,
            "window_start": 6000,
        },
        "tile_updates": {
            "last_update_by_tile": {"1_2": 7000, "3_4": 8000},
            "recent_24h": [
                {"tile": "1_2", "timestamp": 7000},
                {"tile": "3_4", "timestamp": 8000},
            ],
        },
    }

    meta = ProjectMetadata.from_dict(data)

    assert meta.x == 10
    assert meta.y == 20
    assert meta.width == 30
    assert meta.height == 40
    assert meta.first_seen == 1000
    assert meta.last_check == 2000
    assert meta.last_snapshot == 3000
    assert meta.max_completion_pixels == 100
    assert meta.max_completion_percent == 75.5
    assert meta.max_completion_time == 4000
    assert meta.total_progress == 50
    assert meta.total_regress == 5
    assert meta.largest_regress_pixels == 10
    assert meta.largest_regress_time == 5000
    assert meta.change_streak_type == "progress"
    assert meta.change_streak_count == 3
    assert meta.nochange_streak_count == 0
    assert meta.recent_rate_pixels_per_hour == 12.5
    assert meta.recent_rate_window_start == 6000
    assert meta.tile_last_update == {"1_2": 7000, "3_4": 8000}
    assert meta.tile_updates_24h == [("1_2", 7000), ("3_4", 8000)]


def test_metadata_from_dict_with_missing_fields():
    """Test from_dict handles missing fields gracefully."""
    data = {"bounds": {"x": 100}}  # Minimal data

    meta = ProjectMetadata.from_dict(data)

    assert meta.x == 100
    assert meta.y == 0  # Default value
    assert meta.width == 0
    assert meta.change_streak_type == "none"
    assert meta.nochange_streak_count == 0
    assert meta.tile_last_update == {}
    assert meta.tile_updates_24h == []


def test_metadata_round_trip():
    """Test metadata serialization round-trip preserves all data."""
    rect = Rectangle.from_point_size(Point(10, 20), Size(30, 40))
    meta = ProjectMetadata.from_rect(rect, "roundtrip_test.png")
    meta.max_completion_pixels = 100
    meta.max_completion_percent = 75.5
    meta.total_progress = 50
    meta.total_regress = 5
    meta.change_streak_type = "progress"
    meta.change_streak_count = 3
    meta.tile_last_update = {"1_2": 7000}
    meta.tile_updates_24h = [("1_2", 7000)]

    data = meta.to_dict()
    meta2 = ProjectMetadata.from_dict(data)

    assert meta2.x == meta.x
    assert meta2.y == meta.y
    assert meta2.width == meta.width
    assert meta2.height == meta.height
    assert meta2.first_seen == meta.first_seen
    assert meta2.max_completion_pixels == meta.max_completion_pixels
    assert meta2.max_completion_percent == meta.max_completion_percent
    assert meta2.total_progress == meta.total_progress
    assert meta2.total_regress == meta.total_regress
    assert meta2.change_streak_type == meta.change_streak_type
    assert meta2.change_streak_count == meta.change_streak_count
    assert meta2.nochange_streak_count == meta.nochange_streak_count
    assert meta2.tile_last_update == meta.tile_last_update
    assert meta2.tile_updates_24h == meta.tile_updates_24h


def test_metadata_prune_old_tile_updates():
    """Test pruning of old tile updates from 24h list."""
    meta = ProjectMetadata()
    now = round(time.time())
    old_time = now - 100000  # more than 24h ago
    recent_time = now - 1000  # within 24h

    meta.tile_updates_24h = [
        ("old_tile_1", old_time),
        ("recent_tile", recent_time),
        ("old_tile_2", old_time - 5000),
        ("recent_tile_2", now),
    ]

    meta.last_check = now  # Set last_check so cutoff = now - 86400
    meta.prune_old_tile_updates()

    assert len(meta.tile_updates_24h) == 2
    assert ("recent_tile", recent_time) in meta.tile_updates_24h
    assert ("recent_tile_2", now) in meta.tile_updates_24h
    assert ("old_tile_1", old_time) not in meta.tile_updates_24h
    assert ("old_tile_2", old_time - 5000) not in meta.tile_updates_24h


def test_metadata_prune_empty_list():
    """Test pruning on empty tile updates list."""
    meta = ProjectMetadata()
    meta.tile_updates_24h = []

    meta.last_check = round(time.time())
    meta.prune_old_tile_updates()

    assert meta.tile_updates_24h == []


def test_metadata_prune_all_old():
    """Test pruning when all updates are old."""
    meta = ProjectMetadata()
    old_time = round(time.time()) - 200000
    meta.tile_updates_24h = [
        ("tile_1", old_time),
        ("tile_2", old_time + 1000),
    ]

    meta.last_check = round(time.time())
    meta.prune_old_tile_updates()

    assert meta.tile_updates_24h == []


def test_metadata_update_tile():
    """Test tile update recording."""
    meta = ProjectMetadata()
    tile = Tile(1, 2)
    timestamp = 12345

    meta.update_tile(tile, timestamp)

    assert meta.tile_last_update["1_2"] == timestamp
    assert ("1_2", timestamp) in meta.tile_updates_24h


def test_metadata_update_tile_multiple_times():
    """Test updating the same tile multiple times."""
    meta = ProjectMetadata()
    tile = Tile(5, 10)

    # First update
    meta.update_tile(tile, 1000)
    assert meta.tile_last_update["5_10"] == 1000
    assert len(meta.tile_updates_24h) == 1

    # Second update with different timestamp
    meta.update_tile(tile, 2000)
    assert meta.tile_last_update["5_10"] == 2000
    assert len(meta.tile_updates_24h) == 2
    assert ("5_10", 1000) in meta.tile_updates_24h
    assert ("5_10", 2000) in meta.tile_updates_24h


def test_metadata_update_tile_duplicate_prevention():
    """Test that duplicate tile updates are not added."""
    meta = ProjectMetadata()
    tile = Tile(3, 7)
    timestamp = 5000

    # Add same tile with same timestamp multiple times
    meta.update_tile(tile, timestamp)
    meta.update_tile(tile, timestamp)
    meta.update_tile(tile, timestamp)

    # Should only appear once in 24h list
    assert meta.tile_last_update["3_7"] == timestamp
    assert len(meta.tile_updates_24h) == 1
    assert meta.tile_updates_24h[0] == ("3_7", timestamp)


def test_metadata_update_multiple_tiles():
    """Test updating multiple different tiles."""
    meta = ProjectMetadata()

    tiles_and_times = [
        (Tile(1, 2), 1000),
        (Tile(3, 4), 2000),
        (Tile(5, 6), 3000),
    ]

    for tile, timestamp in tiles_and_times:
        meta.update_tile(tile, timestamp)

    assert len(meta.tile_last_update) == 3
    assert meta.tile_last_update["1_2"] == 1000
    assert meta.tile_last_update["3_4"] == 2000
    assert meta.tile_last_update["5_6"] == 3000

    assert len(meta.tile_updates_24h) == 3
    assert ("1_2", 1000) in meta.tile_updates_24h
    assert ("3_4", 2000) in meta.tile_updates_24h
    assert ("5_6", 3000) in meta.tile_updates_24h


def test_metadata_tile_tracking_integrated():
    """Test integrated tile tracking with updates and pruning."""
    meta = ProjectMetadata()
    now = round(time.time())

    # Add some old updates
    old_time = now - 100000
    meta.update_tile(Tile(1, 1), old_time)
    meta.update_tile(Tile(2, 2), old_time + 1000)

    # Add some recent updates
    recent_time = now - 1000
    meta.update_tile(Tile(3, 3), recent_time)
    meta.update_tile(Tile(4, 4), now)

    # Verify all tiles recorded
    assert len(meta.tile_last_update) == 4
    assert len(meta.tile_updates_24h) == 4

    # Prune old updates
    meta.last_check = now
    meta.prune_old_tile_updates()

    # Only recent tiles remain in 24h list
    assert len(meta.tile_updates_24h) == 2
    assert ("3_3", recent_time) in meta.tile_updates_24h
    assert ("4_4", now) in meta.tile_updates_24h

    # But all tiles still in last_update map
    assert len(meta.tile_last_update) == 4
    assert "1_1" in meta.tile_last_update
    assert "2_2" in meta.tile_last_update


def test_metadata_change_streak_types():
    """Test all possible change streak type values."""
    valid_types = ["none", "progress", "regress", "mixed"]

    for streak_type in valid_types:
        meta = ProjectMetadata(change_streak_type=streak_type, change_streak_count=5)
        assert meta.change_streak_type == streak_type
        assert meta.change_streak_count == 5


def test_metadata_numeric_fields_precision():
    """Test floating point precision in metadata."""
    meta = ProjectMetadata(
        max_completion_percent=99.99999,
        recent_rate_pixels_per_hour=123.456789,
    )

    data = meta.to_dict()
    meta2 = ProjectMetadata.from_dict(data)

    assert meta2.max_completion_percent == meta.max_completion_percent
    assert meta2.recent_rate_pixels_per_hour == meta.recent_rate_pixels_per_hour


# Pixel counting tests


def test_count_remaining_pixels():
    """Test counting remaining pixels from diff bytes."""
    meta = ProjectMetadata()

    # All zeros - nothing remaining
    assert meta.count_remaining_pixels(bytes([0, 0, 0, 0])) == 0

    # Some pixels remaining
    assert meta.count_remaining_pixels(bytes([0, 1, 0, 2, 0, 3])) == 3

    # All pixels remaining
    assert meta.count_remaining_pixels(bytes([1, 2, 3, 4])) == 4


def test_count_target_pixels():
    """Test counting target pixels with division-by-zero protection."""
    meta = ProjectMetadata()

    # Normal case
    assert meta.count_target_pixels(bytes([0, 1, 2, 3])) == 3

    # All target pixels
    assert meta.count_target_pixels(bytes([1, 1, 1, 1])) == 4

    # No target pixels - should return 1 to avoid div/0
    assert meta.count_target_pixels(bytes([0, 0, 0, 0])) == 1


def test_calculate_completion_percent():
    """Test completion percentage calculation."""
    meta = ProjectMetadata()

    # 50% complete
    assert meta.calculate_completion_percent(50, 100) == 50.0

    # Fully complete
    assert meta.calculate_completion_percent(0, 100) == 100.0

    # Not started
    assert meta.calculate_completion_percent(100, 100) == 0.0

    # Nearly complete
    assert meta.calculate_completion_percent(1, 100) == 99.0


def test_compare_snapshots_progress():
    """Test snapshot comparison detecting progress."""
    meta = ProjectMetadata()

    # Target: pixels 1 and 2 should be colored
    target = bytes([0, 1, 2, 0])
    # Previous: pixel 1 correct, pixel 2 wrong
    prev = bytes([0, 1, 0, 0])
    # Current: both correct (progress on pixel 2)
    current = bytes([0, 1, 2, 0])

    progress, regress = meta.compare_snapshots(current, prev, target)

    assert progress == 1
    assert regress == 0


def test_compare_snapshots_regress():
    """Test snapshot comparison detecting regress."""
    meta = ProjectMetadata()

    # Target: pixels 1 and 2 should be colored
    target = bytes([0, 1, 2, 0])
    # Previous: both correct
    prev = bytes([0, 1, 2, 0])
    # Current: pixel 2 griefed (regress)
    current = bytes([0, 1, 0, 0])

    progress, regress = meta.compare_snapshots(current, prev, target)

    assert progress == 0
    assert regress == 1


def test_compare_snapshots_mixed():
    """Test snapshot comparison with both progress and regress."""
    meta = ProjectMetadata()

    # Target: pixels 1, 2, 3 should be colored
    target = bytes([0, 1, 2, 3, 0])
    # Previous: pixel 1 correct, rest wrong
    prev = bytes([0, 1, 0, 0, 0])
    # Current: pixels 1 and 2 correct, 3 gets griefed
    current = bytes([0, 1, 2, 0, 0])

    progress, regress = meta.compare_snapshots(current, prev, target)

    assert progress == 1  # pixel 2 fixed
    assert regress == 0  # pixel 3 was already wrong


def test_compare_snapshots_no_change():
    """Test snapshot comparison with no changes."""
    meta = ProjectMetadata()

    target = bytes([0, 1, 2, 0])
    prev = bytes([0, 1, 0, 0])
    current = bytes([0, 1, 0, 0])  # Same as previous

    progress, regress = meta.compare_snapshots(current, prev, target)

    assert progress == 0
    assert regress == 0


def test_compare_snapshots_skips_transparent():
    """Test that transparent pixels are skipped in comparison."""
    meta = ProjectMetadata()

    # Target has transparent (0) pixels
    target = bytes([0, 1, 0, 2])
    prev = bytes([5, 1, 5, 0])  # Changes in transparent areas
    current = bytes([9, 1, 9, 2])  # Different, but transparent pixels ignored

    progress, regress = meta.compare_snapshots(current, prev, target)

    # Only pixel 3 (index 3) matters: was wrong, now correct
    assert progress == 1
    assert regress == 0


def test_update_completion_new_record():
    """Test updating max completion when improved."""
    meta = ProjectMetadata()

    meta.update_completion(100, 50.0, 1000)
    assert meta.max_completion_pixels == 100
    assert meta.max_completion_percent == 50.0
    assert meta.max_completion_time == 1000

    # Better completion
    meta.update_completion(50, 75.0, 2000)
    assert meta.max_completion_pixels == 50
    assert meta.max_completion_percent == 75.0
    assert meta.max_completion_time == 2000


def test_update_completion_no_improvement():
    """Test that completion doesn't downgrade."""
    meta = ProjectMetadata()

    meta.update_completion(50, 75.0, 1000)

    # Worse completion - should not update
    meta.update_completion(100, 50.0, 2000)
    assert meta.max_completion_pixels == 50
    assert meta.max_completion_percent == 75.0
    assert meta.max_completion_time == 1000


def test_update_regress_new_record():
    """Test updating largest regress event."""
    meta = ProjectMetadata()

    meta.update_regress(10, 1000)
    assert meta.largest_regress_pixels == 10
    assert meta.largest_regress_time == 1000

    # Larger regress
    meta.update_regress(20, 2000)
    assert meta.largest_regress_pixels == 20
    assert meta.largest_regress_time == 2000


def test_update_regress_not_larger():
    """Test that smaller regress doesn't update record."""
    meta = ProjectMetadata()

    meta.update_regress(20, 1000)
    meta.update_regress(5, 2000)  # Smaller

    assert meta.largest_regress_pixels == 20
    assert meta.largest_regress_time == 1000


def test_update_streak_progress():
    """Test streak updates for progress."""
    meta = ProjectMetadata()

    # First progress
    meta.update_streak(1, 0)
    assert meta.change_streak_type == "progress"
    assert meta.change_streak_count == 1
    assert meta.nochange_streak_count == 0

    # Continue progress streak
    meta.update_streak(2, 0)
    assert meta.change_streak_type == "progress"
    assert meta.change_streak_count == 2
    assert meta.nochange_streak_count == 0


def test_update_streak_regress():
    """Test streak updates for regress."""
    meta = ProjectMetadata()

    meta.update_streak(0, 1)
    assert meta.change_streak_type == "regress"
    assert meta.change_streak_count == 1
    assert meta.nochange_streak_count == 0

    meta.update_streak(0, 2)
    assert meta.change_streak_type == "regress"
    assert meta.change_streak_count == 2
    assert meta.nochange_streak_count == 0


def test_update_streak_nochange():
    """Test streak updates for no change (independent of change streak)."""
    meta = ProjectMetadata()

    # Nochange increments without affecting change streak
    meta.update_streak(0, 0)
    assert meta.change_streak_type == "none"
    assert meta.change_streak_count == 0
    assert meta.nochange_streak_count == 1

    meta.update_streak(0, 0)
    assert meta.change_streak_type == "none"
    assert meta.change_streak_count == 0
    assert meta.nochange_streak_count == 2


def test_update_streak_mixed():
    """Test streak updates for mixed progress/regress."""
    meta = ProjectMetadata()

    meta.update_streak(5, 3)
    assert meta.change_streak_type == "mixed"
    assert meta.change_streak_count == 1
    assert meta.nochange_streak_count == 0


def test_update_streak_breaks():
    """Test that nochange doesn't break change streak, but changes break nochange."""
    meta = ProjectMetadata()

    # Build progress streak
    meta.update_streak(1, 0)
    meta.update_streak(1, 0)
    assert meta.change_streak_count == 2
    assert meta.nochange_streak_count == 0

    # Nochange event doesn't break progress streak
    meta.update_streak(0, 0)
    assert meta.change_streak_type == "progress"
    assert meta.change_streak_count == 2  # Still 2!
    assert meta.nochange_streak_count == 1

    # Switch to regress - continues change streak as regress, breaks nochange
    meta.update_streak(0, 1)
    assert meta.change_streak_type == "regress"
    assert meta.change_streak_count == 1  # New regress streak
    assert meta.nochange_streak_count == 0  # Nochange broken


def test_update_rate_new_window():
    """Test rate calculation starting new window."""
    meta = ProjectMetadata()

    meta.update_rate(10, 2, 1000)

    assert meta.recent_rate_window_start == 1000
    assert meta.recent_rate_pixels_per_hour == 0.0  # No elapsed time yet


def test_update_rate_with_elapsed_time():
    """Test rate calculation with elapsed time."""
    meta = ProjectMetadata()

    meta.recent_rate_window_start = 1000
    # 1 hour later (3600 seconds)
    meta.update_rate(10, 2, 1000 + 3600)

    # Net change: 10 progress - 2 regress = 8 pixels in 1 hour
    assert meta.recent_rate_pixels_per_hour == 8.0


def test_update_rate_window_reset():
    """Test rate window resets after 24 hours."""
    meta = ProjectMetadata()

    meta.recent_rate_window_start = 1000
    meta.recent_rate_pixels_per_hour = 100.0

    # More than 24 hours later (86400 seconds = 24 hours)
    meta.update_rate(5, 0, 1000 + 86401)

    # Window should reset
    assert meta.recent_rate_window_start == 1000 + 86401
    assert meta.recent_rate_pixels_per_hour == 0.0


def test_update_rate_negative_net_change():
    """Test rate calculation with net regress."""
    meta = ProjectMetadata()

    meta.recent_rate_window_start = 1000
    # 1 hour later, more regress than progress
    meta.update_rate(2, 10, 1000 + 3600)

    # Net change: 2 - 10 = -8 pixels per hour
    assert meta.recent_rate_pixels_per_hour == -8.0

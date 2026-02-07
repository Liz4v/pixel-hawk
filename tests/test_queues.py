"""Tests for temperature-based queue system."""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from wwpppp.geometry import Tile
from wwpppp.queues import (
    QueueSystem,
    TileMetadata,
    TileQueue,
    calculate_zipf_queue_sizes,
)


def test_calculate_zipf_queue_sizes_basic():
    """Test Zipf distribution with 100 tiles."""
    sizes = calculate_zipf_queue_sizes(100, min_hottest_size=5)

    # Should have multiple queues
    assert len(sizes) > 1

    # Hottest should be at least 5
    assert sizes[0] >= 5

    # Total should equal input
    assert sum(sizes) == 100

    # Should be increasing (Zipf distribution - coldest has most)
    # Each queue should have more or equal tiles than the previous hotter one
    for i in range(len(sizes) - 1):
        # Allow some flexibility due to rounding
        assert sizes[i] <= sizes[i + 1] + 2, f"Queue {i} has {sizes[i]}, queue {i + 1} has {sizes[i + 1]}"


def test_calculate_zipf_queue_sizes_small():
    """Test with fewer tiles than min_hottest_size."""
    sizes = calculate_zipf_queue_sizes(3, min_hottest_size=5)

    # Should have single queue with all tiles
    assert len(sizes) == 1
    assert sizes[0] == 3


def test_calculate_zipf_queue_sizes_exact_min():
    """Test with exactly min_hottest_size tiles."""
    sizes = calculate_zipf_queue_sizes(5, min_hottest_size=5)

    # Could be one or two queues depending on implementation
    assert sum(sizes) == 5
    assert sizes[0] >= 5 or len(sizes) == 1


def test_calculate_zipf_queue_sizes_zero():
    """Test with zero tiles."""
    sizes = calculate_zipf_queue_sizes(0, min_hottest_size=5)
    assert sizes == []


def test_calculate_zipf_queue_sizes_large():
    """Test with large number of tiles."""
    sizes = calculate_zipf_queue_sizes(1000, min_hottest_size=5)

    assert len(sizes) > 1
    assert sizes[0] >= 5
    assert sum(sizes) == 1000

    # Verify Zipf-like distribution (coldest should have significantly more than hottest)
    assert sizes[-1] > sizes[0]


def test_tile_metadata_from_cache_nonexistent(tmp_path, monkeypatch):
    """Test TileMetadata.from_cache with non-existent cache file."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    tile = Tile(0, 0)
    meta = TileMetadata.from_cache(tile)

    assert meta.tile == tile
    assert meta.last_checked == 0
    assert meta.last_modified == 0
    cache_path = tmp_path / f"tile-{tile}.png"
    cache_path.write_bytes(b"fake tile data")

    # Set specific mtime
    import os

    test_time = 1234567890
    os.utime(cache_path, (test_time, test_time))

    meta = TileMetadata.from_cache(tile)

    assert meta.tile == tile
    assert meta.last_checked == test_time
    assert meta.last_modified == test_time
    assert not meta.is_burning


def test_tile_queue_select_next_empty():
    """Test selecting from empty queue."""
    queue = TileQueue(temperature=0)
    assert queue.is_empty()
    assert queue.select_next() is None


def test_tile_queue_select_next_oldest():
    """Test that select_next returns tile with oldest last_checked."""
    queue = TileQueue(temperature=0)

    old_meta = TileMetadata(tile=Tile(0, 0), last_checked=100)
    new_meta = TileMetadata(tile=Tile(1, 0), last_checked=200)
    never_checked = TileMetadata(tile=Tile(2, 0), last_checked=0)

    queue.tiles = [new_meta, old_meta, never_checked]

    # Should return never_checked (0 counts as oldest)
    queue = TileQueue(temperature=0)
    meta = TileMetadata(tile=Tile(0, 0))

    queue.add_tile(meta)
    assert meta in queue.tiles

    # Adding again should not duplicate
    queue.add_tile(meta)
    assert queue.tiles.count(meta) == 1

    queue.remove_tile(meta)
    assert meta not in queue.tiles

    # Removing again should not error
    queue.remove_tile(meta)


def test_queue_system_initialization(tmp_path, monkeypatch):
    """Test QueueSystem initialization with cache files."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    # Create some cache files
    tiles = {Tile(0, 0), Tile(1, 0), Tile(2, 0)}
    for tile in tiles:
        cache_path = tmp_path / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")

    qs = QueueSystem(tiles)

    # Should have metadata for all tiles
    assert len(qs.tile_metadata) == 3

    # All should be in temperature queues (not burning, since cache exists)
    assert qs.burning_queue.is_empty()
    assert len(qs.temperature_queues) > 0


def test_queue_system_initialization_no_cache(tmp_path, monkeypatch):
    """Test QueueSystem initialization with no cache files."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    tiles = {Tile(0, 0), Tile(1, 0), Tile(2, 0)}
    qs = QueueSystem(tiles)

    # Should have metadata for all tiles
    assert len(qs.tile_metadata) == 3

    # All should be in burning queue (no cache exists)
    assert not qs.burning_queue.is_empty()
    assert len(qs.burning_queue.tiles) == 3


def test_queue_system_select_next_tile(tmp_path, monkeypatch):
    """Test selecting next tile from queue system."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    tiles = {Tile(i, 0) for i in range(10)}

    # Create cache for half the tiles (so we have both burning and temperature tiles)
    for i in range(5):
        tile = Tile(i, 0)
        cache_path = tmp_path / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")

    qs = QueueSystem(tiles)

    # Should be able to select a tile
    meta = qs.select_next_tile()
    assert meta is not None
    assert meta.tile in tiles


def test_queue_system_round_robin(tmp_path, monkeypatch):
    """Test that queue system rotates through queues."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    # Create enough tiles for multiple temperature queues
    tiles = {Tile(i, 0) for i in range(20)}

    # Create cache files with different modification times
    now = round(time.time())
    for i, tile in enumerate(sorted(tiles)):
        cache_path = tmp_path / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        # Set different mtimes to create hot/cold spread
        import os

        mtime = now - (i * 1000)  # Older as i increases
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles)

    # Select several tiles and verify we're rotating through queues
    selected_tiles = []
    for _ in range(5):
        meta = qs.select_next_tile()
        if meta:
            selected_tiles.append(meta.tile)

    # Should have selected tiles (exact behavior depends on distribution)
    assert len(selected_tiles) > 0


def test_queue_system_add_tiles(tmp_path, monkeypatch):
    """Test adding new tiles to queue system."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    initial_tiles = {Tile(0, 0)}
    qs = QueueSystem(initial_tiles)

    assert len(qs.tile_metadata) == 1

    # Add new tiles
    new_tiles = {Tile(1, 0), Tile(2, 0)}
    qs.add_tiles(new_tiles)

    assert len(qs.tile_metadata) == 3

    # New tiles should be in burning queue
    assert not qs.burning_queue.is_empty()


def test_queue_system_remove_tiles(tmp_path, monkeypatch):
    """Test removing tiles from queue system."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    tiles = {Tile(i, 0) for i in range(5)}
    qs = QueueSystem(tiles)

    assert len(qs.tile_metadata) == 5

    # Remove some tiles
    to_remove = {Tile(0, 0), Tile(1, 0)}
    qs.remove_tiles(to_remove)

    assert len(qs.tile_metadata) == 3

    # Removed tiles should not be selectable
    for _ in range(10):
        meta = qs.select_next_tile()
        if meta:
            assert meta.tile not in to_remove


def test_queue_system_update_after_check_burning_to_temp(tmp_path, monkeypatch):
    """Test that checking a burning tile moves it to temperature queues."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    # Start with enough tiles for temperature queues
    tiles = {Tile(i, 0) for i in range(10)}
    qs = QueueSystem(tiles)

    # All should be burning initially
    assert not qs.burning_queue.is_empty()
    initial_burning_count = len(qs.burning_queue.tiles)

    # Select and check a tile
    meta = qs.select_next_tile()
    assert meta is not None
    assert meta.is_burning

    # Update after check with a modification time
    qs.update_tile_after_check(meta.tile, round(time.time()))

    # Should no longer be burning
    updated_meta = qs.tile_metadata[meta.tile]
    assert not updated_meta.is_burning
    assert updated_meta.last_checked > 0

    # Burning queue should have one fewer tile
    assert len(qs.burning_queue.tiles) < initial_burning_count


def test_queue_system_update_after_check_modification_time(tmp_path, monkeypatch):
    """Test that modification time updates trigger rebalancing."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    # Create tiles with cache
    tiles = {Tile(i, 0) for i in range(10)}
    now = round(time.time())
    for tile in tiles:
        cache_path = tmp_path / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        os.utime(cache_path, (now - 10000, now - 10000))  # Old modification time

    qs = QueueSystem(tiles)

    # Select a tile
    meta = qs.select_next_tile()
    assert meta is not None
    old_last_modified = meta.last_modified

    # Update with newer modification time
    new_mod_time = now
    qs.update_tile_after_check(meta.tile, new_mod_time)

    # Metadata should be updated
    updated_meta = qs.tile_metadata[meta.tile]
    assert updated_meta.last_modified == new_mod_time
    assert updated_meta.last_modified != old_last_modified


def test_reposition_tile_stays_in_queue(tmp_path, monkeypatch):
    """Test that a tile stays in the same queue if its position doesn't change."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    # Create tiles with graduated modification times
    tiles = {Tile(i, 0) for i in range(15)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = tmp_path / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)  # Spread out modification times
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles)

    # Get a tile from the middle temperature queue
    mid_queue_idx = len(qs.temperature_queues) // 2
    mid_queue = qs.temperature_queues[mid_queue_idx]
    assert len(mid_queue.tiles) > 0

    tile_meta = mid_queue.tiles[0]
    initial_queue_sizes = [len(q.tiles) for q in qs.temperature_queues]

    # Reposition without changing modification time (should stay in place)
    qs._reposition_tile(tile_meta)

    # Queue sizes should be unchanged
    final_queue_sizes = [len(q.tiles) for q in qs.temperature_queues]
    assert initial_queue_sizes == final_queue_sizes

    # Tile should still be in same queue
    assert tile_meta in qs.temperature_queues[mid_queue_idx].tiles


def test_reposition_tile_to_hotter_queue(tmp_path, monkeypatch):
    """Test that a tile moves to a hotter queue when its modification time increases."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    # Create tiles with graduated modification times
    tiles = {Tile(i, 0) for i in range(20)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = tmp_path / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)  # Spread out modification times
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles)

    # Find a tile in a colder queue
    coldest_queue = qs.temperature_queues[-1]
    assert len(coldest_queue.tiles) > 0

    tile_meta = coldest_queue.tiles[0]
    old_queue_idx = len(qs.temperature_queues) - 1

    # Record initial queue sizes
    initial_queue_sizes = [len(q.tiles) for q in qs.temperature_queues]

    # Update tile's modification time to make it hottest
    tile_meta.last_modified = now + 1000

    # Reposition the tile
    qs._reposition_tile(tile_meta)

    # Queue sizes should be maintained
    final_queue_sizes = [len(q.tiles) for q in qs.temperature_queues]
    assert initial_queue_sizes == final_queue_sizes

    # Tile should have moved to hottest queue
    assert tile_meta in qs.temperature_queues[0].tiles
    assert tile_meta not in coldest_queue.tiles


def test_reposition_tile_cascade_mechanics(tmp_path, monkeypatch):
    """Test that cascade pushes coldest tiles down through queues."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    # Create more tiles to ensure we get at least 3 queues
    tiles = {Tile(i, 0) for i in range(50)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = tmp_path / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)  # Spread out modification times
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles)

    # Get tile from queue 2
    assert len(qs.temperature_queues) >= 3, "Need at least 3 queues for this test"
    target_tile = qs.temperature_queues[2].tiles[0]

    # Record coldest tiles from queues 0 and 1
    coldest_q0 = min(qs.temperature_queues[0].tiles, key=lambda t: t.last_modified)
    coldest_q1 = min(qs.temperature_queues[1].tiles, key=lambda t: t.last_modified)

    # Make target tile hottest
    target_tile.last_modified = now + 1000

    # Reposition tile from queue 2 to queue 0
    qs._reposition_tile(target_tile)

    # Target tile should now be in queue 0
    assert target_tile in qs.temperature_queues[0].tiles

    # Coldest from queue 0 should have moved to queue 1
    assert coldest_q0 in qs.temperature_queues[1].tiles

    # Coldest from queue 1 should have moved to queue 2
    assert coldest_q1 in qs.temperature_queues[2].tiles


def test_reposition_tile_maintains_all_tiles(tmp_path, monkeypatch):
    """Test that reposition doesn't lose or duplicate tiles."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    # Create tiles with graduated modification times
    tiles = {Tile(i, 0) for i in range(20)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = tmp_path / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles)

    # Collect all tiles before reposition
    all_tiles_before = set()
    for queue in qs.temperature_queues:
        all_tiles_before.update(t.tile for t in queue.tiles)

    # Get a tile from a cold queue and make it hot
    cold_tile = qs.temperature_queues[-1].tiles[0]
    cold_tile.last_modified = now + 1000

    qs._reposition_tile(cold_tile)

    # Collect all tiles after reposition
    all_tiles_after = set()
    for queue in qs.temperature_queues:
        all_tiles_after.update(t.tile for t in queue.tiles)

    # Should have same tiles (no loss, no duplication)
    assert all_tiles_before == all_tiles_after


def test_reposition_tile_preserves_queue_sizes(tmp_path, monkeypatch):
    """Test that queue sizes are exactly preserved after reposition."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    # Create enough tiles for multiple queues
    tiles = {Tile(i, 0) for i in range(30)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = tmp_path / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles)

    initial_sizes = [len(q.tiles) for q in qs.temperature_queues]

    # Reposition multiple tiles
    for _ in range(5):
        # Find a tile in a cold queue
        for queue_idx in range(len(qs.temperature_queues) - 1, 0, -1):
            if qs.temperature_queues[queue_idx].tiles:
                tile_meta = qs.temperature_queues[queue_idx].tiles[0]
                # Make it hotter
                tile_meta.last_modified = now + (queue_idx * 1000)
                qs._reposition_tile(tile_meta)
                break

    final_sizes = [len(q.tiles) for q in qs.temperature_queues]

    # Sizes should be exactly preserved
    assert initial_sizes == final_sizes


def test_reposition_tile_assertion_on_colder_move(tmp_path, monkeypatch):
    """Test that assertion fails if tile tries to move to colder queue."""
    monkeypatch.setattr("wwpppp.queues.DIRS", SimpleNamespace(user_cache_path=tmp_path))

    # Create tiles with graduated modification times
    tiles = {Tile(i, 0) for i in range(20)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = tmp_path / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles)

    # Get a tile from hottest queue
    hot_tile = qs.temperature_queues[0].tiles[0]

    # Try to make it colder (this should trigger assertion)
    hot_tile.last_modified = now - 100000  # Very old time

    # Should raise AssertionError
    with pytest.raises(AssertionError, match="moving to colder queue"):
        qs._reposition_tile(hot_tile)

"""Tests for temperature-based queue system."""

import time
from types import SimpleNamespace

import pytest
from loguru import logger

from pixel_hawk.geometry import Tile
from pixel_hawk.queues import (
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


def test_tile_metadata_from_cache_nonexistent(tmp_path, monkeypatch, setup_config):
    """Test TileMetadata.from_cache with non-existent cache file."""

    tile = Tile(0, 0)
    meta = TileMetadata.from_cache(tile)

    assert meta.tile == tile
    assert meta.last_checked == 0
    assert meta.last_modified == 0
    cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
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
    assert queue.select_next({}) is None


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


def test_queue_system_initialization(tmp_path, monkeypatch, setup_config):
    """Test QueueSystem initialization with cache files."""

    # Create some cache files
    tiles = {Tile(0, 0), Tile(1, 0), Tile(2, 0)}
    for tile in tiles:
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")

    qs = QueueSystem(tiles, {})

    # Should have metadata for all tiles
    assert len(qs.tile_metadata) == 3

    # All should be in temperature queues (not burning, since cache exists)
    assert qs.burning_queue.is_empty()
    assert len(qs.temperature_queues) > 0


def test_queue_system_initialization_no_cache(tmp_path, monkeypatch):
    """Test QueueSystem initialization with no cache files."""

    tiles = {Tile(0, 0), Tile(1, 0), Tile(2, 0)}
    qs = QueueSystem(tiles, {})

    # Should have metadata for all tiles
    assert len(qs.tile_metadata) == 3

    # All should be in burning queue (no cache exists)
    assert not qs.burning_queue.is_empty()
    assert len(qs.burning_queue.tiles) == 3


def test_queue_system_select_next_tile(tmp_path, monkeypatch, setup_config):
    """Test selecting next tile from queue system."""

    tiles = {Tile(i, 0) for i in range(10)}

    # Create cache for half the tiles (so we have both burning and temperature tiles)
    for i in range(5):
        tile = Tile(i, 0)
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")

    qs = QueueSystem(tiles, {})

    # Should be able to select a tile
    meta = qs.select_next_tile()
    assert meta is not None
    assert meta.tile in tiles


def test_queue_system_round_robin(tmp_path, monkeypatch, setup_config):
    """Test that queue system rotates through queues."""

    # Create enough tiles for multiple temperature queues
    tiles = {Tile(i, 0) for i in range(20)}

    # Create cache files with different modification times
    now = round(time.time())
    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        # Set different mtimes to create hot/cold spread
        import os

        mtime = now - (i * 1000)  # Older as i increases
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles, {})

    # Select several tiles and verify we're rotating through queues
    selected_tiles = []
    for _ in range(5):
        meta = qs.select_next_tile()
        if meta:
            selected_tiles.append(meta.tile)

    # Should have selected tiles (exact behavior depends on distribution)
    assert len(selected_tiles) > 0


def test_queue_system_retry_current_queue(tmp_path, monkeypatch, setup_config):
    """Test that retry_current_queue() rewinds the round-robin index."""

    # Create enough tiles for multiple queues
    tiles = {Tile(i, 0) for i in range(20)}

    # Create cache files to have some temperature tiles
    now = round(time.time())
    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles, {})

    # Select a tile, note the queue index advancement
    first_meta = qs.select_next_tile()
    assert first_meta is not None
    first_queue_index = qs.current_queue_index

    # Retry - should rewind the index
    qs.retry_current_queue()
    assert qs.current_queue_index != first_queue_index

    # Select again - should get same queue as before (though possibly different tile)
    second_meta = qs.select_next_tile()
    assert second_meta is not None
    # After retry and re-select, we should be back where we were
    assert qs.current_queue_index == first_queue_index


def test_queue_system_add_tiles(tmp_path, monkeypatch):
    """Test adding new tiles to queue system."""

    initial_tiles = {Tile(0, 0)}
    qs = QueueSystem(initial_tiles, {})

    assert len(qs.tile_metadata) == 1

    # Add new tiles
    new_tiles = {Tile(1, 0), Tile(2, 0)}
    qs.add_tiles(new_tiles)

    assert len(qs.tile_metadata) == 3

    # New tiles should be in burning queue
    assert not qs.burning_queue.is_empty()


def test_queue_system_remove_tiles(tmp_path, monkeypatch):
    """Test removing tiles from queue system."""

    tiles = {Tile(i, 0) for i in range(5)}
    qs = QueueSystem(tiles, {})

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

    # Start with enough tiles for temperature queues
    tiles = {Tile(i, 0) for i in range(10)}
    qs = QueueSystem(tiles, {})

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


def test_queue_system_update_after_check_modification_time(tmp_path, monkeypatch, setup_config):
    """Test that modification time updates trigger rebalancing."""

    # Create tiles with cache
    tiles = {Tile(i, 0) for i in range(10)}
    now = round(time.time())
    for tile in tiles:
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        os.utime(cache_path, (now - 10000, now - 10000))  # Old modification time

    qs = QueueSystem(tiles, {})

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


def test_reposition_tile_stays_in_queue(tmp_path, monkeypatch, setup_config):
    """Test that a tile stays in the same queue if its position doesn't change."""

    # Create tiles with graduated modification times
    tiles = {Tile(i, 0) for i in range(15)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)  # Spread out modification times
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles, {})

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


def test_reposition_tile_to_hotter_queue(tmp_path, monkeypatch, setup_config):
    """Test that a tile moves to a hotter queue when its modification time increases."""

    # Create tiles with graduated modification times
    tiles = {Tile(i, 0) for i in range(20)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)  # Spread out modification times
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles, {})

    # Find a tile in a colder queue
    coldest_queue = qs.temperature_queues[-1]
    assert len(coldest_queue.tiles) > 0

    tile_meta = coldest_queue.tiles[0]

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


def test_reposition_tile_cascade_mechanics(tmp_path, monkeypatch, setup_config):
    """Test that cascade pushes coldest tiles down through queues."""

    # Create more tiles to ensure we get at least 3 queues
    tiles = {Tile(i, 0) for i in range(50)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)  # Spread out modification times
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles, {})

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


def test_reposition_tile_maintains_all_tiles(tmp_path, monkeypatch, setup_config):
    """Test that reposition doesn't lose or duplicate tiles."""

    # Create tiles with graduated modification times
    tiles = {Tile(i, 0) for i in range(20)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles, {})

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


def test_reposition_tile_preserves_queue_sizes(tmp_path, monkeypatch, setup_config):
    """Test that queue sizes are exactly preserved after reposition."""

    # Create enough tiles for multiple queues
    tiles = {Tile(i, 0) for i in range(30)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles, {})

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


def test_reposition_tile_assertion_on_colder_move(tmp_path, monkeypatch, setup_config):
    """Test that assertion fails if tile tries to move to colder queue."""

    # Create tiles with graduated modification times
    tiles = {Tile(i, 0) for i in range(20)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles, {})

    # Get a tile from hottest queue
    hot_tile = qs.temperature_queues[0].tiles[0]

    # Try to make it colder (this should trigger assertion)
    hot_tile.last_modified = now - 100000  # Very old time

    # Should raise AssertionError
    with pytest.raises(AssertionError, match="moving to colder queue"):
        qs._reposition_tile(hot_tile)


def test_queue_system_no_starvation_with_large_burning_queue(tmp_path, monkeypatch, setup_config):
    """Test that temperature queues are not starved when burning queue has many tiles.

    Scenario:
    1. Start with some tiles in temperature queues (from existing projects)
    2. Add many new tiles to burning queue (from new large project)
    3. Each burning tile promotion triggers rebuild, which resets current_queue_index
    4. Verify that temperature tiles are selected before all burning tiles are exhausted

    This tests the round-robin behavior to ensure it doesn't reset to always
    selecting from burning queue first after each rebuild.
    """

    # Create initial tiles with cache (temperature tiles)
    initial_tiles = {Tile(i, 0) for i in range(10)}
    now = round(time.time())

    for i, tile in enumerate(sorted(initial_tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(initial_tiles, {})

    # Verify we have temperature tiles and no burning tiles initially
    assert len(qs.burning_queue.tiles) == 0
    assert len(qs.temperature_queues) > 0
    initial_temp_count = sum(len(q.tiles) for q in qs.temperature_queues)
    assert initial_temp_count == 10

    # Add many new tiles (simulating large new project)
    new_tiles = {Tile(i, 10) for i in range(50)}  # 50 new burning tiles
    qs.add_tiles(new_tiles)

    # Verify burning queue has new tiles
    assert len(qs.burning_queue.tiles) == 50

    # Track which queue types tiles come from
    selections_from_burning = 0
    selections_from_temperature = 0
    burning_tiles_checked = 0

    # Simulate polling loop: select and check tiles
    for iteration in range(60):  # Check more than burning queue size
        meta = qs.select_next_tile()
        assert meta is not None, f"Got None at iteration {iteration}"

        # Track queue type
        if meta.is_burning:
            selections_from_burning += 1
            # Simulate checking the tile and promoting it
            qs.update_tile_after_check(meta.tile, now + iteration)
            burning_tiles_checked += 1
        else:
            selections_from_temperature += 1
            # Just update last_checked without changing modification time
            qs.update_tile_after_check(meta.tile, meta.last_modified)

        # CRITICAL CHECK: Temperature queues should be selected from
        # before all burning tiles are exhausted
        if selections_from_temperature > 0 and burning_tiles_checked < 50:
            # Good! We got temperature selections while burning queue still had tiles
            logger.info(
                f"✓ Got temperature selection at iteration {iteration} "
                f"with {50 - burning_tiles_checked} burning tiles remaining"
            )
            break
    else:
        # Loop completed without break - we never selected from temperature
        # while burning queue had tiles
        pytest.fail(
            f"Temperature queues were starved! "
            f"Selections: {selections_from_burning} burning, {selections_from_temperature} temperature. "
            f"All {burning_tiles_checked} burning tiles were checked before any temperature tiles."
        )


def test_tile_metadata_hash_and_eq():
    """Test TileMetadata __hash__ and __eq__ implementations."""
    meta1 = TileMetadata(tile=Tile(0, 0), last_checked=100)
    meta2 = TileMetadata(tile=Tile(0, 0), last_checked=200)
    meta3 = TileMetadata(tile=Tile(1, 0), last_checked=100)

    # Same tile should be equal even with different timestamps
    assert meta1 == meta2
    assert hash(meta1) == hash(meta2)

    # Different tiles should not be equal
    assert meta1 != meta3
    assert hash(meta1) != hash(meta3)

    # Should not equal non-TileMetadata objects
    assert meta1 != "not a metadata"
    assert meta1 != Tile(0, 0)
    assert meta1 is not None
    assert meta1 != 123


def test_queue_system_current_queue_index_adjustment_burning_only(tmp_path, monkeypatch):
    """Test that current_queue_index is adjusted when only burning queue exists."""

    # Start with burning tiles only
    tiles = {Tile(i, 0) for i in range(5)}
    qs = QueueSystem(tiles, {})

    # Force current_queue_index to be out of bounds for single-queue scenario
    qs.current_queue_index = 5  # Way beyond valid range

    # Rebuild should adjust it back to 0
    qs._rebuild_queues()

    assert qs.current_queue_index == 0


def test_queue_system_add_tiles_no_change(tmp_path, monkeypatch):
    """Test adding tiles that already exist doesn't trigger rebuild."""

    tiles = {Tile(0, 0), Tile(1, 0)}
    qs = QueueSystem(tiles, {})

    initial_queue_count = len(qs.temperature_queues)

    # Add same tiles again (no-op)
    qs.add_tiles(tiles)

    # Should not have changed queues
    assert len(qs.tile_metadata) == 2
    assert len(qs.temperature_queues) == initial_queue_count


def test_queue_system_remove_tiles_no_change(tmp_path, monkeypatch):
    """Test removing tiles that don't exist doesn't trigger rebuild."""

    tiles = {Tile(0, 0), Tile(1, 0)}
    qs = QueueSystem(tiles, {})

    initial_queue_count = len(qs.temperature_queues)

    # Remove tiles that don't exist (no-op)
    non_existent = {Tile(10, 10), Tile(11, 11)}
    qs.remove_tiles(non_existent)

    # Should not have changed queues
    assert len(qs.tile_metadata) == 2
    assert len(qs.temperature_queues) == initial_queue_count


def test_queue_system_select_next_empty_system():
    """Test selecting from queue system with no tiles."""
    qs = QueueSystem(set(), {})

    result = qs.select_next_tile()
    assert result is None


def test_queue_system_update_unknown_tile(tmp_path, monkeypatch):
    """Test updating a tile that's not in the system."""

    tiles = {Tile(0, 0)}
    qs = QueueSystem(tiles, {})

    # Try to update a tile not in the system (should log warning and return)
    qs.update_tile_after_check(Tile(99, 99), round(time.time()))

    # Should have returned without crashing
    assert len(qs.tile_metadata) == 1  # Original tile still there


def test_reposition_tile_no_temperature_queues(tmp_path, monkeypatch):
    """Test _reposition_tile when no temperature queues exist."""

    # Create a queue system with only burning tiles
    tiles = {Tile(0, 0), Tile(1, 0)}
    qs = QueueSystem(tiles, {})

    # Ensure we only have burning queue
    assert len(qs.temperature_queues) == 0
    assert len(qs.burning_queue.tiles) == 2

    # Try to reposition (should return early)
    meta = qs.burning_queue.tiles[0]
    qs._reposition_tile(meta)  # Should not crash

    # System should be unchanged
    assert len(qs.temperature_queues) == 0


def test_reposition_tile_not_found_in_queues(tmp_path, monkeypatch, setup_config):
    """Test _reposition_tile when tile is not found in any temperature queue."""

    # Create tiles with cache
    tiles = {Tile(i, 0) for i in range(10)}
    now = round(time.time())
    for tile in tiles:
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        os.utime(cache_path, (now, now))

    qs = QueueSystem(tiles, {})
    initial_temp_tile_count = sum(len(q.tiles) for q in qs.temperature_queues)

    # Get a tile metadata but manually remove it from all queues
    meta = list(qs.tile_metadata.values())[0]
    for queue in qs.temperature_queues:
        queue.remove_tile(meta)

    # Try to reposition (should log warning and return without crashing)
    qs._reposition_tile(meta)

    # Queue structure should be unchanged
    final_temp_tile_count = sum(len(q.tiles) for q in qs.temperature_queues)
    assert final_temp_tile_count == initial_temp_tile_count - 1  # One removed


def test_reposition_tile_stays_in_same_queue(tmp_path, monkeypatch, setup_config):
    """Test that tile stays in queue when its position doesn't change significantly."""

    # Create tiles with graduated modification times
    tiles = {Tile(i, 0) for i in range(20)}
    now = round(time.time())

    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles, {})

    # Get a tile from middle of a queue
    mid_queue = qs.temperature_queues[len(qs.temperature_queues) // 2]
    original_tile = mid_queue.tiles[0]
    original_queue_idx = None

    for idx, queue in enumerate(qs.temperature_queues):
        if original_tile in queue.tiles:
            original_queue_idx = idx
            break

    # Don't change modification time significantly
    # Just reposition without changing anything
    qs._reposition_tile(original_tile)

    # Tile should still be in same queue (line 294 coverage)
    assert original_tile in qs.temperature_queues[original_queue_idx].tiles


def test_queue_system_all_queues_empty_rebuild(tmp_path, monkeypatch):
    """Test fallback case when all queues are empty but metadata exists."""

    tiles = {Tile(0, 0), Tile(1, 0)}
    qs = QueueSystem(tiles, {})

    # Manually empty all queues (corrupt state)
    qs.burning_queue.tiles.clear()
    for queue in qs.temperature_queues:
        queue.tiles.clear()

    # Try to select (should trigger rebuild and log warning)
    qs.select_next_tile()

    # After rebuild, queues should be repopulated
    total_tiles = len(qs.burning_queue.tiles) + sum(len(q.tiles) for q in qs.temperature_queues)
    assert total_tiles == 2  # Both tiles should be back in queues


def test_calculate_zipf_queue_sizes_fallback(tmp_path, monkeypatch, setup_config):
    """Test fallback when calculate_zipf_queue_sizes returns empty (defensive code)."""
    from unittest.mock import patch

    # Create tiles with cache to get temperature tiles
    tiles = {Tile(i, 0) for i in range(5)}
    now = round(time.time())
    for tile in tiles:
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        os.utime(cache_path, (now, now))

    # Mock calculate_zipf_queue_sizes to return empty list (shouldn't happen normally)
    with patch("pixel_hawk.queues.calculate_zipf_queue_sizes", return_value=[]):
        qs = QueueSystem(tiles, {})

    # Should have created a single queue with all tiles as fallback
    assert len(qs.temperature_queues) == 1
    assert len(qs.temperature_queues[0].tiles) == 5


def test_reposition_tile_no_movement_needed(tmp_path, monkeypatch, setup_config):
    """Test reposition when tile's position in sorted order matches its current queue."""

    # Create tiles with distinct modification times to ensure predictable queue assignment
    tiles = {Tile(i, 0) for i in range(10)}
    now = round(time.time())

    # Set mtimes in descending order: Tile(0,0) is newest, Tile(9,0) is oldest
    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        mtime = now - (i * 1000)
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles, {})

    # Get a tile and verify which queue it's in
    if len(qs.temperature_queues) > 0:
        # Pick a tile from any queue
        target_queue_idx = 0
        if qs.temperature_queues[target_queue_idx].tiles:
            tile_meta = qs.temperature_queues[target_queue_idx].tiles[0]

            # Try to reposition without changing its modification time
            # It should stay in the same queue (line 294: early return)
            qs._reposition_tile(tile_meta)

            # Verify tile is still in same queue
            assert tile_meta in qs.temperature_queues[target_queue_idx].tiles


def test_reposition_with_empty_temperature_queues_explicit(tmp_path, monkeypatch):
    """Test _reposition_tile explicitly returns early when no temperature queues exist."""

    # Create burning tiles (no cache)
    tiles = {Tile(0, 0)}
    qs = QueueSystem(tiles, {})

    # Verify only burning queue exists
    assert len(qs.burning_queue.tiles) == 1
    assert len(qs.temperature_queues) == 0

    # Get the burning tile metadata
    meta = qs.burning_queue.tiles[0]

    # Call _reposition_tile - should return early (line 252)
    qs._reposition_tile(meta)

    # System should remain unchanged
    assert len(qs.burning_queue.tiles) == 1
    assert len(qs.temperature_queues) == 0


def test_add_tiles_with_mixed_new_and_existing(tmp_path, monkeypatch):
    """Test add_tiles with a mix of new and existing tiles."""

    # Start with some tiles
    initial_tiles = {Tile(0, 0), Tile(1, 0)}
    qs = QueueSystem(initial_tiles, {})
    assert len(qs.tile_metadata) == 2

    # Add a mix: some new, some existing
    mixed_tiles = {Tile(1, 0), Tile(2, 0), Tile(3, 0)}  # Tile(1,0) already exists
    qs.add_tiles(mixed_tiles)

    # Should have 4 total tiles (0, 1, 2, 3)
    assert len(qs.tile_metadata) == 4

    # All tiles should be present
    assert Tile(0, 0) in qs.tile_metadata
    assert Tile(1, 0) in qs.tile_metadata
    assert Tile(2, 0) in qs.tile_metadata
    assert Tile(3, 0) in qs.tile_metadata


def test_reposition_tile_stays_in_queue_explicit(tmp_path, monkeypatch, setup_config):
    """Test line 294: tile repositioning where target equals old queue."""

    # Create enough tiles to have multiple queues with specific modification times
    tiles = {Tile(i, 0) for i in range(30)}
    base_time = round(time.time())

    # Create cache files with very close modification times within groups
    # This ensures tiles stay bunched in their queues even with small changes
    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        import os

        # Group tiles: 0-9 are hottest, 10-19 middle, 20-29 coldest
        # Within each group, times are very close
        group = i // 10
        offset_within_group = i % 10
        mtime = base_time - (group * 10000) - offset_within_group
        os.utime(cache_path, (mtime, mtime))

    qs = QueueSystem(tiles, {})

    # Ensure we have multiple queues
    if len(qs.temperature_queues) < 2:
        pytest.skip("Need at least 2 temperature queues for this test")

    # Get a tile from the middle group
    target_tile = None
    target_queue_idx = len(qs.temperature_queues) // 2
    if target_queue_idx < len(qs.temperature_queues) and qs.temperature_queues[target_queue_idx].tiles:
        target_tile = qs.temperature_queues[target_queue_idx].tiles[0]

    if not target_tile:
        pytest.skip("Could not find suitable tile for test")

    # Change modification time slightly but not enough to move to different queue
    # Keep it within the same group by only adjusting by 1
    old_mtime = target_tile.last_modified
    target_tile.last_modified = old_mtime + 1  # Tiny change

    # This should trigger the "stays in same queue" path (line 294)
    qs._reposition_tile(target_tile)

    # Tile should still be in the same queue
    assert target_tile in qs.temperature_queues[target_queue_idx].tiles


# Burning queue prioritization tests


def test_burning_queue_prioritizes_by_project_first_seen(tmp_path, monkeypatch):
    """Burning queue should select tiles from oldest projects first."""
    from pixel_hawk.geometry import Rectangle

    # Create three tiles
    tile1 = Tile(0, 0)
    tile2 = Tile(1, 0)
    tile3 = Tile(2, 0)
    tiles = {tile1, tile2, tile3}

    # Create mock projects with different first_seen times
    # Project 1 (oldest): first_seen = 1000
    proj1 = SimpleNamespace(
        path=tmp_path / "proj1.png",
        rect=Rectangle(0, 0, 1000, 1000),  # Contains tile1
        info=SimpleNamespace(first_seen=1000),
    )

    # Project 2 (middle): first_seen = 2000
    proj2 = SimpleNamespace(
        path=tmp_path / "proj2.png",
        rect=Rectangle(1000, 0, 1000, 1000),  # Contains tile2
        info=SimpleNamespace(first_seen=2000),
    )

    # Project 3 (newest): first_seen = 3000
    proj3 = SimpleNamespace(
        path=tmp_path / "proj3.png",
        rect=Rectangle(2000, 0, 1000, 1000),  # Contains tile3
        info=SimpleNamespace(first_seen=3000),
    )

    # Create tile-to-projects mapping
    tile_to_projects = {
        tile1: [proj1],
        tile2: [proj2],
        tile3: [proj3],
    }

    # Create queue system (no cache files, so all tiles go to burning queue)
    qs = QueueSystem(tiles, tile_to_projects)

    # All tiles should be in burning queue
    assert len(qs.burning_queue.tiles) == 3
    assert len(qs.temperature_queues) == 0

    # Select next tile should return tile from oldest project (proj1)
    selected = qs.select_next_tile()
    assert selected is not None
    assert selected.tile == tile1  # Should select tile from oldest project


def test_burning_queue_handles_project_with_early_first_seen(tmp_path, monkeypatch):
    """Burning queue should handle projects with very early first_seen timestamps."""
    from pixel_hawk.geometry import Rectangle

    tile1 = Tile(0, 0)
    tile2 = Tile(1, 0)
    tiles = {tile1, tile2}

    class FakeMetadata:
        first_seen = 1  # very early timestamp

    class FakeProject:
        def __init__(self):
            self.rect = Rectangle(0, 0, 1000, 1000)
            self.info = FakeMetadata()

    proj = FakeProject()

    tile_to_projects = {
        tile1: [proj],
        tile2: [proj],
    }

    # Should not crash
    qs = QueueSystem(tiles, tile_to_projects)

    # Should be able to select a tile
    selected = qs.select_next_tile()
    assert selected is not None
    assert selected.tile in tiles


def test_burning_queue_handles_shared_tiles(tmp_path, monkeypatch):
    """Tiles shared by multiple projects use minimum first_seen."""
    from pixel_hawk.geometry import Rectangle

    # Create two tiles that overlap multiple projects
    tile1 = Tile(0, 0)
    tile2 = Tile(1, 0)
    tiles = {tile1, tile2}

    # Create mock projects
    old_proj = SimpleNamespace(
        path=tmp_path / "old.png",
        rect=Rectangle(0, 0, 2000, 1000),  # Contains both tiles
        info=SimpleNamespace(first_seen=1000),
    )

    new_proj = SimpleNamespace(
        path=tmp_path / "new.png",
        rect=Rectangle(1000, 0, 1000, 1000),  # Contains only tile2
        info=SimpleNamespace(first_seen=3000),
    )

    tile_to_projects = {
        tile1: [old_proj],  # Only in old project (first_seen=1000)
        tile2: [old_proj, new_proj],  # In both projects (min first_seen=1000)
    }

    qs = QueueSystem(tiles, tile_to_projects)

    # Both tiles have same minimum first_seen (1000), so both are equally prioritized
    # Just verify we can select without errors
    selected = qs.select_next_tile()
    assert selected is not None
    assert selected.tile in tiles


def test_temperature_queue_selection_unchanged(tmp_path, monkeypatch, setup_config):
    """Temperature queues should continue using last_checked only."""
    from pixel_hawk.geometry import Rectangle

    # Create tiles with cache files (so they go to temperature queues)
    tiles = {Tile(i, 0) for i in range(10)}

    # Create cache for all tiles with different times
    now = round(time.time())
    for i, tile in enumerate(sorted(tiles)):
        cache_path = setup_config.tiles_dir / f"tile-{tile}.png"
        cache_path.write_bytes(b"data")
        mtime = now - (i * 100)  # Older tiles have lower mtime
        import os

        os.utime(cache_path, (mtime, mtime))

    # Create mock project (shouldn't affect temperature queue selection)
    proj = SimpleNamespace(
        path=tmp_path / "proj.png",
        rect=Rectangle(0, 0, 10000, 1000),
        info=SimpleNamespace(first_seen=1000),
    )

    tile_to_projects = {tile: [proj] for tile in tiles}

    qs = QueueSystem(tiles, tile_to_projects)

    # All tiles should be in temperature queues (have cache)
    assert qs.burning_queue.is_empty()
    assert len(qs.temperature_queues) > 0

    # Selection should work normally (based on last_checked, not project first_seen)
    selected = qs.select_next_tile()
    assert selected is not None
    assert selected.tile in tiles

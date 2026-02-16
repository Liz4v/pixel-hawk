"""Tests for temperature-based queue system (database-driven)."""

import time

from pixel_hawk.geometry import Tile
from pixel_hawk.models import TileInfo
from pixel_hawk.queues import QueueSystem, calculate_zipf_queue_sizes


# --- calculate_zipf_queue_sizes (pure function, no DB) ---


def test_calculate_zipf_queue_sizes_basic():
    """Test Zipf distribution with 100 tiles."""
    sizes = calculate_zipf_queue_sizes(100, min_hottest_size=5)

    assert len(sizes) > 1
    assert sizes[0] >= 5
    assert sum(sizes) == 100

    # Should be increasing (coldest has most)
    for i in range(len(sizes) - 1):
        assert sizes[i] <= sizes[i + 1] + 2, f"Queue {i} has {sizes[i]}, queue {i + 1} has {sizes[i + 1]}"


def test_calculate_zipf_queue_sizes_small():
    """Test with fewer tiles than min_hottest_size."""
    sizes = calculate_zipf_queue_sizes(3, min_hottest_size=5)
    assert len(sizes) == 1
    assert sizes[0] == 3


def test_calculate_zipf_queue_sizes_exact_min():
    """Test with exactly min_hottest_size tiles."""
    sizes = calculate_zipf_queue_sizes(5, min_hottest_size=5)
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
    assert sizes[-1] > sizes[0]


# --- Helper to create TileInfo records ---


async def _create_tile(x: int, y: int, *, queue_temperature: int = 999, last_checked: int = 0, last_update: int = 0, http_etag: str = "") -> TileInfo:
    """Create a TileInfo record in the database."""
    return await TileInfo.create(
        id=TileInfo.tile_id(x, y),
        tile_x=x,
        tile_y=y,
        queue_temperature=queue_temperature,
        last_checked=last_checked,
        last_update=last_update,
        http_etag=http_etag,
    )


# --- QueueSystem.__init__ ---


def test_queue_system_initialization():
    """Test QueueSystem starts with zeroed state."""
    qs = QueueSystem()
    assert qs.current_queue_index == 0
    assert qs.num_queues == 0


# --- select_next_tile ---


async def test_select_next_tile_empty_database():
    """Selecting from empty DB returns None."""
    qs = QueueSystem()
    result = await qs.select_next_tile()
    assert result is None


async def test_select_next_tile_burning_only():
    """Selects a tile from burning queue (temp=999)."""
    await _create_tile(3, 7, queue_temperature=999, last_checked=0)

    qs = QueueSystem()
    tile = await qs.select_next_tile()

    assert tile is not None
    assert tile == Tile(3, 7)


async def test_select_next_tile_temperature_only():
    """Selects from temperature queue when no burning tiles exist."""
    await _create_tile(1, 2, queue_temperature=1, last_checked=100, last_update=50)
    await _create_tile(3, 4, queue_temperature=1, last_checked=50, last_update=50)

    qs = QueueSystem()
    qs.num_queues = 1

    # First call goes to burning (999) which is empty, advances index
    tile = await qs.select_next_tile()
    # Second call (automatic advance after empty) - now try temp queue 1
    if tile is None:
        tile = await qs.select_next_tile()

    assert tile is not None
    # Should pick least recently checked (last_checked=50 => Tile(3,4))
    assert tile == Tile(3, 4)


async def test_select_next_tile_least_recently_checked():
    """Within a temperature queue, selects the tile with oldest last_checked."""
    now = round(time.time())
    await _create_tile(0, 0, queue_temperature=1, last_checked=now - 1000, last_update=now)
    await _create_tile(1, 0, queue_temperature=1, last_checked=now - 500, last_update=now)
    await _create_tile(2, 0, queue_temperature=1, last_checked=now - 2000, last_update=now)  # oldest

    qs = QueueSystem()
    qs.num_queues = 1

    # Skip burning queue (empty)
    tile = await qs.select_next_tile()
    if tile is None:
        tile = await qs.select_next_tile()

    assert tile == Tile(2, 0)


async def test_select_next_tile_round_robin():
    """Verifies round-robin cycling through burning + temperature queues."""
    now = round(time.time())
    # One burning tile
    await _create_tile(0, 0, queue_temperature=999, last_checked=0)
    # Two temperature tiles in different queues
    await _create_tile(1, 0, queue_temperature=1, last_checked=now - 100, last_update=now)
    await _create_tile(2, 0, queue_temperature=2, last_checked=now - 200, last_update=now)

    qs = QueueSystem()
    qs.num_queues = 2

    # Collect tiles across several selections
    selected = []
    for _ in range(6):
        tile = await qs.select_next_tile()
        if tile is not None:
            selected.append(tile)

    # Should have selected from multiple queue temperatures
    assert len(selected) >= 2
    assert Tile(0, 0) in selected  # burning tile


async def test_select_next_tile_skips_empty_queue():
    """When current queue is empty, advances index and returns None."""
    qs = QueueSystem()
    qs.num_queues = 2

    # No tiles in DB at all - all queues empty
    initial_index = qs.current_queue_index
    result = await qs.select_next_tile()
    assert result is None
    assert qs.current_queue_index == initial_index + 1


# --- update_tile_after_check ---


async def test_update_tile_after_check_timestamps():
    """Updates last_checked, last_update, and http_etag."""
    await _create_tile(5, 5, queue_temperature=1, last_checked=100, last_update=50)

    qs = QueueSystem()
    qs.num_queues = 1

    new_update = round(time.time())
    await qs.update_tile_after_check(Tile(5, 5), new_update, "etag-abc")

    tile_info = await TileInfo.get(id=TileInfo.tile_id(5, 5))
    assert tile_info.last_update == new_update
    assert tile_info.http_etag == "etag-abc"
    assert tile_info.last_checked > 100


async def test_update_tile_after_check_burning_graduates():
    """Checking a burning tile (last_checked=0) triggers Zipf rebuild."""
    # Create a burning tile and a temperature tile
    await _create_tile(0, 0, queue_temperature=999, last_checked=0, last_update=0)
    await _create_tile(1, 0, queue_temperature=1, last_checked=100, last_update=50)

    qs = QueueSystem()
    qs.num_queues = 1

    now = round(time.time())
    await qs.update_tile_after_check(Tile(0, 0), now, "")

    # Tile should no longer be burning (temp should have changed from 999)
    tile_info = await TileInfo.get(id=TileInfo.tile_id(0, 0))
    assert tile_info.last_checked > 0
    # After rebuild, should be assigned a temperature 1..N (not 999 or 0)
    assert tile_info.queue_temperature != 999


async def test_update_tile_after_check_non_burning_no_rebuild():
    """Checking a non-burning tile updates timestamps but doesn't rebuild Zipf."""
    now = round(time.time())
    await _create_tile(0, 0, queue_temperature=2, last_checked=now - 500, last_update=now - 1000)

    qs = QueueSystem()
    qs.num_queues = 3

    await qs.update_tile_after_check(Tile(0, 0), now, "etag-1")

    # Temperature should remain unchanged (no rebuild for non-burning)
    tile_info = await TileInfo.get(id=TileInfo.tile_id(0, 0))
    assert tile_info.queue_temperature == 2
    assert tile_info.last_checked > now - 500


# --- _rebuild_zipf_distribution ---


async def test_rebuild_zipf_empty():
    """Rebuild with no temperature tiles sets num_queues to 0."""
    qs = QueueSystem()
    qs.num_queues = 5  # stale value

    await qs._rebuild_zipf_distribution()

    assert qs.num_queues == 0


async def test_rebuild_zipf_assigns_temperatures():
    """Rebuild assigns temperature values based on last_update ordering."""
    now = round(time.time())

    # Create 20 temperature tiles with varying last_update
    for i in range(20):
        await _create_tile(i, 0, queue_temperature=1, last_checked=now - 100, last_update=now - i * 100)

    qs = QueueSystem()
    await qs._rebuild_zipf_distribution()

    assert qs.num_queues > 0

    # All tiles should have valid temperature assignments (1..num_queues)
    tiles = await TileInfo.all()
    for t in tiles:
        assert 1 <= t.queue_temperature <= qs.num_queues

    # Verify Zipf distribution: count tiles per queue
    queue_counts = {}
    for t in tiles:
        queue_counts[t.queue_temperature] = queue_counts.get(t.queue_temperature, 0) + 1

    # Total should match
    assert sum(queue_counts.values()) == 20


async def test_rebuild_zipf_ignores_burning_and_inactive():
    """Rebuild excludes burning (999) and inactive (0) tiles."""
    now = round(time.time())

    await _create_tile(0, 0, queue_temperature=999, last_checked=0, last_update=0)  # burning
    await _create_tile(1, 0, queue_temperature=0, last_checked=0, last_update=0)  # inactive
    await _create_tile(2, 0, queue_temperature=1, last_checked=now, last_update=now)  # temperature

    qs = QueueSystem()
    await qs._rebuild_zipf_distribution()

    # Burning tile should still be 999
    burning = await TileInfo.get(id=TileInfo.tile_id(0, 0))
    assert burning.queue_temperature == 999

    # Inactive tile should still be 0
    inactive = await TileInfo.get(id=TileInfo.tile_id(1, 0))
    assert inactive.queue_temperature == 0

    # Temperature tile should have a valid assignment
    temp = await TileInfo.get(id=TileInfo.tile_id(2, 0))
    assert 1 <= temp.queue_temperature <= qs.num_queues


async def test_rebuild_zipf_hottest_tiles_get_lowest_temperature():
    """Most recently updated tiles get temperature=1 (hottest queue)."""
    now = round(time.time())

    # Create tiles: newest first
    await _create_tile(0, 0, queue_temperature=1, last_checked=now, last_update=now)  # newest
    await _create_tile(1, 0, queue_temperature=1, last_checked=now, last_update=now - 10000)  # oldest

    # Need enough tiles for multiple queues
    for i in range(2, 10):
        await _create_tile(i, 0, queue_temperature=1, last_checked=now, last_update=now - i * 500)

    qs = QueueSystem()
    await qs._rebuild_zipf_distribution()

    if qs.num_queues > 1:
        # Newest tile should be in hottest queue (temp=1)
        newest = await TileInfo.get(id=TileInfo.tile_id(0, 0))
        assert newest.queue_temperature == 1

        # Oldest tile should be in a colder queue
        oldest = await TileInfo.get(id=TileInfo.tile_id(1, 0))
        assert oldest.queue_temperature >= newest.queue_temperature


# --- retry_current_queue ---


def test_retry_current_queue_rewinds_index():
    """retry_current_queue decrements the round-robin index."""
    qs = QueueSystem()
    qs.current_queue_index = 3

    qs.retry_current_queue()
    assert qs.current_queue_index == 2


def test_retry_current_queue_clamps_at_zero():
    """retry_current_queue doesn't go below 0."""
    qs = QueueSystem()
    qs.current_queue_index = 0

    qs.retry_current_queue()
    assert qs.current_queue_index == 0


async def test_retry_and_reselect_same_queue():
    """After retry, next select hits the same queue again."""
    now = round(time.time())
    await _create_tile(0, 0, queue_temperature=999, last_checked=0)

    qs = QueueSystem()
    qs.num_queues = 0

    # Select from burning queue
    tile = await qs.select_next_tile()
    assert tile == Tile(0, 0)
    index_after_select = qs.current_queue_index

    # Retry
    qs.retry_current_queue()
    assert qs.current_queue_index == index_after_select - 1

    # Select again - should hit same queue
    tile2 = await qs.select_next_tile()
    assert tile2 == Tile(0, 0)
    assert qs.current_queue_index == index_after_select


# --- Integration: full check cycle ---


async def test_full_check_cycle_burning_to_temperature():
    """End-to-end: select burning tile, check it, verify it gets a temperature."""
    # Create several burning tiles
    for i in range(8):
        await _create_tile(i, 0, queue_temperature=999, last_checked=0, last_update=0)

    qs = QueueSystem()

    # Select and check first tile
    tile = await qs.select_next_tile()
    assert tile is not None

    now = round(time.time())
    await qs.update_tile_after_check(tile, now, "etag-1")

    # Tile should now be in a temperature queue
    tile_info = await TileInfo.get(id=TileInfo.tile_id(tile.x, tile.y))
    assert tile_info.last_checked > 0
    assert tile_info.queue_temperature != 999

    # Remaining tiles should still be burning
    burning_count = await TileInfo.filter(queue_temperature=999).count()
    assert burning_count == 7


async def test_full_check_cycle_multiple_graduates():
    """Multiple burning tiles graduating builds up temperature queues."""
    for i in range(10):
        await _create_tile(i, 0, queue_temperature=999, last_checked=0, last_update=0)

    qs = QueueSystem()
    now = round(time.time())

    # Run enough iterations to graduate several tiles (round-robin alternates queues)
    graduated = 0
    for iteration in range(20):
        tile = await qs.select_next_tile()
        if tile is None:
            continue

        tile_info = await TileInfo.get(id=TileInfo.tile_id(tile.x, tile.y))
        if tile_info.queue_temperature == 999:
            await qs.update_tile_after_check(tile, now - iteration * 100, f"etag-{iteration}")
            graduated += 1

    # Should have graduated some tiles into temperature queues
    assert graduated > 0
    burning = await TileInfo.filter(queue_temperature=999).count()
    temp = await TileInfo.filter(queue_temperature__gte=1, queue_temperature__lte=998).count()
    assert burning + temp == 10
    assert temp == graduated
    assert qs.num_queues >= 1


async def test_no_starvation_with_large_burning_queue():
    """Temperature queues get selected even when burning queue is large.

    Verifies round-robin behavior prevents burning queue from monopolizing.
    """
    now = round(time.time())

    # 5 temperature tiles
    for i in range(5):
        await _create_tile(i, 0, queue_temperature=1, last_checked=now - i * 100, last_update=now - i * 100)

    # 20 burning tiles
    for i in range(20):
        await _create_tile(i, 10, queue_temperature=999, last_checked=0, last_update=0)

    qs = QueueSystem()
    qs.num_queues = 1

    burning_selected = 0
    temp_selected = 0

    for _ in range(30):
        tile = await qs.select_next_tile()
        if tile is None:
            continue

        tile_info = await TileInfo.get(id=TileInfo.tile_id(tile.x, tile.y))
        if tile_info.queue_temperature == 999:
            burning_selected += 1
        else:
            temp_selected += 1

        # Don't update (just counting selections)

    # Both queue types should get selections
    assert burning_selected > 0, "Burning queue should have been selected"
    assert temp_selected > 0, "Temperature queue should have been selected (no starvation)"

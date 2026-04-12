"""Tests for temperature-based queue system (database-driven)."""

import time

from pixel_hawk.models.tile import TileInfo
from pixel_hawk.watcher.queues import QueueSystem, calculate_zipf_queue_sizes

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
    assert sizes == ()


def test_calculate_zipf_queue_sizes_large():
    """Test with large number of tiles."""
    sizes = calculate_zipf_queue_sizes(1000, min_hottest_size=5)

    assert len(sizes) > 1
    assert sizes[0] >= 5
    assert sum(sizes) == 1000
    assert sizes[-1] > sizes[0]


# --- Helper to create TileInfo records ---


async def _create_tile(
    x: int, y: int, *, heat: int = 999, last_checked: int = 0, last_update: int = 0, etag: str = ""
) -> TileInfo:
    """Create a TileInfo record in the database."""
    return await TileInfo.create(
        id=TileInfo.tile_id(x, y),
        x=x,
        y=y,
        heat=heat,
        last_checked=last_checked,
        last_update=last_update,
        etag=etag,
    )


# --- QueueSystem.__init__ ---


def test_queue_system_initialization():
    """Test QueueSystem starts with empty iterator and zero queues."""
    qs = QueueSystem()
    assert list(qs.queue_iterator) == []
    assert qs.num_queues == 0


# --- start ---


async def test_start_empty_database():
    """start() with no tiles sets num_queues to 0."""
    qs = QueueSystem()
    await qs.start()
    assert qs.num_queues == 0


async def test_start_loads_num_queues_from_db():
    """start() loads num_queues from existing temperature tiles in DB."""
    now = round(time.time())
    for i in range(10):
        await _create_tile(i, 0, heat=1, last_checked=now, last_update=now - i * 100)

    qs = QueueSystem()
    assert qs.num_queues == 0
    await qs.start()
    assert qs.num_queues >= 1


# --- select_next_tile ---


async def test_select_next_tile_empty_database():
    """Selecting from empty DB returns None."""
    qs = QueueSystem()
    result = await qs.select_next_tile()
    assert result is None


async def test_select_next_tile_burning_only():
    """Selects a tile from burning queue (heat=999)."""
    await _create_tile(3, 7, heat=999, last_checked=0)

    qs = QueueSystem()
    tile_info = await qs.select_next_tile()

    assert tile_info is not None
    assert (tile_info.x, tile_info.y) == (3, 7)


async def test_select_next_tile_temperature_only():
    """Selects from temperature queue when no burning tiles exist."""
    await _create_tile(1, 2, heat=1, last_checked=100, last_update=50)
    await _create_tile(3, 4, heat=1, last_checked=50, last_update=50)

    qs = QueueSystem()
    await qs.start()

    # Burning queue is empty; skips to temp queue automatically
    tile_info = await qs.select_next_tile()

    assert tile_info is not None
    # Should pick least recently checked (last_checked=50 => tile (3,4))
    assert (tile_info.x, tile_info.y) == (3, 4)


async def test_select_next_tile_least_recently_checked():
    """Within a temperature queue, selects the tile with oldest last_checked."""
    now = round(time.time())
    await _create_tile(0, 0, heat=1, last_checked=now - 1000, last_update=now)
    await _create_tile(1, 0, heat=1, last_checked=now - 500, last_update=now)
    await _create_tile(2, 0, heat=1, last_checked=now - 2000, last_update=now)  # oldest

    qs = QueueSystem()
    await qs.start()

    # Burning queue is empty; skips to temp queue automatically
    tile_info = await qs.select_next_tile()

    assert tile_info is not None
    assert (tile_info.x, tile_info.y) == (2, 0)


async def test_select_next_tile_round_robin():
    """Verifies iterator cycling through burning + temperature queues."""
    now = round(time.time())
    # One burning tile
    await _create_tile(0, 0, heat=999, last_checked=0)
    # Two temperature tiles in different queues
    await _create_tile(1, 0, heat=1, last_checked=now - 100, last_update=now)
    await _create_tile(2, 0, heat=2, last_checked=now - 200, last_update=now)

    qs = QueueSystem()
    await qs.start()

    # Collect tiles across several selections
    selected = []
    for _ in range(6):
        tile_info = await qs.select_next_tile()
        if tile_info is not None:
            selected.append((tile_info.x, tile_info.y))

    # Should have selected from multiple queue temperatures
    assert len(selected) >= 2
    assert (0, 0) in selected  # burning tile


async def test_select_next_tile_skips_empty_queue():
    """When all queues are empty, returns None after exhausting iterator."""
    qs = QueueSystem()
    qs.num_queues = 2

    # No tiles in DB at all - all queues empty
    result = await qs.select_next_tile()
    assert result is None


# --- redistribute_queues ---


async def test_redistribute_empty():
    """Redistribute with no temperature tiles sets num_queues to 0."""
    qs = QueueSystem()
    qs.num_queues = 5  # stale value

    await qs.redistribute_queues()

    assert qs.num_queues == 0


async def test_redistribute_assigns_temperatures():
    """Redistribute assigns temperature values based on last_update ordering."""
    now = round(time.time())

    # Create 20 temperature tiles with varying last_update
    for i in range(20):
        await _create_tile(i, 0, heat=1, last_checked=now - 100, last_update=now - i * 100)

    qs = QueueSystem()
    await qs.redistribute_queues()

    assert qs.num_queues > 0

    # All tiles should have valid temperature assignments (1..num_queues)
    tiles = await TileInfo.all()
    for t in tiles:
        assert 1 <= t.heat <= qs.num_queues

    # Verify Zipf distribution: count tiles per queue
    queue_counts = {}
    for t in tiles:
        queue_counts[t.heat] = queue_counts.get(t.heat, 0) + 1

    # Total should match
    assert sum(queue_counts.values()) == 20


async def test_redistribute_ignores_unchecked_burning_and_inactive():
    """Redistribute excludes unchecked burning (last_update=0) and inactive (heat=0) tiles."""
    now = round(time.time())

    await _create_tile(0, 0, heat=999, last_checked=0, last_update=0)  # unchecked burning
    await _create_tile(1, 0, heat=0, last_checked=0, last_update=0)  # inactive
    await _create_tile(2, 0, heat=1, last_checked=now, last_update=now)  # temperature

    qs = QueueSystem()
    await qs.redistribute_queues()

    # Unchecked burning tile should still be 999
    burning = await TileInfo.get_by_id(TileInfo.tile_id(0, 0))
    assert burning.heat == 999

    # Inactive tile should still be 0
    inactive = await TileInfo.get_by_id(TileInfo.tile_id(1, 0))
    assert inactive.heat == 0

    # Temperature tile should have a valid assignment
    temp = await TileInfo.get_by_id(TileInfo.tile_id(2, 0))
    assert 1 <= temp.heat <= qs.num_queues


async def test_redistribute_hottest_tiles_get_highest_temperature():
    """Most recently updated tiles get the highest temperature (hottest queue)."""
    now = round(time.time())

    # Create tiles: newest first
    await _create_tile(0, 0, heat=1, last_checked=now, last_update=now)  # newest
    await _create_tile(1, 0, heat=1, last_checked=now, last_update=now - 10000)  # oldest

    # Need enough tiles for multiple queues
    for i in range(2, 10):
        await _create_tile(i, 0, heat=1, last_checked=now, last_update=now - i * 500)

    qs = QueueSystem()
    await qs.redistribute_queues()

    if qs.num_queues > 1:
        # Newest tile should be in hottest queue (highest temperature)
        newest = await TileInfo.get_by_id(TileInfo.tile_id(0, 0))
        assert newest.heat == qs.num_queues

        # Oldest tile should be in a colder queue (lower temperature)
        oldest = await TileInfo.get_by_id(TileInfo.tile_id(1, 0))
        assert oldest.heat <= newest.heat


async def test_redistribute_optimistic_no_changes():
    """Running redistribute twice with same state writes zero updates the second time."""
    now = round(time.time())

    for i in range(10):
        await _create_tile(i, 0, heat=1, last_checked=now, last_update=now - i * 100)

    qs = QueueSystem()
    await qs.redistribute_queues()

    # Capture heat values after first redistribute
    tiles_before = {t.id: t.heat for t in await TileInfo.all()}

    # Second redistribute should be a no-op (optimistic fast path)
    await qs.redistribute_queues()

    # All heats should be identical
    tiles_after = {t.id: t.heat for t in await TileInfo.all()}
    assert tiles_before == tiles_after


async def test_redistribute_graduates_checked_burning_tile():
    """Burning tiles with last_update > 0 are included in redistribution."""
    now = round(time.time())

    # Burning tile that has been checked (has last_update)
    burning = await _create_tile(0, 0, heat=999, last_checked=now, last_update=now)

    # Some temperature tiles
    for i in range(1, 10):
        await _create_tile(i, 0, heat=1, last_checked=now, last_update=now - i * 100)

    qs = QueueSystem()
    await qs.redistribute_queues()

    # Burning tile should be graduated to hottest queue (most recent last_update)
    await burning.refresh_from_db()
    assert burning.heat != 999
    assert burning.heat == qs.num_queues


# --- Iterator behavior ---


async def test_iterator_exhaustion_triggers_redistribute():
    """When iterator exhausts, redistribution runs and a new cycle starts."""
    now = round(time.time())
    for i in range(5):
        await _create_tile(i, 0, heat=1, last_checked=now - 100, last_update=now - i * 100)

    qs = QueueSystem()
    await qs.start()

    # Exhaust the iterator: burning (empty) + num_queues temperature queues
    # Each select_next_tile call that returns a tile advances by 1
    # After all queues exhausted, next call triggers redistribute + new cycle
    selected_count = 0
    for _ in range(20):
        tile = await qs.select_next_tile()
        if tile:
            selected_count += 1

    # Should have selected tiles across multiple cycles
    assert selected_count >= 5


async def test_full_cycle_with_iterator():
    """Multiple complete iterator cycles selecting tiles."""
    now = round(time.time())
    for i in range(10):
        await _create_tile(i, 0, heat=1, last_checked=now - 1000 + i, last_update=now - i * 100)

    qs = QueueSystem()
    await qs.start()

    tiles_seen = set()
    for t in range(50):
        tile = await qs.select_next_tile()
        if tile:
            tiles_seen.add(tile.id)
            # Simulate real usage: update last_checked so LRU rotates
            tile.last_checked = now + t
            await tile.save()

    # Should have seen all 10 tiles across cycles
    assert len(tiles_seen) == 10


# --- Integration: full check cycle ---


async def test_full_check_cycle_burning_to_temperature():
    """End-to-end: burning tile stays burning until iterator cycle triggers redistribute."""
    for i in range(8):
        await _create_tile(i, 0, heat=999, last_checked=0, last_update=0)

    qs = QueueSystem()
    await qs.start()

    # Select and check first burning tile
    tile_info = await qs.select_next_tile()
    assert tile_info is not None
    assert tile_info.heat == 999

    # Simulate has_tile_changed mutation
    now = round(time.time())
    tile_info.last_checked = now
    tile_info.last_update = now
    tile_info.etag = "etag-1"

    await tile_info.save()

    # Tile should still be burning (deferred graduation)
    await tile_info.refresh_from_db()
    assert tile_info.heat == 999
    assert tile_info.last_update == now

    # Exhaust iterator to trigger redistribute
    for _ in range(20):
        await qs.select_next_tile()

    # After redistribute, checked burning tile should be graduated
    await tile_info.refresh_from_db()
    assert tile_info.heat != 999
    assert 1 <= tile_info.heat <= qs.num_queues


async def test_full_check_cycle_multiple_graduates():
    """Multiple burning tiles graduating via deferred redistribution."""
    for i in range(10):
        await _create_tile(i, 0, heat=999, last_checked=0, last_update=0)

    qs = QueueSystem()
    now = round(time.time())

    # Check several burning tiles (they stay heat=999 but get last_update > 0)
    checked = 0
    for iteration in range(30):
        tile_info = await qs.select_next_tile()
        if tile_info is None:
            continue

        if tile_info.heat == 999 and tile_info.last_update == 0:
            # Simulate has_tile_changed mutation
            tile_info.last_checked = now
            tile_info.last_update = now - iteration * 100
            tile_info.etag = f"etag-{iteration}"
            await tile_info.save()
            checked += 1

    assert checked > 0

    # After cycles complete, checked tiles should have graduated
    graduated = await TileInfo.count_by_heat(heat_gte=1, heat_lte=998)
    still_burning = await TileInfo.count_by_heat(heat_gte=999, heat_lte=999)
    assert graduated + still_burning == 10
    assert graduated > 0
    assert qs.num_queues >= 1


async def test_no_starvation_with_large_burning_queue():
    """Temperature queues get selected even when burning queue is large.

    Verifies iterator behavior prevents burning queue from monopolizing.
    """
    now = round(time.time())

    # 5 temperature tiles
    for i in range(5):
        await _create_tile(i, 0, heat=1, last_checked=now - i * 100, last_update=now - i * 100)

    # 20 burning tiles
    for i in range(20):
        await _create_tile(i, 10, heat=999, last_checked=0, last_update=0)

    qs = QueueSystem()
    await qs.start()

    burning_selected = 0
    temp_selected = 0

    for _ in range(30):
        tile_info = await qs.select_next_tile()
        if tile_info is None:
            continue

        if tile_info.heat == 999:
            burning_selected += 1
        else:
            temp_selected += 1

        # Don't update (just counting selections)

    # Both queue types should get selections
    assert burning_selected > 0, "Burning queue should have been selected"
    assert temp_selected > 0, "Temperature queue should have been selected (no starvation)"

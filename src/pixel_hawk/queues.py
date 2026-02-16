"""Heat-based tile queue system with Zipf distribution.

Implements intelligent tile checking using database-backed heat queues:
- Burning queue (heat=999): tiles not yet checked (last_update=0)
- Heat queues (heat=1-998): higher temp = hotter (more recently updated)
- Inactive (heat=0): tiles with no active projects

Queue sizes follow Zipf distribution (harmonic series), with the hottest queue
having a specific number of tiles and the coldest having the most.

An in-memory iterator cycles through queues from burning to coldest. When the
iterator completes a full cycle, redistribution runs automatically. Burning
tiles graduate into temperature queues when redistribution discovers them
(last_update > 0) — no manual heat changes on check.

Redistribution is optimistic: it computes target heats and only writes tiles
whose current heat differs from the target.
"""

import functools
from collections import defaultdict
from collections.abc import Iterator

from loguru import logger

from .models import TileInfo


@functools.lru_cache(3)
def calculate_zipf_queue_sizes(total_tiles: int, min_hottest_size: int = 4) -> tuple[int, ...]:
    """Calculate queue sizes following Zipf distribution (harmonic series).

    Returns a list of queue sizes from hottest to coldest, where:
    - Each queue i gets size proportional to 1/(k-i+1) where k is total queues
    - Coldest queue has the most tiles, hottest has the least
    - Hottest queue has at least min_hottest_size tiles
    - If total_tiles < min_hottest_size, return single queue with all tiles

    Args:
        total_tiles: Total number of tiles to distribute
        min_hottest_size: Minimum size for hottest queue

    Returns:
        List of queue sizes from hottest to coldest (e.g., [5, 10, 20, 65])
    """
    if total_tiles <= min_hottest_size:
        return (total_tiles,) if total_tiles > 0 else ()

    # Calculate number of queues that satisfies min hottest size
    # For k queues with reverse Zipf, hottest queue gets:
    # size = total_tiles * (1/k) / H_k where H_k = sum(1/i for i in 1..k)
    # We want: total_tiles * (1/k) / H_k >= min_hottest_size
    # Upper bound: Since H_k >= 1, hottest_size <= total_tiles/k,
    # so k <= total_tiles/min_hottest_size
    #
    # hottest_size decreases monotonically with k, so use binary search
    # to find the largest k where hottest_size >= min_hottest_size

    left, right = 1, (total_tiles // min_hottest_size)
    num_queues = 1

    while left <= right:
        k = (left + right) // 2
        harmonic = sum(1.0 / i for i in range(1, k + 1))
        hottest_size = total_tiles * (1.0 / k) / harmonic

        if round(hottest_size) >= min_hottest_size:
            # This k works, try larger k
            num_queues = k
            left = k + 1
        else:
            # This k too large, try smaller k
            right = k - 1

    if num_queues >= 999:
        # Wow we need to be watching 26170 tiles to get to 999 queues! That's 0.6% of the canvas!
        logger.warning("Uh-oh! Burning queue is mingled with regular heat tiles. It will still work, but with quirks.")

    # Calculate harmonic sum for the chosen number of queues
    harmonic = sum(1.0 / i for i in range(1, num_queues + 1))

    # Distribute tiles according to reverse Zipf
    # Queue i gets tiles proportional to 1/(num_queues - i + 1)
    sizes = []
    allocated = 0
    for i in range(1, num_queues + 1):
        # Reverse Zipf: first queue (hottest) uses largest denominator
        proportion = (1.0 / (num_queues - i + 1)) / harmonic
        size = round(total_tiles * proportion)
        sizes.append(size)
        allocated += size

    # Adjust for rounding errors: distribute remainder to coldest queues
    remainder = total_tiles - allocated
    for i in range(len(sizes) - 1, -1, -1):
        if remainder == 0:
            break
        if remainder > 0:
            sizes[i] += 1
            remainder -= 1
        else:
            # Over-allocated, remove from coldest
            if sizes[i] > 1:
                sizes[i] -= 1
                remainder += 1

    return tuple(sizes)


class QueueSystem:
    """Manages heat-based tile queues with Zipf distribution.

    Query-driven architecture: selects tiles by querying database for least
    recently checked tile in current queue. The database is the single source
    of truth for all tile state.

    An in-memory iterator cycles through burning (999), then hottest to coldest
    temperature queues. When the iterator exhausts, redistribute_queues() runs
    and a fresh iterator is created. Burning tiles graduate automatically when
    redistribution discovers they have last_update > 0.
    """

    def __init__(self):
        """Initialize queue system with empty iterator."""
        self.queue_iterator: Iterator[int] = iter(())
        self.num_queues = 0  # Set by start(), updated by redistribute_queues

    async def start(self) -> None:
        """Load queue state from existing database. Call after DB is ready."""
        await self.redistribute_queues()

    async def select_next_tile(self) -> TileInfo | None:
        """Select next tile to check by advancing through heat queues.

        Iterates from burning (999) through hottest to coldest queue, querying
        the database for the least recently checked tile. When the iterator
        exhausts (full cycle), triggers redistribution and starts a new cycle.

        Returns:
            TileInfo to check, or None if no tiles exist
        """
        tile = await self._try_select()
        if tile:
            return tile

        # Iterator exhausted — full cycle complete, redistribute and retry
        await self.redistribute_queues()
        return await self._try_select()

    async def redistribute_queues(self) -> None:
        """Reassign heat values using Zipf distribution, optimistically.

        Selects tiles with heat > 0 and last_update > 0 (checked at least once),
        which includes burning tiles that have been checked. Computes target heat
        for each tile based on last_update recency, and only updates tiles whose
        current heat differs from the target.
        """
        # Fetch all tiles eligible for temperature queues:
        # heat > 0 excludes inactive; last_update > 0 excludes never-checked burning
        temp_tiles = await TileInfo.filter(heat__gt=0, last_update__gt=0).order_by("-last_update").all()

        if not temp_tiles:
            self.num_queues = 0
            self.queue_iterator = iter([999])
            return

        queue_sizes = calculate_zipf_queue_sizes(len(temp_tiles))
        self.num_queues = len(queue_sizes)
        self.queue_iterator = iter([999] + list(range(self.num_queues, 0, -1)))

        # Walk tiles and collect mismatches grouped by target heat
        updates: defaultdict[int, list[int]] = defaultdict(list)
        current_idx = 0
        for temp_idx, queue_size in enumerate(queue_sizes):
            target_heat = self.num_queues - temp_idx
            for tile in temp_tiles[current_idx : current_idx + queue_size]:
                if tile.heat != target_heat:
                    updates[target_heat].append(tile.id)
            current_idx += queue_size

        if not updates:
            logger.debug(f"Queues unchanged: {self.num_queues} queues, {len(temp_tiles)} tiles")
            return

        total_updated = 0
        for target_heat, tile_ids in updates.items():
            await TileInfo.filter(id__in=tile_ids).update(heat=target_heat)
            total_updated += len(tile_ids)

        logger.debug(f"Redistributed: {self.num_queues} queues, {len(temp_tiles)} tiles, {total_updated} updated")

    async def _try_select(self) -> TileInfo | None:
        """Advance the iterator, querying each queue for the least recently checked tile."""
        for heat in self.queue_iterator:
            tile_info = await TileInfo.filter(heat=heat).order_by("last_checked").first()
            if tile_info:
                logger.debug(f"Using queue heat={heat}")
                return tile_info
        return None

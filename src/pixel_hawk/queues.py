"""Heat-based tile queue system with Zipf distribution.

Implements intelligent tile checking using database-backed heat queues:
- Burning queue (temp=999): tiles that have never been checked (last_checked=0)
- Heat queues (temp=1-998): higher temp = hotter (more recently updated)
- Inactive (temp=0): tiles with no active projects

Queue sizes follow Zipf distribution (harmonic series), with the hottest queue
having a specific number of tiles and the coldest having the most. Tiles are selected
round-robin between queues, querying the database for the least-recently-checked tile
within each queue.

This module is query-driven: no tile metadata is loaded into memory. The database
is the single source of truth for all tile state.
"""

from loguru import logger

from .models import TileInfo


def calculate_zipf_queue_sizes(total_tiles: int, min_hottest_size: int = 4) -> list[int]:
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
        return [total_tiles] if total_tiles > 0 else []

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

    return sizes


class QueueSystem:
    """Manages heat-based tile queues with Zipf distribution.

    Query-driven architecture: selects tiles by querying database for least
    recently checked tile in current queue. No tile metadata is loaded into
    memory - the database is the single source of truth.

    Maintains a burning queue (temp=999) for never-checked tiles and multiple
    heat queues (temp=1 to num_queues) where higher temp = hotter.
    Selects tiles round-robin: burning, then hottest to coldest.
    """

    def __init__(self):
        """Initialize queue system with database-backed selection."""
        self.current_queue_index = 0  # Round-robin position across queues
        self.num_queues = 0  # Set by start() from existing DB state, updated by _rebuild_zipf_distribution

    async def start(self) -> None:
        """Load num_queues from existing database state. Call after DB is ready."""
        await self._rebuild_zipf_distribution()

    async def select_next_tile(self) -> TileInfo | None:
        """Select next tile to check using round-robin across heat queues.

        Queries database directly for least recently checked tile in current queue.
        Skips empty queues, trying all queues before giving up.

        Returns:
            TileInfo to check, or None if all queues are empty
        """

        # Determine current queue heat (999 for burning, or 1 to num_queues)
        # Round-robin cycles through: burning (999), then hottest (N) down to coldest (1)
        heats = [999] + list(range(self.num_queues, 0, -1))
        total_queues = len(heats)

        # Try each queue starting from current position; skip empty queues
        for _ in range(total_queues):
            current_temp = heats[self.current_queue_index % total_queues]

            # Query database for least recently checked tile in this heat queue
            tile_info = await TileInfo.filter(heat=current_temp).order_by("last_checked").first()

            # Advance round-robin index for next call
            self.current_queue_index += 1

            if tile_info:
                logger.debug(f"Using queue temp={current_temp}")
                return tile_info

        return None

    async def update_tile_after_check(self, tile_info: TileInfo) -> None:
        """Persist tile_info to database and handle burning→heat graduation.

        Assumes tile_info fields (last_checked, last_update, etag) have
        already been updated by the caller (has_tile_changed).

        Args:
            tile_info: The TileInfo with updated fields to persist
        """
        was_burning = tile_info.heat == 999

        # Graduate from burning queue into regular heat pool
        if was_burning:
            tile_info.heat = 1  # Temporary; rebuild will reassign

        await tile_info.save()

        # If tile graduated from burning queue, trigger Zipf rebuild
        if was_burning:
            await self._rebuild_zipf_distribution()

    async def _rebuild_zipf_distribution(self) -> None:
        """Rebuild heat assignments using Zipf distribution.

        Only called when tiles graduate from burning queue.
        Queries all non-burning tiles, sorts by last_update, and assigns
        heat values (1-N) based on Zipf distribution.
        """
        # Fetch all non-burning tiles (heat in [1,998])
        temp_tiles = await TileInfo.filter(heat__gt=0, heat__lt=999).order_by("-last_update").all()

        if not temp_tiles:
            self.num_queues = 0
            return

        # Calculate Zipf queue sizes
        num_tiles = len(temp_tiles)
        queue_sizes = calculate_zipf_queue_sizes(num_tiles)
        self.num_queues = len(queue_sizes)

        # Assign tiles to heat queues (hottest = most recent last_update)
        current_idx = 0
        for temp_idx, queue_size in enumerate(queue_sizes):
            tiles_in_queue = temp_tiles[current_idx : current_idx + queue_size]
            tile_ids = [t.id for t in tiles_in_queue]

            # Bulk update: hottest tiles (temp_idx=0) get highest heat number
            await TileInfo.filter(id__in=tile_ids).update(heat=self.num_queues - temp_idx)

            current_idx += queue_size

        logger.debug(f"Rebuilt Zipf distribution: {self.num_queues} queues, {num_tiles} tiles")

    def retry_current_queue(self) -> None:
        """Rewind round-robin index to retry current queue.

        Call this when a tile check fails and should be retried from the same
        queue on the next check cycle.
        """
        self.current_queue_index = max(0, self.current_queue_index - 1)

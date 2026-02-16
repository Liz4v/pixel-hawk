"""Temperature-based tile queue system with Zipf distribution.

Implements intelligent tile checking using database-backed temperature queues:
- Burning queue (temp=999): tiles that have never been checked (last_checked=0)
- Temperature queues (temp=1-998): hot to cold, based on last_update timestamp
- Inactive (temp=0): tiles with no active projects

Queue sizes follow Zipf distribution (harmonic series), with the hottest queue
having a specific number of tiles and the coldest having the most. Tiles are selected
round-robin between queues, querying the database for the least-recently-checked tile
within each queue.

This module is query-driven: no tile metadata is loaded into memory. The database
is the single source of truth for all tile state.
"""

import time

from loguru import logger

from .geometry import Tile
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
    """Manages temperature-based tile queues with Zipf distribution.

    Query-driven architecture: selects tiles by querying database for least
    recently checked tile in current queue. No tile metadata is loaded into
    memory - the database is the single source of truth.

    Maintains a burning queue (temp=999) for never-checked tiles and multiple
    temperature queues (temp=1 to num_queues) from hot to cold. Selects tiles
    round-robin between queues.
    """

    def __init__(self):
        """Initialize queue system with database-backed selection."""
        self.current_queue_index = 0  # Round-robin position across queues
        self.num_queues = 0  # Set by start() from existing DB state, updated by _rebuild_zipf_distribution

    async def start(self) -> None:
        """Load num_queues from existing database state. Call after DB is ready."""
        await self._rebuild_zipf_distribution()

    async def select_next_tile(self) -> Tile | None:
        """Select next tile to check using round-robin across temperature queues.

        Queries database directly for least recently checked tile in current queue.
        Skips empty queues, trying all queues before giving up.

        Returns:
            Tile object to check, or None if all queues are empty
        """

        # Determine current queue temperature (999 for burning, or 1 to num_queues)
        # Round-robin cycles through: burning (999), temp 1, temp 2, ..., temp N
        queue_temperatures = [999] + list(range(1, self.num_queues + 1))
        total_queues = len(queue_temperatures)

        # Try each queue starting from current position; skip empty queues
        for _ in range(total_queues):
            current_temp = queue_temperatures[self.current_queue_index % total_queues]

            # Query database for least recently checked tile in this temperature queue
            tile_info = await TileInfo.filter(queue_temperature=current_temp).order_by("last_checked").first()

            # Advance round-robin index for next call
            self.current_queue_index += 1

            if tile_info:
                # Convert TileInfo to Tile object
                return Tile(x=tile_info.tile_x, y=tile_info.tile_y)

        return None

    async def update_tile_after_check(self, tile: Tile, new_last_update: int, http_etag: str) -> None:
        """Update tile in database after checking.

        Args:
            tile: The tile that was checked
            new_last_update: Parsed Last-Modified timestamp or current time
            http_etag: ETag header value from response
        """
        now = round(time.time())
        tile_id = TileInfo.tile_id(tile.x, tile.y)
        tile_info = await TileInfo.get(id=tile_id)

        was_burning = tile_info.last_checked == 0

        # Update timestamps and ETag
        tile_info.last_checked = now
        tile_info.last_update = new_last_update
        tile_info.http_etag = http_etag

        # Graduate from burning queue into temperature pool
        if was_burning:
            tile_info.queue_temperature = 1  # Temporary; rebuild will reassign

        await tile_info.save()

        # If tile graduated from burning queue, trigger Zipf rebuild
        if was_burning:
            await self._rebuild_zipf_distribution()

    async def _rebuild_zipf_distribution(self) -> None:
        """Rebuild temperature queue assignments using Zipf distribution.

        Only called when tiles graduate from burning queue.
        Queries all non-burning tiles, sorts by last_update, and assigns
        temperature values (1-N) based on Zipf distribution.
        """
        # Fetch all non-burning tiles (queue_temperature != 999 and != 0)
        temp_tiles = (
            await TileInfo.filter(
                queue_temperature__gt=0,
                queue_temperature__lt=999,
            )
            .order_by("-last_update")
            .all()
        )

        if not temp_tiles:
            self.num_queues = 0
            return

        # Calculate Zipf queue sizes
        num_tiles = len(temp_tiles)
        queue_sizes = calculate_zipf_queue_sizes(num_tiles)
        self.num_queues = len(queue_sizes)

        # Assign tiles to temperature queues (hottest = most recent last_update)
        current_idx = 0
        for temp_idx, queue_size in enumerate(queue_sizes):
            tiles_in_queue = temp_tiles[current_idx : current_idx + queue_size]
            tile_ids = [t.id for t in tiles_in_queue]

            # Bulk update temperature assignment (temp 1 = hottest)
            await TileInfo.filter(id__in=tile_ids).update(queue_temperature=temp_idx + 1)

            current_idx += queue_size

        logger.debug(f"Rebuilt Zipf distribution: {self.num_queues} queues, {num_tiles} tiles")

    def retry_current_queue(self) -> None:
        """Rewind round-robin index to retry current queue.

        Call this when a tile check fails and should be retried from the same
        queue on the next check cycle.
        """
        self.current_queue_index = max(0, self.current_queue_index - 1)

"""Temperature-based tile queue system with Zipf distribution.

Implements intelligent tile checking using multiple temperature-based queues:
- Burning queue: tiles that have never been checked
- Temperature queues: hot to cold, based on last modification time

Queue sizes follow Zipf distribution (harmonic series), with the hottest queue
having at least 5 tiles and the coldest having the most. Tiles are selected
round-robin between queues, choosing the least-recently-checked tile within each.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

from . import DIRS
from .geometry import Tile


@dataclass
class TileMetadata:
    """Metadata for a single tile, tracking check and modification times."""

    tile: Tile
    last_checked: int = 0  # timestamp of last check (0 = never checked)
    last_modified: int = 0  # timestamp from server's Last-Modified header (0 = unknown)

    def __hash__(self):
        return hash(self.tile)

    def __eq__(self, other):
        if isinstance(other, TileMetadata):
            return self.tile == other.tile
        return False

    @property
    def is_burning(self) -> bool:
        """True if tile has never been checked."""
        return self.last_checked == 0

    @property
    def cache_path(self) -> Path:
        """Path to cached tile file."""
        return DIRS.user_cache_path / f"tile-{self.tile}.png"

    @classmethod
    def from_cache(cls, tile: Tile) -> "TileMetadata":
        """Create TileMetadata by reading cache file if it exists."""
        meta = cls(tile=tile)
        cache_path = meta.cache_path
        if cache_path.exists():
            stat = cache_path.stat()
            # File mtime is the last modification time from server
            meta.last_modified = round(stat.st_mtime)
            # File exists, so it has been checked at least once
            # Use mtime as a proxy for last checked (conservative)
            meta.last_checked = round(stat.st_mtime)
        return meta


def calculate_zipf_queue_sizes(total_tiles: int, min_hottest_size: int = 5) -> list[int]:
    """Calculate queue sizes following Zipf distribution (harmonic series).

    Returns a list of queue sizes from hottest to coldest, where:
    - Each queue i gets size proportional to 1/(k-i+1) where k is total queues
    - Coldest queue has the most tiles, hottest has the least
    - Hottest queue has at least min_hottest_size tiles
    - If total_tiles < min_hottest_size, return single queue with all tiles

    Args:
        total_tiles: Total number of tiles to distribute
        min_hottest_size: Minimum size for hottest queue (default 5)

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

        if hottest_size >= min_hottest_size:
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


@dataclass
class TileQueue:
    """A single temperature-based tile queue."""

    temperature: int  # 0 = hottest, higher = colder, -1 = burning
    tiles: list[TileMetadata] = field(default_factory=list)

    def is_empty(self) -> bool:
        return len(self.tiles) == 0

    def select_next(self) -> Optional[TileMetadata]:
        """Select tile with oldest last_checked time (0 = oldest)."""
        if not self.tiles:
            return None

        # Find tile with oldest last_checked (0 counts as oldest)
        oldest = min(self.tiles, key=lambda t: t.last_checked)
        return oldest

    def remove_tile(self, tile_meta: TileMetadata) -> None:
        """Remove a tile from this queue."""
        try:
            self.tiles.remove(tile_meta)
        except ValueError:
            pass

    def add_tile(self, tile_meta: TileMetadata) -> None:
        """Add a tile to this queue if not already present."""
        if tile_meta not in self.tiles:
            self.tiles.append(tile_meta)


class QueueSystem:
    """Manages temperature-based tile queues with Zipf distribution.

    Maintains a burning queue for never-checked tiles and multiple temperature
    queues from hot to cold. Selects tiles round-robin between queues, choosing
    the least-recently-checked tile within each queue.
    """

    def __init__(self, tiles: set[Tile]):
        """Initialize queue system with the given tiles.

        Args:
            tiles: Set of all tiles to track
        """
        self.tile_metadata: dict[Tile, TileMetadata] = {}
        self.burning_queue = TileQueue(temperature=-1)
        self.temperature_queues: list[TileQueue] = []
        self.current_queue_index = 0

        # Load metadata from cache and initialize queues
        for tile in tiles:
            meta = TileMetadata.from_cache(tile)
            self.tile_metadata[tile] = meta

        self._rebuild_queues()

    def _rebuild_queues(self) -> None:
        """Rebuild all queues from current tile metadata."""
        # Clear existing queues
        self.burning_queue = TileQueue(temperature=-1)
        self.temperature_queues = []

        # Separate burning tiles from temperature tiles
        burning_tiles = []
        temp_tiles = []

        for meta in self.tile_metadata.values():
            if meta.is_burning:
                burning_tiles.append(meta)
            else:
                temp_tiles.append(meta)

        # Add burning tiles to burning queue
        for meta in burning_tiles:
            self.burning_queue.add_tile(meta)

        if not temp_tiles:
            # No temperature tiles, no temperature queues
            logger.debug("No temperature tiles, only burning queue")
            return

        # Sort temperature tiles by last_modified (most recent first)
        temp_tiles.sort(key=lambda t: t.last_modified, reverse=True)

        # Calculate queue sizes using Zipf distribution
        queue_sizes = calculate_zipf_queue_sizes(len(temp_tiles))

        if not queue_sizes:
            logger.warning("Failed to calculate queue sizes, using single queue")
            queue_sizes = [len(temp_tiles)]

        logger.info(f"Queue distribution (Zipf): {queue_sizes} for {len(temp_tiles)} tiles")

        # Create temperature queues and assign tiles
        idx = 0
        for temp_level, size in enumerate(queue_sizes):
            queue = TileQueue(temperature=temp_level)
            for _ in range(size):
                if idx < len(temp_tiles):
                    queue.add_tile(temp_tiles[idx])
                    idx += 1
            self.temperature_queues.append(queue)

        # Reset queue index to start from burning
        self.current_queue_index = 0

    def add_tiles(self, tiles: set[Tile]) -> None:
        """Add new tiles to the system (typically from new projects)."""
        changed = False
        for tile in tiles:
            if tile not in self.tile_metadata:
                meta = TileMetadata.from_cache(tile)
                self.tile_metadata[tile] = meta
                changed = True

        if changed:
            self._rebuild_queues()

    def remove_tiles(self, tiles: set[Tile]) -> None:
        """Remove tiles from the system (typically from deleted projects)."""
        changed = False
        for tile in tiles:
            if tile in self.tile_metadata:
                del self.tile_metadata[tile]
                changed = True

        if changed:
            self._rebuild_queues()

    def select_next_tile(self) -> Optional[TileMetadata]:
        """Select the next tile to check using round-robin queue selection.

        Returns:
            TileMetadata for the tile to check, or None if no tiles available
        """
        if not self.tile_metadata:
            return None

        # Build list of all queues (burning + temperature)
        all_queues = [self.burning_queue] + self.temperature_queues

        if not all_queues:
            return None

        # Try each queue starting from current index, skipping empty ones
        attempts = 0
        while attempts < len(all_queues):
            queue = all_queues[self.current_queue_index]

            # Advance to next queue for next call
            self.current_queue_index = (self.current_queue_index + 1) % len(all_queues)
            attempts += 1

            if not queue.is_empty():
                tile_meta = queue.select_next()
                if tile_meta:
                    return tile_meta

        # All queues empty (shouldn't happen if tile_metadata is not empty)
        logger.warning("All queues empty but tile_metadata not empty - rebuilding")
        self._rebuild_queues()
        return None

    def update_tile_after_check(self, tile: Tile, modified_time: int) -> None:
        """Update tile metadata after checking it.

        Args:
            tile: The tile that was checked
            modified_time: Last-Modified timestamp from server (0 if unknown)
        """
        meta = self.tile_metadata.get(tile)
        if not meta:
            logger.warning(f"Tile {tile} not in metadata")
            return

        now = round(time.time())
        old_last_modified = meta.last_modified
        was_burning = meta.is_burning

        # Update metadata
        meta.last_checked = now
        if modified_time > 0:
            meta.last_modified = modified_time

        # Check if tile needs to move between queues
        needs_rebalance = False

        if was_burning:
            # Graduated from burning queue
            needs_rebalance = True
        elif modified_time > 0 and modified_time != old_last_modified:
            # Modification time changed, may need to move to hotter queue
            needs_rebalance = True

        if needs_rebalance:
            self._rebuild_queues()

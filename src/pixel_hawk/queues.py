"""Temperature-based tile queue system with Zipf distribution.

Implements intelligent tile checking using multiple temperature-based queues:
- Burning queue: tiles that have never been checked
- Temperature queues: hot to cold, based on last modification time

Queue sizes follow Zipf distribution (harmonic series), with the hottest queue
having a specific number of tiles and the coldest having the most. Tiles are selected
round-robin between queues, choosing the least-recently-checked tile within each.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from loguru import logger

from .config import get_config
from .geometry import Tile

if TYPE_CHECKING:
    from .projects import Project


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
        return get_config().tiles_dir / f"tile-{self.tile}.png"

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


@dataclass
class TileQueue:
    """A single temperature-based tile queue."""

    temperature: int | None  # 0 = coldest, higher = hotter, None = burning
    tiles: list[TileMetadata] = field(default_factory=list)

    def is_empty(self) -> bool:
        return len(self.tiles) == 0

    def select_next(self, tile_to_projects: dict[Tile, set[Project]]) -> Optional[TileMetadata]:
        """Select tile with oldest last_checked time (0 = oldest).

        For burning queue (where all last_checked = 0), uses project first_seen
        timestamps to prioritize tiles from older projects.

        Args:
            tile_to_projects: Mapping of tiles to projects for burning queue prioritization
        """
        if not self.tiles:
            return None

        # Temperature queues use standard last_checked selection
        if self.temperature is not None:
            oldest = min(self.tiles, key=lambda t: t.last_checked)
            return oldest

        # Burning queue: prioritize by oldest project first_seen
        def tile_priority(tile_meta: TileMetadata) -> int:
            """Returns min_first_seen for sorting."""
            projects = tile_to_projects.get(tile_meta.tile, set())
            min_first_seen = min((p.info.first_seen for p in projects), default=1 << 58)
            return min_first_seen

        oldest = min(self.tiles, key=tile_priority)
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

    def __str__(self) -> str:
        descr = "burning" if self.temperature is None else f"temp={self.temperature}"
        return f"{descr} queue ({len(self.tiles)} tiles)"


class QueueSystem:
    """Manages temperature-based tile queues with Zipf distribution.

    Maintains a burning queue for never-checked tiles and multiple temperature
    queues from hot to cold. Selects tiles round-robin between queues, choosing
    the least-recently-checked tile within each queue.
    """

    def __init__(self, tiles: set[Tile], tile_to_projects: dict):
        """Initialize queue system with the given tiles.

        Args:
            tiles: Set of all tiles to track
            tile_to_projects: Mapping of tiles to projects that contain them
        """
        self.tile_to_projects = tile_to_projects
        self.tile_metadata: dict[Tile, TileMetadata] = {}
        self.burning_queue = TileQueue(temperature=None)
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
        self.burning_queue = TileQueue(temperature=None)
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
            # Ensure current_queue_index is valid (only burning queue exists)
            if self.current_queue_index >= 1:
                self.current_queue_index = 0
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
        # Higher temperature numbers = hotter queues
        idx = 0
        for queue_idx, size in enumerate(queue_sizes):
            temp_level = len(queue_sizes) - 1 - queue_idx  # Reverse: hottest gets highest number
            queue = TileQueue(temperature=temp_level)
            for _ in range(size):
                if idx < len(temp_tiles):
                    queue.add_tile(temp_tiles[idx])
                    idx += 1
            self.temperature_queues.append(queue)

        # Preserve round-robin position across rebuilds to prevent queue starvation
        # Ensure current_queue_index is within bounds for new queue count
        all_queues_count = 1 + len(self.temperature_queues)  # burning + temperature
        if self.current_queue_index >= all_queues_count:
            self.current_queue_index = self.current_queue_index % all_queues_count

    def _reposition_tile(self, tile_meta: TileMetadata) -> None:
        """Surgically move a tile to the correct queue based on its modification time.

        When a tile moves to a hotter queue, cascade tiles down through intervening
        queues to maintain Zipf distribution sizes.
        """
        if not self.temperature_queues:
            # No temperature queues exist yet (shouldn't happen but handle gracefully)
            return

        # Find which queue currently contains this tile
        old_queue_idx = None
        for idx, queue in enumerate(self.temperature_queues):
            if tile_meta in queue.tiles:
                old_queue_idx = idx
                break

        if old_queue_idx is None:
            # Tile not in any queue, shouldn't happen but handle gracefully
            logger.warning(f"Tile {tile_meta.tile} not found in any temperature queue during reposition")
            return

        # Get all temperature tiles and sort by last_modified (most recent first)
        temp_tiles = [m for m in self.tile_metadata.values() if not m.is_burning]
        temp_tiles.sort(key=lambda t: t.last_modified, reverse=True)

        # Find where this tile falls in the sorted order
        position = temp_tiles.index(tile_meta)

        # Calculate which queue this position belongs to based on target queue sizes
        queue_sizes = [len(q.tiles) for q in self.temperature_queues]
        cumulative = 0
        target_queue_idx = 0
        for idx, size in enumerate(queue_sizes):
            if position < cumulative + size:
                target_queue_idx = idx
                break
            cumulative += size
        else:
            # Tile belongs in last queue
            target_queue_idx = len(self.temperature_queues) - 1

        if target_queue_idx == old_queue_idx:
            # Tile stays in same queue, no repositioning needed
            return

        # Tile modification times can only increase, so tile can only move to hotter queue
        assert target_queue_idx < old_queue_idx, f"Tile {tile_meta.tile} moving to colder queue (impossible)"

        # Tile moving to hotter queue - cascade tiles down
        # Remove tile from old queue
        self.temperature_queues[old_queue_idx].remove_tile(tile_meta)

        # Cascade: push tile into target queue, bump coldest tile to next queue
        tile_to_insert = tile_meta
        for queue_idx in range(target_queue_idx, old_queue_idx):
            queue = self.temperature_queues[queue_idx]

            # Find coldest tile in this queue (lowest last_modified value = oldest modification)
            if queue.tiles:
                coldest = min(queue.tiles, key=lambda t: t.last_modified)
                queue.remove_tile(coldest)
                queue.add_tile(tile_to_insert)
                tile_to_insert = coldest
            else:
                # Queue is empty, just add the tile
                queue.add_tile(tile_to_insert)
                break
        else:
            # Cascade complete, add final carried tile back to old queue
            self.temperature_queues[old_queue_idx].add_tile(tile_to_insert)

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

        # Try each queue starting from current index, skipping empty ones
        attempts = 0
        while attempts < len(all_queues):
            queue = all_queues[self.current_queue_index]

            # Advance to next queue for next call
            self.current_queue_index = (self.current_queue_index + 1) % len(all_queues)
            attempts += 1

            if not queue.is_empty():
                tile_meta = queue.select_next(self.tile_to_projects)
                if tile_meta:
                    logger.debug(f"Examining tile {tile_meta.tile} from {queue}")
                    return tile_meta

        logger.warning("All queues empty but tile_metadata not empty - rebuilding")
        self._rebuild_queues()
        return None

    def retry_current_queue(self) -> None:
        """Rewind the round-robin index to retry the current queue.

        Call this when a tile check fails and should be retried from the same
        queue on the next check cycle.
        """
        all_queues_count = 1 + len(self.temperature_queues)
        if all_queues_count > 0:
            self.current_queue_index = (self.current_queue_index - 1) % all_queues_count

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
        if was_burning:
            # Graduated from burning queue - need full rebuild since temperature tile count changed
            self._rebuild_queues()
        elif modified_time > 0 and modified_time != old_last_modified:
            # Modification time changed - surgically move to appropriate queue
            self._reposition_tile(meta)

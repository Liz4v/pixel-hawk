"""Tile fetching, caching, and temperature-based queue checking.

Manages communication with the WPlace tile backend. Tiles are downloaded from
https://backend.wplace.live/files/s0/tiles/{x}/{y}.png and cached locally as
paletted PNG files. HTTP conditional requests minimize bandwidth usage.

The TileChecker class implements intelligent tile monitoring using temperature-
based queues with Zipf distribution: burning queue for never-checked tiles,
and multiple hot-to-cold queues based on modification time. Checks exactly one
tile per polling cycle, selecting round-robin between queues and choosing the
least-recently-checked tile within each queue.
"""

import io
import os
import time
from email.utils import formatdate, parsedate_to_datetime
from typing import TYPE_CHECKING, Iterable

import requests
from loguru import logger
from PIL import Image

from . import DIRS
from .geometry import Rectangle, Size, Tile
from .palette import PALETTE
from .queues import QueueSystem

if TYPE_CHECKING:
    from .projects import ProjectShim


def has_tile_changed(tile: Tile) -> tuple[bool, int]:
    """Downloads the indicated tile from the server and updates the cache.

    Returns:
        Tuple of (changed, last_modified_time):
        - changed: True if tile content changed
        - last_modified_time: Integer timestamp from server's Last-Modified header

    last_modified_time==0 indicates a server failure (network/decode error, status not 200 or 304)
    """
    url = f"https://backend.wplace.live/files/s0/tiles/{tile.x}/{tile.y}.png"

    # Check for cached tile and prepare If-Modified-Since header
    cache_path = DIRS.user_cache_path / f"tile-{tile}.png"
    headers = {}
    mtime = 0
    if cache_path.exists():
        mtime = round(cache_path.stat().st_mtime)
        headers["If-Modified-Since"] = formatdate(mtime, usegmt=True)

    try:
        response = requests.get(url, headers=headers, timeout=5)
    except Exception as e:
        logger.debug(f"Tile {tile}: Request failed: {e}")
        return False, 0

    if response.status_code == 304:  # Not Modified
        return False, mtime

    if response.status_code != 200:
        logger.debug(f"Tile {tile}: HTTP {response.status_code}")
        return False, 0
    data = response.content

    # The server is known to always give us a Last-Modified header on 200 responses, so we rely on
    # that. We require this information for queue management! If server behaviour changes, and we
    # stop getting 304 responses, we may need to refactor to compare image data instead.
    try:
        last_modified = response.headers["Last-Modified"]
        last_modified_timestamp = int(parsedate_to_datetime(last_modified).timestamp())
    except Exception as e:
        logger.warning(f"Tile {tile}: Failed to parse Last-Modified header: {e}")
        # Fallback to current time, hoping we'll still get 304s next time if tile is unchanged
        last_modified_timestamp = int(time.time())

    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        logger.debug(f"Tile {tile}: image decode failed: {e}")
        return False, 0

    # ensure() may close img and return new image, or return img unchanged.
    # Either way, the with statement ensures the result gets closed at block end.
    with PALETTE.ensure(img) as paletted:
        logger.info(f"Tile {tile}: Change detected, updating cache...")
        paletted.save(cache_path)
        os.utime(cache_path, (last_modified_timestamp, last_modified_timestamp))
    return True, last_modified_timestamp


def stitch_tiles(rect: Rectangle) -> Image.Image:
    """Stitches tiles from cache together, exactly covering the given rectangle."""
    image = PALETTE.new(rect.size)
    for tile in rect.tiles:
        cache_path = DIRS.user_cache_path / f"tile-{tile}.png"
        if not cache_path.exists():
            logger.debug(f"{tile}: Tile missing from cache, leaving transparent")
            continue
        with Image.open(cache_path) as tile_image:
            offset = tile.to_point() - rect.point
            image.paste(tile_image, Rectangle.from_point_size(offset, Size(1000, 1000)))
    return image


class TileChecker:
    """Manages temperature-based tile checking with Zipf-distributed queues.

    Uses QueueSystem to implement intelligent tile checking that prioritizes
    recently-modified tiles while still monitoring quieter areas. Tiles are
    organized into a burning queue (never checked) and multiple temperature
    queues (hot to cold) based on last modification time.
    """

    def __init__(self, projects: Iterable[ProjectShim]):
        """Initialize with a mapping of project paths to projects."""
        self.tiles: dict[Tile, set[ProjectShim]] = {}
        self._build_index(projects)

        # Initialize queue system with all indexed tiles
        self.queue_system = QueueSystem(set(self.tiles.keys()))

    def _build_index(self, projects: Iterable[ProjectShim]) -> None:
        """Index tiles to projects for quick lookup."""
        for proj in projects:
            for tile in proj.rect.tiles:
                self.tiles.setdefault(tile, set()).add(proj)
        logger.info(f"Indexed {len(self.tiles)} tiles.")

    def add_project(self, proj: ProjectShim) -> None:
        """Add a project and index its tiles."""
        new_tiles = set()
        for tile in proj.rect.tiles:
            if tile not in self.tiles:
                new_tiles.add(tile)
            self.tiles.setdefault(tile, set()).add(proj)

        if new_tiles:
            self.queue_system.add_tiles(new_tiles)

    def remove_project(self, proj: ProjectShim) -> None:
        """Remove a project and clean up its tiles from the index."""
        removed_tiles = set()
        for tile in proj.rect.tiles:
            projs = self.tiles.get(tile)
            if projs:
                projs.discard(proj)
                if not projs:
                    del self.tiles[tile]
                    removed_tiles.add(tile)

        if removed_tiles:
            self.queue_system.remove_tiles(removed_tiles)

    def check_next_tile(self) -> None:
        """Check one tile for changes using queue-based selection and update affected projects."""
        if not self.tiles:
            return  # No tiles to check

        tile_meta = self.queue_system.select_next_tile()
        if not tile_meta:
            logger.warning("No tile selected from queue system")
            return

        tile = tile_meta.tile
        changed, last_modified = has_tile_changed(tile)

        if last_modified == 0:
            # Server failure - don't update metadata or advance round-robin
            self.queue_system.retry_current_queue()
            return

        # Update queue system with check results
        self.queue_system.update_tile_after_check(tile, last_modified)

        for proj in self.tiles.get(tile) or ():
            if changed:
                proj.run_diff(changed_tile=tile)
            else:
                proj.run_nochange()

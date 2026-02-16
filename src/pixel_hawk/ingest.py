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

import asyncio
import time
from email.utils import formatdate, parsedate_to_datetime
from typing import TYPE_CHECKING, Iterable

import httpx
from humanize import naturaldelta
from loguru import logger
from PIL import Image, UnidentifiedImageError

from .config import get_config
from .geometry import Rectangle, Size, Tile
from .models import TileInfo
from .palette import PALETTE, ColorsNotInPalette
from .queues import QueueSystem

if TYPE_CHECKING:
    from .projects import Project


async def stitch_tiles(rect: Rectangle) -> Image.Image:
    """Stitches tiles from cache together, exactly covering the given rectangle."""
    image = PALETTE.new(rect.size)
    for tile in rect.tiles:
        cache_path = get_config().tiles_dir / f"tile-{tile}.png"
        if not cache_path.exists():
            logger.debug(f"{tile}: Tile missing from cache, leaving transparent")
            continue
        async with PALETTE.aopen_file(cache_path) as tile_image:
            offset = tile.to_point() - rect.point
            image.paste(tile_image, Rectangle.from_point_size(offset, Size(1000, 1000)))
    return image


class TileChecker:
    """Manages temperature-based tile checking with database-backed queues.

    Uses QueueSystem to implement intelligent tile checking that prioritizes
    recently-modified tiles while still monitoring quieter areas. Tiles are
    queried from the database on demand - no tile metadata is loaded into memory.

    Creates and owns an httpx.AsyncClient for tile fetching.
    """

    def __init__(self, projects: Iterable[Project]):
        """Initialize with projects to monitor. Creates an httpx.AsyncClient for tile fetching."""
        self.client = httpx.AsyncClient(timeout=5)

        # Build tile→projects index (for diff operations)
        self.tiles: dict[Tile, set[Project]] = {}
        for proj in projects:
            for tile in proj.rect.tiles:
                self.tiles.setdefault(tile, set()).add(proj)

        # Create QueueSystem (start() must be called to load state from DB)
        self.queue_system = QueueSystem()

        logger.info(f"Indexed {len(self.tiles)} tiles.")

    async def start(self) -> None:
        """Load queue state from database. Call after DB is ready."""
        await self.queue_system.start()

    async def check_next_tile(self) -> None:
        """Check one tile for changes using queue-based selection and update affected projects."""
        if not self.tiles:
            return  # No tiles to check

        # Select next tile from database via QueueSystem
        tile_info = await self.queue_system.select_next_tile()
        if not tile_info:
            logger.warning("No next tile returned by the queue system. No active projects?")
            return

        # Check tile (mutates tile_info fields: last_checked, last_update, etag)
        changed = await self.has_tile_changed(tile_info)

        # Persist tile_info updates and handle burning→temperature graduation
        await self.queue_system.update_tile_after_check(tile_info)

        # Diff against affected projects
        tile = tile_info.tile
        if changed:
            for proj in self.tiles.get(tile) or ():
                await proj.run_diff(changed_tile=tile)
        else:
            untouched = tile_info.last_checked - tile_info.last_update
            logger.debug(f"Tile {tile}: Unchanged for {untouched}s ({naturaldelta(untouched)})")
            for proj in self.tiles.get(tile) or ():
                await proj.run_nochange()

    async def has_tile_changed(self, tile_info: TileInfo) -> bool:
        """Downloads the indicated tile from the server and updates the cache.

        Mutates tile_info fields directly: last_checked is always updated,
        last_update and etag are updated on successful 200 responses.

        Args:
            tile_info: TileInfo to check and update in place

        Returns:
            True if tile was modified, False if 304 Not Modified or error.
        """
        tile = tile_info.tile
        url = f"https://backend.wplace.live/files/s0/tiles/{tile.x}/{tile.y}.png"
        cache_path = get_config().tiles_dir / f"tile-{tile}.png"

        # Build conditional request headers from TileInfo
        request_headers = {}
        if tile_info.last_update > 0:
            request_headers["If-Modified-Since"] = formatdate(tile_info.last_update, usegmt=True)
        if tile_info.etag:
            request_headers["If-None-Match"] = tile_info.etag

        tile_info.last_checked = now = round(time.time())
        try:
            response = await self.client.get(url, headers=request_headers)
        except Exception as e:
            logger.debug(f"Tile {tile}: Request failed: {e}")
            return False

        if response.status_code == 304:
            return False

        if response.status_code != 200:
            logger.debug(f"Tile {tile}: HTTP {response.status_code}")
            return False

        # Save response headers
        tile_info.etag = response.headers.get("ETag", "")
        last_modified_str = response.headers.get("Last-Modified", "")
        if last_modified_str:
            try:
                tile_info.last_update = round(parsedate_to_datetime(last_modified_str).timestamp())
            except Exception:
                tile_info.last_update = now
        else:
            tile_info.last_update = now

        # Save response body
        data = response.content
        try:
            async with PALETTE.aopen_bytes(data) as img:
                logger.info(f"Tile {tile}: Change detected, updating cache...")
                await asyncio.to_thread(img.save, cache_path)
        except (UnidentifiedImageError, ColorsNotInPalette) as e:
            logger.debug(f"Tile {tile}: image decode failed: {e}")
            return False

        return True

    async def close(self) -> None:
        """Close the httpx client."""
        await self.client.aclose()

"""Tile fetching, caching, and query-driven project diffing.

Manages communication with the WPlace tile backend. Tiles are downloaded from
https://backend.wplace.live/files/s0/tiles/{x}/{y}.png and cached locally as
paletted PNG files. HTTP conditional requests minimize bandwidth usage.

The TileChecker class implements intelligent tile monitoring using temperature-
based queues with Zipf distribution: burning queue for never-checked tiles,
and multiple hot-to-cold queues based on modification time. Checks exactly one
tile per polling cycle, selecting round-robin between queues and choosing the
least-recently-checked tile within each queue. When a tile changes, affected
projects are discovered via database query through the TileProject junction
table, and Project objects are constructed on demand for diffing.
"""

import asyncio
import time
from email.utils import formatdate, parsedate_to_datetime

import httpx
from humanize import naturaldelta
from loguru import logger
from PIL import UnidentifiedImageError

from .config import get_config
from .models import ProjectInfo, ProjectState, TileInfo
from .palette import PALETTE, ColorsNotInPalette
from .projects import Project
from .queues import QueueSystem


class TileChecker:
    """Manages temperature-based tile checking with database-backed queues.

    Uses QueueSystem to implement intelligent tile checking that prioritizes
    recently-modified tiles while still monitoring quieter areas. Tiles are
    queried from the database on demand - no tile metadata is loaded into memory.

    Creates and owns an httpx.AsyncClient for tile fetching.
    """

    def __init__(self):
        """Initialize tile checker. Creates an httpx.AsyncClient for tile fetching."""
        self.client = httpx.AsyncClient(timeout=5)
        self.queue_system = QueueSystem()

    async def start(self) -> None:
        """Load queue state from database. Call after DB is ready."""
        await self.queue_system.start()

    async def _get_projects_for_tile(self, tile_info: TileInfo) -> list[Project]:
        """Query database for projects affected by a tile, returning Project objects."""
        infos = await ProjectInfo.filter(
            project_tiles__tile_id=tile_info.id,
            state__in=[ProjectState.ACTIVE, ProjectState.PASSIVE],
        ).prefetch_related("owner")

        projects = []
        for info in infos:
            path = get_config().projects_dir / str(info.owner.id) / info.filename
            projects.append(Project(path, info.rectangle, info))
        return projects

    async def check_next_tile(self) -> None:
        """Check one tile for changes using queue-based selection and update affected projects."""
        # Select next tile from database via QueueSystem
        tile_info = await self.queue_system.select_next_tile()
        if not tile_info:
            logger.warning("No next tile returned by the queue system. No active projects?")
            return

        # Check tile (mutates tile_info fields: last_checked, last_update, etag)
        changed = await self.has_tile_changed(tile_info)
        await tile_info.save()

        # Query affected projects from database
        projects = await self._get_projects_for_tile(tile_info)
        if changed:
            for proj in projects:
                await proj.run_diff()
        else:
            untouched = tile_info.last_checked - tile_info.last_update
            logger.debug(f"Tile {tile_info.tile}: Unchanged for {untouched}s ({naturaldelta(untouched)})")
            for proj in projects:
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

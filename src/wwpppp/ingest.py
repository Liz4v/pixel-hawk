"""Tile fetching, caching, and round-robin checking.

Manages communication with the WPlace tile backend. Tiles are downloaded from
https://backend.wplace.live/files/s0/tiles/{x}/{y}.png and cached locally as
paletted PNG files. HTTP conditional requests minimize bandwidth usage.

The TileChecker class implements round-robin tile monitoring: it tracks which
tiles are associated with which projects and checks exactly one tile per polling
cycle, rotating through all indexed tiles to avoid hammering the backend.
"""

import io
import os
from email.utils import formatdate, parsedate_to_datetime
from typing import TYPE_CHECKING, Iterable

import requests
from loguru import logger
from PIL import Image

from . import DIRS
from .geometry import Rectangle, Size, Tile
from .palette import PALETTE

if TYPE_CHECKING:
    from .projects import ProjectShim


def has_tile_changed(tile: Tile) -> bool:
    """Downloads the indicated tile from the server and updates the cache. Returns whether it changed."""
    url = f"https://backend.wplace.live/files/s0/tiles/{tile.x}/{tile.y}.png"

    # Check for cached tile and prepare If-Modified-Since header
    cache_path = DIRS.user_cache_path / f"tile-{tile}.png"
    headers = {}
    if cache_path.exists():
        mtime = cache_path.stat().st_mtime
        headers["If-Modified-Since"] = formatdate(mtime, usegmt=True)

    response = requests.get(url, headers=headers, timeout=5)

    # Handle 304 Not Modified
    if response.status_code == 304:
        logger.info(f"Tile {tile}: Not modified (304).")
        return False

    if response.status_code != 200:
        logger.debug(f"Tile {tile}: HTTP {response.status_code}")
        return False
    data = response.content

    # Extract Last-Modified header if present
    last_modified = response.headers.get("Last-Modified")
    if last_modified:
        try:
            last_modified = int(parsedate_to_datetime(last_modified).timestamp())
        except Exception as e:
            logger.warning(f"Tile {tile}: Failed to parse Last-Modified header: {e}")

    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        logger.debug(f"Tile {tile}: image decode failed: {e}")
        return False
    with PALETTE.ensure(img) as paletted:
        if cache_path.exists():
            with Image.open(cache_path) as cached:
                if bytes(cached.tobytes()) == bytes(paletted.tobytes()):
                    logger.info(f"Tile {tile}: No change detected.")
                    return False  # no change
        logger.info(f"Tile {tile}: Change detected, updating cache...")
        paletted.save(cache_path)

        # Set file mtime to match server's Last-Modified timestamp
        if isinstance(last_modified, int):
            try:
                os.utime(cache_path, (last_modified, last_modified))
            except Exception as e:
                logger.debug(f"Tile {tile}: Failed to set mtime: {e}")
    return True


def stitch_tiles(rect: Rectangle) -> Image.Image:
    """Stitches tiles from cache together, exactly covering the given rectangle."""
    image = PALETTE.new(rect.size)
    for tile in rect.tiles:
        cache_path = DIRS.user_cache_path / f"tile-{tile}.png"
        if not cache_path.exists():
            logger.warning(f"{tile}: Tile missing from cache, leaving transparent")
            continue
        with Image.open(cache_path) as tile_image:
            offset = tile.to_point() - rect.point
            image.paste(tile_image, Rectangle.from_point_size(offset, Size(1000, 1000)))
    return image


class TileChecker:
    """Manages round-robin tile checking and tile-to-project indexing."""

    def __init__(self, projects: Iterable[ProjectShim]):
        """Initialize with a mapping of project paths to projects."""
        self.tiles: dict[Tile, set[ProjectShim]] = {}
        self.current_tile_index = 0
        self._build_index(projects)

    def _build_index(self, projects: Iterable[ProjectShim]) -> None:
        """Index tiles to projects for quick lookup."""
        for proj in projects:
            for tile in proj.rect.tiles:
                self.tiles.setdefault(tile, set()).add(proj)
        logger.info(f"Indexed {len(self.tiles)} tiles.")

    def add_project(self, proj: ProjectShim) -> None:
        """Add a project and index its tiles."""
        for tile in proj.rect.tiles:
            self.tiles.setdefault(tile, set()).add(proj)

    def remove_project(self, proj: ProjectShim) -> None:
        """Remove a project and clean up its tiles from the index."""
        for tile in proj.rect.tiles:
            projs = self.tiles.get(tile)
            if projs:
                projs.discard(proj)
                if not projs:
                    del self.tiles[tile]

    def check_next_tile(self) -> None:
        """Check one tile for changes (round-robin) and update affected projects."""
        if not self.tiles:
            return  # No tiles to check

        tiles_list = list(self.tiles.keys())
        # Handle case where tiles were removed and index is now out of bounds
        if self.current_tile_index >= len(tiles_list):
            self.current_tile_index = 0

        tile = tiles_list[self.current_tile_index]
        if has_tile_changed(tile):
            for proj in self.tiles.get(tile) or ():
                proj.run_diff()

        # Advance to next tile for next cycle, wrapping around when we reach the end
        self.current_tile_index = (self.current_tile_index + 1) % len(tiles_list)

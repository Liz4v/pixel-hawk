import io
import os
from email.utils import formatdate, parsedate_to_datetime

import requests
from loguru import logger
from PIL import Image

from . import DIRS
from .geometry import Rectangle, Size, Tile
from .palette import PALETTE


def has_tile_changed(tile: Tile) -> bool:
    """Downloads the indicated tile from the server and updates the cache. Returns whether it changed."""
    url = f"https://backend.wplace.live/files/s0/tiles/{tile.x}/{tile.y}.png"

    # Check for cached tile and prepare If-Modified-Since header
    cache_path = DIRS.user_cache_path / f"tile-{tile}.png"
    headers = {}
    if cache_path.exists():
        try:
            mtime = cache_path.stat().st_mtime
            headers["If-Modified-Since"] = formatdate(mtime, usegmt=True)
            logger.debug(f"Tile {tile}: Sending If-Modified-Since: {headers['If-Modified-Since']}")
        except Exception as e:
            logger.debug(f"Tile {tile}: Failed to read cache mtime: {e}")

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
    logger.debug(f"Tile {tile}: {last_modified=!r}")
    if last_modified:
        try:
            last_modified = int(parsedate_to_datetime(last_modified).timestamp())
        except Exception as e:
            logger.debug(f"Tile {tile}: Failed to parse Last-Modified header: {e}")

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

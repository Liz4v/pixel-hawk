import itertools
import os
import re
from pathlib import Path
from typing import Iterable, NamedTuple

from loguru import logger
from PIL import Image

from . import DIRS
from .geometry import Point, Rectangle, Size
from .palette import PALETTE, ColorNotInPalette

_RE_FILENAME = re.compile(r"^wplace-cached-(\d+)000x(\d+)000-(\d+)_(\d+)_0_0-([-\d]+T[-\d]+Z)\.png$")


class LazyImage:
    """Represents a temporary image that is loaded lazily from disk."""

    def __init__(self, path: Path):
        """Initializes a LazyImage with the given file path."""
        self.path = path
        self._image = None

    @property
    def image(self) -> Image.Image:
        """The image resource, loaded lazily from disk."""
        if self._image is None:
            logger.debug(f"{self.path.name}: Loading image...")
            self._image = Image.open(self.path)
        return self._image

    def __del__(self) -> None:
        """Ensures the image is closed and deleted."""
        if self._image is not None:
            self._image.close()
        self.path.unlink(missing_ok=True)  # delete after use


class FoundTile(NamedTuple):
    """Represents a tile found in the downloads inbox."""

    tile: tuple[int, int]
    source: LazyImage
    offset: Point

    def obtain(self) -> bool:
        """Extracts the tile image from the source image, storing in cache if not fully transparent"""
        rect = Rectangle(self.offset * 1000, Size(1000, 1000))
        with self.source.image.crop(rect.pilbox) as cropped:
            try:
                image = PALETTE.ensure(cropped)
            except ColorNotInPalette as e:
                logger.error(f"Tile {self.tile} contains color not in palette: {e}")
                return False
        with image:
            _, maximum = image.getextrema()
            if maximum == 0:
                return False  # fully transparent
            cache_path = DIRS.user_cache_path / f"tile-{self.tile[0]}_{self.tile[1]}.png"
            logger.info(f"{cache_path.name}: Storing tile {self.tile}")
            image.save(cache_path)
        mtime = self.source.path.stat().st_mtime
        os.utime(cache_path, (mtime, mtime))
        return True


def search_tiles(path: Path | None = None) -> Iterable[FoundTile]:
    """Searches the inbox directory (or given path) for tile images, yielding FoundTile instances."""
    inbox_path = DIRS.user_downloads_path
    found_paths: list[tuple[str, Path, list[str]]] = []
    for path in (path,) if path else inbox_path.iterdir():
        match = _RE_FILENAME.fullmatch(path.name)
        if not match:
            continue
        *numbers, timestamp = match.groups()
        found_paths.append((timestamp, path, numbers))
    # sort by timestamp descending
    found_paths.sort(reverse=True)
    for _, path, numbers in found_paths:
        source = LazyImage(path)
        w, h, x1, y1 = map(int, numbers)
        for x2, y2 in itertools.product(range(w), range(h)):
            tile = (x1 + x2, y1 + y2)
            yield FoundTile(tile=tile, source=source, offset=Point(x2, y2))


def stitch_tiles(rect: Rectangle) -> Image.Image:
    """Stitches tiles from cache together, exactly covering the given rectangle."""
    image = PALETTE.new(rect.size)
    for tile in rect.tiles:
        cache_path = DIRS.user_cache_path / f"tile-{tile[0]}_{tile[1]}.png"
        if not cache_path.exists():
            logger.warning(f"{tile}: Tile missing from cache, leaving transparent")
            continue
        with Image.open(cache_path) as tile_image:
            offset = Point(*tile) * 1000 - rect.point
            image.paste(tile_image, Rectangle(offset, Size(1000, 1000)).pilbox)
    return image

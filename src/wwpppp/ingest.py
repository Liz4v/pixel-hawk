import itertools
import pathlib
import re
import typing

from loguru import logger
from PIL import Image

from .geometry import Point, Rectangle, Size
from .palette import PALETTE
from .settings import DIRS

_RE_FILENAME = re.compile(r"^wplace-cached-(\d+)000x(\d+)000-(\d+)_(\d+)_0_0-([-\d]+T[-\d]+Z)\.png$")


class LazyImage:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self._image = None

    @property
    def image(self) -> Image.Image:
        if self._image is None:
            logger.debug("%s: Loading image...", self.path.name)
            self._image = Image.open(self.path)
        return self._image


class FoundTile(typing.NamedTuple):
    tile: tuple[int, int]
    source: LazyImage
    offset: Point

    def obtain(self) -> bool:
        """Extract the tile image from the source image, storing in cache if not fully transparent"""
        rect = Rectangle(self.offset * 1000, Size(1000, 1000))
        with self.source.image.crop(rect.pilbox()) as cropped:
            image = PALETTE.ensure(cropped)
        with image:
            _, maximum = image.getextrema()
            if maximum == 0:
                return False  # fully transparent
            cache_path = DIRS.user_cache_path / f"tile-{self.tile[0]}_{self.tile[1]}.png"
            logger.info("Storing tile %s to cache %s", self.tile, cache_path.name)
            image.save(cache_path)
        # set mtime to match source file
        mtime = self.source.path.stat().st_mtime
        cache_path.touch(times=(mtime, mtime))
        return True


def search_tiles() -> typing.Iterable[FoundTile]:
    inbox_path = DIRS.user_downloads_path
    found_paths = []
    for path in inbox_path.iterdir():
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
            yield FoundTile(
                tile=tile,
                source=source,
                offset=Point(x2, y2),
            )
        # maybe delete or move the file after processing?
        # path.unlink()


def stitch_tiles(rect: Rectangle) -> Image.Image:
    """Stitch together tiles covering the given rectangle from cache"""
    image = PALETTE.new(rect.size)
    for tile in rect.tiles:
        cache_path = DIRS.user_cache_path / f"tile-{tile[0]}_{tile[1]}.png"
        if not cache_path.exists():
            logger.warning("Tile %s missing from cache, leaving transparent", tile)
            continue
        with Image.open(cache_path) as tile_image:
            offset = Point(
                (tile[0] * 1000) - rect.point.x,
                (tile[1] * 1000) - rect.point.y,
            )
            image.paste(tile_image, offset.pilbox())
    return image

from bisect import bisect_left
from itertools import chain
from pathlib import Path

from loguru import logger
from PIL import Image

_COLORS = """
    FF00FF 000000 3C3C3C 787878 D2D2D2 FFFFFF 600018 ED1C24 FF7F27 F6AA09 F9DD3B FFFABC 0EB968 13E67B 87FF5E 0C816E
    10AEA6 13E1BE 60F7F2 28509E 4093E4 6B50F6 99B1FB 780C99 AA38B9 E09FF9 CB007A EC1F80 F38DA9 684634 95682A F8B277
    AAAAAA A50E1E FA8072 E45C1A 9C8431 C5AD31 E8D45F 4A6B3A 5A944A 84C573 0F799F BBFAF2 7DC7FF 4D31B8 4A4284 7A71C4
    B5AEF1 9B5249 D18078 FAB6A4 DBA463 7B6352 9C846B D6B594 D18051 FFC5A5 6D643F 948C6B CDC59E 333941 6D758D B3B9D1
"""


class Palette:
    def __init__(self, colors: list[bytes]):
        """Initialize the palette with the given list of RGB colors (as bytes). The first color is treated as transparent."""
        self._raw = bytes(chain.from_iterable(colors))
        rgb2pal = {int.from_bytes(c, "big"): i for i, c in enumerate(colors) if i}
        rgb2pal[0x10AE82] = rgb2pal[0x10AEA6]  # wrong teal reported in wplacepaint.com
        self._idx = tuple(sorted(rgb2pal.keys()))
        self._values = bytes(rgb2pal[c] for c in self._idx)

    def open_image(self, path: str | Path) -> Image.Image:
        """Open an image from `path`, convert to this palette if needed, and overwrite the file if converted."""
        image = Image.open(path)
        paletted = self.ensure(image)  # this close `image`
        if image is paletted:
            return image
        logger.info(f"{Path(path).name}: Overwriting with paletted version...")
        paletted.save(path)
        return paletted

    def ensure(self, image: Image.Image) -> Image.Image:
        """Convert `image` to this palette if needed, returning the converted image."""
        if image.mode == "P" and bytes(image.getpalette()) == self._raw:  # type: ignore[attr-defined]
            return image  # no need to convert
        size = image.size
        with _ensure_rgba(image) as rgba:  # `image` will be closed by the end of this block
            data = bytes(map(self.lookup, rgba.getdata()))  # type: ignore[misc]
        image = self.new(size)
        image.putdata(data)
        return image

    def lookup(self, rgba: tuple) -> int:
        """Look up the palette index for the given RGBA color."""
        if rgba[3] == 0:
            return 0
        rgb = (rgba[0] << 16) | (rgba[1] << 8) | rgba[2]
        position = bisect_left(self._idx, rgb)
        if position == len(self._idx) or self._idx[position] != rgb:
            raise ColorNotInPalette(f"#{rgb:06X}")
        return self._values[position]

    def new(self, size: tuple[int, int]) -> Image.Image:
        """Create a new image with this palette and given size."""
        image = Image.new("P", size)
        image.putpalette(self._raw)
        image.info["transparency"] = 0
        return image


def _ensure_rgba(image: Image.Image) -> Image.Image:
    """Ensure the given image is in RGBA mode, converting if needed."""
    if image.mode == "RGBA":
        return image
    with image:
        return image.convert("RGBA")


class ColorNotInPalette(KeyError):
    """Raised when a color is not found in the palette."""


PALETTE = Palette([bytes.fromhex(c) for c in _COLORS.split()])

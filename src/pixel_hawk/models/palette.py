"""WPlace palette enforcement and color conversion.

Defines the official WPlace color palette (not expected to change) and provides
the PALETTE singleton for converting images to paletted mode. The first color
(FF00FF magenta) is treated as transparent for project overlays.

The Palette class validates images against the palette and provides exact color
matching via binary search. Colors not in the palette raise ColorsNotInPalette.

AsyncImage wraps blocking I/O calls to run in a thread on first access. Supports
async context manager (auto-closes) and direct call (caller must close) patterns.

Singleton:
    PALETTE — palette operations (sync methods + async wrappers via aopen_*)
"""

import asyncio
from bisect import bisect_left
from functools import partial
from io import BytesIO
from itertools import chain
from pathlib import Path
from typing import Callable, cast

from loguru import logger
from PIL import Image

# We don't want to deal with too-large images.
Image.MAX_IMAGE_PIXELS = 1_000_0000

# THIS IS THE OFFICIAL WPLACE PALETTE. It is not expected to change.
# The first color (FF00FF) is just my transparency placeholder. I don't plan to change it either.
_COLORS = """
    FF00FF 000000 3C3C3C 787878 D2D2D2 FFFFFF 600018 ED1C24 FF7F27 F6AA09 F9DD3B FFFABC 0EB968 13E67B 87FF5E 0C816E
    10AEA6 13E1BE 60F7F2 28509E 4093E4 6B50F6 99B1FB 780C99 AA38B9 E09FF9 CB007A EC1F80 F38DA9 684634 95682A F8B277
    AAAAAA A50E1E FA8072 E45C1A 9C8431 C5AD31 E8D45F 4A6B3A 5A944A 84C573 0F799F BBFAF2 7DC7FF 4D31B8 4A4284 7A71C4
    B5AEF1 9B5249 D18078 FAB6A4 DBA463 7B6352 9C846B D6B594 D18051 FFC5A5 6D643F 948C6B CDC59E 333941 6D758D B3B9D1
"""

RGBATuple = tuple[int, int, int, int]


class Palette:
    """Synchronous palette enforcement for WPlace images.

    Converts images to the official WPlace palette using exact color matching
    via binary search. The first palette entry is treated as transparent.
    """

    def __init__(self, colors: list[bytes]):
        """Initialize with a list of 3-byte RGB colors. The first color is treated as transparent."""
        self._raw = bytes(chain.from_iterable(colors))
        rgb2pal = {int.from_bytes(c, "big"): i for i, c in enumerate(colors) if i}
        rgb2pal[0x10AE82] = rgb2pal[0x10AEA6]  # wrong teal reported in wplacepaint.com
        self._idx = tuple(sorted(rgb2pal.keys()))
        self._values = bytes(rgb2pal[c] for c in self._idx)

    def aopen_file(self, path: str | Path) -> AsyncImage[Image.Image]:
        """Return an AsyncImage that will open and palette-convert the file at `path`."""
        return AsyncImage(self.open_file, path)

    def aopen_bytes(self, payload: bytes) -> AsyncImage[Image.Image]:
        """Return an AsyncImage that will open and palette-convert `payload` bytes."""
        return AsyncImage(self.open_bytes, payload)

    def open_file(self, path: str | Path) -> Image.Image:
        """Open an image from `path`, convert to this palette if needed, and overwrite the file if converted.
        Returns an open Image that the caller must close (use with statement or explicit close)."""
        image = Image.open(path)
        paletted = self.ensure(image)  # Closes `image` and returns new image if conversion needed
        if image is paletted:  # If same object, no conversion happened
            return image  # Original image still open, caller must close
        # Conversion happened: original `image` was closed, `paletted` is new image
        logger.info(f"{Path(path).name}: Overwriting with paletted version...")
        paletted.save(path)
        return paletted  # New image still open, caller must close

    def open_bytes(self, payload: bytes) -> Image.Image:
        """Open an image from bytes and convert to this palette if needed.
        Returns an open Image that the caller must close (use with statement or explicit close)."""
        image = Image.open(BytesIO(payload))
        paletted = self.ensure(image)  # Closes `image` and returns new image if conversion needed
        return paletted  # Caller must close

    def ensure(self, image: Image.Image) -> Image.Image:
        """Convert `image` to this palette if needed, returning the converted image.

        Ownership semantics:
        - If no conversion needed: returns `image` unchanged (caller still owns it)
        - If conversion needed: closes `image` and returns a new Image (caller owns new image)
        """
        if image.mode == "P":
            palette = image.getpalette()
            assert palette is not None, "Paletted image must have a palette"
            if bytes(palette) == self._raw:
                return image  # Already correct palette, return as-is (still caller's responsibility to close)
        size = image.size
        colors_not_in_palette: dict[int, int] = {}
        with _ensure_rgba(image) as rgba:  # Closes input `image` at end of this block
            flattened = cast(tuple[RGBATuple, ...], rgba.get_flattened_data())
            data = bytes(map(partial(self.lookup, colors_not_in_palette), flattened))
        if colors_not_in_palette:
            raise ColorsNotInPalette(colors_not_in_palette)
        # Input image now closed, create new paletted image
        image = self.new(size)
        image.putdata(data)
        return image  # Return new image (caller must close)

    def lookup(self, colors_not_in_palette: dict[int, int], rgba: RGBATuple) -> int:
        """Look up the palette index for an RGBA color via binary search.

        Transparent pixels (alpha == 0) return index 0. Unknown colors are recorded
        in `colors_not_in_palette` (rgb -> count) and also return 0.
        """
        if rgba[3] == 0:
            return 0
        rgb = (rgba[0] << 16) | (rgba[1] << 8) | rgba[2]
        position = bisect_left(self._idx, rgb)
        if position < len(self._idx) and self._idx[position] == rgb:
            return self._values[position]  # exact match
        # mismatch! :( build error report
        colors_not_in_palette[rgb] = colors_not_in_palette.get(rgb, 0) + 1
        return 0

    def new(self, size: tuple[int, int]) -> Image.Image:
        """Create a new image with this palette and given size."""
        image = Image.new("P", size)
        image.putpalette(self._raw)
        image.info["transparency"] = 0
        return image


def _ensure_rgba(image: Image.Image) -> Image.Image:
    """Ensure the given image is in RGBA mode, converting if needed.

    If conversion is needed, the original image is closed and a new RGBA image is returned.
    """
    if image.mode == "RGBA":
        return image
    with image:
        return image.convert("RGBA")


class ColorsNotInPalette(ValueError):
    """Raised when there are colors not found in the palette."""

    def __init__(self, report: dict[int, int]) -> None:
        detail = f"{len(report)} colors" if len(report) > 5 else ", ".join(f"#{i:06x}" for i in report.keys())
        super().__init__(f"Found {sum(report.values())} pixels not in the palette ({detail})")


_MISSING = object()


class AsyncImage[T]:
    """Deferred async handle for a blocking I/O call, run in a thread on first access.

    Supports two usage patterns:

    As an async context manager (auto-closes the result if it has a close method)::

        async with handle as image:
            ...

    As a direct awaitable (caller must close)::

        image = await handle()

    The callable may return None (e.g. for optional snapshots), which is preserved
    as a legitimate result distinct from "not yet loaded".
    """

    def __init__(self, function: Callable[..., T], *args, **kwargs) -> None:
        self.callable = partial(function, *args, **kwargs)
        self._result = _MISSING

    async def __call__(self) -> T:
        """Run the blocking operation in a thread, returning the result.

        The result is cached — repeated calls return the same instance.
        """
        if self._result is _MISSING:
            self._result = await asyncio.to_thread(self.callable)
        return cast(T, self._result)

    async def __aenter__(self):
        """Enter the async context, loading the result if not already loaded."""
        return await self()

    async def __aexit__(self, *_) -> None:
        """Exit the async context, closing the result if it has a close method."""
        getattr(self._result, "close", lambda: None)()


PALETTE = Palette([bytes.fromhex(c) for c in _COLORS.split()])

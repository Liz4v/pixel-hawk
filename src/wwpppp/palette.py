import itertools
import pathlib

from loguru import logger
from PIL import Image

from .geometry import Size

_COLORS = """
    FF00FF 000000 3C3C3C 787878 D2D2D2 FFFFFF 600018 ED1C24 FF7F27 F6AA09 F9DD3B FFFABC 0EB968 13E67B 87FF5E 0C816E
    10AEA6 13E1BE 60F7F2 28509E 4093E4 6B50F6 99B1FB 780C99 AA38B9 E09FF9 CB007A EC1F80 F38DA9 684634 95682A F8B277
    AAAAAA A50E1E FA8072 E45C1A 9C8431 C5AD31 E8D45F 4A6B3A 5A944A 84C573 0F799F BBFAF2 7DC7FF 4D31B8 4A4284 7A71C4
    B5AEF1 9B5249 D18078 FAB6A4 DBA463 7B6352 9C846B D6B594 D18051 FFC5A5 6D643F 948C6B CDC59E 333941 6D758D B3B9D1
"""


class Palette:
    def __init__(self, colors: list[bytes]):
        self.raw = bytes(itertools.chain.from_iterable(colors))
        self.dict = {(*c, 255): i for i, c in enumerate(colors) if i}

        self.image = Image.new("P", (1, 1))
        self.image.putpalette(self.raw)

    def open_image(self, path: str | pathlib.Path) -> Image.Image | None:
        image = Image.open(path)
        paletted = self.ensure(image)
        if image is paletted or image is None:
            return image
        logger.info("Overwriting %s with paletted version...", path.name)
        image.close()
        paletted.save(path)
        return paletted

    def ensure(self, image: Image.Image) -> Image.Image | None:
        if image.mode == "P" and bytes(image.getpalette()) == self.raw:
            return image
        with image.convert("RGBA") as rgba:
            image.close()
            try:
                data = bytes(0 if rgba[3] == 0 else self.dict[rgba] for rgba in rgba.getdata())
            except KeyError:
                return None  # contains colors not in palette
        paletted = self.new(image.size)
        paletted.putdata(data)
        return paletted

    def new(self, size: Size) -> Image.Image:
        image = Image.new("P", size)
        image.putpalette(self.raw)
        image.info["transparency"] = 0
        return image


PALETTE = Palette([bytes.fromhex(c) for c in _COLORS.split()])

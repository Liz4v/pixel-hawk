import pathlib
import re
import typing

from loguru import logger

from .geometry import Point, Rectangle, Size
from .palette import PALETTE
from .settings import DIRS

_RE_HAS_COORDS = re.compile(r"[- _](\d+)[- _](\d+)[- _](\d+)[- _](\d+)\.png$", flags=re.IGNORECASE)


class Project:
    def __init__(self, path: pathlib.Path, rect: Rectangle):
        self.path = path
        self.rect = rect

    @classmethod
    def try_open(cls, path: pathlib.Path) -> typing.Self | None:
        match = _RE_HAS_COORDS.search(path.name)
        if not match:
            return None  # no coords or already excluded
        tx, ty, px, py = map(int, match.groups())
        point = Point.from4(tx, ty, px, py)

        image = PALETTE.open_image(path)
        if image is None:
            logger.warning("%s: Colors not in palette", path.name)
            path.rename(path.with_suffix(".invalid.png"))
            return None
        size = Size(*image.size)
        image.close()  # we'll reopen later if needed

        rect = Rectangle(point, size)
        return cls(path, rect)


def get_project_paths() -> typing.Generator[Project]:
    dirpath = DIRS.user_pictures_path / "wplace"
    dirpath.mkdir(parents=True, exist_ok=True)
    logger.info("Searching for projects in %s", dirpath)
    return filter(None, map(Project.try_open, dirpath.iterdir()))

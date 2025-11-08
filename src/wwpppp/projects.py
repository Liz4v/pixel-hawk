import pathlib
import pickle
import re
import sqlite3
import typing

from loguru import logger

from .geometry import Point, Rectangle, Size
from .palette import PALETTE
from .settings import DIRS

PROJ_PATH = DIRS.user_pictures_path / "wplace"
_RE_HAS_COORDS = re.compile(r"[- _](\d+)[- _](\d+)[- _](\d+)[- _](\d+)\.png$", flags=re.IGNORECASE)


class Project:
    def __init__(self, path: pathlib.Path, rect: Rectangle):
        self.path = path
        self.rect = rect
        self._image = None

    @property
    def image(self):
        if self._image is None:
            self._image = PALETTE.open_image(self.path)
        return self._image

    @classmethod
    def try_open(cls, path: pathlib.Path) -> "Project" | None:
        cached = DISK_CACHE.get(path.name)
        if cached:
            try:
                return cls(path, *cached)
            except TypeError:
                logger.warning("%s: Cache data invalid, reprocessing", path.name)

        match = _RE_HAS_COORDS.search(path.name)
        if not match:
            return None  # no coords or already excluded
        point = Point.from4(*map(int, match.groups()))

        image = PALETTE.open_image(path)
        if image is None:
            logger.warning("%s: Colors not in palette", path.name)
            path.rename(path.with_suffix(".invalid.png"))
            return None
        size = Size(*image.size)
        image.close()  # we'll reopen later if needed

        rect = Rectangle(point, size)
        return cls(path, *DISK_CACHE.set(path.name, rect))


def get_project_paths() -> typing.Generator[Project]:
    PROJ_PATH.mkdir(parents=True, exist_ok=True)
    logger.info("Searching for projects in %s", PROJ_PATH)
    return filter(None, map(Project.try_open, PROJ_PATH.iterdir()))


class DiskCache:
    def __init__(self):
        self.cache_file = PROJ_PATH / "cache.sqlite"
        self.db = sqlite3.connect(self.cache_file)
        self._check_table_exists()

    def _check_table_exists(self):
        cursor = self.db.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS cache (filename TEXT, contents BLOB, PRIMARY KEY (filename))")
        self.db.commit()

    def get(self, key: str) -> dict | None:
        cursor = self.db.cursor()
        cursor.execute("SELECT contents FROM cache WHERE filename = ?", (key,))
        row = cursor.fetchone()
        if row:
            return pickle.loads(row[0])

    def set(self, key: str, *args) -> tuple:
        cursor = self.db.cursor()
        cursor.execute("REPLACE INTO cache (filename, contents) VALUES (?, ?)", (key, pickle.dumps(args)))
        self.db.commit()
        return args


DISK_CACHE = DiskCache()

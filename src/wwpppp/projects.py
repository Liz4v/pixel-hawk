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
    @classmethod
    def iter(cls) -> typing.Iterable["Project"]:
        PROJ_PATH.mkdir(parents=True, exist_ok=True)
        logger.info("Searching for projects in %s", PROJ_PATH)
        return filter(None, map(Project.try_open, PROJ_PATH.iterdir()))

    @classmethod
    def try_open(cls, path: pathlib.Path) -> "Project" | None:
        cached = CachedProjectMetadata(path)
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
        return cls(path, *cached(rect))

    def __init__(self, path: pathlib.Path, rect: Rectangle):
        self.path = path
        self.rect = rect
        self._image = None

    @property
    def image(self):
        if self._image is None:
            self._image = PALETTE.open_image(self.path)
        return self._image


class CachedProjectMetadata(list):
    _db = None

    @classmethod
    def _cursor(cls):
        if cls._db is None:
            cls._db = sqlite3.connect(PROJ_PATH / "projects.db")
            cursor = cls._db.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    filename TEXT,
                    mtime INT,
                    contents BLOB,
                    PRIMARY KEY (filename)
                )
            """)
            cls._db.commit()
        return cls._db.cursor()

    def __init__(self, path: pathlib.Path):
        self.key = path.name
        self.check = int(path.stat().st_mtime)
        cursor = self._cursor()
        cursor.execute(
            "SELECT contents FROM cache WHERE filename = ? AND mtime = ?",
            (self.key, self.check),
        )
        row = cursor.fetchone()
        super().__init__(pickle.loads(row[0]) if row else ())

    def __call__(self, *args) -> tuple:
        cursor = self._cursor()
        cursor.execute(
            "REPLACE INTO cache (filename, mtime, contents) VALUES (?, ?, ?)",
            (self.key, self.check, pickle.dumps(args)),
        )
        self._db.commit()
        return args

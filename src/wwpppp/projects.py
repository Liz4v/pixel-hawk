import pickle
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger
from PIL import Image

from . import DIRS
from .geometry import Point, Rectangle, Size
from .ingest import stitch_tiles
from .palette import PALETTE

_RE_HAS_COORDS = re.compile(r"[- _](\d+)[- _](\d+)[- _](\d+)[- _](\d+)\.png$", flags=re.IGNORECASE)


class Project:
    @classmethod
    def iter(cls) -> set["Project"]:
        path = DIRS.user_pictures_path / "wplace"
        path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Searching for projects in {path}")
        maybe_projects = (cls.try_open(p) for p in sorted(path.iterdir()))
        return filter(None, maybe_projects)

    @classmethod
    def try_open(cls, path: Path) -> Optional["Project"]:
        match = _RE_HAS_COORDS.search(path.name)
        if not match or not path.is_file():
            return None  # no coords or otherwise invalid/irrelevant

        cached = CachedProjectMetadata(path)
        if cached:
            try:
                return cls(path, *cached)
            except TypeError:
                logger.warning(f"{path.name}: Cache data invalid, reprocessing")

        image = PALETTE.open_image(path)
        if image is None:
            logger.warning(f"{path.name}: Colors not in palette")
            path.rename(path.with_suffix(".invalid.png"))
            return None
        rect = Rectangle(Point.from4(*map(int, match.groups())), Size(*image.size))
        image.close()  # we'll reopen later if needed

        logger.info(f"{path.name}: Detected project at {rect}")

        new = cls(path, *cached(rect))
        new.compare_with_current()
        return new

    def __init__(self, path: Path, rect: Rectangle):
        self.path = path
        self.rect = rect
        self._image = None

    def __eq__(self, other) -> bool:
        return self.path == getattr(other, "path", ...)

    def __hash__(self):
        return hash(self.path)

    @property
    def image(self) -> Image.Image:
        if self._image is None:
            self._image = PALETTE.open_image(self.path)
        return self._image

    @image.deleter
    def image(self) -> None:
        if self._image is not None:
            self._image.close()
            self._image = None

    def __del__(self):
        del self.image

    def compare_with_current(self) -> None:
        """Compare each pixel between both images. It will generate a new image only with the differences."""
        target_data = self.image.getdata()
        with stitch_tiles(self.rect) as current:
            newdata = map(pixel_compare, current.getdata(), target_data)
            remaining_data, fix_data = map(bytes, zip(*newdata))

        fix_path = self.path.with_suffix(".fix.png")
        remaining_path = self.path.with_suffix(".remaining.png")

        if remaining_data == target_data:
            return  # project is not started, no need for diffs

        if max(remaining_data) == 0:
            logger.info(f"{self.path.name}: No remaining pixels, project is complete and ungriefed.")
            remaining_path.unlink(missing_ok=True)
            fix_path.unlink(missing_ok=True)
            return
        self._save_diff(remaining_path, remaining_data)

        fix_path = self.path.with_suffix(".fix.png")
        if fix_data == remaining_data or max(fix_data) == 0:
            fix_path.unlink(missing_ok=True)
            return
        self._save_diff(fix_path, fix_data)

    def _save_diff(self, path: Path, data: bytes) -> None:
        with PALETTE.new(self.rect.size) as diff_image:
            diff_image.putdata(data)
            diff_image.save(path)
        opaque = sum(1 for v in data if v)
        percentage = opaque * 100 / len(data)
        time_to_go = timedelta(seconds=27) * opaque
        days, hours = divmod(round(time_to_go.total_seconds() / 3600), 24)
        when = (datetime.now() + time_to_go).strftime("%b %d %H:%M")
        logger.info(f"{path.name}: Saved diff ({opaque}px, {percentage:.2f}%, {days}d{hours}h to {when}).")

    def forget(self) -> None:
        cache = CachedProjectMetadata(self.path)
        cache.forget()


def pixel_compare(current: int, desired: int) -> tuple[int, int]:
    """Returns a tuple of (remaining, fix) pixel values."""
    return (0, 0) if desired == current else (desired, current and desired)


class CachedProjectMetadata(list):
    _db = None

    @classmethod
    def _cursor(cls):
        if cls._db is None:
            cls._db = sqlite3.connect(DIRS.user_cache_path / "projects.db")
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

    def __init__(self, path: Path):
        self.key = path.name
        try:
            self.check = int(path.stat().st_mtime)
        except FileNotFoundError:
            self.check = 0  # signal missing file
        super().__init__(self._load())

    def _load(self) -> list:
        cursor = self._cursor()
        cursor.execute("SELECT mtime, contents FROM cache WHERE filename = ? ", (self.key,))
        check, blob = cursor.fetchone() or (None,None)
        if check != self.check:
            return []
        return pickle.loads(blob)

    def __call__(self, *args) -> tuple:
        cursor = self._cursor()
        cursor.execute(
            "REPLACE INTO cache (filename, mtime, contents) VALUES (?, ?, ?)",
            (self.key, self.check, pickle.dumps(args)),
        )
        self._db.commit()
        return args

    def forget(self) -> None:
        cursor = self._cursor()
        cursor.execute("DELETE FROM cache WHERE filename = ?", (self.key,))
        self._db.commit()

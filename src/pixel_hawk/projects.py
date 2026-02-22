"""Project loading, validation, and diff computation.

Loads projects from ProjectInfo database records. Project PNG files must have
coordinate-only filenames (format: <tx>_<ty>_<px>_<py>.png) and use the WPlace palette.

The Project class orchestrates diff computation by:
- Loading target project images and stitching current canvas tiles
- Comparing current state against previous snapshots to detect progress/regress
- Delegating pixel counting and statistical calculations to ProjectInfo
- Persisting project info to SQLite via Tortoise ORM
- Saving PNG snapshots to the snapshots directory
- Logging detailed progress reports with completion estimates

Projects are loaded from database, not discovered from filesystem.
Pixel-level comparison and metadata update logic lives in metadata.py.
"""

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from . import metadata
from .config import get_config
from .geometry import Rectangle, Size
from .models import HistoryChange, ProjectInfo
from .palette import PALETTE, AsyncImage, ColorsNotInPalette

if TYPE_CHECKING:
    from PIL import Image


class Project:
    """Represents a wplace project stored on disk that has been validated."""

    def __init__(self, info: ProjectInfo):
        """Represents a wplace project validated from a ProjectInfo record.

        Derives path and rect from info. Requires info.owner to be prefetched.
        """
        self.info = info
        self.rect = info.rectangle
        self.path = get_config().projects_dir / str(info.owner.id) / info.filename
        try:
            self.mtime = round(self.path.stat().st_mtime)
        except OSError:
            self.mtime = 0

    def has_been_modified(self) -> bool:
        """Check if the file has been modified since it was loaded."""
        try:
            current_mtime = round(self.path.stat().st_mtime)
            return current_mtime != self.mtime
        except OSError:
            return self.mtime != 0

    @classmethod
    async def from_info(cls, info: ProjectInfo) -> Project | None:
        """Load a project from ProjectInfo record. Returns None if file missing or invalid."""
        # Construct path from owner ID and filename
        # Note: owner should be prefetched before calling this method
        path = get_config().projects_dir / str(info.owner.id) / info.filename

        try:
            async with PALETTE.aopen_file(path) as image:
                size = Size(*image.size)
        except FileNotFoundError:
            # File missing - log warning but don't fail
            await info.fetch_related("owner")
            logger.warning(f"{info.owner.name}/{info.name}: File not found at {path}")
            return None
        except ColorsNotInPalette as e:
            await info.fetch_related("owner")
            logger.error(f"{info.owner.name}/{info.name}: Invalid palette: {e}")
            return None

        rect = info.rectangle
        # Verify size matches database record
        if rect.size != size:
            await info.fetch_related("owner")
            logger.error(f"{info.owner.name}/{info.name}: Size mismatch - DB says {rect.size}, file is {size}")
            return None

        new = cls(info)
        await new.run_diff()
        return new

    def __eq__(self, other) -> bool:
        return self.path == getattr(other, "path", ...)

    def __hash__(self):
        return hash(self.path)

    @property
    def snapshot_path(self) -> Path:
        """Path to the snapshot file for this project.

        Uses same subfolder structure as projects: snapshots/{owner_id}/{filename}.
        """
        return get_config().snapshots_dir / str(self.info.owner.id) / self.info.filename

    async def save_snapshot(self, image) -> None:
        """Save current canvas snapshot to disk."""
        try:
            # Ensure person subdirectory exists
            self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(image.save, self.snapshot_path)
            self.info.last_snapshot = round(time.time())
        except Exception as e:
            logger.error(f"Failed to save snapshot for {self.path.name}: {e}")

    def load_snapshot_if_exists(self) -> AsyncImage:
        """Return an AsyncImage that loads the previous snapshot, or yields None if absent."""

        def _load() -> Image.Image | None:
            if not self.snapshot_path.exists():
                return None
            try:
                return PALETTE.open_file(self.snapshot_path)
            except Exception as e:
                logger.warning(f"Failed to load snapshot for {self.path.name}: {e}")
                return None

        return AsyncImage(_load)

    async def run_diff(self) -> HistoryChange:
        """Compares current canvas against project target and previous snapshot.

        Tracks progress (pixels placed toward goal) and regress (pixels removed/griefed),
        updates info with completion history, saves snapshot and persists to DB.
        Returns the HistoryChange record (saved only when progress or regress occurred).
        """
        # If any tiles have been missing from cache, maybe they just arrived.
        if self.info.has_missing_tiles:
            self.info.has_missing_tiles = self._has_missing_tiles()

        # Load target project image
        async with PALETTE.aopen_file(self.path) as target:
            target_data = get_flattened_data(target)

        # Load previous snapshot before overwriting
        async with self.load_snapshot_if_exists() as previous_snapshot:
            prev_data = get_flattened_data(previous_snapshot) if previous_snapshot else b""

        # Stitch current canvas state
        with await stitch_tiles(self.rect) as current:
            current_data = get_flattened_data(current)
            await self.save_snapshot(current)

        # Process diff: count, compare, update info, build log message, create history record
        change = metadata.process_diff(self.info, current_data, target_data, prev_data)
        if change.progress_pixels or change.regress_pixels:
            await change.save()

        # Log and save
        logger.info(self.info.last_log_message)
        await self.info.save()
        return change

    async def run_nochange(self) -> None:
        self.info.last_check = round(time.time())
        await self.info.save()

    def _has_missing_tiles(self) -> bool:
        """Check if any tiles required by this project are missing from cache."""
        for tile in self.rect.tiles:
            tile_path = get_config().tiles_dir / f"tile-{tile}.png"
            if not tile_path.exists():
                return True
        return False


def get_flattened_data(image: Image.Image) -> bytes:
    target_flattened = image.get_flattened_data()
    assert target_flattened is not None, "Image must have data"
    return bytes(target_flattened)  # type: ignore[arg-type]


async def count_cached_tiles(rect: Rectangle) -> tuple[int, int]:
    """Count how many of a rectangle's tiles exist in the cache directory.

    Returns (cached, total) counts.
    """
    base_path = get_config().tiles_dir

    def _count() -> tuple[int, int]:
        tiles = list(rect.tiles)
        cached = sum(1 for t in tiles if (base_path / f"tile-{t}.png").exists())
        return cached, len(tiles)

    return await asyncio.to_thread(_count)


async def stitch_tiles(rect: Rectangle) -> Image.Image:
    """Stitches tiles from cache together, exactly covering the given rectangle."""
    image = PALETTE.new(rect.size)
    base_path = get_config().tiles_dir
    for tile in rect.tiles:
        cache_path = base_path / f"tile-{tile}.png"
        if not cache_path.exists():
            logger.debug(f"{tile}: Tile missing from cache, leaving transparent")
            continue
        async with PALETTE.aopen_file(cache_path) as tile_image:
            offset = tile.to_point() - rect.point
            image.paste(tile_image, Rectangle.from_point_size(offset, Size(1000, 1000)))
    return image

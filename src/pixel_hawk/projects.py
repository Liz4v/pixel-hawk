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
from .geometry import Rectangle, Size, Tile
from .ingest import stitch_tiles
from .models import DiffStatus, ProjectInfo
from .palette import PALETTE, AsyncImage, ColorsNotInPalette

if TYPE_CHECKING:
    from PIL import Image


class Project:
    """Represents a wplace project stored on disk that has been validated."""

    def __init__(self, path: Path, rect: Rectangle, info: ProjectInfo):
        """Represents a wplace project stored at `path`, covering the area defined by `rect`."""
        self.path = path
        self.rect = rect
        self.mtime = 0
        try:
            self.mtime = round(path.stat().st_mtime)
        except OSError:
            pass
        self.info = info

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

        new = cls(path, rect, info)
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

    async def run_diff(self, changed_tile: Tile | None = None) -> None:
        """Compares current canvas against project target and previous snapshot.

        Args:
            changed_tile: The specific tile that changed (if known), for efficient metadata updates

        Tracks progress (pixels placed toward goal) and regress (pixels removed/griefed),
        updates info with completion history, saves snapshot and persists to DB.
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

        # Update tile metadata if a specific tile changed
        if changed_tile is not None and change.status == DiffStatus.IN_PROGRESS:
            self._update_single_tile_metadata(changed_tile)
        elif change.status == DiffStatus.IN_PROGRESS:
            self._update_tile_metadata()

        # Log and save
        logger.info(self.info.last_log_message)
        await self.info.save()

    async def run_nochange(self) -> None:
        self.info.last_check = round(time.time())
        metadata.prune_old_tile_updates(self.info)  # regular cleanup task
        await self.info.save()

    def _update_single_tile_metadata(self, tile: Tile) -> None:
        """Update metadata for a single tile that changed."""
        tile_path = get_config().tiles_dir / f"tile-{tile}.png"
        if tile_path.exists():
            mtime = round(tile_path.stat().st_mtime)
            tile_str = str(tile)

            last_update = self.info.tile_last_update.get(tile_str, 0)
            if mtime > last_update:
                metadata.update_tile(self.info, tile, mtime)

    def _update_tile_metadata(self) -> None:
        """Update tile modification times from cached tile files."""
        metadata.prune_old_tile_updates(self.info)

        for tile in self.rect.tiles:
            tile_path = get_config().tiles_dir / f"tile-{tile}.png"
            if tile_path.exists():
                mtime = round(tile_path.stat().st_mtime)
                tile_str = str(tile)

                last_update = self.info.tile_last_update.get(tile_str, 0)
                if mtime > last_update:
                    metadata.update_tile(self.info, tile, mtime)

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

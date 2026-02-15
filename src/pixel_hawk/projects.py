"""Project discovery, parsing, validation, and diff computation.

Scans get_config().projects_dir for PNG files with coordinate information
encoded in the filename (format: *_x_y_w_h.png). Valid project images must use
the WPlace palette and are cached in memory with their metadata.

The Project class orchestrates diff computation by:
- Loading target project images and stitching current canvas tiles
- Comparing current state against previous snapshots to detect progress/regress
- Delegating pixel counting and statistical calculations to ProjectMetadata
- Persisting YAML metadata and PNG snapshots adjacent to project files
- Logging detailed progress reports with completion estimates

Invalid files are moved to get_config().rejected_dir to avoid repeated parsing.
Pixel-level comparison and metadata update logic lives in metadata.py.
"""

import asyncio
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from ruamel.yaml import YAML

from .config import get_config
from .geometry import Point, Rectangle, Size, Tile
from .ingest import stitch_tiles
from .metadata import DiffStatus, ProjectMetadata
from .palette import PALETTE, AsyncImage, ColorsNotInPalette

if TYPE_CHECKING:
    from PIL import Image

_RE_HAS_COORDS = re.compile(r"[- _](\d+)[- _](\d+)[- _](\d+)[- _](\d+)\.png$", flags=re.IGNORECASE)

yaml = YAML(typ="safe")
yaml.default_flow_style = False
yaml.width = 120


class Project:
    """Represents a wplace project stored on disk that has been validated."""

    def __init__(self, path: Path, rect: Rectangle):
        """Represents a wplace project stored at `path`, covering the area defined by `rect`."""
        self.path = path
        self.rect = rect
        self.mtime = 0
        try:
            self.mtime = round(path.stat().st_mtime)
        except OSError:
            pass
        self.metadata = self.load_metadata()

    def has_been_modified(self) -> bool:
        """Check if the file has been modified since it was loaded."""
        try:
            current_mtime = round(self.path.stat().st_mtime)
            return current_mtime != self.mtime
        except OSError:
            return self.mtime != 0

    @classmethod
    async def iter(cls) -> list[Project]:
        """Returns all valid projects found in the projects directory."""
        path = get_config().projects_dir
        logger.info(f"Searching for projects in {path}")
        items = await asyncio.to_thread(lambda: sorted(path.iterdir()))
        results = []
        for f in items:
            p = await cls.try_open(f)
            if p is not None:
                results.append(p)
        return results

    @classmethod
    async def scan_directory(cls) -> set[Path]:
        """Returns the set of PNG files in the projects directory."""
        path = get_config().projects_dir
        return await asyncio.to_thread(lambda: {p for p in path.glob("*.png") if p.is_file()})

    @classmethod
    def _reject(cls, path: Path, reason: str) -> None:
        """Move a file to the rejected directory."""
        dest = get_config().rejected_dir / path.name
        logger.warning(f"{path.name}: Rejected ({reason}), moving to {dest}")
        path.rename(dest)

    @classmethod
    async def try_open(cls, path: Path) -> Project | None:
        """Attempts to open a project from the given path. Returns None if invalid."""

        match = _RE_HAS_COORDS.search(path.name)
        if not match or not path.is_file():
            if path.is_file():
                cls._reject(path, "no coordinates in filename")
            return None

        try:
            # Convert now, but close immediately. We'll reopen later as needed.
            async with PALETTE.aopen_file(path) as image:
                size = Size(*image.size)
        except ColorsNotInPalette as e:
            cls._reject(path, str(e))
            return None
        rect = Rectangle.from_point_size(Point.from4(*map(int, match.groups())), size)

        logger.info(f"{path.name}: Detected project at {rect}")

        new = cls(path, rect)
        await new.run_diff()
        return new

    def __eq__(self, other) -> bool:
        return self.path == getattr(other, "path", ...)

    def __hash__(self):
        return hash(self.path)

    @property
    def snapshot_path(self) -> Path:
        """Path to the snapshot file for this project."""
        # project_123_456_789_012.png -> project_123_456_789_012.snapshot.png
        return get_config().snapshots_dir / self.path.name.replace(".png", ".snapshot.png")

    @property
    def metadata_path(self) -> Path:
        """Path to the metadata YAML file for this project."""
        # project_123_456_789_012.png -> project_123_456_789_012.metadata.yaml
        return get_config().metadata_dir / self.path.name.replace(".png", ".metadata.yaml")

    def load_metadata(self) -> ProjectMetadata:
        """Load metadata from YAML file, or create new if file doesn't exist."""
        if not self.metadata_path.exists():
            return ProjectMetadata.from_rect(self.rect, self.path.with_suffix("").name)

        try:
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                data = yaml.load(f)
            return ProjectMetadata.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load metadata for {self.path.name}: {e}. Creating new.")
            return ProjectMetadata.from_rect(self.rect, self.path.with_suffix("").name)

    def save_metadata(self) -> None:
        """Save metadata to YAML file."""
        try:
            with open(self.metadata_path, "w", encoding="utf-8") as f:
                yaml.dump(self.metadata.to_dict(), f)
        except Exception as e:
            logger.error(f"Failed to save metadata for {self.path.name}: {e}")

    async def save_snapshot(self, image) -> None:
        """Save current canvas snapshot to disk."""
        try:
            await asyncio.to_thread(image.save, self.snapshot_path)
            self.metadata.last_snapshot = round(time.time())
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
        updates metadata with completion history, saves snapshot and metadata.
        """
        # If any tiles have been missing from cache, maybe they just arrived.
        if self.metadata.has_missing_tiles:
            self.metadata.has_missing_tiles = self._has_missing_tiles()

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

        # Process diff: count, compare, update metadata, build log message
        result = self.metadata.process_diff(current_data, target_data, prev_data)

        # Update tile metadata if a specific tile changed
        if changed_tile is not None and result.status == DiffStatus.IN_PROGRESS:
            self._update_single_tile_metadata(changed_tile)
        elif result.status == DiffStatus.IN_PROGRESS:
            # Fallback: check all tiles in project area (less efficient)
            self._update_tile_metadata()

        # Log and save
        logger.info(self.metadata.last_log_message)
        self.save_metadata()

    async def run_nochange(self) -> None:
        self.metadata.last_check = round(time.time())
        self.metadata.prune_old_tile_updates()  # regular cleanup task
        self.save_metadata()

    def _update_single_tile_metadata(self, tile: Tile) -> None:
        """Update metadata for a single tile that changed."""
        tile_path = get_config().tiles_dir / f"tile-{tile}.png"
        if tile_path.exists():
            mtime = round(tile_path.stat().st_mtime)
            tile_str = str(tile)

            # Check if this tile has been updated since last check
            last_update = self.metadata.tile_last_update.get(tile_str, 0)
            if mtime > last_update:
                self.metadata.update_tile(tile, mtime)

    def _update_tile_metadata(self) -> None:
        """Update tile modification times from cached tile files."""
        # Prune old 24h entries
        self.metadata.prune_old_tile_updates()

        # Check each tile in project area
        for tile in self.rect.tiles:
            tile_path = get_config().tiles_dir / f"tile-{tile}.png"
            if tile_path.exists():
                mtime = round(tile_path.stat().st_mtime)
                tile_str = str(tile)

                # Check if this tile has been updated since last check
                last_update = self.metadata.tile_last_update.get(tile_str, 0)
                if mtime > last_update:
                    self.metadata.update_tile(tile, mtime)

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

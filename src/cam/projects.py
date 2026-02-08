"""Project discovery, parsing, validation, and diff computation.

Scans DIRS.user_pictures_path / 'wplace' for PNG files with coordinate information
encoded in the filename (format: *_x_y_w_h.png). Valid project images must use
the WPlace palette and are cached in memory with their metadata.

The Project class orchestrates diff computation by:
- Loading target project images and stitching current canvas tiles
- Comparing current state against previous snapshots to detect progress/regress
- Delegating pixel counting and statistical calculations to ProjectMetadata
- Persisting YAML metadata and PNG snapshots adjacent to project files
- Logging detailed progress reports with completion estimates

Invalid files are represented as ProjectShim instances to avoid repeated parsing.
Pixel-level comparison and metadata update logic lives in metadata.py.
"""

import re
import time
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, ContextManager, Iterable

from loguru import logger
from ruamel.yaml import YAML

from . import DIRS
from .geometry import Point, Rectangle, Size, Tile
from .ingest import stitch_tiles
from .metadata import DiffStatus, ProjectMetadata
from .palette import PALETTE, ColorNotInPalette

if TYPE_CHECKING:
    from PIL import Image

_RE_HAS_COORDS = re.compile(r"[- _](\d+)[- _](\d+)[- _](\d+)[- _](\d+)\.png$", flags=re.IGNORECASE)

yaml = YAML(typ="safe")
yaml.default_flow_style = False
yaml.width = 120


class ProjectShim:
    """Represents a file that may or may not be a valid project."""

    def __init__(self, path: Path, rect: Rectangle = Rectangle(0, 0, 0, 0)):
        self.path = path
        self.rect = rect
        self.mtime: int = 0
        try:
            self.mtime = round(path.stat().st_mtime)
        except OSError:
            pass  # If the file doesn't exist or can't be accessed, we treat it as having no mtime

    def has_been_modified(self) -> bool:
        """Check if the file has been modified since it was marked invalid."""
        try:
            current_mtime = round(self.path.stat().st_mtime)
            return current_mtime != self.mtime
        except OSError:
            return self.mtime != 0

    def run_diff(self, changed_tile: "Tile | None" = None) -> None:
        """No-op for invalid project files."""
        pass

    def run_nochange(self) -> None:
        """No-op for invalid project files."""
        pass

    def get_first_seen(self) -> int:
        """Return sentinel value for invalid projects (far future, so they don't win selection)."""
        return 1 << 58


class Project(ProjectShim):
    """Represents a wplace project stored on disk that has been validated."""

    @classmethod
    def iter(cls) -> Iterable[ProjectShim]:
        """Yields all projects (valid and invalid) found in the user pictures directory."""
        path = DIRS.user_pictures_path / "wplace"
        path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Searching for projects in {path}")
        return (cls.try_open(p) for p in sorted(path.iterdir()))

    @classmethod
    def scan_directory(cls) -> set[Path]:
        """Returns the set of PNG files in the user pictures/wplace directory."""
        path = DIRS.user_pictures_path / "wplace"
        path.mkdir(parents=True, exist_ok=True)
        return {p for p in path.glob("*.png") if p.is_file()}

    @classmethod
    def try_open(cls, path: Path) -> ProjectShim:
        """Attempts to open a project from the given path. Returns ProjectShim if invalid."""

        match = _RE_HAS_COORDS.search(path.name)
        if not match or not path.is_file():
            return ProjectShim(path)  # no coords or otherwise invalid/irrelevant

        try:
            # Convert now, but close immediately. We'll reopen later as needed.
            with PALETTE.open_image(path) as image:
                size = Size(*image.size)
        except ColorNotInPalette as e:
            logger.warning(f"{path.name}: Color not in palette: {e}")
            path.rename(path.with_suffix(".invalid.png"))
            return ProjectShim(path)
        rect = Rectangle.from_point_size(Point.from4(*map(int, match.groups())), size)

        logger.info(f"{path.name}: Detected project at {rect}")

        new = cls(path, rect)
        new.run_diff()
        return new

    def __init__(self, path: Path, rect: Rectangle):
        """Represents a wplace project stored at `path`, covering the area defined by `rect`."""
        super().__init__(path, rect)
        self.metadata = self.load_metadata()

    def get_first_seen(self) -> int:
        """Return the timestamp when this project was first detected."""
        return self.metadata.first_seen

    def __eq__(self, other) -> bool:
        return self.path == getattr(other, "path", ...)

    def __hash__(self):
        return hash(self.path)

    @property
    def snapshot_path(self) -> Path:
        """Path to the snapshot file for this project."""
        # project_123_456_789_012.png -> project_123_456_789_012.snapshot.png
        return self.path.with_suffix(".snapshot.png")

    @property
    def metadata_path(self) -> Path:
        """Path to the metadata YAML file for this project."""
        # project_123_456_789_012.png -> project_123_456_789_012.metadata.yaml
        return self.path.with_suffix(".metadata.yaml")

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

    def save_snapshot(self, image) -> None:
        """Save current canvas snapshot to disk."""
        try:
            image.save(self.snapshot_path)
            self.metadata.last_snapshot = round(time.time())
        except Exception as e:
            logger.error(f"Failed to save snapshot for {self.path.name}: {e}")

    def load_snapshot_if_exists(self) -> ContextManager[Image.Image | None]:
        """Return previous snapshot if it exists, or nullcontext if not."""
        if not self.snapshot_path.exists():
            return nullcontext()  # No snapshot yet, caller should handle as needed
        try:
            return PALETTE.open_image(self.snapshot_path)
        except Exception as e:
            logger.warning(f"Failed to load snapshot for {self.path.name}: {e}")
            return nullcontext()

    def run_diff(self, changed_tile: Tile | None = None) -> None:
        """Compares current canvas against project target and previous snapshot.

        Args:
            changed_tile: The specific tile that changed (if known), for efficient metadata updates

        Tracks progress (pixels placed toward goal) and regress (pixels removed/griefed),
        updates metadata with completion history, saves snapshot and metadata.
        """
        # If any tiles have been missing from cache, check again.
        if self.metadata.has_missing_tiles:
            self.metadata.has_missing_tiles = self._has_missing_tiles()

        # Load target project image
        with PALETTE.open_image(self.path) as target:
            target_data = get_flattened_data(target)

        # Load previous snapshot before overwriting
        with self.load_snapshot_if_exists() as previous_snapshot:
            prev_data = get_flattened_data(previous_snapshot) if previous_snapshot else b""

        # Stitch current canvas state
        with stitch_tiles(self.rect) as current:
            current_data = get_flattened_data(current)
            self.save_snapshot(current)

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

    def run_nochange(self) -> None:
        self.metadata.last_check = round(time.time())
        self.metadata.prune_old_tile_updates()  # regular cleanup task
        self.metadata.update_streak(0, 0)  # This will increment nochange streak and reset change streak
        self.save_metadata()

    def _update_single_tile_metadata(self, tile: Tile) -> None:
        """Update metadata for a single tile that changed."""
        tile_path = DIRS.user_cache_path / f"tile-{tile}.png"
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
            tile_path = DIRS.user_cache_path / f"tile-{tile}.png"
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
            tile_path = DIRS.user_cache_path / f"tile-{tile}.png"
            if not tile_path.exists():
                return True
        return False


def get_flattened_data(image: Image.Image) -> bytes:
    target_flattened = image.get_flattened_data()
    assert target_flattened is not None, "Image must have data"
    return bytes(target_flattened)  # type: ignore[arg-type]

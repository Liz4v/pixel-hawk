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
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from loguru import logger
from ruamel.yaml import YAML

from . import DIRS
from .geometry import Point, Rectangle, Size
from .ingest import stitch_tiles
from .metadata import ProjectMetadata
from .palette import PALETTE, ColorNotInPalette

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

    def run_diff(self) -> None:
        """No-op for invalid project files."""
        pass

    def run_nochange(self) -> None:
        """No-op for invalid project files."""
        pass


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
            return ProjectMetadata.from_rect(self.rect)

        try:
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                data = yaml.load(f)
            return ProjectMetadata.from_dict(data)
        except Exception as e:
            logger.warning(f"Failed to load metadata for {self.path.name}: {e}. Creating new.")
            return ProjectMetadata.from_rect(self.rect)

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

    def load_snapshot(self):
        """Load previous snapshot if it exists, or None."""
        if not self.snapshot_path.exists():
            return None
        try:
            return PALETTE.open_image(self.snapshot_path)
        except Exception as e:
            logger.warning(f"Failed to load snapshot for {self.path.name}: {e}")
            return None

    def run_diff(self) -> None:
        """Compares current canvas against project target and previous snapshot.

        Tracks progress (pixels placed toward goal) and regress (pixels removed/griefed),
        updates metadata with completion history and tile updates, saves snapshot and metadata.
        """
        now = round(time.time())
        self.metadata.last_check = now

        # Load and compare images, then close them immediately
        with ExitStack() as stack:
            # Load target project image
            target = stack.enter_context(PALETTE.open_image(self.path))
            target_data = target.get_flattened_data()
            assert target_data is not None, "Target image must have data"

            # Load previous snapshot before overwriting (managed by ExitStack)
            previous_snapshot = self.load_snapshot()
            if previous_snapshot is not None:
                stack.enter_context(previous_snapshot)

            # Stitch current canvas state and save as new snapshot
            current = stack.enter_context(stitch_tiles(self.rect))
            current_data = current.get_flattened_data()
            assert current_data is not None, "Current image must have data"

            # Compare current vs target to find remaining pixels
            newdata = map(pixel_compare, current_data, target_data)  # type: ignore[arg-type]
            remaining = bytes(newdata)

            # Save current snapshot (overwrites previous_snapshot file)
            self.save_snapshot(current)

            # Check if project not started
            if remaining == target_data:
                self.save_metadata()
                return  # project is not started, no pixels placed

            # Calculate current completion state
            num_remaining = self.metadata.count_remaining_pixels(remaining)
            num_target = self.metadata.count_target_pixels(target_data)
            percent_complete = self.metadata.calculate_completion_percent(num_remaining, num_target)

            # Compare with previous snapshot to detect progress/regress
            progress_pixels = 0
            regress_pixels = 0

            if previous_snapshot is not None:
                prev_data = previous_snapshot.get_flattened_data()
                assert prev_data is not None, "Previous snapshot must have data"

                # Detect progress and regress by comparing snapshots
                progress_pixels, regress_pixels = self.metadata.compare_snapshots(current_data, prev_data, target_data)
        # All images closed - continue with metadata updates

        # Track tile changes by comparing current state with cached tile metadata
        self._update_tile_metadata()

        # Update totals
        self.metadata.total_progress += progress_pixels
        self.metadata.total_regress += regress_pixels

        # Update max completion if improved
        self.metadata.update_completion(num_remaining, percent_complete, now)

        # Update largest regress
        self.metadata.update_regress(regress_pixels, now)

        # Update streak (before checking completion so streak reflects final progress)
        self.metadata.update_streak(progress_pixels, regress_pixels)

        # Check for completion
        if max(remaining) == 0:
            self.metadata.last_log_message = (log_message := f"{self.path.name}: Complete! {num_target} pixels total.")
            logger.info(log_message)
            self.save_metadata()
            return

        # Calculate rate (pixels per hour)
        self.metadata.update_rate(progress_pixels, regress_pixels, now)

        # Build log message
        time_to_go = timedelta(seconds=27) * num_remaining
        days, hours = divmod(round(time_to_go.total_seconds() / 3600), 24)
        when = (datetime.now() + time_to_go).strftime("%b %d %H:%M")

        status_parts = [
            f"{self.path.name}:",
            f"{num_remaining}px remaining ({percent_complete:.2f}% complete)",
        ]

        if progress_pixels > 0 or regress_pixels > 0:
            status_parts.append(f"[+{progress_pixels}/-{regress_pixels}]")

        if self.metadata.change_streak_count > 1:
            status_parts.append(f"({self.metadata.change_streak_type} x{self.metadata.change_streak_count})")

        if self.metadata.nochange_streak_count > 0:
            status_parts.append(f"(nochange x{self.metadata.nochange_streak_count})")

        status_parts.append(f"ETA: {days}d{hours}h to {when}")

        self.metadata.last_log_message = (log_message := " ".join(status_parts))
        logger.info(log_message)

        # Save updated metadata
        self.save_metadata()

    def run_nochange(self) -> None:
        self.metadata.last_check = round(time.time())
        self.metadata.prune_old_tile_updates()  # regular cleanup task
        self.metadata.update_streak(0, 0)  # This will increment nochange streak and reset change streak
        self.save_metadata()

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


def pixel_compare(current: int, desired: int) -> int:
    """Returns the desired pixel value if it differs from the current pixel, otherwise returns transparent."""
    return 0 if desired == current else desired

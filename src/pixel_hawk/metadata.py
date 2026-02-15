"""Project metadata computation methods (mixin for ProjectInfo).

ProjectInfoMixin provides all computation and diff logic that the ProjectInfo
Tortoise ORM model inherits. This keeps models.py focused on field declarations
and metadata.py focused on business logic.

Methods operate on self (a ProjectInfo instance), mutating fields in place.
process_diff() returns a dict of HistoryChange field values so the caller can
create the DB record without circular imports.
"""

import time
from datetime import datetime, timedelta
from typing import Any

from .geometry import Point, Rectangle, Size, Tile


class ProjectInfoMixin:
    """Mixin providing computation methods for ProjectInfo.

    All methods operate on self, mutating ORM fields in place.
    """

    # These attributes are declared on ProjectInfo (the ORM model).
    # Listed here for readability; actual storage is via Tortoise fields.
    name: str
    x: int
    y: int
    width: int
    height: int
    first_seen: int
    last_check: int
    last_snapshot: int
    max_completion_pixels: int
    max_completion_percent: float
    max_completion_time: int
    total_progress: int
    total_regress: int
    largest_regress_pixels: int
    largest_regress_time: int
    recent_rate_pixels_per_hour: float
    recent_rate_window_start: int
    tile_last_update: dict[str, int]
    tile_updates_24h: list
    has_missing_tiles: bool
    last_log_message: str

    @property
    def rectangle(self) -> Rectangle:
        return Rectangle.from_point_size(Point(self.x, self.y), Size(self.width, self.height))

    def prune_old_tile_updates(self) -> None:
        """Remove tile updates older than cutoff_time from 24h list."""
        cutoff_time = self.last_check - 86400
        self.tile_updates_24h = [entry for entry in self.tile_updates_24h if entry[1] >= cutoff_time]

    def update_tile(self, tile: Tile, timestamp: int) -> None:
        """Record a tile update, maintaining last update map and 24h list."""
        tile_str = str(tile)
        self.tile_last_update[tile_str] = timestamp
        # Add to 24h list if not already present with this timestamp
        entry = [tile_str, timestamp]
        if entry not in self.tile_updates_24h:
            self.tile_updates_24h.append(entry)

    def count_remaining_pixels(self, remaining_bytes: Any) -> int:
        """Count non-zero pixels in remaining diff bytes."""
        return sum(1 for v in remaining_bytes if v)

    def count_target_pixels(self, target_bytes: Any) -> int:
        """Count non-zero pixels in target image bytes."""
        count = sum(1 for v in target_bytes if v)
        return count or 1  # Return 1 to avoid division by zero

    def calculate_completion_percent(self, num_remaining: int, num_target: int) -> float:
        """Calculate completion percentage from remaining and target pixel counts."""
        return 100.0 - (num_remaining * 100.0 / num_target)

    def compare_snapshots(self, current_data: bytes, prev_data: bytes, target_data: bytes) -> tuple[int, int]:
        """Compare current and previous snapshots to detect progress and regress.

        Returns:
            Tuple of (progress_pixels, regress_pixels)
        """
        progress_pixels = 0
        regress_pixels = 0

        for curr_px, prev_px, target_px in zip(current_data, prev_data, target_data):
            if target_px == 0:  # Skip transparent pixels (not part of project)
                continue
            if prev_px != target_px and curr_px == target_px:
                progress_pixels += 1
            elif prev_px == target_px and curr_px != target_px:
                regress_pixels += 1

        return progress_pixels, regress_pixels

    def update_completion(self, num_remaining: int, percent_complete: float, timestamp: int) -> None:
        """Update max completion if improved."""
        if self.max_completion_pixels == 0 or num_remaining < self.max_completion_pixels:
            self.max_completion_pixels = num_remaining
            self.max_completion_percent = percent_complete
            self.max_completion_time = timestamp

    def update_regress(self, regress_pixels: int, timestamp: int) -> None:
        """Update largest regress event if applicable."""
        if regress_pixels > self.largest_regress_pixels:
            self.largest_regress_pixels = regress_pixels
            self.largest_regress_time = timestamp

    def update_rate(self, progress_pixels: int, regress_pixels: int, timestamp: int) -> None:
        """Update completion rate (pixels per hour)."""
        if self.recent_rate_window_start > 0:
            elapsed_hours = (timestamp - self.recent_rate_window_start) / 3600.0
            if elapsed_hours > 0:
                net_change = progress_pixels - regress_pixels
                self.recent_rate_pixels_per_hour = net_change / elapsed_hours
        else:
            # Start rate tracking window
            self.recent_rate_window_start = timestamp

        # Reset rate window if too old (more than 24 hours)
        if timestamp - self.recent_rate_window_start > 86400:
            self.recent_rate_window_start = timestamp
            self.recent_rate_pixels_per_hour = 0.0

    def process_diff(self, current_data: bytes, target_data: bytes, prev_data: bytes) -> dict:
        """Process a project diff: count pixels, compare snapshots, update metadata, build log message.

        Returns:
            Dict with HistoryChange field values: status, num_remaining, num_target,
            completion_percent, progress_pixels, regress_pixels, timestamp.
            Status values are strings from DiffStatus: "not_started", "in_progress", "complete".
        """
        # Update last check timestamp
        self.last_check = timestamp = round(time.time())

        # Count target pixels
        num_target = self.count_target_pixels(target_data)

        # Compare current vs target to find remaining pixels
        remaining = bytes(0 if target == current else target for current, target in zip(current_data, target_data))

        # Check if project not started (all target pixels remain, and no previous snapshot)
        if not prev_data and remaining == target_data:
            self.last_log_message = f"{self.name}: Not started"
            return {
                "status": "not_started",
                "num_remaining": 0,
                "num_target": num_target,
                "completion_percent": 0.0,
                "progress_pixels": 0,
                "regress_pixels": 0,
                "timestamp": timestamp,
            }

        # Count remaining pixels and calculate completion
        num_remaining = self.count_remaining_pixels(remaining)
        percent_complete = self.calculate_completion_percent(num_remaining, num_target)

        # Compare with previous snapshot to detect progress/regress
        progress_pixels = 0
        regress_pixels = 0

        if prev_data:
            progress_pixels, regress_pixels = self.compare_snapshots(current_data, prev_data, target_data)

        # Update totals
        self.total_progress += progress_pixels
        self.total_regress += regress_pixels

        # Update max completion if improved
        self.update_completion(num_remaining, percent_complete, timestamp)

        # Update largest regress
        self.update_regress(regress_pixels, timestamp)

        # Check for completion
        if max(remaining) == 0:
            self.last_log_message = f"{self.name}: Complete! {num_target} pixels total. {self.rectangle.to_link()}"
            return {
                "status": "complete",
                "num_remaining": 0,
                "num_target": num_target,
                "completion_percent": 100.0,
                "progress_pixels": progress_pixels,
                "regress_pixels": regress_pixels,
                "timestamp": timestamp,
            }

        # Calculate rate (pixels per hour)
        self.update_rate(progress_pixels, regress_pixels, timestamp)

        # Build log message for in-progress project
        time_to_go = timedelta(seconds=27) * num_remaining
        days, hours = divmod(round(time_to_go.total_seconds() / 3600), 24)
        when = (datetime.now() + time_to_go).strftime("%b %d %H:%M")

        status_parts = [
            f"{self.name}:",
            f"{num_remaining}px remaining ({percent_complete:.2f}% complete)",
        ]

        if progress_pixels > 0 or regress_pixels > 0:
            status_parts.append(f"[+{progress_pixels}/-{regress_pixels}]")

        status_parts.append(f"ETA: {days}d{hours}h to {when}.")
        status_parts.append(self.rectangle.to_link())

        self.last_log_message = " ".join(status_parts)
        return {
            "status": "in_progress",
            "num_remaining": num_remaining,
            "num_target": num_target,
            "completion_percent": percent_complete,
            "progress_pixels": progress_pixels,
            "regress_pixels": regress_pixels,
            "timestamp": timestamp,
        }

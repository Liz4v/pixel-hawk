"""Project metadata tracking, pixel counting, and persistence.

ProjectMetadata encapsulates all project state and statistics:
- Completion tracking: max completion achieved, remaining pixels, percentages
- Progress/regress detection: compares snapshots to identify pixel changes
- Tile update tracking: maintains last-update timestamps and 24-hour rolling history
- Streak tracking: consecutive checks with same outcome (progress/regress/nochange)
- Rate calculation: pixels per hour based on recent activity window
- Largest regress event: records worst griefing incident
- Missing tiles flag: indicates if any required tiles are absent from cache

The process_diff() method orchestrates the complete diff workflow: counting pixels,
comparing snapshots, updating all statistics, and building status log messages.
Provides pixel counting utilities (remaining, target, completion percent) and
snapshot comparison logic. Serializes to/from YAML for persistence.

DiffStatus and DiffResult provide typed return values for diff operations.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum, auto
from typing import Any

from .geometry import Rectangle, Tile


class DiffStatus(StrEnum):
    """Status of a project diff operation."""

    NOT_STARTED = auto()  # Project has no pixels placed
    IN_PROGRESS = auto()  # Project is partially complete
    COMPLETE = auto()  # Project is fully complete


@dataclass
class DiffResult:
    """Result of processing a project diff."""

    status: DiffStatus
    num_remaining: int = 0
    num_target: int = 0


@dataclass
class ProjectMetadata:
    """Persistent metadata for a project, tracking completion history and tile updates."""

    # Project identity
    name: str = ""  # Project filename

    # Project bounds
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0

    # Timestamps
    first_seen: int = 0  # When project was first detected
    last_check: int = 0  # Most recent diff check
    last_snapshot: int = 0  # When snapshot was last saved

    # Completion tracking (max ever = best completion state)
    max_completion_pixels: int = 0  # Lowest remaining pixel count achieved
    max_completion_percent: float = 0.0  # Highest completion percentage achieved
    max_completion_time: int = 0  # When max completion was reached

    # Progress/regress counters (lifetime totals)
    total_progress: int = 0  # Total pixels placed toward goal (cumulative)
    total_regress: int = 0  # Total pixels removed/griefed (cumulative)

    # Largest regress event
    largest_regress_pixels: int = 0
    largest_regress_time: int = 0

    # Change streak (progress or regress events; nochange events do not break this)
    change_streak_type: str = "none"  # "progress", "regress", "mixed", "none"
    change_streak_count: int = 0
    # Nochange streak (consecutive nochange events; any change event breaks this)
    nochange_streak_count: int = 0

    # Rate tracking (recent window)
    recent_rate_pixels_per_hour: float = 0.0
    recent_rate_window_start: int = 0  # Start of current rate measurement window

    # Tile updates
    # Map of tile coordinate string (e.g., "123_456") to last update timestamp
    tile_last_update: dict[str, int] = field(default_factory=dict)
    # List of tile updates in last 24h: [(tile_str, timestamp), ...]
    tile_updates_24h: list[tuple[str, int]] = field(default_factory=list)

    # Cache state
    # True if any tiles required by this project are missing from cache (not yet fetched)
    # When true, counts and percentages may be inaccurate (comparing against transparent pixels)
    has_missing_tiles: bool = True

    # Last log message
    last_log_message: str = ""

    @classmethod
    def from_rect(cls, rect: Rectangle, name: str) -> "ProjectMetadata":
        """Create new metadata from project rectangle."""
        now = round(time.time())
        return cls(
            name=name,
            x=rect.point.x,
            y=rect.point.y,
            width=rect.size.w,
            height=rect.size.h,
            first_seen=now,
            last_check=now,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for YAML serialization."""
        return {
            "name": self.name,
            "bounds": {"x": self.x, "y": self.y, "width": self.width, "height": self.height},
            "timestamps": {
                "first_seen": self.first_seen,
                "last_check": self.last_check,
                "last_snapshot": self.last_snapshot,
            },
            "max_completion": {
                "pixels_remaining": self.max_completion_pixels,
                "percent_complete": self.max_completion_percent,
                "achieved_at": self.max_completion_time,
            },
            "totals": {
                "progress_pixels": self.total_progress,
                "regress_pixels": self.total_regress,
            },
            "largest_regress": {
                "pixels": self.largest_regress_pixels,
                "timestamp": self.largest_regress_time,
            },
            "streak": {
                "change_type": self.change_streak_type,
                "change_count": self.change_streak_count,
                "nochange_count": self.nochange_streak_count,
            },
            "recent_rate": {
                "pixels_per_hour": self.recent_rate_pixels_per_hour,
                "window_start": self.recent_rate_window_start,
            },
            "tile_updates": {
                "last_update_by_tile": self.tile_last_update,
                "recent_24h": [{"tile": tile, "timestamp": ts} for tile, ts in self.tile_updates_24h],
            },
            "cache_state": {
                "has_missing_tiles": self.has_missing_tiles,
            },
            "last_log_message": self.last_log_message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectMetadata":
        """Load from dictionary (from YAML)."""
        bounds = data.get("bounds", {})
        timestamps = data.get("timestamps", {})
        max_comp = data.get("max_completion", {})
        totals = data.get("totals", {})
        largest_reg = data.get("largest_regress", {})
        streak = data.get("streak", {})
        rate = data.get("recent_rate", {})
        tile_updates = data.get("tile_updates", {})
        cache_state = data.get("cache_state", {})

        return cls(
            name=data.get("name", ""),
            x=bounds.get("x", 0),
            y=bounds.get("y", 0),
            width=bounds.get("width", 0),
            height=bounds.get("height", 0),
            first_seen=timestamps.get("first_seen", 0),
            last_check=timestamps.get("last_check", 0),
            last_snapshot=timestamps.get("last_snapshot", 0),
            max_completion_pixels=max_comp.get("pixels_remaining", 0),
            max_completion_percent=max_comp.get("percent_complete", 0.0),
            max_completion_time=max_comp.get("achieved_at", 0),
            total_progress=totals.get("progress_pixels", 0),
            total_regress=totals.get("regress_pixels", 0),
            largest_regress_pixels=largest_reg.get("pixels", 0),
            largest_regress_time=largest_reg.get("timestamp", 0),
            change_streak_type=streak.get("change_type", "none"),
            change_streak_count=streak.get("change_count", 0),
            nochange_streak_count=streak.get("nochange_count", 0),
            recent_rate_pixels_per_hour=rate.get("pixels_per_hour", 0.0),
            recent_rate_window_start=rate.get("window_start", 0),
            tile_last_update=tile_updates.get("last_update_by_tile", {}),
            tile_updates_24h=[(item["tile"], item["timestamp"]) for item in tile_updates.get("recent_24h", [])],
            has_missing_tiles=cache_state.get("has_missing_tiles", True),
            last_log_message=data.get("last_log_message", ""),
        )

    def prune_old_tile_updates(self) -> None:
        """Remove tile updates older than cutoff_time from 24h list."""
        cutoff_time = self.last_check - 86400
        self.tile_updates_24h = [(tile, ts) for tile, ts in self.tile_updates_24h if ts >= cutoff_time]

    def update_tile(self, tile: Tile, timestamp: int) -> None:
        """Record a tile update, maintaining last update map and 24h list."""
        tile_str = str(tile)
        self.tile_last_update[tile_str] = timestamp
        # Add to 24h list if not already present with this timestamp
        if (tile_str, timestamp) not in self.tile_updates_24h:
            self.tile_updates_24h.append((tile_str, timestamp))

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

        Args:
            current_data: Current canvas state (iterable of pixel values)
            prev_data: Previous canvas state (iterable of pixel values)
            target_data: Target project image (iterable of pixel values)

        Returns:
            Tuple of (progress_pixels, regress_pixels)
        """
        progress_pixels = 0
        regress_pixels = 0

        for curr_px, prev_px, target_px in zip(current_data, prev_data, target_data):
            if target_px == 0:  # Skip transparent pixels (not part of project)
                continue
            if prev_px != target_px and curr_px == target_px:
                # Was wrong, now correct: progress
                progress_pixels += 1
            elif prev_px == target_px and curr_px != target_px:
                # Was correct, now wrong: regress
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

    def update_streak(self, progress_pixels: int, regress_pixels: int) -> None:
        """Update streak based on progress and regress pixel counts.

        Change streaks (progress/regress/mixed) continue across nochange events.
        Nochange streaks (no pixel changes) reset when any change occurs.
        """
        if progress_pixels == 0 and regress_pixels == 0:
            # Nochange event: increment nochange streak, don't touch change streak
            self.nochange_streak_count += 1
            return
        elif regress_pixels == 0:
            event = "progress"
        elif progress_pixels == 0:
            event = "regress"
        else:
            event = "mixed"

        self.nochange_streak_count = 0  # Break nochange streak
        if self.change_streak_type == event:
            self.change_streak_count += 1  # Continue existing streak
        else:  # New streak type
            self.change_streak_type = event
            self.change_streak_count = 1

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

    def process_diff(self, current_data: bytes, target_data: bytes, prev_data: bytes) -> DiffResult:
        """Process a project diff: count pixels, compare snapshots, update metadata, build log message.

        Args:
            current_data: Current canvas state (iterable of pixel values)
            target_data: Target project image (iterable of pixel values)
            prev_data: Previous canvas state (optional, for progress/regress detection)

        Returns:
            DiffResult with status, log message, and pixel counts
        """
        # Update last check timestamp
        self.last_check = timestamp = round(time.time())

        # Count target pixels
        num_target = self.count_target_pixels(target_data)

        # Compare current vs target to find remaining pixels (pixel_compare logic inlined)
        remaining = bytes(0 if target == current else target for current, target in zip(current_data, target_data))

        # Check if project not started (all target pixels remain, and no previous snapshot)
        if not prev_data and remaining == target_data:
            self.last_log_message = f"{self.name}: Not started"
            return DiffResult(status=DiffStatus.NOT_STARTED, num_target=num_target)

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

        # Update streak (before checking completion so streak reflects final progress)
        self.update_streak(progress_pixels, regress_pixels)

        # Check for completion
        if max(remaining) == 0:
            self.last_log_message = f"{self.name}: Complete! {num_target} pixels total."
            return DiffResult(status=DiffStatus.COMPLETE, num_remaining=0, num_target=num_target)

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

        if self.change_streak_count > 1:
            status_parts.append(f"({self.change_streak_type} x{self.change_streak_count})")

        if self.nochange_streak_count > 0:
            status_parts.append(f"(nochange x{self.nochange_streak_count})")

        status_parts.append(f"ETA: {days}d{hours}h to {when}")

        self.last_log_message = " ".join(status_parts)
        return DiffResult(status=DiffStatus.IN_PROGRESS, num_remaining=num_remaining, num_target=num_target)

"""Project metadata computation service layer.

Provides standalone functions for ProjectInfo business logic: counting pixels,
comparing snapshots, updating completion tracking, and processing diffs.

All functions take ProjectInfo as the first parameter and mutate its fields in place.
process_diff() creates and returns a HistoryChange object for the caller to persist.
"""

import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .geometry import Tile
from .models import DiffStatus, HistoryChange

if TYPE_CHECKING:
    from .models import ProjectInfo


def prune_old_tile_updates(info: ProjectInfo) -> None:
    """Remove tile updates older than cutoff_time from 24h list."""
    cutoff_time = info.last_check - 86400
    info.tile_updates_24h = [entry for entry in info.tile_updates_24h if entry[1] >= cutoff_time]


def update_tile(info: ProjectInfo, tile: Tile, timestamp: int) -> None:
    """Record a tile update, maintaining last update map and 24h list."""
    tile_str = str(tile)
    info.tile_last_update[tile_str] = timestamp
    # Add to 24h list if not already present with this timestamp
    entry = [tile_str, timestamp]
    if entry not in info.tile_updates_24h:
        info.tile_updates_24h.append(entry)


def compare_snapshots(current_data: bytes, prev_data: bytes, target_data: bytes) -> tuple[int, int]:
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


def update_completion(info: ProjectInfo, num_remaining: int, percent_complete: float, timestamp: int) -> None:
    """Update max completion if improved."""
    if info.max_completion_pixels == 0 or num_remaining < info.max_completion_pixels:
        info.max_completion_pixels = num_remaining
        info.max_completion_percent = percent_complete
        info.max_completion_time = timestamp


def update_regress(info: ProjectInfo, regress_pixels: int, timestamp: int) -> None:
    """Update largest regress event if applicable."""
    if regress_pixels > info.largest_regress_pixels:
        info.largest_regress_pixels = regress_pixels
        info.largest_regress_time = timestamp


def update_rate(info: ProjectInfo, progress_pixels: int, regress_pixels: int, timestamp: int) -> None:
    """Update completion rate (pixels per hour)."""
    if info.recent_rate_window_start > 0:
        elapsed_hours = (timestamp - info.recent_rate_window_start) / 3600.0
        if elapsed_hours > 0:
            net_change = progress_pixels - regress_pixels
            info.recent_rate_pixels_per_hour = net_change / elapsed_hours
    else:
        # Start rate tracking window
        info.recent_rate_window_start = timestamp

    # Reset rate window if too old (more than 24 hours)
    if timestamp - info.recent_rate_window_start > 86400:
        info.recent_rate_window_start = timestamp
        info.recent_rate_pixels_per_hour = 0.0


def process_diff(info: ProjectInfo, current_data: bytes, target_data: bytes, prev_data: bytes) -> HistoryChange:
    """Process a project diff: count pixels, compare snapshots, update metadata, build log message.

    Returns:
        HistoryChange object (not yet saved to DB - caller must await change.save()).
    """
    # Update last check timestamp
    info.last_check = timestamp = round(time.time())

    # Count target pixels
    num_target = sum(1 for v in target_data if v) or 1  # avoid division by zero

    # Compare current vs target to find remaining pixels
    remaining = bytes(0 if target == current else target for current, target in zip(current_data, target_data))

    # Check if project not started (all target pixels remain, and no previous snapshot)
    if not prev_data and remaining == target_data:
        info.last_log_message = f"{info.owner.name}/{info.name}: Not started"
        return HistoryChange(
            project=info,
            timestamp=timestamp,
            status=DiffStatus.NOT_STARTED,
            num_remaining=0,
            num_target=num_target,
            completion_percent=0.0,
            progress_pixels=0,
            regress_pixels=0,
        )

    # Count remaining pixels and calculate completion
    num_remaining = sum(1 for v in remaining if v)
    percent_complete = 100.0 - (num_remaining * 100.0 / num_target)

    # Compare with previous snapshot to detect progress/regress
    progress_pixels = 0
    regress_pixels = 0

    if prev_data:
        progress_pixels, regress_pixels = compare_snapshots(current_data, prev_data, target_data)

    # Update totals
    info.total_progress += progress_pixels
    info.total_regress += regress_pixels

    # Update max completion if improved
    update_completion(info, num_remaining, percent_complete, timestamp)

    # Update largest regress
    update_regress(info, regress_pixels, timestamp)

    # Check for completion
    if max(remaining) == 0:
        info.last_log_message = f"{info.owner.name}/{info.name}: Complete! {num_target} pixels total. {info.rectangle.to_link()}"
        return HistoryChange(
            project=info,
            timestamp=timestamp,
            status=DiffStatus.COMPLETE,
            num_remaining=0,
            num_target=num_target,
            completion_percent=100.0,
            progress_pixels=progress_pixels,
            regress_pixels=regress_pixels,
        )

    # Calculate rate (pixels per hour)
    update_rate(info, progress_pixels, regress_pixels, timestamp)

    # Build log message for in-progress project
    time_to_go = timedelta(seconds=27) * num_remaining
    days, hours = divmod(round(time_to_go.total_seconds() / 3600), 24)
    when = (datetime.now() + time_to_go).strftime("%b %d %H:%M")

    status_parts = [
        f"{info.owner.name}/{info.name}:",
        f"{num_remaining}px remaining ({percent_complete:.2f}% complete)",
    ]

    if progress_pixels > 0 or regress_pixels > 0:
        status_parts.append(f"[+{progress_pixels}/-{regress_pixels}]")

    status_parts.append(f"ETA: {days}d{hours}h to {when}.")
    status_parts.append(info.rectangle.to_link())

    info.last_log_message = " ".join(status_parts)
    return HistoryChange(
        project=info,
        timestamp=timestamp,
        status=DiffStatus.IN_PROGRESS,
        num_remaining=num_remaining,
        num_target=num_target,
        completion_percent=percent_complete,
        progress_pixels=progress_pixels,
        regress_pixels=regress_pixels,
    )

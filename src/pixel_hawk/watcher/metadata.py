"""Project metadata computation service layer.

Provides standalone functions for ProjectInfo business logic: counting pixels,
comparing snapshots, updating completion tracking, and processing diffs.

All functions take ProjectInfo as the first parameter and mutate its fields in place.
process_diff() creates and returns a HistoryChange object for the caller to persist.
"""

import math
import time
from typing import TYPE_CHECKING

from ..models.project import DiffStatus, HistoryChange

if TYPE_CHECKING:
    from ..models.project import ProjectInfo


def find_regressed_indices(current_data: bytes, prev_data: bytes, target_data: bytes) -> list[int]:
    """Return flat array indices of all regressed pixels.

    A pixel is regressed when it was correct in the previous snapshot but is now wrong.
    """
    return [
        i
        for i, (curr_px, prev_px, target_px) in enumerate(zip(current_data, prev_data, target_data))
        if target_px != 0 and prev_px == target_px and curr_px != target_px
    ]


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
    if info.max_completion_time == 0 or num_remaining < info.max_completion_pixels:
        info.max_completion_pixels = num_remaining
        info.max_completion_percent = percent_complete
        info.max_completion_time = timestamp


def update_regress(info: ProjectInfo, regress_pixels: int, timestamp: int) -> None:
    """Update largest regress event if applicable."""
    if regress_pixels > info.largest_regress_pixels:
        info.largest_regress_pixels = regress_pixels
        info.largest_regress_time = timestamp


RATE_HALF_LIFE_HOURS = 12.0
RATE_STALE_THRESHOLD = 604800  # 7 days


def update_rate(info: ProjectInfo, progress_pixels: int, regress_pixels: int, timestamp: int) -> None:
    """Update completion rate using time-weighted exponential moving average."""
    if info.recent_rate_window_start > 0:
        elapsed_seconds = timestamp - info.recent_rate_window_start
        if elapsed_seconds <= 0:
            return

        if elapsed_seconds > RATE_STALE_THRESHOLD:
            info.recent_rate_pixels_per_hour = 0.0
        else:
            elapsed_hours = elapsed_seconds / 3600.0
            instant_rate = (progress_pixels - regress_pixels) / elapsed_hours

            if info.recent_rate_pixels_per_hour == 0.0:
                info.recent_rate_pixels_per_hour = instant_rate
            else:
                decay = math.exp(-elapsed_hours / RATE_HALF_LIFE_HOURS)
                info.recent_rate_pixels_per_hour = decay * info.recent_rate_pixels_per_hour + (1 - decay) * instant_rate

    info.recent_rate_window_start = timestamp


def process_diff(info: ProjectInfo, current_data: bytes, target_data: bytes, prev_data: bytes) -> HistoryChange:
    """Process a project diff: count pixels, compare snapshots, update metadata, build log message.

    Requires info.owner to be prefetched.

    Returns:
        HistoryChange object (not yet saved to DB - caller must await change.save()).
    """
    owner = info.owner
    # Update last check timestamp
    info.last_check = timestamp = round(time.time())

    # Count target pixels
    num_target = sum(1 for v in target_data if v) or 1  # avoid division by zero

    # Compare current vs target to find remaining pixels
    remaining = bytes(0 if target == current else target for current, target in zip(current_data, target_data))

    # Check if project not started (all target pixels remain, and no previous snapshot)
    if not prev_data and remaining == target_data:
        info.last_log_message = f"{owner.name}/{info.name}: Not started"
        return HistoryChange(
            project=info,
            project_id=info.id,
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
        info.last_log_message = (
            f"{owner.name}/{info.name}: Complete! {num_target} pixels total. {info.rectangle.to_link()}"
        )
        return HistoryChange(
            project=info,
            project_id=info.id,
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
    rate = info.recent_rate_pixels_per_hour
    seconds_to_go = round(num_remaining / rate * 3600) if rate > 0 else 27 * num_remaining
    days, hours = divmod(round(seconds_to_go / 3600), 24)
    when = time.strftime("%b %d %H:%M", time.localtime(time.time() + seconds_to_go))

    status_parts = [
        f"{owner.name}/{info.name}:",
        f"{num_remaining}px remaining ({percent_complete:.2f}% complete)",
    ]

    if progress_pixels > 0 or regress_pixels > 0:
        status_parts.append(f"[+{progress_pixels}/-{regress_pixels}]")

    status_parts.append(f"ETA: {days}d{hours}h to {when}.")
    status_parts.append(info.rectangle.to_link())

    info.last_log_message = " ".join(status_parts)
    return HistoryChange(
        project=info,
        project_id=info.id,
        timestamp=timestamp,
        status=DiffStatus.IN_PROGRESS,
        num_remaining=num_remaining,
        num_target=num_target,
        completion_percent=percent_complete,
        progress_pixels=progress_pixels,
        regress_pixels=regress_pixels,
    )

"""Living watch message service layer for pixel-hawk.

Discord-agnostic business logic for watch messages: creating, removing,
formatting, and updating persistent Discord messages that reflect project status.
"""

import time

from loguru import logger

from ..models.entities import DiffStatus, HistoryChange, Person, ProjectInfo, ProjectState, WatchMessage
from .access import ErrorMsg


async def format_watch_message(info: ProjectInfo) -> str:
    """Format a comprehensive, Discord-formatted status display for a project.

    Richer than ``_format_project()`` in commands.py (which is a compact list entry).
    Queries the latest HistoryChange internally.
    """
    state = ProjectState(info.state)
    lines = [f"## `{info.id:04}`: {info.name}"]

    if state == ProjectState.CREATING:
        lines.append("Status: **CREATING** \u2014 awaiting coordinates")
        return "\n".join(lines)

    lines.append(f"<{info.rectangle.to_link()}>")
    lines.append(f"State: **{state.name}**")

    if state == ProjectState.INACTIVE:
        lines.append("*Inactive \u2014 not being monitored*")
        return "\n".join(lines)

    if info.last_check == 0:
        lines.append("*Not yet checked*")
        return "\n".join(lines)

    # Query recent changes first; derive latest from there when possible (saves a query)
    cutoff = round(time.time()) - 86400
    changes_24h = await HistoryChange.filter(project=info, timestamp__gte=cutoff).order_by("-timestamp").all()
    latest = changes_24h[0] if changes_24h else await HistoryChange.filter(project=info).order_by("-timestamp").first()

    if latest and latest.status == DiffStatus.COMPLETE:
        lines.append(f"\u2705 **Complete** since <t:{info.max_completion_time}:R>")
        lines.append(f"Total pixels: {latest.num_target:,}")
    elif latest and latest.status == DiffStatus.NOT_STARTED:
        lines.append(f"Not started \u00b7 {latest.num_target:,} px")
    elif latest:
        emoji = "\u23f3" if latest.completion_percent < 50 else "\u231b"
        lines.append(
            f"{emoji} **{latest.completion_percent:.1f}%** complete \u00b7 {latest.num_remaining:,} / {latest.num_target:,} px remaining"
        )
        if latest.progress_pixels or latest.regress_pixels:
            lines.append(f"Last diff: +{latest.progress_pixels:,} / -{latest.regress_pixels:,}")

    # Rate and ETA
    if info.recent_rate_pixels_per_hour and latest and latest.num_remaining > 0:
        rate = info.recent_rate_pixels_per_hour
        if rate > 0:
            eta = round(time.time() + latest.num_remaining / rate * 3600)
            lines.append(f"Rate: {rate:.1f} px/hr \u00b7 ETA: <t:{eta}:R> (<t:{eta}:f>)")
        else:
            lines.append(f"Rate: {rate:.1f} px/hr")

    # 24h activity
    if changes_24h:
        p24 = sum(c.progress_pixels for c in changes_24h)
        r24 = sum(c.regress_pixels for c in changes_24h)
        lines.append(f"Last 24h: +{p24:,} / -{r24:,}")

    # Lifetime totals
    if info.total_progress or info.total_regress:
        lines.append(f"Lifetime: +{info.total_progress:,} / -{info.total_regress:,}")

    # Records
    if info.max_completion_percent > 0 and latest and latest.status != DiffStatus.COMPLETE:
        lines.append(f"Best: {info.max_completion_percent:.1f}% (<t:{info.max_completion_time}:R>)")

    if info.largest_regress_pixels > 0:
        lines.append(f"Worst grief: {info.largest_regress_pixels:,} px (<t:{info.largest_regress_time}:R>)")

    lines.append(f"Last checked <t:{info.last_check}:R>")

    return "\n".join(lines)


async def create_watch(discord_id: int, project_id: int, channel_id: int, guild_id: int) -> tuple[str, int]:
    """Validate and format a watch message. Returns (content, project_id).

    The caller sends the Discord message, then calls ``save_watch_message``
    with the resulting message_id.
    """
    person = await Person.filter(discord_id=discord_id).first()
    if person is None:
        raise ErrorMsg("No linked account found.")

    info = await ProjectInfo.filter(id=project_id).prefetch_related("owner").first()
    if info is None:
        raise ErrorMsg(f"Project {project_id:04} not found.")
    if info.owner.id != person.id:
        raise ErrorMsg(f"Project {project_id:04} is not yours.")

    existing = await WatchMessage.filter(project_id=project_id, channel_id=channel_id).first()
    if existing:
        link = f"https://discord.com/channels/{guild_id}/{existing.channel_id}/{existing.message_id}"
        raise ErrorMsg(f"Project {project_id:04} is already being watched in this channel: {link}")

    content = await format_watch_message(info)
    return content, info.id


async def save_watch_message(project_id: int, channel_id: int, message_id: int) -> None:
    """Persist a watch message record after the Discord message has been sent."""
    await WatchMessage.create(project_id=project_id, channel_id=channel_id, message_id=message_id)
    logger.info(f"Watch created: project={project_id:04} channel={channel_id} message={message_id}")


async def remove_watch(discord_id: int, project_id: int, channel_id: int) -> int:
    """Remove a watch for a project in a channel. Returns message_id for deletion."""
    person = await Person.filter(discord_id=discord_id).first()
    if person is None:
        raise ErrorMsg("No linked account found.")

    info = await ProjectInfo.filter(id=project_id).prefetch_related("owner").first()
    if info is None:
        raise ErrorMsg(f"Project {project_id:04} not found.")
    if info.owner.id != person.id:
        raise ErrorMsg(f"Project {project_id:04} is not yours.")

    watch = await WatchMessage.filter(project_id=project_id, channel_id=channel_id).first()
    if watch is None:
        raise ErrorMsg(f"Project {project_id:04} is not being watched in this channel.")

    message_id = watch.message_id
    await watch.delete()
    logger.info(f"Watch removed: project={project_id:04} channel={channel_id}")
    return message_id


async def get_watches_for_projects(project_ids: list[int]) -> list[WatchMessage]:
    """Batch-query all WatchMessage records for the given project IDs."""
    if not project_ids:
        return []
    return await WatchMessage.filter(project_id__in=project_ids).prefetch_related("project__owner").all()


async def delete_watches_for_project(project_id: int) -> int:
    """Delete all WatchMessage records for a project. Returns count deleted."""
    deleted = await WatchMessage.filter(project_id=project_id).delete()
    if deleted:
        logger.info(f"Deleted {deleted} watch message(s) for project {project_id:04}")
    return deleted

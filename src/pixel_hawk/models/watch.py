"""WatchMessage entity: persistent Discord messages that track a project."""

from dataclasses import dataclass, field

from . import db
from .db import columns
from .project import ProjectInfo


@dataclass
class WatchMessage:
    """A Discord message that auto-updates with project stats on every diff."""

    _EXCLUDE_COLUMNS = frozenset({"project"})

    message_id: int = 0
    project_id: int = 0
    channel_id: int = 0
    project: ProjectInfo | None = field(default=None)

    @classmethod
    def _from_row(cls, row, project: ProjectInfo | None = None) -> WatchMessage:
        kwargs = {col: row[col] for col in columns(cls)}
        return cls(project=project, **kwargs)

    @classmethod
    async def create(cls, *, project_id: int, channel_id: int, message_id: int) -> WatchMessage:
        await db.execute(
            "INSERT INTO watch_message (message_id, project_id, channel_id) VALUES (?, ?, ?)",
            (message_id, project_id, channel_id),
        )
        return cls(message_id=message_id, project_id=project_id, channel_id=channel_id)

    async def delete(self) -> None:
        await db.execute("DELETE FROM watch_message WHERE message_id = ?", (self.message_id,))

    @classmethod
    async def get_by_project_channel(cls, project_id: int, channel_id: int) -> WatchMessage | None:
        row = await db.fetch_one(
            "SELECT * FROM watch_message WHERE project_id = ? AND channel_id = ?", (project_id, channel_id)
        )
        return cls._from_row(row) if row else None

    @classmethod
    async def filter_by_project(cls, project_id: int) -> list[WatchMessage]:
        return [
            cls._from_row(r)
            for r in await db.fetch_all("SELECT * FROM watch_message WHERE project_id = ?", (project_id,))
        ]

    @classmethod
    async def filter_by_projects_with_owner(cls, project_ids: list[int]) -> list[WatchMessage]:
        """Batch query watches for given project IDs, with project and owner data."""
        if not project_ids:
            return []
        placeholders = ",".join("?" * len(project_ids))
        rows = await db.fetch_all(
            f"SELECT w.message_id, w.project_id, w.channel_id, "
            f"p.id AS p_id, p.owner_id, p.name AS p_name, p.state, p.x, p.y, p.width, p.height, "
            f"p.first_seen, p.last_check, p.last_snapshot, p.max_completion_pixels, p.max_completion_percent, "
            f"p.max_completion_time, p.total_progress, p.total_regress, p.largest_regress_pixels, "
            f"p.largest_regress_time, p.recent_rate_pixels_per_hour, p.recent_rate_window_start, "
            f"p.has_missing_tiles, p.last_log_message, "
            f"o.name AS owner_name, o.discord_id AS owner_discord_id, o.access AS owner_access, "
            f"o.max_active_projects AS owner_max_active_projects, o.max_watched_tiles AS owner_max_watched_tiles, "
            f"o.watched_tiles_count AS owner_watched_tiles_count, o.active_projects_count AS owner_active_projects_count "
            f"FROM watch_message w "
            f"JOIN project p ON w.project_id = p.id "
            f"JOIN person o ON p.owner_id = o.id "
            f"WHERE w.project_id IN ({placeholders})",
            tuple(project_ids),
        )
        result = []
        for row in rows:
            # The JOIN aliases project columns with `p_id` and `p_name` to avoid
            # collisions with `w.project_id`. Build a plain dict row the entity
            # loaders can read.
            project_row = {
                "id": row["p_id"],
                "owner_id": row["owner_id"],
                "name": row["p_name"],
                "state": row["state"],
                "x": row["x"],
                "y": row["y"],
                "width": row["width"],
                "height": row["height"],
                "first_seen": row["first_seen"],
                "last_check": row["last_check"],
                "last_snapshot": row["last_snapshot"],
                "max_completion_pixels": row["max_completion_pixels"],
                "max_completion_percent": row["max_completion_percent"],
                "max_completion_time": row["max_completion_time"],
                "total_progress": row["total_progress"],
                "total_regress": row["total_regress"],
                "largest_regress_pixels": row["largest_regress_pixels"],
                "largest_regress_time": row["largest_regress_time"],
                "recent_rate_pixels_per_hour": row["recent_rate_pixels_per_hour"],
                "recent_rate_window_start": row["recent_rate_window_start"],
                "has_missing_tiles": row["has_missing_tiles"],
                "last_log_message": row["last_log_message"],
                "owner_name": row["owner_name"],
                "owner_discord_id": row["owner_discord_id"],
                "owner_access": row["owner_access"],
                "owner_max_active_projects": row["owner_max_active_projects"],
                "owner_max_watched_tiles": row["owner_max_watched_tiles"],
                "owner_watched_tiles_count": row["owner_watched_tiles_count"],
                "owner_active_projects_count": row["owner_active_projects_count"],
            }
            project = ProjectInfo._from_joined_row(project_row)
            watch = cls(
                message_id=row["message_id"],
                project_id=row["project_id"],
                channel_id=row["channel_id"],
                project=project,
            )
            result.append(watch)
        return result

    @classmethod
    async def count_by_project(cls, project_id: int) -> int:
        return await db.fetch_int("SELECT COUNT(*) FROM watch_message WHERE project_id = ?", (project_id,))

    @classmethod
    async def delete_by_project(cls, project_id: int) -> int:
        count = await cls.count_by_project(project_id)
        if count:
            await db.execute("DELETE FROM watch_message WHERE project_id = ?", (project_id,))
        return count

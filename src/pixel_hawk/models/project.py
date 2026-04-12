"""ProjectInfo, HistoryChange, and related enums.

`ProjectInfo` is the persistent metadata dataclass for a project. `HistoryChange`
records each diff event. `ProjectState` / `DiffStatus` are the accompanying enums.
"""

import random
import sqlite3
import time
from dataclasses import dataclass, field
from enum import IntEnum

from . import db
from .db import columns
from .geometry import Point, Rectangle, Size
from .person import Person
from .tile import TileInfo


class DiffStatus(IntEnum):
    """Status of a project diff operation."""

    NOT_STARTED = 0
    IN_PROGRESS = 10
    COMPLETE = 20


class ProjectState(IntEnum):
    """State of a project for quota and monitoring purposes."""

    ACTIVE = 0  # Watched, counts towards quota
    PASSIVE = 10  # Checked if tile updates, doesn't count towards quota
    INACTIVE = 20  # Not checked, doesn't count towards quota
    CREATING = 30  # Newly uploaded via Discord, not yet configured


# Coercion map from raw sqlite column value to dataclass field type.
_PROJECT_ADAPTERS = {
    "state": ProjectState,
    "has_missing_tiles": bool,
}


@dataclass
class ProjectInfo:
    """Persistent metadata for a project. Pure dataclass."""

    _EXCLUDE_COLUMNS = frozenset({"owner"})

    id: int = 0
    owner_id: int = 0
    name: str = ""
    state: ProjectState = ProjectState.ACTIVE
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    first_seen: int = 0
    last_check: int = 0
    last_snapshot: int = 0
    max_completion_pixels: int = 0
    max_completion_percent: float = 0.0
    max_completion_time: int = 0
    total_progress: int = 0
    total_regress: int = 0
    largest_regress_pixels: int = 0
    largest_regress_time: int = 0
    recent_rate_pixels_per_hour: float = 0.0
    recent_rate_window_start: int = 0
    has_missing_tiles: bool = True
    last_log_message: str = ""
    owner: Person = field(default_factory=Person)

    @classmethod
    def _from_row(cls, row) -> ProjectInfo:
        kwargs = {}
        for col in columns(cls):
            val = row[col]
            adapter = _PROJECT_ADAPTERS.get(col)
            kwargs[col] = adapter(val) if adapter else val
        return cls(**kwargs)

    @classmethod
    def _from_joined_row(cls, row) -> ProjectInfo:
        """Build a ProjectInfo from a row that also includes aliased owner_* columns."""
        info = cls._from_row(row)
        info.owner = Person(
            id=row["owner_id"],
            name=row["owner_name"],
            discord_id=row["owner_discord_id"],
            access=row["owner_access"],
            max_active_projects=row["owner_max_active_projects"],
            max_watched_tiles=row["owner_max_watched_tiles"],
            watched_tiles_count=row["owner_watched_tiles_count"],
            active_projects_count=row["owner_active_projects_count"],
        )
        return info

    def reset_tracking(self) -> None:
        """Reset percentage-based tracking fields after a target image change."""
        self.last_check = 0
        self.last_snapshot = 0
        self.max_completion_pixels = 0
        self.max_completion_percent = 0.0
        self.max_completion_time = 0
        self.largest_regress_pixels = 0
        self.largest_regress_time = 0
        self.recent_rate_pixels_per_hour = 0.0
        self.recent_rate_window_start = 0
        self.has_missing_tiles = True
        self.last_log_message = ""

    @property
    def rectangle(self) -> Rectangle:
        assert self.state != ProjectState.CREATING, "CREATING projects have no coordinates"
        return Rectangle.from_point_size(Point(self.x, self.y), Size(self.width, self.height))

    @property
    def filename(self) -> str:
        """Filename for this project's PNG on disk."""
        if self.state == ProjectState.CREATING:
            return f"new_{self.id}.png"
        tx, ty, px, py = Point(self.x, self.y).to4()
        return f"{tx}_{ty}_{px}_{py}.png"

    def _column_values(self, fields: list[str]) -> tuple:
        vals = []
        for f in fields:
            v = getattr(self, f)
            if f == "state":
                v = int(v)
            elif f == "has_missing_tiles":
                v = int(v)
            vals.append(v)
        return tuple(vals)

    async def save_as_new(self, max_attempts: int = 50) -> None:
        """Save this instance as a new record with a random ID."""
        cols = columns(type(self))
        col_list = ", ".join(cols)
        placeholders = ", ".join("?" * len(cols))
        for _ in range(max_attempts):
            self.id = random.randint(1, 9999)
            try:
                await db.execute(
                    f"INSERT INTO project ({col_list}) VALUES ({placeholders})",
                    self._column_values(list(cols)),
                )
                return
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError(f"Failed to save project with unique ID after {max_attempts} attempts")

    async def save(self) -> None:
        """Update this record in the database."""
        fields = [c for c in columns(type(self)) if c != "id"]
        sets = ", ".join(f"{f} = ?" for f in fields)
        vals = self._column_values(fields)
        await db.execute(f"UPDATE project SET {sets} WHERE id = ?", (*vals, self.id))

    async def delete(self) -> None:
        await db.execute("DELETE FROM project WHERE id = ?", (self.id,))

    async def fetch_related_owner(self) -> None:
        """Load the owner Person for this project."""
        self.owner = await Person.get_by_id(self.owner_id)

    @classmethod
    async def get_by_id(cls, project_id: int) -> ProjectInfo | None:
        row = await db.fetch_one("SELECT * FROM project WHERE id = ?", (project_id,))
        return cls._from_row(row) if row else None

    @classmethod
    async def get_by_id_with_owner(cls, project_id: int) -> ProjectInfo | None:
        row = await db.fetch_one(
            "SELECT p.*, o.name AS owner_name, o.discord_id AS owner_discord_id, o.access AS owner_access, "
            "o.max_active_projects AS owner_max_active_projects, o.max_watched_tiles AS owner_max_watched_tiles, "
            "o.watched_tiles_count AS owner_watched_tiles_count, o.active_projects_count AS owner_active_projects_count "
            "FROM project p JOIN person o ON p.owner_id = o.id WHERE p.id = ?",
            (project_id,),
        )
        if not row:
            return None
        return cls._from_joined_row(row)

    @classmethod
    async def get_or_none_by_owner(cls, owner_id: int) -> ProjectInfo | None:
        """Return the first project for an owner, or None."""
        row = await db.fetch_one("SELECT * FROM project WHERE owner_id = ?", (owner_id,))
        return cls._from_row(row) if row else None

    @classmethod
    async def filter_by_owner(
        cls, owner_id: int, *, state: ProjectState | None = None, order_by: str = ""
    ) -> list[ProjectInfo]:
        sql = "SELECT * FROM project WHERE owner_id = ?"
        params: list = [owner_id]
        if state is not None:
            sql += " AND state = ?"
            params.append(int(state))
        if order_by:
            sql += f" ORDER BY {order_by}"
        return [cls._from_row(r) for r in await db.fetch_all(sql, tuple(params))]

    @classmethod
    async def get_by_owner_name(cls, owner_id: int, name: str) -> ProjectInfo | None:
        row = await db.fetch_one("SELECT * FROM project WHERE owner_id = ? AND name = ?", (owner_id, name))
        return cls._from_row(row) if row else None

    @classmethod
    async def filter_by_owner_name(cls, owner_id: int, name: str, *, exclude_id: int = 0) -> ProjectInfo | None:
        sql = "SELECT * FROM project WHERE owner_id = ? AND name = ?"
        params: list = [owner_id, name]
        if exclude_id:
            sql += " AND id != ?"
            params.append(exclude_id)
        row = await db.fetch_one(sql, tuple(params))
        return cls._from_row(row) if row else None

    @classmethod
    async def filter_by_coords(
        cls, owner_id: int, x: int, y: int, *, exclude_id: int = 0, exclude_state: ProjectState | None = None
    ) -> ProjectInfo | None:
        sql = "SELECT * FROM project WHERE owner_id = ? AND x = ? AND y = ?"
        params: list = [owner_id, x, y]
        if exclude_id:
            sql += " AND id != ?"
            params.append(exclude_id)
        if exclude_state is not None:
            sql += " AND state != ?"
            params.append(int(exclude_state))
        row = await db.fetch_one(sql, tuple(params))
        return cls._from_row(row) if row else None

    @classmethod
    async def count_by_owner(cls, owner_id: int) -> int:
        return await db.fetch_int("SELECT COUNT(*) FROM project WHERE owner_id = ?", (owner_id,))

    @classmethod
    async def count_by_owner_state(cls, owner_id: int, state: ProjectState) -> int:
        return await db.fetch_int(
            "SELECT COUNT(*) FROM project WHERE owner_id = ? AND state = ?",
            (owner_id, int(state)),
        )

    @classmethod
    async def count_all(cls) -> int:
        return await db.fetch_int("SELECT COUNT(*) FROM project")

    @classmethod
    async def from_rect(
        cls, rect: Rectangle, owner_id: int, name: str, state: ProjectState = ProjectState.ACTIVE
    ) -> ProjectInfo:
        now = round(time.time())
        info = cls(
            owner_id=owner_id,
            name=name,
            state=state,
            x=rect.point.x,
            y=rect.point.y,
            width=rect.size.w,
            height=rect.size.h,
            first_seen=now,
            last_check=now,
        )
        await info.save_as_new()
        return info

    @classmethod
    async def get_or_create_from_rect(cls, rect: Rectangle, owner_id: int, name: str) -> ProjectInfo:
        existing = await cls.filter_by_owner_name(owner_id, name)
        if existing:
            return existing
        return await cls.from_rect(rect, owner_id, name)

    async def link_tiles(self) -> int:
        """Create TileInfo and TileProject records for all tiles in this project's rectangle.

        Runs inside a transaction so a mid-mutation failure leaves no partial rows.
        """
        created_count = 0
        is_active = self.state == ProjectState.ACTIVE
        async with db.transaction():
            for tile in self.rectangle.tiles:
                tile_id = TileInfo.tile_id(tile.x, tile.y)
                existing = await db.fetch_one("SELECT * FROM tile WHERE id = ?", (tile_id,))
                if not existing:
                    await db.execute(
                        "INSERT INTO tile (id, x, y, heat, last_checked, last_update, etag) "
                        "VALUES (?, ?, ?, 0, 0, 0, '')",
                        (tile_id, tile.x, tile.y),
                    )
                    tile_heat = 0
                else:
                    tile_heat = existing["heat"]
                tp = await db.fetch_one(
                    "SELECT id FROM tile_project WHERE tile_id = ? AND project_id = ?", (tile_id, self.id)
                )
                if not tp:
                    await db.execute("INSERT INTO tile_project (tile_id, project_id) VALUES (?, ?)", (tile_id, self.id))
                    created_count += 1
                if is_active and tile_heat == 0:
                    await db.execute("UPDATE tile SET heat = 999 WHERE id = ?", (tile_id,))
        return created_count

    async def unlink_tiles(self) -> int:
        """Delete all TileProject records for this project and adjust tile heat.

        Wrapped in a transaction so the delete + heat rebalance land atomically.
        """
        async with db.transaction():
            tile_ids = [
                r["tile_id"]
                for r in await db.fetch_all("SELECT tile_id FROM tile_project WHERE project_id = ?", (self.id,))
            ]
            deleted = 0
            if tile_ids:
                await db.execute("DELETE FROM tile_project WHERE project_id = ?", (self.id,))
                deleted = len(tile_ids)
                tiles = await TileInfo.filter_by_ids(tile_ids)
                for tile_info in tiles:
                    await tile_info.adjust_project_heat()
            return deleted

    async def adjust_linked_tiles_heat(self) -> None:
        """Re-evaluate heat on all tiles linked to this project."""
        tile_ids = [
            r["tile_id"]
            for r in await db.fetch_all("SELECT tile_id FROM tile_project WHERE project_id = ?", (self.id,))
        ]
        tiles = await TileInfo.filter_by_ids(tile_ids)
        for tile_info in tiles:
            await tile_info.adjust_project_heat()

    @classmethod
    async def get_projects_for_tile(cls, tile_id: int) -> list[ProjectInfo]:
        """Get all ACTIVE/PASSIVE projects linked to a tile, with owners."""
        rows = await db.fetch_all(
            "SELECT p.*, o.name AS owner_name, o.discord_id AS owner_discord_id, o.access AS owner_access, "
            "o.max_active_projects AS owner_max_active_projects, o.max_watched_tiles AS owner_max_watched_tiles, "
            "o.watched_tiles_count AS owner_watched_tiles_count, o.active_projects_count AS owner_active_projects_count "
            "FROM project p "
            "JOIN tile_project tp ON tp.project_id = p.id "
            "JOIN person o ON p.owner_id = o.id "
            "WHERE tp.tile_id = ? AND p.state IN (?, ?)",
            (tile_id, int(ProjectState.ACTIVE), int(ProjectState.PASSIVE)),
        )
        return [cls._from_joined_row(row) for row in rows]


_HISTORY_ADAPTERS = {"status": DiffStatus}


@dataclass
class HistoryChange:
    """Record of a single diff event for a project."""

    _EXCLUDE_COLUMNS = frozenset({"project"})

    id: int = 0
    project_id: int = 0
    timestamp: int = 0
    status: DiffStatus = DiffStatus.NOT_STARTED
    num_remaining: int = 0
    num_target: int = 0
    completion_percent: float = 0.0
    progress_pixels: int = 0
    regress_pixels: int = 0
    project: ProjectInfo = field(default_factory=ProjectInfo)

    @classmethod
    def _from_row(cls, row) -> HistoryChange:
        kwargs = {}
        for col in columns(cls):
            val = row[col]
            adapter = _HISTORY_ADAPTERS.get(col)
            kwargs[col] = adapter(val) if adapter else val
        return cls(**kwargs)

    def _insert_values(self, cols: tuple[str, ...]) -> tuple:
        vals = []
        for c in cols:
            v = getattr(self, c)
            if c == "status":
                v = int(v)
            vals.append(v)
        return tuple(vals)

    @classmethod
    async def create(
        cls,
        *,
        project: ProjectInfo,
        timestamp: int,
        status: DiffStatus,
        num_remaining: int = 0,
        num_target: int = 0,
        completion_percent: float = 0.0,
        progress_pixels: int = 0,
        regress_pixels: int = 0,
    ) -> HistoryChange:
        row_id = await db.execute_insert(
            "INSERT INTO history_change (project_id, timestamp, status, num_remaining, num_target, "
            "completion_percent, progress_pixels, regress_pixels) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                project.id,
                timestamp,
                int(status),
                num_remaining,
                num_target,
                completion_percent,
                progress_pixels,
                regress_pixels,
            ),
        )
        return cls(
            id=row_id,
            project_id=project.id,
            project=project,
            timestamp=timestamp,
            status=status,
            num_remaining=num_remaining,
            num_target=num_target,
            completion_percent=completion_percent,
            progress_pixels=progress_pixels,
            regress_pixels=regress_pixels,
        )

    async def save(self) -> None:
        """Insert this record if new (id=0), or update if existing."""
        cols = columns(type(self))
        if self.id == 0:
            insert_cols = tuple(c for c in cols if c != "id")
            col_list = ", ".join(insert_cols)
            placeholders = ", ".join("?" * len(insert_cols))
            project_id = self.project.id if self.project else self.project_id
            self.project_id = project_id
            self.id = await db.execute_insert(
                f"INSERT INTO history_change ({col_list}) VALUES ({placeholders})",
                self._insert_values(insert_cols),
            )
        else:
            update_cols = tuple(c for c in cols if c != "id")
            sets = ", ".join(f"{c} = ?" for c in update_cols)
            vals = self._insert_values(update_cols)
            await db.execute(f"UPDATE history_change SET {sets} WHERE id = ?", (*vals, self.id))

    @classmethod
    async def filter_by_project(
        cls, project_id: int, *, since: int = 0, order_desc: bool = True, limit: int = 0
    ) -> list[HistoryChange]:
        sql = "SELECT * FROM history_change WHERE project_id = ?"
        params: list = [project_id]
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " ORDER BY timestamp DESC" if order_desc else " ORDER BY timestamp ASC"
        if limit:
            sql += f" LIMIT {limit}"
        return [cls._from_row(r) for r in await db.fetch_all(sql, tuple(params))]

    @classmethod
    async def count_by_project(cls, project_id: int) -> int:
        return await db.fetch_int("SELECT COUNT(*) FROM history_change WHERE project_id = ?", (project_id,))

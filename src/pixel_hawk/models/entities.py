"""Dataclass models for pixel-hawk persistence.

Pure Python dataclasses with class methods for database operations.
All SQL lives here or in db.py — no ORM framework.

Person: Represents a person who can own projects.
ProjectInfo: Project metadata with coordinates and tracking stats.
ProjectState: Enum for project state (active/passive/inactive/creating).
HistoryChange: Per-diff event log recording pixel changes.
DiffStatus: Enum for project diff states.
TileInfo: Tile metadata (coordinates, timestamps, queue assignment, HTTP headers).
TileProject: Junction table for many-to-many tile-project relationships.
GuildConfig: Per-guild bot configuration.
WatchMessage: Persistent Discord watch messages.
"""

import random
import time
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag

from . import db
from .geometry import Point, Rectangle, Size, Tile


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


class BotAccess(IntFlag):
    """Bitmask for bot-level access control on a Person."""

    ALLOWED = 0x100
    ADMIN = 0x10000000


@dataclass
class Person:
    """Represents a person who can own projects."""

    id: int = 0
    name: str = ""
    discord_id: int | None = None
    access: int = 0
    max_active_projects: int = 50
    max_watched_tiles: int = 10
    watched_tiles_count: int = 0
    active_projects_count: int = 0

    @classmethod
    def _from_row(cls, row) -> Person:
        return cls(
            id=row["id"],
            name=row["name"],
            discord_id=row["discord_id"],
            access=row["access"],
            max_active_projects=row["max_active_projects"],
            max_watched_tiles=row["max_watched_tiles"],
            watched_tiles_count=row["watched_tiles_count"],
            active_projects_count=row["active_projects_count"],
        )

    @classmethod
    async def create(cls, *, name: str, discord_id: int | None = None, access: int = 0,
                     max_active_projects: int = 50, max_watched_tiles: int = 10) -> Person:
        row_id = await db.execute_insert(
            "INSERT INTO person (name, discord_id, access, max_active_projects, max_watched_tiles) VALUES (?, ?, ?, ?, ?)",
            (name, discord_id, access, max_active_projects, max_watched_tiles),
        )
        return cls(id=row_id, name=name, discord_id=discord_id, access=access,
                   max_active_projects=max_active_projects, max_watched_tiles=max_watched_tiles)

    async def save(self, update_fields: list[str] | None = None) -> None:
        if update_fields:
            sets = ", ".join(f"{f} = ?" for f in update_fields)
            vals = tuple(getattr(self, f) for f in update_fields)
            await db.execute(f"UPDATE person SET {sets} WHERE id = ?", (*vals, self.id))
        else:
            await db.execute(
                "UPDATE person SET name=?, discord_id=?, access=?, max_active_projects=?, max_watched_tiles=?, "
                "watched_tiles_count=?, active_projects_count=? WHERE id=?",
                (self.name, self.discord_id, self.access, self.max_active_projects, self.max_watched_tiles,
                 self.watched_tiles_count, self.active_projects_count, self.id),
            )

    @classmethod
    async def get(cls, **kwargs) -> Person:
        where, params = _where_clause(kwargs)
        row = await db.fetch_one(f"SELECT * FROM person WHERE {where}", params)
        assert row is not None, f"Person not found: {kwargs}"
        return cls._from_row(row)

    @classmethod
    async def filter(cls, **kwargs) -> list[Person]:
        if not kwargs:
            return [cls._from_row(r) for r in await db.fetch_all("SELECT * FROM person")]
        where, params = _where_clause(kwargs)
        return [cls._from_row(r) for r in await db.fetch_all(f"SELECT * FROM person WHERE {where}", params)]

    @classmethod
    async def all(cls) -> list[Person]:
        return [cls._from_row(r) for r in await db.fetch_all("SELECT * FROM person")]

    @classmethod
    async def count(cls, **kwargs) -> int:
        if not kwargs:
            val = await db.fetch_val("SELECT COUNT(*) FROM person")
        else:
            where, params = _where_clause(kwargs)
            val = await db.fetch_val(f"SELECT COUNT(*) FROM person WHERE {where}", params)
        return val or 0

    async def update_totals(self) -> None:
        """Recalculate and save watched tiles and active projects count."""
        rows = await db.fetch_all(
            "SELECT x, y, width, height FROM project WHERE owner_id = ? AND state = ?",
            (self.id, int(ProjectState.ACTIVE)),
        )
        tiles: set[Tile] = set()
        self.active_projects_count = 0
        for row in rows:
            self.active_projects_count += 1
            rect = Rectangle.from_point_size(Point(row["x"], row["y"]), Size(row["width"], row["height"]))
            tiles.update(rect.tiles)
        self.watched_tiles_count = len(tiles)
        await self.save(update_fields=["watched_tiles_count", "active_projects_count"])


@dataclass
class ProjectInfo:
    """Persistent metadata for a project. Pure dataclass."""

    id: int = 0
    owner_id: int = 0
    owner: Person = field(default_factory=Person)
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

    @classmethod
    def _from_row(cls, row, owner: Person | None = None) -> ProjectInfo:
        info = cls(
            id=row["id"],
            owner_id=row["owner_id"],
            name=row["name"],
            state=ProjectState(row["state"]),
            x=row["x"],
            y=row["y"],
            width=row["width"],
            height=row["height"],
            first_seen=row["first_seen"],
            last_check=row["last_check"],
            last_snapshot=row["last_snapshot"],
            max_completion_pixels=row["max_completion_pixels"],
            max_completion_percent=row["max_completion_percent"],
            max_completion_time=row["max_completion_time"],
            total_progress=row["total_progress"],
            total_regress=row["total_regress"],
            largest_regress_pixels=row["largest_regress_pixels"],
            largest_regress_time=row["largest_regress_time"],
            recent_rate_pixels_per_hour=row["recent_rate_pixels_per_hour"],
            recent_rate_window_start=row["recent_rate_window_start"],
            has_missing_tiles=bool(row["has_missing_tiles"]),
            last_log_message=row["last_log_message"],
        )
        if owner:
            info.owner = owner
        return info

    @classmethod
    async def _from_row_with_owner(cls, row) -> ProjectInfo:
        """Create a ProjectInfo from a joined row that includes owner columns."""
        # Check if owner columns are present (from a JOIN)
        try:
            owner = Person(
                id=row["owner_id"],
                name=row["owner_name"],
                discord_id=row["owner_discord_id"],
                access=row["owner_access"],
                max_active_projects=row["owner_max_active_projects"],
                max_watched_tiles=row["owner_max_watched_tiles"],
                watched_tiles_count=row["owner_watched_tiles_count"],
                active_projects_count=row["owner_active_projects_count"],
            )
        except (IndexError, KeyError):
            owner = None
        return cls._from_row(row, owner)

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

    async def save_as_new(self, max_attempts: int = 50) -> None:
        """Save this instance as a new record with a random ID."""
        for _ in range(max_attempts):
            self.id = random.randint(1, 9999)
            try:
                await db.execute(
                    "INSERT INTO project (id, owner_id, name, state, x, y, width, height, first_seen, last_check, "
                    "last_snapshot, max_completion_pixels, max_completion_percent, max_completion_time, "
                    "total_progress, total_regress, largest_regress_pixels, largest_regress_time, "
                    "recent_rate_pixels_per_hour, recent_rate_window_start, has_missing_tiles, last_log_message) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.id, self.owner_id, self.name, int(self.state), self.x, self.y, self.width, self.height,
                     self.first_seen, self.last_check, self.last_snapshot, self.max_completion_pixels,
                     self.max_completion_percent, self.max_completion_time, self.total_progress, self.total_regress,
                     self.largest_regress_pixels, self.largest_regress_time, self.recent_rate_pixels_per_hour,
                     self.recent_rate_window_start, int(self.has_missing_tiles), self.last_log_message),
                )
                return
            except Exception as e:
                if "UNIQUE constraint" in str(e) or "IntegrityError" in type(e).__name__:
                    continue
                raise
        raise RuntimeError(f"Failed to save project with unique ID after {max_attempts} attempts")

    async def save(self) -> None:
        """Update this record in the database."""
        await db.execute(
            "UPDATE project SET owner_id=?, name=?, state=?, x=?, y=?, width=?, height=?, first_seen=?, "
            "last_check=?, last_snapshot=?, max_completion_pixels=?, max_completion_percent=?, "
            "max_completion_time=?, total_progress=?, total_regress=?, largest_regress_pixels=?, "
            "largest_regress_time=?, recent_rate_pixels_per_hour=?, recent_rate_window_start=?, "
            "has_missing_tiles=?, last_log_message=? WHERE id=?",
            (self.owner_id, self.name, int(self.state), self.x, self.y, self.width, self.height,
             self.first_seen, self.last_check, self.last_snapshot, self.max_completion_pixels,
             self.max_completion_percent, self.max_completion_time, self.total_progress, self.total_regress,
             self.largest_regress_pixels, self.largest_regress_time, self.recent_rate_pixels_per_hour,
             self.recent_rate_window_start, int(self.has_missing_tiles), self.last_log_message, self.id),
        )

    async def delete(self) -> None:
        await db.execute("DELETE FROM project WHERE id = ?", (self.id,))

    async def fetch_related_owner(self) -> None:
        """Load the owner Person for this project."""
        self.owner = await Person.get(id=self.owner_id)

    @classmethod
    async def get(cls, **kwargs) -> ProjectInfo:
        where, params = _where_clause(kwargs, table_map={"owner": "owner_id"})
        row = await db.fetch_one(f"SELECT * FROM project WHERE {where}", params)
        assert row is not None, f"ProjectInfo not found: {kwargs}"
        return cls._from_row(row)

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
        return await cls._from_row_with_owner(row)

    @classmethod
    async def filter_by_owner(cls, owner_id: int, *, state: ProjectState | None = None,
                              order_by: str = "") -> list[ProjectInfo]:
        sql = "SELECT * FROM project WHERE owner_id = ?"
        params: list = [owner_id]
        if state is not None:
            sql += " AND state = ?"
            params.append(int(state))
        if order_by:
            sql += f" ORDER BY {order_by}"
        return [cls._from_row(r) for r in await db.fetch_all(sql, tuple(params))]

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
    async def filter_by_coords(cls, owner_id: int, x: int, y: int, *, exclude_id: int = 0,
                               exclude_state: ProjectState | None = None) -> ProjectInfo | None:
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
        val = await db.fetch_val("SELECT COUNT(*) FROM project WHERE owner_id = ?", (owner_id,))
        return val or 0

    @classmethod
    async def from_rect(cls, rect: Rectangle, owner_id: int, name: str,
                        state: ProjectState = ProjectState.ACTIVE) -> ProjectInfo:
        now = round(time.time())
        info = cls(
            owner_id=owner_id, name=name, state=state,
            x=rect.point.x, y=rect.point.y, width=rect.size.w, height=rect.size.h,
            first_seen=now, last_check=now,
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
        """Create TileInfo and TileProject records for all tiles in this project's rectangle."""
        created_count = 0
        is_active = self.state == ProjectState.ACTIVE
        for tile in self.rectangle.tiles:
            tile_id = TileInfo.tile_id(tile.x, tile.y)
            # get or create tile
            existing = await db.fetch_one("SELECT * FROM tile WHERE id = ?", (tile_id,))
            if not existing:
                await db.execute(
                    "INSERT INTO tile (id, x, y, heat, last_checked, last_update, etag) VALUES (?, ?, ?, 0, 0, 0, '')",
                    (tile_id, tile.x, tile.y),
                )
                tile_heat = 0
            else:
                tile_heat = existing["heat"]
            # get or create tile_project
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
        """Delete all TileProject records for this project and adjust tile heat."""
        tile_ids = [r["tile_id"] for r in await db.fetch_all(
            "SELECT tile_id FROM tile_project WHERE project_id = ?", (self.id,)
        )]
        deleted = 0
        if tile_ids:
            await db.execute("DELETE FROM tile_project WHERE project_id = ?", (self.id,))
            deleted = len(tile_ids)
            for tile_id in tile_ids:
                tile_info = await TileInfo.get_by_id(tile_id)
                if tile_info:
                    await tile_info.adjust_project_heat()
        return deleted

    async def adjust_linked_tiles_heat(self) -> None:
        """Re-evaluate heat on all tiles linked to this project."""
        tile_ids = [r["tile_id"] for r in await db.fetch_all(
            "SELECT tile_id FROM tile_project WHERE project_id = ?", (self.id,)
        )]
        for tile_id in tile_ids:
            tile_info = await TileInfo.get_by_id(tile_id)
            if tile_info:
                await tile_info.adjust_project_heat()

    async def get_projects_for_tile(self, tile_id: int) -> list[ProjectInfo]:
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
        result = []
        for row in rows:
            info = await ProjectInfo._from_row_with_owner(row)
            result.append(info)
        return result


@dataclass
class HistoryChange:
    """Record of a single diff event for a project."""

    id: int = 0
    project_id: int = 0
    project: ProjectInfo | None = None
    timestamp: int = 0
    status: DiffStatus = DiffStatus.NOT_STARTED
    num_remaining: int = 0
    num_target: int = 0
    completion_percent: float = 0.0
    progress_pixels: int = 0
    regress_pixels: int = 0

    @classmethod
    def _from_row(cls, row) -> HistoryChange:
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            timestamp=row["timestamp"],
            status=DiffStatus(row["status"]),
            num_remaining=row["num_remaining"],
            num_target=row["num_target"],
            completion_percent=row["completion_percent"],
            progress_pixels=row["progress_pixels"],
            regress_pixels=row["regress_pixels"],
        )

    @classmethod
    async def create(cls, *, project: ProjectInfo, timestamp: int, status: DiffStatus,
                     num_remaining: int = 0, num_target: int = 0, completion_percent: float = 0.0,
                     progress_pixels: int = 0, regress_pixels: int = 0) -> HistoryChange:
        row_id = await db.execute_insert(
            "INSERT INTO history_change (project_id, timestamp, status, num_remaining, num_target, "
            "completion_percent, progress_pixels, regress_pixels) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (project.id, timestamp, int(status), num_remaining, num_target,
             completion_percent, progress_pixels, regress_pixels),
        )
        return cls(id=row_id, project_id=project.id, project=project, timestamp=timestamp, status=status,
                   num_remaining=num_remaining, num_target=num_target, completion_percent=completion_percent,
                   progress_pixels=progress_pixels, regress_pixels=regress_pixels)

    async def save(self) -> None:
        """Insert this record if new (id=0), or update if existing."""
        if self.id == 0:
            project_id = self.project.id if self.project else self.project_id
            self.id = await db.execute_insert(
                "INSERT INTO history_change (project_id, timestamp, status, num_remaining, num_target, "
                "completion_percent, progress_pixels, regress_pixels) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, self.timestamp, int(self.status), self.num_remaining, self.num_target,
                 self.completion_percent, self.progress_pixels, self.regress_pixels),
            )
        else:
            await db.execute(
                "UPDATE history_change SET project_id=?, timestamp=?, status=?, num_remaining=?, num_target=?, "
                "completion_percent=?, progress_pixels=?, regress_pixels=? WHERE id=?",
                (self.project_id, self.timestamp, int(self.status), self.num_remaining, self.num_target,
                 self.completion_percent, self.progress_pixels, self.regress_pixels, self.id),
            )

    @classmethod
    async def filter_by_project(cls, project_id: int, *, since: int = 0, order_desc: bool = True,
                                limit: int = 0) -> list[HistoryChange]:
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
        val = await db.fetch_val("SELECT COUNT(*) FROM history_change WHERE project_id = ?", (project_id,))
        return val or 0


@dataclass
class TileInfo:
    """Persistent metadata for a single WPlace tile."""

    id: int = 0
    x: int = 0
    y: int = 0
    heat: int = 999
    last_checked: int = 0
    last_update: int = 0
    etag: str = ""

    @classmethod
    def _from_row(cls, row) -> TileInfo:
        return cls(
            id=row["id"], x=row["x"], y=row["y"], heat=row["heat"],
            last_checked=row["last_checked"], last_update=row["last_update"], etag=row["etag"],
        )

    @staticmethod
    def tile_id(x: int, y: int) -> int:
        """Compute primary key from tile coordinates."""
        return x * 10000 + y

    @property
    def tile(self) -> Tile:
        return Tile(self.x, self.y)

    @classmethod
    async def create(cls, *, id: int, x: int, y: int, heat: int = 999,
                     last_checked: int = 0, last_update: int = 0, etag: str = "") -> TileInfo:
        await db.execute(
            "INSERT INTO tile (id, x, y, heat, last_checked, last_update, etag) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, x, y, heat, last_checked, last_update, etag),
        )
        return cls(id=id, x=x, y=y, heat=heat, last_checked=last_checked, last_update=last_update, etag=etag)

    @classmethod
    async def get(cls, **kwargs) -> TileInfo:
        where, params = _where_clause(kwargs)
        row = await db.fetch_one(f"SELECT * FROM tile WHERE {where}", params)
        assert row is not None, f"TileInfo not found: {kwargs}"
        return cls._from_row(row)

    @classmethod
    async def get_by_id(cls, tile_id: int) -> TileInfo | None:
        row = await db.fetch_one("SELECT * FROM tile WHERE id = ?", (tile_id,))
        return cls._from_row(row) if row else None

    @classmethod
    async def get_or_create(cls, *, id: int, x: int, y: int, heat: int = 999,
                            last_checked: int = 0, last_update: int = 0, etag: str = "") -> tuple[TileInfo, bool]:
        existing = await cls.get_by_id(id)
        if existing:
            return existing, False
        tile = await cls.create(id=id, x=x, y=y, heat=heat, last_checked=last_checked,
                                last_update=last_update, etag=etag)
        return tile, True

    async def save(self, update_fields: list[str] | None = None) -> None:
        if update_fields:
            sets = ", ".join(f"{f} = ?" for f in update_fields)
            vals = tuple(getattr(self, f) for f in update_fields)
            await db.execute(f"UPDATE tile SET {sets} WHERE id = ?", (*vals, self.id))
        else:
            await db.execute(
                "UPDATE tile SET x=?, y=?, heat=?, last_checked=?, last_update=?, etag=? WHERE id=?",
                (self.x, self.y, self.heat, self.last_checked, self.last_update, self.etag, self.id),
            )

    async def refresh_from_db(self) -> None:
        """Reload this tile's fields from the database."""
        row = await db.fetch_one("SELECT * FROM tile WHERE id = ?", (self.id,))
        assert row is not None
        self.x, self.y, self.heat = row["x"], row["y"], row["heat"]
        self.last_checked, self.last_update, self.etag = row["last_checked"], row["last_update"], row["etag"]

    @classmethod
    async def all(cls) -> list[TileInfo]:
        return [cls._from_row(r) for r in await db.fetch_all("SELECT * FROM tile")]

    @classmethod
    async def filter_for_redistribution(cls) -> list[TileInfo]:
        """Get all tiles eligible for temperature queue redistribution (heat>0, last_update>0), ordered by -last_update."""
        return [cls._from_row(r) for r in await db.fetch_all(
            "SELECT * FROM tile WHERE heat > 0 AND last_update > 0 ORDER BY last_update DESC"
        )]

    @classmethod
    async def filter_by_heat(cls, heat: int, *, order_by_last_checked: bool = False) -> list[TileInfo]:
        sql = "SELECT * FROM tile WHERE heat = ?"
        if order_by_last_checked:
            sql += " ORDER BY last_checked ASC"
        return [cls._from_row(r) for r in await db.fetch_all(sql, (heat,))]

    @classmethod
    async def select_from_queue(cls, heat: int) -> TileInfo | None:
        """Select least recently checked tile from a heat queue."""
        row = await db.fetch_one("SELECT * FROM tile WHERE heat = ? ORDER BY last_checked ASC LIMIT 1", (heat,))
        return cls._from_row(row) if row else None

    @classmethod
    async def bulk_update_heat(cls, tile_ids: list[int], heat: int) -> None:
        if not tile_ids:
            return
        placeholders = ",".join("?" * len(tile_ids))
        await db.execute(f"UPDATE tile SET heat = ? WHERE id IN ({placeholders})", (heat, *tile_ids))

    @classmethod
    async def count_by_heat(cls, *, heat_gte: int = 0, heat_lte: int = 999) -> int:
        val = await db.fetch_val("SELECT COUNT(*) FROM tile WHERE heat >= ? AND heat <= ?", (heat_gte, heat_lte))
        return val or 0

    async def adjust_project_heat(self) -> None:
        """Verifies if heat 0 is consistent with the presence or absence of an ACTIVE project."""
        has_active = await db.fetch_val(
            "SELECT EXISTS(SELECT 1 FROM tile_project tp JOIN project p ON tp.project_id = p.id "
            "WHERE tp.tile_id = ? AND p.state = ?)",
            (self.id, int(ProjectState.ACTIVE)),
        )
        if not has_active:
            if self.heat != 0:
                self.heat = 0
                await self.save(update_fields=["heat"])
        elif self.heat == 0:
            self.heat = 999
            await self.save(update_fields=["heat"])


@dataclass
class TileProject:
    """Many-to-many relationship between tiles and projects."""

    id: int = 0
    tile_id: int = 0
    project_id: int = 0

    @classmethod
    def _from_row(cls, row) -> TileProject:
        return cls(id=row["id"], tile_id=row["tile_id"], project_id=row["project_id"])

    @classmethod
    async def create(cls, *, tile: TileInfo | None = None, project: ProjectInfo | None = None,
                     tile_id: int = 0, project_id: int = 0) -> TileProject:
        tid = tile.id if tile else tile_id
        pid = project.id if project else project_id
        row_id = await db.execute_insert(
            "INSERT INTO tile_project (tile_id, project_id) VALUES (?, ?)", (tid, pid)
        )
        return cls(id=row_id, tile_id=tid, project_id=pid)

    @classmethod
    async def filter_by_tile(cls, tile_id: int) -> list[TileProject]:
        return [cls._from_row(r) for r in await db.fetch_all(
            "SELECT * FROM tile_project WHERE tile_id = ?", (tile_id,)
        )]

    @classmethod
    async def filter_by_project(cls, project_id: int) -> list[TileProject]:
        return [cls._from_row(r) for r in await db.fetch_all(
            "SELECT * FROM tile_project WHERE project_id = ?", (project_id,)
        )]


@dataclass
class GuildConfig:
    """Per-guild configuration for the Discord bot."""

    guild_id: int = 0
    required_role: str = ""
    max_active_projects: int = 50
    max_watched_tiles: int = 10

    @classmethod
    def _from_row(cls, row) -> GuildConfig:
        return cls(
            guild_id=row["guild_id"], required_role=row["required_role"],
            max_active_projects=row["max_active_projects"], max_watched_tiles=row["max_watched_tiles"],
        )

    @classmethod
    async def create(cls, *, guild_id: int, required_role: str,
                     max_active_projects: int = 50, max_watched_tiles: int = 10) -> GuildConfig:
        await db.execute(
            "INSERT INTO guild_config (guild_id, required_role, max_active_projects, max_watched_tiles) "
            "VALUES (?, ?, ?, ?)",
            (guild_id, required_role, max_active_projects, max_watched_tiles),
        )
        return cls(guild_id=guild_id, required_role=required_role,
                   max_active_projects=max_active_projects, max_watched_tiles=max_watched_tiles)

    @classmethod
    async def get_by_guild(cls, guild_id: int) -> GuildConfig | None:
        row = await db.fetch_one("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,))
        return cls._from_row(row) if row else None

    @classmethod
    async def get(cls, **kwargs) -> GuildConfig:
        where, params = _where_clause(kwargs)
        row = await db.fetch_one(f"SELECT * FROM guild_config WHERE {where}", params)
        assert row is not None, f"GuildConfig not found: {kwargs}"
        return cls._from_row(row)

    async def save(self) -> None:
        await db.execute(
            "UPDATE guild_config SET required_role=?, max_active_projects=?, max_watched_tiles=? WHERE guild_id=?",
            (self.required_role, self.max_active_projects, self.max_watched_tiles, self.guild_id),
        )

    @classmethod
    async def update_or_create(cls, *, guild_id: int, defaults: dict) -> GuildConfig:
        existing = await cls.get_by_guild(guild_id)
        if existing:
            for k, v in defaults.items():
                setattr(existing, k, v)
            await existing.save()
            return existing
        return await cls.create(guild_id=guild_id, **defaults)


@dataclass
class WatchMessage:
    """A Discord message that auto-updates with project stats on every diff."""

    message_id: int = 0
    project_id: int = 0
    project: ProjectInfo | None = None
    channel_id: int = 0

    @classmethod
    def _from_row(cls, row, project: ProjectInfo | None = None) -> WatchMessage:
        return cls(
            message_id=row["message_id"], project_id=row["project_id"],
            channel_id=row["channel_id"], project=project,
        )

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
        return [cls._from_row(r) for r in await db.fetch_all(
            "SELECT * FROM watch_message WHERE project_id = ?", (project_id,)
        )]

    @classmethod
    async def filter_by_projects_with_owner(cls, project_ids: list[int]) -> list[WatchMessage]:
        """Batch query watches for given project IDs, with project and owner data."""
        if not project_ids:
            return []
        placeholders = ",".join("?" * len(project_ids))
        rows = await db.fetch_all(
            f"SELECT w.*, p.id AS p_id, p.owner_id, p.name AS p_name, p.state, p.x, p.y, p.width, p.height, "
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
            owner = Person(
                id=row["owner_id"], name=row["owner_name"], discord_id=row["owner_discord_id"],
                access=row["owner_access"], max_active_projects=row["owner_max_active_projects"],
                max_watched_tiles=row["owner_max_watched_tiles"],
                watched_tiles_count=row["owner_watched_tiles_count"],
                active_projects_count=row["owner_active_projects_count"],
            )
            # Build a pseudo-row for ProjectInfo._from_row
            project = ProjectInfo(
                id=row["p_id"], owner_id=row["owner_id"], owner=owner, name=row["p_name"],
                state=ProjectState(row["state"]), x=row["x"], y=row["y"], width=row["width"], height=row["height"],
                first_seen=row["first_seen"], last_check=row["last_check"], last_snapshot=row["last_snapshot"],
                max_completion_pixels=row["max_completion_pixels"],
                max_completion_percent=row["max_completion_percent"],
                max_completion_time=row["max_completion_time"], total_progress=row["total_progress"],
                total_regress=row["total_regress"], largest_regress_pixels=row["largest_regress_pixels"],
                largest_regress_time=row["largest_regress_time"],
                recent_rate_pixels_per_hour=row["recent_rate_pixels_per_hour"],
                recent_rate_window_start=row["recent_rate_window_start"],
                has_missing_tiles=bool(row["has_missing_tiles"]), last_log_message=row["last_log_message"],
            )
            result.append(cls._from_row(row, project=project))
        return result

    @classmethod
    async def count_by_project(cls, project_id: int) -> int:
        val = await db.fetch_val("SELECT COUNT(*) FROM watch_message WHERE project_id = ?", (project_id,))
        return val or 0

    @classmethod
    async def delete_by_project(cls, project_id: int) -> int:
        count = await cls.count_by_project(project_id)
        if count:
            await db.execute("DELETE FROM watch_message WHERE project_id = ?", (project_id,))
        return count


def _where_clause(kwargs: dict, *, table_map: dict[str, str] | None = None) -> tuple[str, tuple]:
    """Build a WHERE clause from keyword arguments."""
    parts = []
    values = []
    for key, val in kwargs.items():
        col = (table_map or {}).get(key, key)
        parts.append(f"{col} = ?")
        values.append(val)
    return " AND ".join(parts), tuple(values)

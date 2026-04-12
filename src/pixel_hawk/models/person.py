"""Person entity: a user who can own projects.

`BotAccess` is an IntFlag bitmask controlling per-user bot permissions.
"""

from dataclasses import dataclass
from enum import IntFlag

from . import db
from ._sql import _columns
from .geometry import Point, Rectangle, Size, Tile


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
        kwargs = {col: row[col] for col in _columns(cls)}
        return cls(**kwargs)

    @classmethod
    async def create(
        cls,
        *,
        name: str,
        discord_id: int | None = None,
        access: int = 0,
        max_active_projects: int = 50,
        max_watched_tiles: int = 10,
    ) -> Person:
        row_id = await db.execute_insert(
            "INSERT INTO person (name, discord_id, access, max_active_projects, max_watched_tiles) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, discord_id, access, max_active_projects, max_watched_tiles),
        )
        return cls(
            id=row_id,
            name=name,
            discord_id=discord_id,
            access=access,
            max_active_projects=max_active_projects,
            max_watched_tiles=max_watched_tiles,
        )

    async def save(self, update_fields: list[str] | None = None) -> None:
        if update_fields:
            fields = update_fields
        else:
            fields = [c for c in _columns(type(self)) if c != "id"]
        sets = ", ".join(f"{f} = ?" for f in fields)
        vals = tuple(getattr(self, f) for f in fields)
        await db.execute(f"UPDATE person SET {sets} WHERE id = ?", (*vals, self.id))

    @classmethod
    async def get_by_id(cls, person_id: int) -> Person:
        row = await db.fetch_one("SELECT * FROM person WHERE id = ?", (person_id,))
        assert row is not None, f"Person not found: id={person_id}"
        return cls._from_row(row)

    @classmethod
    async def get_or_none_by_id(cls, person_id: int) -> Person | None:
        row = await db.fetch_one("SELECT * FROM person WHERE id = ?", (person_id,))
        return cls._from_row(row) if row else None

    @classmethod
    async def get_by_discord_id(cls, discord_id: int) -> Person:
        row = await db.fetch_one("SELECT * FROM person WHERE discord_id = ?", (discord_id,))
        assert row is not None, f"Person not found: discord_id={discord_id}"
        return cls._from_row(row)

    @classmethod
    async def get_or_none_by_discord_id(cls, discord_id: int) -> Person | None:
        row = await db.fetch_one("SELECT * FROM person WHERE discord_id = ?", (discord_id,))
        return cls._from_row(row) if row else None

    @classmethod
    async def count_by_discord_id(cls, discord_id: int) -> int:
        return await db.fetch_int("SELECT COUNT(*) FROM person WHERE discord_id = ?", (discord_id,))

    @classmethod
    async def all(cls) -> list[Person]:
        return [cls._from_row(r) for r in await db.fetch_all("SELECT * FROM person")]

    @classmethod
    async def count_all(cls) -> int:
        return await db.fetch_int("SELECT COUNT(*) FROM person")

    async def update_totals(self) -> None:
        """Recalculate and save watched tiles and active projects count."""
        from .project import ProjectState  # avoid circular import at module load

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

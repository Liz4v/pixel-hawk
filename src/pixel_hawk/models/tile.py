"""TileInfo and TileProject entities: WPlace tile metadata and project links."""

from dataclasses import dataclass

from . import db
from ._sql import _columns
from .geometry import Tile


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
        kwargs = {col: row[col] for col in _columns(cls)}
        return cls(**kwargs)

    @staticmethod
    def tile_id(x: int, y: int) -> int:
        """Compute primary key from tile coordinates."""
        return x * 10000 + y

    @property
    def tile(self) -> Tile:
        return Tile(self.x, self.y)

    @classmethod
    async def create(
        cls, *, id: int, x: int, y: int, heat: int = 999, last_checked: int = 0, last_update: int = 0, etag: str = ""
    ) -> TileInfo:
        await db.execute(
            "INSERT INTO tile (id, x, y, heat, last_checked, last_update, etag) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (id, x, y, heat, last_checked, last_update, etag),
        )
        return cls(id=id, x=x, y=y, heat=heat, last_checked=last_checked, last_update=last_update, etag=etag)

    @classmethod
    async def get_by_id(cls, tile_id: int) -> TileInfo | None:
        row = await db.fetch_one("SELECT * FROM tile WHERE id = ?", (tile_id,))
        return cls._from_row(row) if row else None

    @classmethod
    async def get_by_coords(cls, x: int, y: int) -> TileInfo | None:
        row = await db.fetch_one("SELECT * FROM tile WHERE x = ? AND y = ?", (x, y))
        return cls._from_row(row) if row else None

    @classmethod
    async def filter_by_ids(cls, tile_ids: list[int]) -> list[TileInfo]:
        """Batch fetch tiles by id. Single query, empty list on empty input."""
        if not tile_ids:
            return []
        placeholders = ",".join("?" * len(tile_ids))
        rows = await db.fetch_all(f"SELECT * FROM tile WHERE id IN ({placeholders})", tuple(tile_ids))
        return [cls._from_row(r) for r in rows]

    @classmethod
    async def get_or_create(
        cls, *, id: int, x: int, y: int, heat: int = 999, last_checked: int = 0, last_update: int = 0, etag: str = ""
    ) -> tuple[TileInfo, bool]:
        existing = await cls.get_by_id(id)
        if existing:
            return existing, False
        tile = await cls.create(
            id=id, x=x, y=y, heat=heat, last_checked=last_checked, last_update=last_update, etag=etag
        )
        return tile, True

    async def save(self, update_fields: list[str] | None = None) -> None:
        if update_fields:
            fields = update_fields
        else:
            fields = [c for c in _columns(type(self)) if c != "id"]
        sets = ", ".join(f"{f} = ?" for f in fields)
        vals = tuple(getattr(self, f) for f in fields)
        await db.execute(f"UPDATE tile SET {sets} WHERE id = ?", (*vals, self.id))

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
        """Tiles eligible for temperature queue redistribution, ordered by -last_update."""
        return [
            cls._from_row(r)
            for r in await db.fetch_all(
                "SELECT * FROM tile WHERE heat > 0 AND last_update > 0 ORDER BY last_update DESC"
            )
        ]

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
        return await db.fetch_int("SELECT COUNT(*) FROM tile WHERE heat >= ? AND heat <= ?", (heat_gte, heat_lte))

    async def adjust_project_heat(self) -> None:
        """Verify heat is consistent with the presence or absence of ACTIVE projects."""
        from .project import ProjectState

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
        kwargs = {col: row[col] for col in _columns(cls)}
        return cls(**kwargs)

    @classmethod
    async def create(
        cls, *, tile: TileInfo | None = None, project=None, tile_id: int = 0, project_id: int = 0
    ) -> TileProject:
        tid = tile.id if tile else tile_id
        pid = project.id if project else project_id
        row_id = await db.execute_insert("INSERT INTO tile_project (tile_id, project_id) VALUES (?, ?)", (tid, pid))
        return cls(id=row_id, tile_id=tid, project_id=pid)

    @classmethod
    async def filter_by_tile(cls, tile_id: int) -> list[TileProject]:
        return [
            cls._from_row(r) for r in await db.fetch_all("SELECT * FROM tile_project WHERE tile_id = ?", (tile_id,))
        ]

    @classmethod
    async def filter_by_project(cls, project_id: int) -> list[TileProject]:
        return [
            cls._from_row(r)
            for r in await db.fetch_all("SELECT * FROM tile_project WHERE project_id = ?", (project_id,))
        ]

    @classmethod
    async def count_by_project(cls, project_id: int) -> int:
        return await db.fetch_int("SELECT COUNT(*) FROM tile_project WHERE project_id = ?", (project_id,))

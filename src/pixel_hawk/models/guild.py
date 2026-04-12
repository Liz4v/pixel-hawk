"""GuildConfig entity: per-guild configuration for the Discord bot."""

from dataclasses import dataclass

from . import db
from ._sql import _columns


@dataclass
class GuildConfig:
    """Per-guild configuration for the Discord bot."""

    guild_id: int = 0
    required_role: str = ""
    max_active_projects: int = 50
    max_watched_tiles: int = 10

    @classmethod
    def _from_row(cls, row) -> GuildConfig:
        kwargs = {col: row[col] for col in _columns(cls)}
        return cls(**kwargs)

    @classmethod
    async def create(
        cls, *, guild_id: int, required_role: str, max_active_projects: int = 50, max_watched_tiles: int = 10
    ) -> GuildConfig:
        await db.execute(
            "INSERT INTO guild_config (guild_id, required_role, max_active_projects, max_watched_tiles) "
            "VALUES (?, ?, ?, ?)",
            (guild_id, required_role, max_active_projects, max_watched_tiles),
        )
        return cls(
            guild_id=guild_id,
            required_role=required_role,
            max_active_projects=max_active_projects,
            max_watched_tiles=max_watched_tiles,
        )

    @classmethod
    async def get_by_guild(cls, guild_id: int) -> GuildConfig | None:
        row = await db.fetch_one("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,))
        return cls._from_row(row) if row else None

    async def save(self) -> None:
        fields = [c for c in _columns(type(self)) if c != "guild_id"]
        sets = ", ".join(f"{f} = ?" for f in fields)
        vals = tuple(getattr(self, f) for f in fields)
        await db.execute(f"UPDATE guild_config SET {sets} WHERE guild_id = ?", (*vals, self.guild_id))

    @classmethod
    async def update_or_create(cls, *, guild_id: int, defaults: dict) -> GuildConfig:
        existing = await cls.get_by_guild(guild_id)
        if existing:
            for k, v in defaults.items():
                setattr(existing, k, v)
            await existing.save()
            return existing
        return await cls.create(guild_id=guild_id, **defaults)

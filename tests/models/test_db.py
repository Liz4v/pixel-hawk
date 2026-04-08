"""Tests for database initialization and query helpers."""

from pixel_hawk.models import db
from pixel_hawk.models.entities import Person, TileInfo


# --- Basic connection and schema ---


async def test_schema_creates_tables():
    """All expected tables exist after database() context."""
    tables = [r[0] for r in await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )]
    assert "person" in tables
    assert "project" in tables
    assert "history_change" in tables
    assert "tile" in tables
    assert "tile_project" in tables
    assert "guild_config" in tables
    assert "watch_message" in tables


async def test_foreign_keys_enabled():
    """PRAGMA foreign_keys is ON."""
    val = await db.fetch_val("PRAGMA foreign_keys")
    assert val == 1


# --- Query helpers ---


async def test_execute_insert_returns_rowid():
    """execute_insert returns the new row's ID."""
    row_id = await db.execute_insert(
        "INSERT INTO person (name, access) VALUES (?, ?)", ("Test", 0)
    )
    assert row_id > 0


async def test_fetch_one_returns_none_on_miss():
    """fetch_one returns None when no rows match."""
    result = await db.fetch_one("SELECT * FROM person WHERE id = ?", (99999,))
    assert result is None


async def test_fetch_all_empty():
    """fetch_all returns empty list on no matches."""
    result = await db.fetch_all("SELECT * FROM person WHERE id = ?", (99999,))
    assert result == []


async def test_fetch_val_returns_scalar():
    """fetch_val returns a single value."""
    await Person.create(name="Alice")
    count = await db.fetch_val("SELECT COUNT(*) FROM person")
    assert count == 1


async def test_fetch_val_returns_none_on_empty():
    """fetch_val returns None when the query has no results."""
    result = await db.fetch_val("SELECT id FROM person WHERE id = ?", (99999,))
    assert result is None


# --- Data roundtrip ---


async def test_person_roundtrip():
    """Data inserted via model survives a raw SQL read."""
    p = await Person.create(name="Alice", discord_id=12345)
    row = await db.fetch_one("SELECT * FROM person WHERE id = ?", (p.id,))
    assert row["name"] == "Alice"
    assert row["discord_id"] == 12345


async def test_tile_roundtrip():
    """TileInfo with manual PK persists correctly."""
    t = await TileInfo.create(id=TileInfo.tile_id(3, 7), x=3, y=7, heat=999, last_checked=100, last_update=200)
    row = await db.fetch_one("SELECT * FROM tile WHERE id = ?", (t.id,))
    assert row["x"] == 3
    assert row["y"] == 7
    assert row["heat"] == 999
    assert row["last_checked"] == 100

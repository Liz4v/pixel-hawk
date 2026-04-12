"""Tests for database initialization and query helpers."""

import aiosqlite
import pytest

from pixel_hawk.models import db
from pixel_hawk.models.person import Person
from pixel_hawk.models.tile import TileInfo


# --- Basic connection and schema ---


async def test_schema_creates_tables():
    """All expected tables exist after database() context."""
    tables = [
        r[0]
        for r in await db.fetch_all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    ]
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
    row_id = await db.execute_insert("INSERT INTO person (name, access) VALUES (?, ?)", ("Test", 0))
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


# --- Nested database() connection save/restore ---


async def test_nested_database_restores_outer_connection(tmp_path):
    """Opening a second database() inside the first preserves the outer _conn."""
    outer_conn = db.get_conn()
    inner_path = str(tmp_path / "inner.db")
    async with db.database(db_path=inner_path):
        inner_conn = db.get_conn()
        assert inner_conn is not outer_conn
    # After exiting the inner context, the outer connection is the current one
    assert db.get_conn() is outer_conn


# --- fetch_int edge cases ---


async def test_fetch_int_no_rows():
    """fetch_int returns 0 when the query matches no rows."""
    val = await db.fetch_int("SELECT id FROM person WHERE id = ?", (99999,))
    assert val == 0


async def test_fetch_int_null_scalar():
    """fetch_int returns 0 for a SELECT NULL query."""
    val = await db.fetch_int("SELECT NULL")
    assert val == 0


async def test_fetch_int_normal_count():
    """fetch_int returns the integer for a normal scalar query."""
    await Person.create(name="a")
    await Person.create(name="b")
    val = await db.fetch_int("SELECT COUNT(*) FROM person")
    assert val == 2


# --- _assert_db_writable ---


async def test_assert_db_writable_raises_on_exclusive_lock(tmp_path):
    """A second connection holding an exclusive lock blocks database() startup."""
    db_path = str(tmp_path / "locked.db")
    # Open first connection and take an exclusive lock
    locker = await aiosqlite.connect(db_path)
    await locker.execute("CREATE TABLE dummy (id INTEGER)")
    await locker.commit()
    await locker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(Exception):
            async with db.database(db_path=db_path):
                pass
    finally:
        await locker.rollback()
        await locker.close()


# --- db.transaction() ---


async def test_transaction_commits_on_clean_exit():
    """A clean transaction() block commits all statements."""
    async with db.transaction():
        await db.execute("INSERT INTO person (name) VALUES (?)", ("tx-clean",))
    count = await db.fetch_int("SELECT COUNT(*) FROM person WHERE name = ?", ("tx-clean",))
    assert count == 1


async def test_transaction_rolls_back_on_exception():
    """Raising inside transaction() rolls back all statements in the block."""

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with db.transaction():
            await db.execute("INSERT INTO person (name) VALUES (?)", ("tx-rollback",))
            raise Boom()
    count = await db.fetch_int("SELECT COUNT(*) FROM person WHERE name = ?", ("tx-rollback",))
    assert count == 0


async def test_transaction_suppresses_inner_autocommit():
    """db.execute() inside a transaction does not auto-commit mid-block."""
    async with db.transaction():
        await db.execute("INSERT INTO person (name) VALUES (?)", ("first",))
        await db.execute("INSERT INTO person (name) VALUES (?)", ("second",))
        # Both rows should be visible to the same connection, but only commit on exit
        mid_count = await db.fetch_int("SELECT COUNT(*) FROM person WHERE name IN (?, ?)", ("first", "second"))
        assert mid_count == 2
    final_count = await db.fetch_int("SELECT COUNT(*) FROM person WHERE name IN (?, ?)", ("first", "second"))
    assert final_count == 2


async def test_transaction_nested_is_noop():
    """Nested transaction() is a no-op — outer transaction controls the boundary."""

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with db.transaction():
            await db.execute("INSERT INTO person (name) VALUES (?)", ("outer",))
            async with db.transaction():
                await db.execute("INSERT INTO person (name) VALUES (?)", ("inner",))
            # Inner exit should NOT have committed — outer raise rolls back both
            raise Boom()
    count = await db.fetch_int("SELECT COUNT(*) FROM person WHERE name IN (?, ?)", ("outer", "inner"))
    assert count == 0


# --- Migration bootstrap ---


async def test_migrations_bootstrap_stamps_without_running(tmp_path, monkeypatch):
    """Version 0 + person table → stamps to len(MIGRATIONS), no migration runs."""
    ran: list[str] = []

    async def m1(conn: aiosqlite.Connection) -> None:
        ran.append("m1")

    monkeypatch.setattr(db, "MIGRATIONS", [m1])
    db_path = str(tmp_path / "bootstrap.db")
    async with db.database(db_path=db_path):
        version = await db.fetch_val("PRAGMA user_version")
        assert version == 1
        assert ran == []


async def test_migrations_run_after_bootstrap(tmp_path, monkeypatch):
    """After bootstrap, appending a new migration runs only the new one."""
    ran: list[str] = []

    async def m1(conn: aiosqlite.Connection) -> None:
        ran.append("m1")

    async def m2(conn: aiosqlite.Connection) -> None:
        ran.append("m2")

    db_path = str(tmp_path / "append.db")

    # First run: bootstrap with one migration, stamps to 1 without running
    monkeypatch.setattr(db, "MIGRATIONS", [m1])
    async with db.database(db_path=db_path):
        assert await db.fetch_val("PRAGMA user_version") == 1
    assert ran == []

    # Second run: append m2, should run only m2
    monkeypatch.setattr(db, "MIGRATIONS", [m1, m2])
    async with db.database(db_path=db_path):
        assert await db.fetch_val("PRAGMA user_version") == 2
    assert ran == ["m2"]


async def test_migrations_empty_list_stamps_to_zero(tmp_path):
    """With MIGRATIONS empty, version stamps to 0 and nothing runs."""
    db_path = str(tmp_path / "empty.db")
    async with db.database(db_path=db_path):
        assert await db.fetch_val("PRAGMA user_version") == 0

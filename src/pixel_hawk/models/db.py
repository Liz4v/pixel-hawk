"""Database initialization and raw SQL query helpers.

Owns the SQLite schema, connection lifecycle, and query helper functions.
Provides database() async context manager for application lifecycle.
Uses aiosqlite for async access and dataclass conversion at the boundary.
"""

import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

import aiosqlite
from loguru import logger

from .config import get_config

# Module-level connection, set by database() context manager
_conn: aiosqlite.Connection | None = None
_in_transaction: bool = False

# Migration functions are applied in order. Append new entries to add a migration.
# Each migration runs inside db.transaction() and bumps PRAGMA user_version on success.
MIGRATIONS: list[Callable[[aiosqlite.Connection], Awaitable[None]]] = []

SCHEMA = """\
CREATE TABLE IF NOT EXISTS person (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    discord_id INTEGER UNIQUE,
    access INTEGER NOT NULL DEFAULT 0,
    max_active_projects INTEGER NOT NULL DEFAULT 50,
    max_watched_tiles INTEGER NOT NULL DEFAULT 10,
    watched_tiles_count INTEGER NOT NULL DEFAULT 0,
    active_projects_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS project (
    id INTEGER PRIMARY KEY,
    owner_id INTEGER NOT NULL REFERENCES person(id),
    name TEXT NOT NULL,
    state INTEGER NOT NULL DEFAULT 0,
    x INTEGER NOT NULL DEFAULT 0,
    y INTEGER NOT NULL DEFAULT 0,
    width INTEGER NOT NULL DEFAULT 0,
    height INTEGER NOT NULL DEFAULT 0,
    first_seen INTEGER NOT NULL DEFAULT 0,
    last_check INTEGER NOT NULL DEFAULT 0,
    last_snapshot INTEGER NOT NULL DEFAULT 0,
    max_completion_pixels INTEGER NOT NULL DEFAULT 0,
    max_completion_percent REAL NOT NULL DEFAULT 0.0,
    max_completion_time INTEGER NOT NULL DEFAULT 0,
    total_progress INTEGER NOT NULL DEFAULT 0,
    total_regress INTEGER NOT NULL DEFAULT 0,
    largest_regress_pixels INTEGER NOT NULL DEFAULT 0,
    largest_regress_time INTEGER NOT NULL DEFAULT 0,
    recent_rate_pixels_per_hour REAL NOT NULL DEFAULT 0.0,
    recent_rate_window_start INTEGER NOT NULL DEFAULT 0,
    has_missing_tiles INTEGER NOT NULL DEFAULT 1,
    last_log_message TEXT NOT NULL DEFAULT '',
    UNIQUE(owner_id, name)
);

CREATE INDEX IF NOT EXISTS idx_project_name ON project(name);
CREATE INDEX IF NOT EXISTS idx_project_state ON project(state);

CREATE TABLE IF NOT EXISTS history_change (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES project(id),
    timestamp INTEGER NOT NULL,
    status INTEGER NOT NULL,
    num_remaining INTEGER NOT NULL DEFAULT 0,
    num_target INTEGER NOT NULL DEFAULT 0,
    completion_percent REAL NOT NULL DEFAULT 0.0,
    progress_pixels INTEGER NOT NULL DEFAULT 0,
    regress_pixels INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tile (
    id INTEGER PRIMARY KEY,
    x INTEGER NOT NULL,
    y INTEGER NOT NULL,
    heat INTEGER NOT NULL DEFAULT 999,
    last_checked INTEGER NOT NULL DEFAULT 0,
    last_update INTEGER NOT NULL DEFAULT 0,
    etag TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tile_heat_last_checked ON tile(heat, last_checked);

CREATE TABLE IF NOT EXISTS tile_project (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tile_id INTEGER NOT NULL REFERENCES tile(id),
    project_id INTEGER NOT NULL REFERENCES project(id),
    UNIQUE(tile_id, project_id)
);

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id INTEGER PRIMARY KEY,
    required_role TEXT NOT NULL,
    max_active_projects INTEGER NOT NULL DEFAULT 50,
    max_watched_tiles INTEGER NOT NULL DEFAULT 10
);

CREATE TABLE IF NOT EXISTS watch_message (
    message_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL,
    UNIQUE(project_id, channel_id)
);
"""


def get_conn() -> aiosqlite.Connection:
    """Get the current database connection. Raises if not initialized."""
    assert _conn is not None, "Database not initialized — use async with database()"
    return _conn


@asynccontextmanager
async def database(db_path: str | None = None):
    """Async context manager for database lifecycle.

    Opens an aiosqlite connection, creates schema if needed, enables foreign keys,
    and ensures clean shutdown on exit.

    Usage:
        async with database():
            # ... use query helpers ...
        # Connection automatically closed
    """
    global _conn, _in_transaction
    if db_path is None:
        db_path = str(get_config().data_dir / "pixel-hawk.db")
    prior_conn = _conn
    prior_tx = _in_transaction
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = sqlite3.Row
    _conn = conn
    _in_transaction = False
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(SCHEMA)
    await conn.commit()
    await _assert_db_writable()
    await _run_migrations(conn)
    try:
        yield
    finally:
        await conn.close()
        _conn = prior_conn
        _in_transaction = prior_tx


async def execute(sql: str, params: tuple = ()) -> aiosqlite.Cursor:
    """Execute a write query (INSERT, UPDATE, DELETE).

    Auto-commits unless running inside a db.transaction() block, in which case
    the enclosing transaction controls the commit/rollback boundary.
    """
    conn = get_conn()
    cursor = await conn.execute(sql, params)
    if not _in_transaction:
        await conn.commit()
    return cursor


@asynccontextmanager
async def transaction():
    """Async context manager for atomic multi-statement mutations.

    Issues BEGIN IMMEDIATE on entry, COMMIT on clean exit, ROLLBACK on exception.
    While active, db.execute() skips its own commit so all statements land atomically.

    Nest-safe: a transaction() nested inside another is a no-op — SQLite does not
    support true nested transactions without savepoints, and we don't need those
    semantics here. The outermost transaction() controls the boundary.
    """
    global _in_transaction
    conn = get_conn()
    if _in_transaction:
        yield
        return
    await conn.execute("BEGIN IMMEDIATE")
    _in_transaction = True
    try:
        yield
    except BaseException:
        await conn.rollback()
        raise
    else:
        await conn.commit()
    finally:
        _in_transaction = False


async def execute_insert(sql: str, params: tuple = ()) -> int:
    """Execute an INSERT and return the lastrowid."""
    cursor = await execute(sql, params)
    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def fetch_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    """Fetch a single row, or None."""
    conn = get_conn()
    cursor = await conn.execute(sql, params)
    return await cursor.fetchone()


async def fetch_all(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Fetch all rows."""
    conn = get_conn()
    cursor = await conn.execute(sql, params)
    return list(await cursor.fetchall())


async def fetch_val(sql: str, params: tuple = ()) -> int | float | str | None:
    """Fetch a single scalar value."""
    row = await fetch_one(sql, params)
    return row[0] if row else None


async def fetch_int(sql: str, params: tuple = ()) -> int:
    """Fetch a single integer value, returning 0 if no row or NULL."""
    val = await fetch_val(sql, params)
    return int(val) if val else 0


async def _assert_db_writable() -> None:
    """Write to the database to verify we own the SQLite lock.

    Raises OperationalError ("database is locked") if another process holds it.
    Writes and then restores PRAGMA user_version so the migration runner sees
    the true schema version.
    """
    conn = get_conn()
    try:
        cursor = await conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current = row[0] if row else 0
        await conn.execute(f"PRAGMA user_version = {int(current)}")
        await conn.commit()
    except Exception:
        logger.critical("Cannot acquire database write lock — is another pixel-hawk instance running?")
        raise


async def _run_migrations(conn: aiosqlite.Connection) -> None:
    """Apply pending migrations and stamp PRAGMA user_version.

    Bootstrap rule: if user_version == 0 and a `person` table already exists
    (via CREATE TABLE IF NOT EXISTS in SCHEMA), stamp to len(MIGRATIONS) without
    running anything. This silently adopts a pre-migration-system prod DB.

    Otherwise, for each migration at index >= version, run it inside
    db.transaction() and bump user_version after each success.
    """
    cursor = await conn.execute("PRAGMA user_version")
    row = await cursor.fetchone()
    version = row[0] if row else 0

    if version == 0:
        bootstrap = await conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='person'")
        if await bootstrap.fetchone() is not None:
            await conn.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")
            await conn.commit()
            return

    for idx in range(version, len(MIGRATIONS)):
        migration = MIGRATIONS[idx]
        logger.info(f"Applying migration {idx}: {getattr(migration, '__name__', '?')}")
        async with transaction():
            await migration(conn)
            await conn.execute(f"PRAGMA user_version = {idx + 1}")

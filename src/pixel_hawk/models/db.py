"""Database initialization and Tortoise ORM configuration.

Owns the TORTOISE_ORM config dict used by both the application and Aerich.
Provides database() async context manager for application lifecycle.
Provides rebuild_table() for Aerich migrations that hit SQLite limitations.
"""

from contextlib import asynccontextmanager

from loguru import logger
from tortoise import Tortoise
from tortoise.exceptions import OperationalError

from .config import get_config

MODELS = ["pixel_hawk.models.entities", "aerich.models"]


def tortoise_config(db_path: str | None = None) -> dict:
    """Build Tortoise ORM config dict. Uses get_config().data_dir for default path."""
    if db_path is None:
        db_path = str(get_config().data_dir / "pixel-hawk.db")
    return {
        "connections": {"default": f"sqlite://{db_path}"},
        "apps": {"models": {"models": MODELS, "default_connection": "default"}},
    }


# Static config for Aerich CLI (uses relative path as fallback)
TORTOISE_ORM = {
    "connections": {"default": "sqlite://nest/data/pixel-hawk.db"},
    "apps": {"models": {"models": MODELS, "default_connection": "default"}},
}


@asynccontextmanager
async def database(db_path: str | None = None):
    """Async context manager for database lifecycle.

    Initializes Tortoise ORM, generates schemas if needed, and ensures
    clean shutdown on exit.

    Usage:
        async with database():
            # ... use Tortoise models ...
        # Database connections automatically closed
    """
    await Tortoise.init(config=tortoise_config(db_path))
    await Tortoise.generate_schemas(safe=True)
    await _assert_db_writable()
    try:
        yield
    finally:
        await Tortoise.close_connections()


async def rebuild_table(db, table: str, *, renames: dict[str, str] | None = None) -> None:
    """Rebuild a SQLite table from current Tortoise models, preserving data.

    Workaround for ``aerich migrate`` failing with
    ``NotSupportError: Modify column is unsupported in SQLite``.

    Renames the old table, creates a fresh one via generate_schemas() (which reads
    current model definitions), copies data for common/renamed columns, and drops the
    old table. New columns get their model defaults; removed columns are discarded.

    Usage in a manually-written migration file::

        from pixel_hawk.models.db import rebuild_table

        async def upgrade(db):
            await rebuild_table(db, "project")
            return ""

    Args:
        db: Database connection passed to migration ``upgrade()``/``downgrade()``.
        table: SQL table name without quotes (e.g. ``"project"``).
        renames: Optional ``{old_column: new_column}`` mapping for renamed columns.
    """
    old = f"_old_{table}"
    renames = renames or {}

    await db.execute_query(f'ALTER TABLE "{table}" RENAME TO "{old}"')
    await Tortoise.generate_schemas(safe=True)

    _, old_info = await db.execute_query(f'PRAGMA table_info("{old}")')
    _, new_info = await db.execute_query(f'PRAGMA table_info("{table}")')
    old_names = {row[1] for row in old_info}
    new_names = {row[1] for row in new_info}

    src, dst = [], []
    for col in sorted(new_names):
        old_col = next((k for k, v in renames.items() if v == col), col)
        if old_col in old_names:
            src.append(f'"{old_col}"')
            dst.append(f'"{col}"')

    cols_src = ", ".join(src)
    cols_dst = ", ".join(dst)
    await db.execute_query(f'INSERT INTO "{table}" ({cols_dst}) SELECT {cols_src} FROM "{old}"')
    await db.execute_query(f'DROP TABLE "{old}"')


async def _assert_db_writable() -> None:
    """Write to the database to verify we own the SQLite lock.

    Raises OperationalError ("database is locked") if another process holds it.
    """
    conn = Tortoise.get_connection("default")
    try:
        await conn.execute_query("PRAGMA user_version = 1")
    except OperationalError:
        logger.critical("Cannot acquire database write lock — is another pixel-hawk instance running?")
        raise

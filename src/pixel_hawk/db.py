"""Database initialization and Tortoise ORM configuration.

Owns the TORTOISE_ORM config dict used by both the application and Aerich.
Provides database() async context manager for application lifecycle.
Provides build_tile_project_relationships() for creating tiles and junction table entries.
"""

from contextlib import asynccontextmanager

from loguru import logger
from tortoise import Tortoise
from tortoise.exceptions import OperationalError

from .config import get_config

MODELS = ["pixel_hawk.models", "aerich.models"]


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

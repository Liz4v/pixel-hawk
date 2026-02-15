"""Database initialization and Tortoise ORM configuration.

Owns the TORTOISE_ORM config dict used by both the application and Aerich.
Provides database() async context manager for application lifecycle.
"""

from contextlib import asynccontextmanager

from tortoise import Tortoise

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
    "connections": {"default": "sqlite://pixel-hawk-data/data/pixel-hawk.db"},
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
    try:
        yield
    finally:
        await Tortoise.close_connections()

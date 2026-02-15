"""Database initialization and Tortoise ORM configuration.

Owns the TORTOISE_ORM config dict used by both the application and Aerich.
Provides init_db() for application startup and close_db() for shutdown.
"""

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


async def init_db(db_path: str | None = None) -> None:
    """Initialize Tortoise ORM and generate schemas if needed."""
    await Tortoise.init(config=tortoise_config(db_path))
    await Tortoise.generate_schemas(safe=True)


async def close_db() -> None:
    """Close all Tortoise ORM connections."""
    await Tortoise.close_connections()

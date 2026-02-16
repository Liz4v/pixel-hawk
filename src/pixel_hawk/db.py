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


async def build_tile_project_relationships(projects: list) -> None:
    """Populate tile-project junction table and create TileInfo records for new tiles.

    Args:
        projects: List of Project objects to build relationships for.

    Creates TileInfo records for tiles not yet in database (burning queue),
    setting last_update to earliest project's first_seen timestamp.
    Creates TileProject junction table entries for all tile-project relationships.
    """
    from .models import TileInfo, TileProject

    # Collect (tile coords, project) mappings and track earliest first_seen per tile
    coords_to_projects: dict[tuple[int, int], list] = {}
    coords_to_earliest_first_seen: dict[tuple[int, int], int] = {}

    for project in projects:
        for tile in project.rect.tiles:
            coords = (tile.x, tile.y)
            coords_to_projects.setdefault(coords, []).append(project)

            # Track earliest first_seen for this tile across all projects
            current_earliest = coords_to_earliest_first_seen.get(coords, 1 << 58)  # Very large int
            coords_to_earliest_first_seen[coords] = min(current_earliest, project.info.first_seen)

    # Fetch existing TileInfo records using computed IDs
    all_coords = list(coords_to_projects.keys())
    existing_tile_ids = [TileInfo.tile_id(x, y) for x, y in all_coords]
    tile_infos = await TileInfo.filter(id__in=existing_tile_ids).all()
    tile_info_map = {(t.tile_x, t.tile_y): t for t in tile_infos}

    # Create TileInfo for new tiles (burning queue)
    missing_tiles = set(all_coords) - set(tile_info_map.keys())
    tiles_created = 0

    for tile_x, tile_y in missing_tiles:
        tile_id = TileInfo.tile_id(tile_x, tile_y)
        earliest_first_seen = coords_to_earliest_first_seen[(tile_x, tile_y)]

        tile_info = await TileInfo.create(
            id=tile_id,
            tile_x=tile_x,
            tile_y=tile_y,
            last_checked=0,  # Never checked (burning queue indicator)
            last_update=earliest_first_seen,  # Set to earliest project's first_seen
            http_etag="",
            queue_temperature=999,  # Burning queue
        )
        tile_info_map[(tile_x, tile_y)] = tile_info
        tiles_created += 1

    # Create tile-project relationships (use get_or_create for idempotency)
    relationships_created = 0
    for coords, projects_list in coords_to_projects.items():
        tile_info = tile_info_map[coords]
        for project in projects_list:
            _, created = await TileProject.get_or_create(
                tile_id=tile_info.id,
                project_id=project.info.id
            )
            if created:
                relationships_created += 1

    logger.info(f"Created {tiles_created} tiles and {relationships_created} tile-project relationships")

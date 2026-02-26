"""Tests for rebuild_table SQLite migration utility."""

from tortoise import Tortoise

from pixel_hawk.models.db import rebuild_table
from pixel_hawk.models.entities import Person, TileInfo


def _get_db():
    return Tortoise.get_connection("default")


# --- Basic operation ---


async def test_rebuild_preserves_data():
    """Rebuilding a table without schema changes preserves all rows and values."""
    await Person.create(name="Alice", max_active_projects=42)
    await Person.create(name="Bob", max_watched_tiles=7)

    await rebuild_table(_get_db(), "person")

    people = {p.name: p for p in await Person.all()}
    assert len(people) == 2
    assert people["Alice"].max_active_projects == 42
    assert people["Bob"].max_watched_tiles == 7


async def test_rebuild_empty_table():
    """Rebuilding an empty table completes without error."""
    await rebuild_table(_get_db(), "person")

    assert await Person.all().count() == 0


async def test_rebuild_many_rows():
    """All rows survive a rebuild, not just the first or last."""
    for i in range(20):
        await Person.create(name=f"person_{i}")

    await rebuild_table(_get_db(), "person")

    assert await Person.all().count() == 20


# --- Schema evolution ---


async def test_extra_old_column_discarded():
    """Columns present in the old table but absent from the model are dropped."""
    db = _get_db()
    await Person.create(name="Alice")

    await db.execute_query('ALTER TABLE "person" ADD COLUMN "legacy_field" TEXT DEFAULT "old"')

    await rebuild_table(db, "person")

    person = await Person.all().first()
    assert person.name == "Alice"

    _, info = await db.execute_query('PRAGMA table_info("person")')
    col_names = {row[1] for row in info}
    assert "legacy_field" not in col_names


async def test_missing_old_column_gets_null():
    """Nullable columns absent from the old table get NULL after rebuild."""
    db = _get_db()
    await Person.create(name="Alice", discord_id=12345)

    # Rebuild the table without discord_id to simulate an old schema
    cols = "id, name, access, max_active_projects, max_watched_tiles, watched_tiles_count, active_projects_count"
    await db.execute_query(f'CREATE TABLE "_person_slim" AS SELECT {cols} FROM "person"')
    await db.execute_query('DROP TABLE "person"')
    await db.execute_query('ALTER TABLE "_person_slim" RENAME TO "person"')

    await rebuild_table(db, "person")

    person = await Person.all().first()
    assert person.name == "Alice"
    assert person.discord_id is None  # nullable column not in old table → NULL


# --- Column renames ---


async def test_column_rename():
    """Data is preserved when a column is renamed via the renames mapping."""
    db = _get_db()
    await Person.create(name="Alice")

    # Simulate old schema where 'name' was called 'old_name'
    await db.execute_query('ALTER TABLE "person" RENAME COLUMN "name" TO "old_name"')

    await rebuild_table(db, "person", renames={"old_name": "name"})

    person = await Person.all().first()
    assert person.name == "Alice"


async def test_multiple_renames():
    """Multiple renames in a single rebuild all map correctly."""
    db = _get_db()
    await Person.create(name="Alice", max_active_projects=10, max_watched_tiles=5)

    await db.execute_query('ALTER TABLE "person" RENAME COLUMN "max_active_projects" TO "old_max_proj"')
    await db.execute_query('ALTER TABLE "person" RENAME COLUMN "max_watched_tiles" TO "old_max_tiles"')

    await rebuild_table(
        db,
        "person",
        renames={"old_max_proj": "max_active_projects", "old_max_tiles": "max_watched_tiles"},
    )

    person = await Person.all().first()
    assert person.name == "Alice"
    assert person.max_active_projects == 10
    assert person.max_watched_tiles == 5


async def test_rename_plus_extra_column():
    """Rename and extra-column-discard work together in a single rebuild."""
    db = _get_db()
    await Person.create(name="Alice")

    await db.execute_query('ALTER TABLE "person" RENAME COLUMN "name" TO "old_name"')
    await db.execute_query('ALTER TABLE "person" ADD COLUMN "obsolete" TEXT DEFAULT "x"')

    await rebuild_table(db, "person", renames={"old_name": "name"})

    person = await Person.all().first()
    assert person.name == "Alice"

    _, info = await db.execute_query('PRAGMA table_info("person")')
    col_names = {row[1] for row in info}
    assert "obsolete" not in col_names


# --- Non-auto-increment primary key ---


async def test_rebuild_manual_pk_table():
    """Tables with generated=False primary keys rebuild correctly."""
    await TileInfo.create(id=TileInfo.tile_id(3, 7), x=3, y=7, heat=999, last_checked=100, last_update=200)
    await TileInfo.create(id=TileInfo.tile_id(0, 1), x=0, y=1, heat=5, last_checked=50, last_update=60)

    await rebuild_table(_get_db(), "tile")

    tiles = {t.id: t for t in await TileInfo.all()}
    assert len(tiles) == 2

    t = tiles[TileInfo.tile_id(3, 7)]
    assert t.x == 3
    assert t.y == 7
    assert t.heat == 999
    assert t.last_checked == 100

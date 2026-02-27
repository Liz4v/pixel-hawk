"""Add per-user and per-guild quota limit columns.

Manual migration — aerich migrate crashes on SQLite ADD COLUMN
(TypeError in SqliteSchemaGenerator._column_default_generator).
"""

import json

from tortoise import BaseDBAsyncClient


async def _refresh_aerich_state(db: BaseDBAsyncClient) -> None:
    """Overwrite stored aerich state with current Tortoise model descriptions.

    Ensures future aerich migrate calls see no phantom diffs from this migration.
    get_models_describe reads Python model definitions (not DB schema), so it
    returns the post-migration state regardless of call timing.
    """
    from aerich.utils import get_models_describe

    content = get_models_describe("models")
    _, rows = await db.execute_query(
        'SELECT "id" FROM "aerich" WHERE "app" = ? ORDER BY "id" DESC LIMIT 1',
        ["models"],
    )
    if rows:
        await db.execute_query(
            'UPDATE "aerich" SET "content" = ? WHERE "id" = ?',
            [json.dumps(content), rows[0][0]],
        )


async def _has_column(db: BaseDBAsyncClient, table: str, column: str) -> bool:
    _, rows = await db.execute_query(f'PRAGMA table_info("{table}")')
    return any(row[1] == column for row in rows)


async def upgrade(db: BaseDBAsyncClient) -> str:
    await _refresh_aerich_state(db)
    statements = []
    for table, column in [
        ("person", "max_active_projects"),
        ("person", "max_watched_tiles"),
        ("guild_config", "max_active_projects"),
        ("guild_config", "max_watched_tiles"),
    ]:
        if not await _has_column(db, table, column):
            statements.append(f'ALTER TABLE "{table}" ADD "{column}" INT NOT NULL DEFAULT {50 if "projects" in column else 10};')
    return "\n".join(statements)


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "person" DROP COLUMN "max_active_projects";
        ALTER TABLE "person" DROP COLUMN "max_watched_tiles";
        ALTER TABLE "guild_config" DROP COLUMN "max_active_projects";
        ALTER TABLE "guild_config" DROP COLUMN "max_watched_tiles";"""

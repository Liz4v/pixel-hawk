"""Add watch_message table for living Discord status messages.

Manual migration — new table with FK to project and unique constraint
on (project_id, channel_id) to enforce one watch per project per channel.
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


async def upgrade(db: BaseDBAsyncClient) -> str:
    await _refresh_aerich_state(db)
    return """
        CREATE TABLE IF NOT EXISTS "watch_message" (
            "message_id" BIGINT PRIMARY KEY NOT NULL,
            "channel_id" BIGINT NOT NULL,
            "project_id" INT NOT NULL REFERENCES "project" ("id") ON DELETE CASCADE,
            UNIQUE ("project_id", "channel_id")
        );"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """DROP TABLE IF EXISTS "watch_message";"""

import json

from tortoise import BaseDBAsyncClient


async def _patch_aerich_state(db: BaseDBAsyncClient) -> None:
    """Patch stored aerich state to match current Tortoise model description format.

    Tortoise ORM upgrade added 'db_default' to field descriptions, and
    ProjectState gained CREATING=30. Without this patch, future aerich
    migrate calls against this database would see phantom diffs and fail.
    """
    _, rows = await db.execute_query(
        'SELECT "id", "content" FROM "aerich" WHERE "app" = ? ORDER BY "id" DESC LIMIT 1',
        ["models"],
    )
    if not rows:
        return
    record_id = rows[0][0]
    content = json.loads(rows[0][1]) if isinstance(rows[0][1], str) else rows[0][1]
    for model_name, model in content.items():
        for key in ("data_fields", "fk_fields"):
            for field in model.get(key, []):
                if "db_default" not in field:
                    field["db_default"] = "__NOT_SET__"
        if model_name == "models.ProjectInfo":
            for field in model.get("data_fields", []):
                if field["name"] == "state":
                    field["description"] = "ACTIVE: 0\nPASSIVE: 10\nINACTIVE: 20\nCREATING: 30"
    await db.execute_query(
        'UPDATE "aerich" SET "content" = ? WHERE "id" = ?',
        [json.dumps(content), record_id],
    )


async def upgrade(db: BaseDBAsyncClient) -> str:
    await _patch_aerich_state(db)
    return """
        CREATE TABLE IF NOT EXISTS "guild_config" (
    "guild_id" BIGINT NOT NULL PRIMARY KEY,
    "required_role" VARCHAR(255) NOT NULL
) /* Per-guild configuration for the Discord bot. */;"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "guild_config";"""

from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "project" DROP COLUMN "tile_updates_24h";
        ALTER TABLE "project" DROP COLUMN "tile_last_update";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "project" ADD "tile_updates_24h" JSON NOT NULL;
        ALTER TABLE "project" ADD "tile_last_update" JSON NOT NULL;"""


MODELS_STATE = (
    "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"
    "nnA="
)

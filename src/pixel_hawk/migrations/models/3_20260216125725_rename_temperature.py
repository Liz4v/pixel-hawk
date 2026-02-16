from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_tile_queue_t_e9c8fc";
        ALTER TABLE "tile" RENAME COLUMN "http_etag" TO "etag";
        ALTER TABLE "tile" RENAME COLUMN "queue_temperature" TO "heat";
        CREATE INDEX "idx_tile_heat_2986e2" ON "tile" ("heat", "last_checked");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_tile_heat_2986e2";
        ALTER TABLE "tile" RENAME COLUMN "heat" TO "queue_temperature";
        ALTER TABLE "tile" RENAME COLUMN "etag" TO "http_etag";
        CREATE INDEX "idx_tile_queue_t_e9c8fc" ON "tile" ("queue_temperature", "last_checked");"""


MODELS_STATE = (
    "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"
    "zVc4PIm9mKyzlqFGSP30huXuf0HV8hk="
)

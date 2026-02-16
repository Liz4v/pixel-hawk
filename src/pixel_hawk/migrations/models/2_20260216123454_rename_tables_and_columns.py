from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "person" ADD "active_projects_count" INT NOT NULL DEFAULT 0;
        ALTER TABLE "project_info" RENAME TO "project";
        ALTER TABLE "tile_info" RENAME TO "tile";
        ALTER TABLE "tile" RENAME COLUMN "tile_y" TO "y";
        ALTER TABLE "tile" RENAME COLUMN "tile_x" TO "x";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "person" DROP COLUMN "active_projects_count";
        ALTER TABLE "project" RENAME TO "project_info";
        ALTER TABLE "tile" RENAME TO "tile_info";
        ALTER TABLE "tile" RENAME COLUMN "y" TO "tile_y";
        ALTER TABLE "tile" RENAME COLUMN "x" TO "tile_x";"""


MODELS_STATE = (
    "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"
)

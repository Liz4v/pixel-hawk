from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_person_name_f404e8";
        ALTER TABLE "person" ADD "access" INT NOT NULL DEFAULT 0;
        ALTER TABLE "person" ADD "discord_id" BIGINT;
        CREATE UNIQUE INDEX "uid_person_discord_195c1f" ON "person" ("discord_id");"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_person_discord_195c1f";
        ALTER TABLE "person" DROP COLUMN "access";
        ALTER TABLE "person" DROP COLUMN "discord_id";
        CREATE UNIQUE INDEX "uid_person_name_f404e8" ON "person" ("name");"""


MODELS_STATE = (
    "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"
    "Or45nK"
)

from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "person" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "watched_tiles_count" INT NOT NULL DEFAULT 0
) /* Represents a person who can own projects. */;
CREATE TABLE IF NOT EXISTS "project_info" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "state" VARCHAR(8) NOT NULL DEFAULT 'active' /* ACTIVE: active\nPASSIVE: passive\nINACTIVE: inactive */,
    "x" INT NOT NULL DEFAULT 0,
    "y" INT NOT NULL DEFAULT 0,
    "width" INT NOT NULL DEFAULT 0,
    "height" INT NOT NULL DEFAULT 0,
    "first_seen" INT NOT NULL DEFAULT 0,
    "last_check" INT NOT NULL DEFAULT 0,
    "last_snapshot" INT NOT NULL DEFAULT 0,
    "max_completion_pixels" INT NOT NULL DEFAULT 0,
    "max_completion_percent" REAL NOT NULL DEFAULT 0,
    "max_completion_time" INT NOT NULL DEFAULT 0,
    "total_progress" INT NOT NULL DEFAULT 0,
    "total_regress" INT NOT NULL DEFAULT 0,
    "largest_regress_pixels" INT NOT NULL DEFAULT 0,
    "largest_regress_time" INT NOT NULL DEFAULT 0,
    "recent_rate_pixels_per_hour" REAL NOT NULL DEFAULT 0,
    "recent_rate_window_start" INT NOT NULL DEFAULT 0,
    "tile_last_update" JSON NOT NULL,
    "tile_updates_24h" JSON NOT NULL,
    "has_missing_tiles" INT NOT NULL DEFAULT 1,
    "last_log_message" TEXT NOT NULL,
    "owner_id" INT NOT NULL REFERENCES "person" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_project_inf_owner_i_00adc5" UNIQUE ("owner_id", "name")
) /* Persistent metadata for a project. Pure Tortoise ORM model. */;
CREATE INDEX IF NOT EXISTS "idx_project_inf_name_735a18" ON "project_info" ("name");
CREATE INDEX IF NOT EXISTS "idx_project_inf_state_15b68c" ON "project_info" ("state");
CREATE TABLE IF NOT EXISTS "history_change" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "timestamp" INT NOT NULL,
    "status" VARCHAR(11) NOT NULL /* NOT_STARTED: not_started\nIN_PROGRESS: in_progress\nCOMPLETE: complete */,
    "num_remaining" INT NOT NULL DEFAULT 0,
    "num_target" INT NOT NULL DEFAULT 0,
    "completion_percent" REAL NOT NULL DEFAULT 0,
    "progress_pixels" INT NOT NULL DEFAULT 0,
    "regress_pixels" INT NOT NULL DEFAULT 0,
    "project_id" INT NOT NULL REFERENCES "project_info" ("id") ON DELETE CASCADE
) /* Record of a single diff event for a project. */;
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSON NOT NULL
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "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"
    "tP71ju/gN+xgp+"
)

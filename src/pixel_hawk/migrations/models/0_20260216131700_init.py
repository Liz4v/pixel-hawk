from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "person" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "name" VARCHAR(255) NOT NULL UNIQUE,
    "watched_tiles_count" INT NOT NULL DEFAULT 0,
    "active_projects_count" INT NOT NULL DEFAULT 0
) /* Represents a person who can own projects. */;
CREATE TABLE IF NOT EXISTS "project" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "state" SMALLINT NOT NULL DEFAULT 0 /* ACTIVE: 0\nPASSIVE: 10\nINACTIVE: 20 */,
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
    CONSTRAINT "uid_project_owner_i_6da7c6" UNIQUE ("owner_id", "name")
) /* Persistent metadata for a project. Pure Tortoise ORM model. */;
CREATE INDEX IF NOT EXISTS "idx_project_name_4d952a" ON "project" ("name");
CREATE INDEX IF NOT EXISTS "idx_project_state_9d5039" ON "project" ("state");
CREATE TABLE IF NOT EXISTS "history_change" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "timestamp" INT NOT NULL,
    "status" SMALLINT NOT NULL /* NOT_STARTED: 0\nIN_PROGRESS: 10\nCOMPLETE: 20 */,
    "num_remaining" INT NOT NULL DEFAULT 0,
    "num_target" INT NOT NULL DEFAULT 0,
    "completion_percent" REAL NOT NULL DEFAULT 0,
    "progress_pixels" INT NOT NULL DEFAULT 0,
    "regress_pixels" INT NOT NULL DEFAULT 0,
    "project_id" INT NOT NULL REFERENCES "project" ("id") ON DELETE CASCADE
) /* Record of a single diff event for a project. */;
CREATE TABLE IF NOT EXISTS "tile" (
    "id" INT NOT NULL PRIMARY KEY,
    "x" INT NOT NULL,
    "y" INT NOT NULL,
    "heat" INT NOT NULL DEFAULT 999,
    "last_checked" INT NOT NULL DEFAULT 0,
    "last_update" INT NOT NULL,
    "etag" VARCHAR(255) NOT NULL DEFAULT ''
) /* Persistent metadata for a single WPlace tile. */;
CREATE INDEX IF NOT EXISTS "idx_tile_heat_2986e2" ON "tile" ("heat", "last_checked");
CREATE TABLE IF NOT EXISTS "tile_project" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "project_id" INT NOT NULL REFERENCES "project" ("id") ON DELETE CASCADE,
    "tile_id" INT NOT NULL REFERENCES "tile" ("id") ON DELETE CASCADE,
    CONSTRAINT "uid_tile_projec_tile_id_75ba4e" UNIQUE ("tile_id", "project_id")
) /* Many-to-many relationship between tiles and projects. */;
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
)

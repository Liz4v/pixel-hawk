from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "project_info" (
    "name" VARCHAR(255) NOT NULL PRIMARY KEY,
    "x" INT NOT NULL,
    "y" INT NOT NULL,
    "width" INT NOT NULL,
    "height" INT NOT NULL,
    "first_seen" INT NOT NULL,
    "last_check" INT NOT NULL,
    "last_snapshot" INT NOT NULL,
    "max_completion_pixels" INT NOT NULL,
    "max_completion_percent" REAL NOT NULL,
    "max_completion_time" INT NOT NULL,
    "total_progress" INT NOT NULL,
    "total_regress" INT NOT NULL,
    "largest_regress_pixels" INT NOT NULL,
    "largest_regress_time" INT NOT NULL,
    "recent_rate_pixels_per_hour" REAL NOT NULL,
    "recent_rate_window_start" INT NOT NULL,
    "tile_last_update" JSON NOT NULL,
    "tile_updates_24h" JSON NOT NULL,
    "has_missing_tiles" INT NOT NULL,
    "last_log_message" TEXT NOT NULL
) /* Persistent metadata for a project. Active Record pattern. */;
CREATE TABLE IF NOT EXISTS "history_change" (
    "id" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
    "timestamp" INT NOT NULL,
    "status" VARCHAR(11) NOT NULL /* NOT_STARTED: not_started\nIN_PROGRESS: in_progress\nCOMPLETE: complete */,
    "num_remaining" INT NOT NULL,
    "num_target" INT NOT NULL,
    "completion_percent" REAL NOT NULL,
    "progress_pixels" INT NOT NULL,
    "regress_pixels" INT NOT NULL,
    "project_id" VARCHAR(255) NOT NULL REFERENCES "project_info" ("name") ON DELETE CASCADE
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

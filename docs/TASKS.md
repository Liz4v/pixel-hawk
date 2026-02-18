# Pixel Hawk Tasks

## Completed

> **Note:** Keep completed task descriptions to a single concise paragraph summarizing what was done.

### ✅ Remove redundant tile JSON fields from ProjectInfo (2026-02-18)

Dropped `tile_last_update` and `tile_updates_24h` JSON fields from `ProjectInfo` — both redundant with `HistoryChange`/`TileInfo`. Removed associated helper functions, the `changed_tile` parameter from `run_diff()`, and 19 dead tests. Aerich migration drops both columns.

### ✅ `/hawk list` command for Discord bot (2026-02-18)

Added `/hawk list` slash command showing all projects for the calling user with state-dependent formatting (completion %, 24h changes, timestamps). Truncates at Discord's 2000-char limit with "... and N more". Core logic separated from handler for testability. 8 tests.

### ✅ Discord bot foundation with admin access command (2026-02-16)

Added optional Discord bot with `config.toml` credentials, `BotAccess` IntFlag, and `/hawk sa myself <uuid>` admin-grant command using a per-startup UUID4 token. Bot runs as a background task alongside polling, silently skipped when unconfigured.

### ✅ Query-driven project lookups via TileProject table (2026-02-16)

Replaced in-memory tile-to-project mapping with on-demand database queries through the `TileProject` junction table: `_get_projects_for_tile()` queries active/passive projects per tile, `Project` objects constructed on demand for each diff cycle, and all project-loading logic removed from startup. PASSIVE projects now correctly receive diffs. Includes tests for state filtering and database-driven lookups.

### ✅ Database-backed tile queue system migration (2026-02-15)

Migrated tile queue system from memory-based to database-backed storage using Tortoise ORM: added `TileInfo` model and `TileProject` junction table, rewrote `QueueSystem` for query-driven architecture, implemented lazy Zipf distribution rebuilds, added full ETag support with dual-header validation, and removed all file mtime logic. Burning queue semantics preserved with `last_checked=0` for never-checked tiles.

### ✅ Migrate data to SQLite (2026-02-15)

Migrated persistence from YAML files to SQLite via Tortoise ORM with Aerich schema migrations: renamed `ProjectMetadata` to `ProjectInfo` as a pure Tortoise model, refactored business logic to functional service layer in `metadata.py`, created `HistoryChange` table for diff event logging, and added `db.py` with async database lifecycle management. Includes one-time YAML-to-SQLite migration for existing data.

### ✅ Detect project regression / griefing (2026-02-12)

Implemented regression detection in the core diff pipeline: per-pixel progress/regress counting on every diff, lifetime accumulation counters, worst-event tracking, and change streak detection (`progress`/`regress`/`mixed`) for identifying sustained attacks. Log messages include `[+N/-N]` indicators and streak info. Alarm/notification deferred to Discord bot.

### ✅ Configurable directory paths with unified nest structure (2026-02-08)

Migrated from platformdirs to configurable local `./nest` directory structure with `Config` dataclass in `config.py`: `load_config()` supports CLI/env/default precedence (`--nest` > `HAWK_NEST` > `./nest`), all subdirectories auto-created at startup, and all path references updated across the codebase. Includes 12 tests covering all configuration scenarios.

### ✅ Refined burning queue to prioritize by project first_seen timestamp (2026-02-08)

Enhanced burning queue tile selection to prioritize tiles from older projects using `first_seen` timestamps: burning queue selects tiles belonging to the oldest projects first, while temperature queues continue using `last_checked`. Includes 6 tests covering prioritization, shared tiles, and edge cases.

### ✅ Enhanced project tracking with snapshots and metadata (2026-02-07)

Implemented project state persistence with `ProjectMetadata` dataclass in `metadata.py`: tracks completion history, progress/regress counters, tile update times, streaks, and completion rate. `Project.run_diff()` saves snapshots and metadata, comparing against both target and previous snapshot to detect progress vs regress. Includes 17 tests with 100% metadata coverage.

### ✅ Intelligent tile checking with warm/cold queues (2026-02-07)

Implemented temperature-based queue system with Zipf distribution for intelligent tile monitoring: burning queue for never-checked tiles, multiple hot-to-cold temperature queues based on modification times, round-robin selection across queues choosing least-recently-checked tiles, and surgical repositioning with cascade mechanics when tiles move to hotter queues. Includes 23 comprehensive tests and integration with `TileChecker`.

### ✅ Fix tile polling - only check ONE tile per cycle (2026-02-07)

Implemented round-robin tile checking that processes exactly one tile per polling cycle instead of checking all tiles. Added `current_tile_index` to `Main` class to track rotation position with automatic wraparound, preventing unnecessary bandwidth usage and backend hammering. Includes proper edge case handling for empty/modified tile lists and comprehensive test coverage.

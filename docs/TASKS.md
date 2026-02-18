# Pixel Hawk Tasks

## Backlog

### Discord Bot for project tracking and notifications

**Status:** Backlog
**Priority:** Medium

**Description:**
Create a Discord bot that integrates with pixel-hawk to provide real-time project monitoring through Discord. Users can add and manage projects via Discord commands under a quota of watched tiles, and the bot maintains living status messages that update as progress changes. Bot will only operate in trusted servers.

**Key Features:**
- Project management through Discord commands (`/hawk add`, `/hawk remove`, `/hawk list`)
- Automatic status message updates showing progress, last change, timestamps
- Server & user whitelist for security
- Rich embeds with progress bars and visual indicators
- Rate limiting to respect Discord API constraints

---

### Memory profiling and optimization for Raspberry Pi deployment

**Status:** Backlog
**Priority:** Low

**Description:**
Add memory profiling to identify and optimize memory usage for deployment on memory-constrained devices like Raspberry Pi. Large projects can consume significant memory during tile stitching and diff computation.

**Implementation Considerations:**
- Add profiling infrastructure (stdlib `tracemalloc` for zero dependencies, or `memray` for detailed analysis)
- Profile tile stitching in `stitch_tiles()` which creates full project-sized images
- Profile diff computation in `Project.run_diff()` which creates multiple byte arrays (`get_flattened_data()`, `bytes(newdata)`, etc.)
- Consider optimizations: stream diff computation to avoid large intermediate byte arrays, crop before stitching to only stitch needed pixels

**Related Code:**
- `Project.run_diff()` in `src/pixel_hawk/projects.py`
- `stitch_tiles()` in `src/pixel_hawk/projects.py`

---

## Completed

> **Note:** Keep completed task descriptions to a single concise paragraph summarizing what was done.

### ✅ `/hawk list` command for Discord bot (2026-02-18)

Added `/hawk list` slash command that shows all projects for the calling user with state-dependent formatting: in-progress projects show completion %, remaining pixels, and 24h progress/regress; complete projects show completion timestamp (Discord relative format); never-checked projects show placeholder text; inactive projects show only the WPlace link. Core logic in `list_projects()` is separated from the handler for testability (same pattern as `grant_admin()`). Streams project formatting and stops when Discord's 2000-char message limit would be exceeded, appending "... and N more". Includes 8 tests covering all states, ordering, and truncation.

### ✅ Discord bot foundation with admin access command (2026-02-16)

Added optional Discord bot integration: `config.toml` at nest root for bot credentials, `BotAccess(IntFlag)` enum with `ADMIN = 0x10000000`, `discord_id` and `access` fields on `Person`, and `/hawk sa myself <uuid>` slash command that grants admin access using a UUID4 token generated fresh each startup (stored in `nest/data/admin-me.txt`). Bot runs alongside the polling loop as a background task and is silently skipped when no token is configured. Removed unique constraint from `Person.name` since Discord identity is now the primary lookup key.

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

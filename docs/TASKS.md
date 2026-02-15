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
- `stitch_tiles()` in `src/pixel_hawk/ingest.py`

---

## Completed

> **Note:** Keep completed task descriptions to a single concise paragraph summarizing what was done.

### ✅ Migrate data to SQLite (2026-02-15)

Migrated persistence from YAML files to SQLite via Tortoise ORM with Aerich schema migrations. Renamed `ProjectMetadata` to `ProjectInfo` (Active Record in `models.py`) and `Project.metadata` to `Project.info`. Extracted computation logic into `ProjectInfoMixin` (in `metadata.py`). Created `HistoryChange` table recording every diff event with pixel counts, completion percentage, and progress/regress deltas. Added `db.py` module for database lifecycle (`init_db`/`close_db`). Implemented one-time YAML migration: existing `.metadata.yaml` files are imported into SQLite on first load and renamed to `.yaml.migrated`. All 187 tests passing with 96.61% coverage.

### ✅ Detect project regression / griefing (2026-02-12)

Implemented regression detection in the core diff pipeline: `ProjectMetadata.compare_snapshots()` counts per-pixel progress and regress on every diff, `process_diff()` accumulates lifetime `total_progress`/`total_regress` counters, tracks `largest_regress_pixels`/`largest_regress_time` for worst-event recording, and maintains change streaks (`progress`/`regress`/`mixed`) to identify sustained attacks. Log messages include `[+N/-N]` change indicators and streak info. Alarm/notification functionality deferred to the Discord bot.

### ✅ Configurable directory paths with unified pixel-hawk-home structure (2026-02-08)

Migrated from platformdirs to configurable local `./pixel-hawk-data` directory structure: created `config.py` module with `Config` dataclass containing 6 computed subdirectory properties (projects, snapshots, metadata, tiles, logs, data); implemented `load_config()` with CLI/env/default precedence (`--pixel-hawk-home` > `PIXEL_HAWK_HOME` > `./pixel-hawk-data`); replaced `DIRS` with module-level `CONFIG` variable and `get_config()` helper; updated `main()` to initialize all subdirectories at startup and log pixel-hawk-home location; migrated all path references in projects.py, ingest.py, queues.py; removed platformdirs dependency from pyproject.toml; created comprehensive test_config.py with 12 tests covering all configuration scenarios; updated conftest.py with autouse `setup_config` fixture; all 178 tests passing with 96.5% coverage; mypy type checking successful.

### ✅ Refined burning queue to prioritize by project first_seen timestamp (2026-02-08)

Enhanced burning queue tile selection to prioritize tiles from older projects using `first_seen` timestamps: added `get_first_seen()` method to `ProjectShim` (returns sentinel `1<<58`) and `Project` (returns `metadata.first_seen`); updated `QueueSystem` to accept and store `tile_to_projects` mapping; modified `TileQueue.select_next()` to calculate minimum `first_seen` across all projects containing each tile using `min()` with default parameter; burning queue now selects tiles from oldest projects first while temperature queues continue using `last_checked`; added 6 comprehensive tests covering prioritization, ProjectShim handling, shared tiles, and method behavior; all 158 tests passing with 97% coverage.

### ✅ Enhanced project tracking with snapshots and metadata (2026-02-07)

Implemented comprehensive project state persistence: created `ProjectMetadata` dataclass in new `metadata.py` module tracking completion history (max completion, progress/regress counters, largest regress event), tile updates (last update per tile, 24h rolling list), streaks, and completion rate; refactored `Project.run_diff()` to save snapshots (PNG) and metadata (YAML) adjacent to project files, comparing current state against both target and previous snapshot to detect progress vs regress; added 17 comprehensive tests in `test_metadata.py` achieving 100% coverage of metadata module; all 31 project tests passing with 86% coverage of projects.py.

### ✅ Intelligent tile checking with warm/cold queues (2026-02-07)

Implemented temperature-based queue system with Zipf distribution for intelligent tile monitoring: burning queue for never-checked tiles, multiple hot-to-cold temperature queues based on modification times, round-robin selection across queues choosing least-recently-checked tiles, and surgical repositioning with cascade mechanics when tiles move to hotter queues. Includes 23 comprehensive tests and integration with `TileChecker`.

### ✅ Fix tile polling - only check ONE tile per cycle (2026-02-07)

Implemented round-robin tile checking that processes exactly one tile per polling cycle instead of checking all tiles. Added `current_tile_index` to `Main` class to track rotation position with automatic wraparound, preventing unnecessary bandwidth usage and backend hammering. Includes proper edge case handling for empty/modified tile lists and comprehensive test coverage.

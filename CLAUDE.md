## Project Overview

pixel-hawk is a change tracker for WPlace paint projects. It polls WPlace tile images, and diffs them against project image files provided by users. The package entry point is exposed as the console script `hawk` (see `pyproject.toml`).

## Quick facts

- **Requires:** Python >= 3.14 (see `pyproject.toml`)
- **Console script:** `hawk = "pixel_hawk.main:main"`
- **Main package:** `src/pixel_hawk`
- **Key dependencies:** `loguru`, `pillow`, `httpx`, `tortoise-orm` (SQLite via `aiosqlite`), `humanize`, `discord.py`, `python-dotenv`
- **Linting:** `ruff` configured with `line-length = 120`

## Where to look for further context

- `pyproject.toml` for packaging/deps and `ruff` config
- `README.md` for user-facing documentation and external resources/links
- `tests/` for unit tests and test patterns

## Quickstart (developer)

See the `uv` documentation: https://pypi.org/project/uv/

- Use `uv` to manage Python and project dependencies. Example:

```bash
uv sync
```

- Run the watcher locally with the console script or module (via your `uv` environment):

```bash
uv run hawk
```

## Configuration and data directories

- Configuration managed through `src/pixel_hawk/models/config.py` (nest path) and environment variables (Discord settings)
- Default nest: `./nest` (current working directory)
- Configurable via CLI flag `--nest` or environment variable `HAWK_NEST`
- Discord settings via `HAWK_BOT_TOKEN` and `HAWK_COMMAND_PREFIX` env vars (see `.env.example`)
- `python-dotenv` auto-loads `.env` from CWD on startup
- All data lives under nest with organized subdirectories:
  - `projects/{person_id}/` — project PNG files organized by person ID (coordinate-only filenames: `{tx}_{ty}_{px}_{py}.png`; CREATING projects use `new_{id}.png`)
  - `tiles/` — cached tiles from WPlace
  - `snapshots/{person_id}/` — canvas state snapshots, same structure as projects (coordinate-only filenames)
  - `rejected/` — project files that failed to import (invalid palette, etc.)
  - `logs/` — application logs
  - `data/` — SQLite database (`pixel-hawk.db`) with Person, ProjectInfo, HistoryChange, TileInfo, TileProject, GuildConfig, and WatchMessage tables
- **Design rationale:** The default `./nest` location allows running pixel-hawk from the project root during development, keeping all data files easily accessible for inspection from IDE and AI agents. This simplifies debugging, testing, and data analysis without requiring path configuration.
- Access configuration via `get_config()` from `models/config.py`
- CONFIG singleton is lazily initialized on first access
- All subdirectories auto-created by `load_config()` on startup

## How it works (high level)

- **Multi-user, query-driven architecture**: Projects are stored in SQLite and discovered on demand via database queries — no in-memory project index. Multiple users can track the same or different coordinates. Projects are keyed by (owner_id, name) with unique constraint.
- The application is fully async, built on `asyncio`. The entry point (`main()`) calls `asyncio.run()` on the async main loop. Blocking I/O (PIL image operations, filesystem access) is offloaded via `asyncio.to_thread`.
- The application runs in a unified ~97 second polling loop (60φ = 30(1+√5), chosen to avoid resonance with WPlace's internal timers) that checks tiles.
- Tile polling uses intelligent temperature-based queue system: `QueueSystem` (in `watcher/queues.py`) maintains burning and temperature queues with Zipf distribution sizing. Tiles are selected round-robin across queues, with least-recently-checked tile selected from each queue.
- `TileChecker` (in `watcher/ingest.py`) manages tile monitoring: creates and owns an `httpx.AsyncClient`, selects tiles via `QueueSystem`, calls `has_tile_changed()` to fetch from WPlace backend, queries affected projects via `TileProject` junction table, and constructs `Project` objects on demand for diffing.
- `has_tile_changed()` (in `watcher/ingest.py`) requests tiles from the WPlace tile backend using `httpx` and updates a cached paletted PNG if there are changes.
- **Initial diff on project creation/edit**: When `new_project()` or `edit_project()` links tiles, `_try_initial_diff()` checks if any tiles are already cached via `count_cached_tiles()`. If so, it immediately runs `Project(info).run_diff()` and includes the formatted status in the response. Partial tile coverage is noted (e.g., "2/4 tiles cached"). If no tiles are cached, the diff is deferred to the polling loop.
- `Person` (in `models/entities.py`) represents users with auto-increment ID. Tracks `watched_tiles_count` (unique tiles across all active projects) and `active_projects_count`. Both updated via `update_totals()` on startup. Has per-user quota limits (`max_active_projects`, `max_watched_tiles`) enforced on project creation/edit. `BotAccess` IntFlag controls permissions: `ADMIN` bypasses guild checks, `ALLOWED` (auto-granted via guild role) marks legitimate users.
- `GuildConfig` (in `models/entities.py`) stores per-guild bot configuration. `guild_id` (Discord snowflake) is the primary key, `required_role` is the Discord role ID (snowflake stored as string). Has guild-level quota ceilings (`max_active_projects`, `max_watched_tiles`) that cap per-user quotas. Configured via `/hawkadmin role <name>`. When no GuildConfig exists for a guild, all non-admin commands are blocked.
- **Access flow**: User commands (`/hawk new`, `/hawk edit`, `/hawk list`, `/hawk delete`, `/hawk watch`, `/hawk unwatch`) call `_check_access()` in `interactions.py`, which delegates to `check_guild_access()` (in guilds) or `check_dm_access()` (in DMs) in `access.py`. In guilds: admins bypass all checks; non-admins must have the guild's configured role; if they do and have no `Person` record, one is auto-created with `BotAccess.ALLOWED`. In DMs: the user must already have a `Person` record with `ADMIN` or `ALLOWED` access (established via prior guild interaction). Admin commands live in a separate command group (`/hawkadmin`, `guild_only=True`).
- `ProjectState` IntEnum (in `models/entities.py`) defines project states: ACTIVE (0), PASSIVE (10), INACTIVE (20), CREATING (30). Setting coordinates on a CREATING project auto-transitions it to ACTIVE. States determine tile linking and polling behavior — see "Tile lifecycle invariants" below.
- `ProjectInfo` (in `models/entities.py`) is a pure Tortoise ORM model with owner FK (Person), name (stored in DB), and state. IDs are randomly assigned (1 to 9999) via `save_as_new()`, which retries on collision (EAFP pattern). Tracks completion history, progress/regress statistics, and rates. Persists to SQLite in `data/pixel-hawk.db`. The `filename` property is state-aware: returns `new_{id}.png` for CREATING projects, coordinate-only `{tx}_{ty}_{px}_{py}.png` otherwise. The `rectangle` property asserts the project is not CREATING.
- `HistoryChange` (in `models/entities.py`) records every diff event per project with pixel counts, completion percentage, and progress/regress deltas.
- Business logic for ProjectInfo lives in `watcher/metadata.py` as standalone functions (functional service layer). Functions take `ProjectInfo` as first parameter and mutate fields in place. Log messages include owner name for multi-user attribution.
- `Project` (in `watcher/projects.py`) is loaded from database via `Project.from_info(info)` classmethod. Constructor takes only `ProjectInfo` and derives `path` and `rect` from it. Files must use the project's palette. Invalid files cause from_info() to return None with warning logged. On success, `from_info()` also runs an initial diff before returning. `run_diff()` returns a `HistoryChange` record. Also carries `regressed_indices` (flat pixel indices of regressed pixels) and `grief_report` (`GriefReport`, falsy when empty) populated by `TileChecker.investigate_regression()` after large regressions.
- `PALETTE` (in `models/palette.py`) enforces and converts images to the project palette (first color treated as transparent). Provides `AsyncImage[T]` for deferred async I/O, and `aopen_file`/`aopen_bytes` methods for async image loading.
- `WatchMessage` (in `models/entities.py`) tracks persistent Discord messages that auto-update with project stats. Uses the Discord message snowflake as primary key (`message_id`). Unique constraint on `(project_id, channel_id)` enforces one watch per project per channel. FK to ProjectInfo with CASCADE delete.
- `Main` (in `main.py`) uses two-phase initialization: sync `__init__` followed by `async start()` to initialize `TileChecker` and refresh person-level statistics. Database lifecycle managed via `async with database(), maybe_bot() as bot:` context managers. No in-memory project loading — project discovery happens on demand in `TileChecker._get_projects_for_tile()`. Runs the polling loop: `TileChecker.check_next_tile()` returns `list[Project]` (all projects linked to the checked tile, whether or not changes occurred). `poll_once()` extracts project IDs for `HawkBot.update_watches()` (edit live Discord messages) and passes projects to `HawkBot.notify_griefs()` (send grief alerts to watch channels).
- Queue system tracks tile metadata (last checked, last modified). Redistribution runs automatically when the queue iterator exhausts (one full cycle), reassigning heat values based on last_update recency. Updates are optimistic: only tiles whose heat differs from the target are written.
- **Tile lifecycle invariants** — Two strict rules govern the relationship between project state, `TileProject` rows, and `TileInfo.heat`:
  1. **TileProject rows exist only for ACTIVE and PASSIVE projects.** INACTIVE and CREATING projects have no tile links. `link_tiles()` creates rows; `unlink_tiles()` removes them. State transitions between linked (ACTIVE/PASSIVE) and unlinked (INACTIVE/CREATING) states must call `link_tiles()` or `unlink_tiles()` accordingly. Coord/image edits skip relinking for INACTIVE projects.
  2. **`TileInfo.heat > 0` only when the tile has at least one ACTIVE project via TileProject.** Heat 0 means "not polled." `adjust_project_heat()` enforces this by querying for ACTIVE projects specifically (not just any TileProject existence). `link_tiles()` uses the in-memory `self.state` to promote tiles (avoids stale-DB reads when `info.save()` hasn't been called yet). State transitions between ACTIVE and PASSIVE call `adjust_linked_tiles_heat()` (after save) to re-evaluate heat. PASSIVE projects piggyback on tiles polled for ACTIVE projects but never drive polling themselves.

## File/Module map (where to look)

### Root (`src/pixel_hawk/`)
- `__init__.py` — empty package marker
- `main.py` — application entry, unified polling loop, DB context manager usage, person totals refresh, `InterceptHandler` (routes stdlib `logging` errors to loguru)

### Models (`src/pixel_hawk/models/`) — data layer
- `config.py` — `Config` dataclass (nest path + directory properties), `load_config()` (CLI/env/default with `dotenv`), `get_config()`, CONFIG singleton
- `db.py` — database async context manager (`database()`), Tortoise ORM config, Aerich integration, `rebuild_table()` migration utility
- `entities.py` — `Person` (user model with watched_tiles_count, active_projects_count, update_totals(), per-user quota limits), `ProjectState` IntEnum (ACTIVE/PASSIVE/INACTIVE/CREATING), `ProjectInfo` (pure Tortoise model with owner FK, random ID via `save_as_new()`), `HistoryChange` (diff event log), `DiffStatus` IntEnum, `TileInfo` (tile metadata: coordinates, heat, timestamps, etag), `TileProject` (tile-project junction table), `GuildConfig` (per-guild bot configuration: required role name, quota ceilings), `WatchMessage` (persistent Discord watch messages, message_id as PK)
- `geometry.py` — `Tile`, `Point`, `Size`, `Rectangle`, `GeoPoint` helpers (tile math, Web Mercator projection)
- `palette.py` — palette enforcement + `PALETTE` singleton + `AsyncImage[T]` (deferred async I/O handle)
- `griefing.py` — `Painter` NamedTuple (WPlace pixel authorship from API), `GriefReport` NamedTuple (regression investigation results: regress count + painters ordered by decreasing sample count)

### Watcher (`src/pixel_hawk/watcher/`) — polling engine
- `queues.py` — `QueueSystem`, temperature-based tile queues with Zipf distribution, tile metadata tracking
- `metadata.py` — functional service layer for ProjectInfo business logic (pixel counting, snapshot comparison, rate tracking, owner-attributed logging)
- `projects.py` — `Project` model (async diffs, snapshots, database-first loading via from_info(), carries `grief_report`), `stitch_tiles()` (async canvas assembly), `count_cached_tiles()` (tile cache check)
- `ingest.py` — `TileChecker` (tile monitoring orchestration, owns `httpx.AsyncClient`, query-driven project lookups via `TileProject`), `has_tile_changed()` (async tile download), `investigate_regression()` (adaptive pixel authorship sampling, stores `GriefReport` on Project), `investigate_pixel()` (single pixel authorship query). `check_next_tile()` returns `list[Project]` of affected projects (empty list if no tile selected or no projects linked)

### Interface (`src/pixel_hawk/interface/`) — user-facing
- `commands.py` — project management service layer: `new_project()` (project creation from uploaded image), `edit_project()` (project modification), `delete_project()` (project deletion with watch cleanup), `list_projects()` (project listing with stats, 24h changes, Discord message truncation), `_try_initial_diff()` (immediate diff when tiles are cached), coordinate/filename parsing helpers
- `watch.py` — living watch message service layer: `format_watch_message()` (comprehensive Discord markdown stats), `format_grief_message()` (grief alert formatting with owner mention and painter list), `create_watch()` / `remove_watch()` (CRUD with ownership validation), `save_watch_message()` (persistence after Discord send), `get_watches_for_projects()` (batch query for update loop), `delete_watches_for_project()` (cleanup helper)
- `access.py` — admin and guild access service layer: `ErrorMsg` (user-facing exception), `grant_admin()` (admin grant, callers responsible for authorization), `set_guild_role()` (per-guild role configuration), `check_guild_access()` (role-based access gate with auto-creation, inherits guild quota ceilings), `check_dm_access()` (DM access gate, requires existing Person with ADMIN or ALLOWED), `get_user_quotas()` / `set_user_quotas()` / `get_guild_quotas()` / `set_guild_quotas()` (quota management with guild ceiling enforcement)
- `interactions.py` — Discord bot wiring: `HawkBot` (user commands under `/hawk` group, admin commands under `/hawkadmin` group with `guild_only=True`), `_check_access()` (guild/DM access gate on user commands), `maybe_bot()` (lifecycle context manager, reads `HAWK_BOT_TOKEN`/`HAWK_COMMAND_PREFIX` env vars, yields bot instance or None), `update_watches()` (edits live Discord messages in TextChannel or DMChannel, auto-cleans on 404/403), `notify_griefs()` (sends grief alert messages to watch channels for projects with grief reports, auto-cleans stale watches). Dispatches to `commands.py`, `watch.py`, and `access.py` service functions

### Scripts and CI
- `scripts/install-service.sh` — Generates and installs systemd service unit; supports split deploy/service user via `--service-user` and `--nest` flags
- `scripts/kill-db-handles.ps1` — Windows utility to kill processes holding dangling handles to the SQLite database (requires Sysinternals `handle.exe`)
- `.github/workflows/deploy.yaml` — Auto-deploy on push to main via GitHub-hosted runner, Tailscale + SSH to production host (stop → pull → sync → migrate → start → verify)

## Architecture conventions

### Python philosophy (The Zen of Python)

This project embraces core principles from PEP 20 ("The Zen of Python"):

- **Explicit is better than implicit**: Use clear type annotations, named parameters, and obvious control flow. Avoid magic behavior or hidden side effects.
- **Simple is better than complex**: Favor straightforward solutions over clever ones. If you can solve a problem with basic Python, do that before reaching for advanced features.
- **Readability counts**: Code is read more often than written. Use descriptive short names, clear structure, and comments only where intent isn't obvious.
- **Flat is better than nested**: Avoid deep nesting in both code structure (prefer early returns) and module organization (prefer a flatter hierarchy).
- **Errors should never pass silently**: Let errors propagate with full stack traces rather than catching and hiding them. Use assertions for invariants—they're readable and provide clear failure points. Avoid try-catch blocks unless you have specific recovery logic.
- **In the face of ambiguity, refuse the temptation to guess**: When requirements or intent are unclear during development, ask for clarification rather than making assumptions. Don't clutter code with speculative checks.
- **If the implementation is hard to explain, it's a bad idea**: Strive for designs that are easy to describe. If you can't explain it simply, reconsider the approach.

### General conventions

- The project is in early stages: public APIs and internals may change. Prefer simplicity, clarity, and small, focused edits.
- Follow existing idioms: use `NamedTuple`/`dataclass`-like shapes, type hints, and explicit resource management.
- Type annotations: Python 3.14 provides deferred evaluation of annotations by default. Use unquoted type annotations (e.g., `def foo() -> Rectangle:` not `def foo() -> 'Rectangle':`). Forward references and self-references work without quotes.
- Avoid unnecessary type unions: prefer falsy defaults over `None` to eliminate `| None` annotations. Use `""` instead of `None` for strings, `0` for numbers, `()` or `[]` for collections — whenever the falsy value isn't meaningful. Similarly, prefer `NOT NULL` database columns with sensible defaults over nullable columns. Only use `None`/nullable when the absence of a value is semantically distinct from the falsy value.
- Preserve logging via `loguru` rather than replacing with ad-hoc prints.
- Async patterns:
  - The project is fully async. Use `async def` for I/O-bound functions. Use `asyncio.to_thread` for blocking PIL/filesystem operations.
  - HTTP requests use `httpx.AsyncClient` (owned by `TileChecker`).
  - `Main` uses two-phase init: sync `__init__` + `async start()` (avoids async `__init__` anti-pattern). `start()` initializes `TileChecker` and refreshes person statistics — no project loading.
- Image handling:
  - Use `PALETTE.ensure(image)` for conversion; no palette manipulation outside `palette.py`.
  - Always close PIL `Image` objects. In async code, prefer `async with PALETTE.aopen_file(path) as im:` or `async with PALETTE.aopen_bytes(data) as im:`. In sync code, prefer `with PALETTE.open_file(path) as im:`.
  - `AsyncImage[T]` wraps a blocking callable, runs it in a thread on first access, and supports both `async with` (auto-closes) and `await handle()` (caller closes) patterns.
  - For functions returning PIL images, use `with await async_fn() as im:` (the sync context manager on the async result).
- Time and date: prefer `round(time.time())` for timestamps to get integer seconds, which simplifies metadata and logging. Avoid using raw `time.time()` as well as `datetime` to keep things simple and consistent.
- Project state: `ProjectInfo` persists to SQLite via Tortoise ORM (`await info.save()`). New projects must use `save_as_new()` (or `from_rect()` which calls it) to assign a random ID — do not use `ProjectInfo.create()` directly. `Project` objects are constructed on demand (not cached in memory) when `TileChecker` needs to diff affected projects. `Project.info` (not `.metadata`) holds the `ProjectInfo` instance. Business logic uses functional service layer: `metadata.process_diff(info, ...)` instead of `info.process_diff(...)`.
- Multi-user workflow: Each Person has an auto-increment ID. ProjectInfo has a randomly assigned ID (via `save_as_new()`) and an owner FK to Person. Directory structure is `projects/{person_id}/{filename}` where filename is state-dependent (`new_{id}.png` for CREATING, coordinate-only otherwise). Names are stored in the database only. Watched tiles are tracked per person with overlap deduplication.
- Error handling: prefer non-fatal logging (warnings/debug) and avoid raising unexpected exceptions in the polling loop. Use `ErrorMsg` (in `access.py`) for errors whose message is intended to be displayed to the user; `interactions.py` catches `ErrorMsg` and sends it as an ephemeral Discord response.
- Defensive programming: Use assertions for "shouldn't happen" cases that indicate logic errors. These should be tested to ensure they catch bugs during development. Example: `assert condition, "clear error message"` for invariants that must hold.
- File size management:
  - If a Python file exceeds 400 lines, review it for simplification and deduplication opportunities; if that's insufficient, review it to split it into two modules; if splitting is not appropriate, add a comment at the top documenting that these approaches were attempted and why they were not viable.
  - Test files don't follow file size directives. Test files mirror the source structure (e.g., `tests/models/test_geometry.py` for `models/geometry.py`).

## Developer workflow & checks

- Linting: run `ruff` (project defines `line-length = 120`).
- Type checking: run `ty check` (configured in `pyproject.toml` under `[tool.ty]`).
  - Run type checks: `uv run ty check`
- Formatting: no explicit formatter in repo; follow current style and ruff suggestions.
- Tests: unit tests live under `tests/`. We use `pytest` with `pytest-asyncio` and `pytest-cov` for coverage.
  - `asyncio_mode = "auto"` is configured in `pyproject.toml` — all `async def` test functions are auto-detected, no `@pytest.mark.asyncio` decorator needed.
  - Coverage is configured in `pyproject.toml` under `[tool.pytest.ini_options]`.
  - The project enforces a coverage threshold for all modules.
  - Test assertions: Write tests that verify assertions fire for "shouldn't happen" cases (use `pytest.raises(AssertionError)`).
  - Run tests: `uv run pytest`

## Aerich migrations and SQLite

Aerich manages schema migrations, but SQLite has gaps that require workarounds:

- **`MODIFY COLUMN` is unsupported in SQLite.** If `aerich migrate` fails with `NotSupportError: Modify column is unsupported in SQLite`, the column type/constraint change needs a table rebuild. Use `rebuild_table()` from `models/db.py` in a manually-written migration file (see its docstring for usage). `MODELS_STATE` at the bottom of migration files is cosmetic — Aerich reads state from its `aerich` DB table, not from migration files, so manual migrations work fine without it.
- **`ALTER COLUMN COMMENT` is unsupported in SQLite.** Enum description changes (e.g., adding a new `IntEnumField` value) trigger this. These are cosmetic — the column type doesn't actually change. Fix by patching the aerich DB state (update the `description` field in the stored content JSON).
- **Tortoise ORM upgrades can cause phantom diffs.** Newer Tortoise versions may add keys to model field descriptions (e.g., `db_default`). The stored aerich state (written by the old Tortoise) lacks these keys, so `aerich migrate` sees phantom changes on every field. Fix by patching the aerich DB content to include the new keys, or by including the patch in a migration file (see migration 3 for an example).
- **Diagnosing aerich diffs:** Compare the aerich DB content with current model descriptions using `dictdiffer.diff()` to see exactly what Aerich thinks changed. Read the last `Aerich` record's `content` field and compare with `aerich.utils.get_models_describe('models')`.
- **Deploy workflow:** Production runs `aerich upgrade` (applies migration files), never `aerich migrate` (generates new migrations). Migration generation is dev-only.
- **CI migration command:** The service user has no home directory, so uv's default cache path fails. Use `uv run --cache-dir /var/cache/pixel-hawk --frozen aerich upgrade` — `--cache-dir` avoids the env var (which sudo blocks), and `--frozen` skips lockfile resolution since `uv sync` already ran as the deploy user. Wrap with `timeout 10` to prevent hangs (e.g. SQLite lock contention).

## Running and debugging

- To debug tile fetching behavior, create a `TileChecker`, call `await checker.has_tile_changed(tile_info)` with a `TileInfo` instance, and observe `get_config().tiles_dir` for generated `tile-*.png` files.
- To debug project diffing: Create Person, ProjectInfo, TileInfo, and TileProject records in the database, place the PNG file in `projects/{person_id}/{tx}_{ty}_{px}_{py}.png`, and trigger `check_next_tile()`. Projects are discovered on demand via database query — no startup loading step needed.

## Code change guidelines

- Suggest minimal, testable code changes and include brief rationale.
- When adding features, propose where to add unit tests (suggest `tests/models/test_geometry.py`, `tests/models/test_palette.py`).
- If modifying image handling, show the expected lifecycle (open -> ensure palette -> close) and indicate why conversions are safe.
- Prefer explicit, type-annotated functions and small helper functions over large refactors.

## Packaging & distribution

- `pyproject.toml` contains project metadata and the console script entry point.
- Use `uv sync` for dependency management and installation.

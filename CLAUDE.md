## Project Overview

pixel-hawk is a change tracker for WPlace paint projects. It polls WPlace tile images, and diffs them against project image files provided by users. The package entry point is exposed as the console script `hawk` (see `pyproject.toml`).

## Quick facts

- **Requires:** Python >= 3.14 (see `pyproject.toml`)
- **Console script:** `hawk = "pixel_hawk.main:main"`
- **Main package:** `src/pixel_hawk`
- **Key dependencies:** `loguru`, `pillow`, `httpx`, `tortoise-orm` (SQLite via `aiosqlite`), `humanize`
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

- Configuration managed through `src/pixel_hawk/config.py`
- Default nest: `./nest` (current working directory)
- Configurable via CLI flag `--nest` or environment variable `HAWK_NEST`
- All data lives under nest with organized subdirectories:
  - `projects/{person_id}/` — project PNG files organized by person ID (coordinate-only filenames: `{tx}_{ty}_{px}_{py}.png`)
  - `tiles/` — cached tiles from WPlace
  - `snapshots/{person_id}/` — canvas state snapshots, same structure as projects (coordinate-only filenames)
  - `logs/` — application logs
  - `data/` — SQLite database (`pixel-hawk.db`) with Person, ProjectInfo, HistoryChange, TileInfo, and TileProject tables
- **Design rationale:** The default `./nest` location allows running pixel-hawk from the project root during development, keeping all data files easily accessible for inspection from IDE and AI agents. This simplifies debugging, testing, and data analysis without requiring path configuration.
- Access configuration via `get_config()` from `config.py`
- CONFIG singleton is lazily initialized on first access
- All subdirectories auto-created by `load_config()` on startup

## How it works (high level)

- **Multi-user, query-driven architecture**: Projects are stored in SQLite and discovered on demand via database queries — no in-memory project index. Multiple users can track the same or different coordinates. Projects are keyed by (owner_id, name) with unique constraint.
- The application is fully async, built on `asyncio`. The entry point (`main()`) calls `asyncio.run()` on the async main loop. Blocking I/O (PIL image operations, filesystem access) is offloaded via `asyncio.to_thread`.
- The application runs in a unified ~97 second polling loop (60φ = 30(1+√5), chosen to avoid resonance with WPlace's internal timers) that checks tiles.
- Tile polling uses intelligent temperature-based queue system: `QueueSystem` (in `queues.py`) maintains burning and temperature queues with Zipf distribution sizing. Tiles are selected round-robin across queues, with least-recently-checked tile selected from each queue.
- `TileChecker` (in `ingest.py`) manages tile monitoring: creates and owns an `httpx.AsyncClient`, selects tiles via `QueueSystem`, calls `has_tile_changed()` to fetch from WPlace backend, queries affected projects via `TileProject` junction table, and constructs `Project` objects on demand for diffing.
- `has_tile_changed()` (in `ingest.py`) requests tiles from the WPlace tile backend using `httpx` and updates a cached paletted PNG if there are changes.
- `Person` (in `models.py`) represents users with auto-increment ID. Tracks `watched_tiles_count` (unique tiles across all active projects) and `active_projects_count`. Both updated via `update_totals()` on startup.
- `ProjectState` IntEnum (in `models.py`) defines project states: ACTIVE (0), PASSIVE (10), INACTIVE (20).
- `ProjectInfo` (in `models.py`) is a pure Tortoise ORM model with owner FK (Person), name (stored in DB), and state. IDs are randomly assigned (1 to 9999) via `save_as_new()`, which retries on collision (EAFP pattern). Tracks completion history, progress/regress statistics, and rates. Persists to SQLite in `data/pixel-hawk.db`. The `filename` property returns coordinate-only format: `{tx}_{ty}_{px}_{py}.png`.
- `HistoryChange` (in `models.py`) records every diff event per project with pixel counts, completion percentage, and progress/regress deltas.
- Business logic for ProjectInfo lives in `metadata.py` as standalone functions (functional service layer). Functions take `ProjectInfo` as first parameter and mutate fields in place. Log messages include owner name for multi-user attribution.
- `Project` (in `projects.py`) is loaded from database via `Project.from_info(info)` classmethod. Filenames are coordinate-only: `{tx}_{ty}_{px}_{py}.png` (tile x, tile y, pixel x 0-999, pixel y 0-999). Files must use the project's palette. Invalid files cause from_info() to return None with warning logged.
- `PALETTE` (in `palette.py`) enforces and converts images to the project palette (first color treated as transparent). Provides `AsyncImage[T]` for deferred async I/O, and `aopen_file`/`aopen_bytes` methods for async image loading.
- `Main` (in `main.py`) uses two-phase initialization: sync `__init__` followed by `async start()` to initialize `TileChecker` and refresh person-level statistics. Database lifecycle managed via `async with database():` context manager. No in-memory project loading — project discovery happens on demand in `TileChecker._get_projects_for_tile()`. Runs the polling loop: `TileChecker.check_next_tile()` handles tile selection, checking, project querying, and diffing.
- Queue system tracks tile metadata (last checked, last modified). Redistribution runs automatically when the queue iterator exhausts (one full cycle), reassigning heat values based on last_update recency. Updates are optimistic: only tiles whose heat differs from the target are written.

## File/Module map (where to look)

- `src/pixel_hawk/__init__.py` — empty package marker (just comment + docstring)
- `src/pixel_hawk/config.py` — `Config` dataclass, `load_config()`, `get_config()`, CONFIG singleton
- `src/pixel_hawk/db.py` — database async context manager (`database()`), Tortoise ORM config, Aerich integration
- `src/pixel_hawk/models.py` — `Person` (user model with watched_tiles_count, active_projects_count, update_totals()), `ProjectState` IntEnum (ACTIVE/PASSIVE/INACTIVE), `ProjectInfo` (pure Tortoise model with owner FK, random ID via `save_as_new()`), `HistoryChange` (diff event log), `DiffStatus` IntEnum, `TileInfo` (tile metadata: coordinates, heat, timestamps, etag), `TileProject` (tile-project junction table)
- `src/pixel_hawk/main.py` — application entry, unified polling loop, DB context manager usage, person totals refresh
- `src/pixel_hawk/geometry.py` — `Tile`, `Point`, `Size`, `Rectangle` helpers (tile math)
- `src/pixel_hawk/ingest.py` — `TileChecker` (tile monitoring orchestration, owns `httpx.AsyncClient`, query-driven project lookups via `TileProject`), `has_tile_changed()` (async tile download)
- `src/pixel_hawk/palette.py` — palette enforcement + `PALETTE` singleton + `AsyncImage[T]` (deferred async I/O handle)
- `src/pixel_hawk/projects.py` — `Project` model (async diffs, snapshots, database-first loading via from_info()), `stitch_tiles()` (async canvas assembly)
- `src/pixel_hawk/metadata.py` — functional service layer for ProjectInfo business logic (pixel counting, snapshot comparison, rate tracking, owner-attributed logging)
- `src/pixel_hawk/queues.py` — `QueueSystem`, temperature-based tile queues with Zipf distribution, tile metadata tracking
- `scripts/rebuild.py` — Idempotent database rebuild from filesystem artifacts (projects, tiles, snapshots)

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
- Multi-user workflow: Each Person has an auto-increment ID. ProjectInfo has a randomly assigned ID (via `save_as_new()`) and an owner FK to Person. Directory structure is `projects/{person_id}/{filename}` where filename is coordinate-only. Names are stored in the database only. Watched tiles are tracked per person with overlap deduplication.
- Error handling: prefer non-fatal logging (warnings/debug) and avoid raising unexpected exceptions in the polling loop.
- Defensive programming: Use assertions for "shouldn't happen" cases that indicate logic errors. These should be tested to ensure they catch bugs during development. Example: `assert condition, "clear error message"` for invariants that must hold.
- File size management:
  - If a Python file exceeds 400 lines, review it for simplification opportunities; if simplification is insufficient, review it to split it into two modules; if splitting is not appropriate, add a comment at the top documenting that these approaches were attempted and why they were not viable.
  - If a Python file is smaller than 20 lines, review it to see if it's still needed; if it is, consider whether it should be merged into another module for simplicity; if it should remain separate, add a comment at the top documenting why it is still needed and why merging was not appropriate.
  - Test files don't follow file size directives. Test files are named after the module they test, prefixed with `test_`, e.g., `test_geometry.py` for `geometry.py`.

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

## Running and debugging

- To debug tile fetching behavior, create a `TileChecker`, call `await checker.has_tile_changed(tile_info)` with a `TileInfo` instance, and observe `get_config().tiles_dir` for generated `tile-*.png` files.
- To debug project diffing: Create Person, ProjectInfo, TileInfo, and TileProject records in the database, place the PNG file in `projects/{person_id}/{tx}_{ty}_{px}_{py}.png`, and trigger `check_next_tile()`. Projects are discovered on demand via database query — no startup loading step needed.

## Code change guidelines

- Suggest minimal, testable code changes and include brief rationale.
- When adding features, propose where to add unit tests (suggest `tests/test_geometry.py`, `tests/test_palette.py`).
- If modifying image handling, show the expected lifecycle (open -> ensure palette -> close) and indicate why conversions are safe.
- Prefer explicit, type-annotated functions and small helper functions over large refactors.

## Packaging & distribution

- `pyproject.toml` contains project metadata and the console script entry point.
- Use `uv sync` for dependency management and installation.

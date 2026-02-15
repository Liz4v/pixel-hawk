## Project Overview

pixel-hawk is a change tracker for WPlace paint projects. It polls WPlace tile images, and diffs them against project image files provided by users. The package entry point is exposed as the console script `pixel-hawk` (see `pyproject.toml`).

## Quick facts

- **Requires:** Python >= 3.14 (see `pyproject.toml`)
- **Console script:** `pixel-hawk = "pixel_hawk.main:main"`
- **Main package:** `src/pixel_hawk`
- **Key dependencies:** `loguru`, `pillow`, `httpx`, `tortoise-orm` (SQLite via `aiosqlite`), `ruamel.yaml` (legacy migration)
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
uv run pixel-hawk
```

## Configuration and data directories

- Configuration managed through `src/pixel_hawk/config.py`
- Default pixel-hawk-home: `./pixel-hawk-data` (current working directory)
- Configurable via CLI flag `--pixel-hawk-home` or environment variable `PIXEL_HAWK_HOME`
- All data lives under pixel-hawk-home with organized subdirectories:
  - `projects/{person_id}/` — project PNG files organized by person ID (coordinate-only filenames: `{tx}_{ty}_{px}_{py}.png`)
  - `tiles/` — cached tiles from WPlace
  - `snapshots/{person_id}/` — canvas state snapshots, same structure as projects (coordinate-only filenames)
  - `metadata/` — legacy YAML metadata files (migrated to SQLite on first load)
  - `logs/` — application logs
  - `data/` — SQLite database (`pixel-hawk.db`) with Person, ProjectInfo, and HistoryChange tables
- **Design rationale:** The default `./pixel-hawk-data` location allows running pixel-hawk from the project root during development, keeping all data files easily accessible for inspection from IDE and AI agents. This simplifies debugging, testing, and data analysis without requiring path configuration.
- Access configuration via `get_config()` from `config.py`
- CONFIG singleton is lazily initialized on first access
- All subdirectories auto-created by `load_config()` on startup

## How it works (high level)

- **Multi-user, database-first architecture**: The application loads projects from SQLite at startup (no filesystem polling). Multiple users can track the same or different coordinates. Projects are keyed by (owner_id, name) with unique constraint.
- The application is fully async, built on `asyncio`. The entry point (`main()`) calls `asyncio.run()` on the async main loop. Blocking I/O (PIL image operations, filesystem access) is offloaded via `asyncio.to_thread`.
- The application runs in a unified ~97 second polling loop (60φ = 30(1+√5), chosen to avoid resonance with WPlace's internal timers) that checks tiles.
- Tile polling uses intelligent temperature-based queue system: `QueueSystem` (in `queues.py`) maintains burning and temperature queues with Zipf distribution sizing. Tiles are selected round-robin across queues, with least-recently-checked tile selected from each queue.
- `TileChecker` (in `ingest.py`) manages tile monitoring: creates and owns an `httpx.AsyncClient`, selects tiles via `QueueSystem`, calls `has_tile_changed()` to fetch from WPlace backend, and triggers project diffs when changes are detected.
- `has_tile_changed()` (in `ingest.py`) requests tiles from the WPlace tile backend using `httpx` and updates a cached paletted PNG if there are changes.
- `Person` (in `models.py`) represents users with auto-increment ID. Tracks `watched_tiles_count` (unique tiles across all active projects, updated on startup).
- `ProjectState` enum (in `models.py`) defines project states: ACTIVE (monitored), PASSIVE (loaded but not monitored), INACTIVE (not loaded).
- `ProjectInfo` (in `models.py`) is a pure Tortoise ORM model with owner FK (Person), name (stored in DB), and state. Tracks completion history, progress/regress statistics, and rates. Persists to SQLite in `data/pixel-hawk.db`. The `filename` property returns coordinate-only format: `{tx}_{ty}_{px}_{py}.png`.
- `HistoryChange` (in `models.py`) records every diff event per project with pixel counts, completion percentage, and progress/regress deltas.
- Business logic for ProjectInfo lives in `metadata.py` as standalone functions (functional service layer). Functions take `ProjectInfo` as first parameter and mutate fields in place. Log messages include owner name for multi-user attribution.
- `Project` (in `projects.py`) is loaded from database via `Project.from_info(info)` classmethod. Filenames are coordinate-only: `{tx}_{ty}_{px}_{py}.png` (tile x, tile y, pixel x 0-999, pixel y 0-999). Files must use the project's palette. Invalid files cause from_info() to return None with warning logged.
- `PALETTE` (in `palette.py`) enforces and converts images to the project palette (first color treated as transparent). Provides `AsyncImage[T]` for deferred async I/O, and `aopen_file`/`aopen_bytes` methods for async image loading.
- `Main` (in `main.py`) uses two-phase initialization: sync `__init__` followed by `async start()` to load projects from database. Database lifecycle managed via `async with database():` context manager. Queries ProjectInfo table for active/passive projects, calls `Project.from_info()` for each, updates watched_tiles_count for all persons, and builds tile index. Runs the polling loop: `TileChecker.check_next_tile()` handles tile selection and checking. On tile changes it diffs updated tiles with project images and logs progress with owner attribution.
- Queue system tracks tile metadata (last checked, last modified) and repositions tiles surgically when modification times change. When a tile moves to a hotter queue, coldest tiles cascade down through intervening queues to maintain Zipf distribution sizes.

## File/Module map (where to look)

- `src/pixel_hawk/__init__.py` — empty package marker (just comment + docstring)
- `src/pixel_hawk/config.py` — `Config` dataclass, `load_config()`, `get_config()`, CONFIG singleton
- `src/pixel_hawk/db.py` — database async context manager (`database()`), Tortoise ORM config, Aerich integration
- `src/pixel_hawk/models.py` — `Person` (user model with watched_tiles_count), `ProjectState` enum (active/passive/inactive), `ProjectInfo` (pure Tortoise model with owner FK), `HistoryChange` (diff event log), `DiffStatus` enum
- `src/pixel_hawk/main.py` — application entry, unified polling loop, database-first project loading, DB context manager usage, watched tiles tracking
- `src/pixel_hawk/geometry.py` — `Tile`, `Point`, `Size`, `Rectangle` helpers (tile math)
- `src/pixel_hawk/ingest.py` — `TileChecker` (tile monitoring orchestration, owns `httpx.AsyncClient`), `has_tile_changed()` (async tile download), `stitch_tiles()` (async canvas assembly)
- `src/pixel_hawk/palette.py` — palette enforcement + `PALETTE` singleton + `AsyncImage[T]` (deferred async I/O handle)
- `src/pixel_hawk/projects.py` — `Project` model (async diffs, snapshots, database-first loading via from_info(), YAML migration)
- `src/pixel_hawk/metadata.py` — functional service layer for ProjectInfo business logic (pixel counting, snapshot comparison, rate tracking, owner-attributed logging)
- `src/pixel_hawk/queues.py` — `QueueSystem`, temperature-based tile queues with Zipf distribution, tile metadata tracking

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
  - `Main` uses two-phase init: sync `__init__` + `async start()` (avoids async `__init__` anti-pattern).
- Image handling:
  - Use `PALETTE.ensure(image)` for conversion; no palette manipulation outside `palette.py`.
  - Always close PIL `Image` objects. In async code, prefer `async with PALETTE.aopen_file(path) as im:` or `async with PALETTE.aopen_bytes(data) as im:`. In sync code, prefer `with PALETTE.open_file(path) as im:`.
  - `AsyncImage[T]` wraps a blocking callable, runs it in a thread on first access, and supports both `async with` (auto-closes) and `await handle()` (caller closes) patterns.
  - For functions returning PIL images, use `with await async_fn() as im:` (the sync context manager on the async result).
- Time and date: prefer `round(time.time())` for timestamps to get integer seconds, which simplifies metadata and logging. Avoid using raw `time.time()` as well as `datetime` to keep things simple and consistent.
- Project state: Projects are loaded from database at startup and kept in memory during runtime. `ProjectInfo` persists to SQLite via Tortoise ORM (`await info.save()`). `Project.info` (not `.metadata`) holds the `ProjectInfo` instance. Business logic uses functional service layer: `metadata.process_diff(info, ...)` instead of `info.process_diff(...)`. The projects dict is keyed by `ProjectInfo.id` (integer), not by Path.
- Multi-user workflow: Each Person has an auto-increment ID. ProjectInfo has an owner FK to Person. Directory structure is `projects/{person_id}/{filename}` where filename is coordinate-only. Names are stored in the database only. Watched tiles are tracked per person with overlap deduplication.
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

- To debug tile fetching behavior, call `await has_tile_changed(tile, client)` with a `Tile` object and an `httpx.AsyncClient` in an async script and observe `get_config().tiles_dir` for generated `tile-*.png` files.
- To debug project loading: Create a Person and ProjectInfo record in the database, place the PNG file in `projects/{person_id}/{tx}_{ty}_{px}_{py}.png`, and watch the log output from `Main.start()`. Use `Project.from_info(info)` to test individual project loading.

## Code change guidelines

- Suggest minimal, testable code changes and include brief rationale.
- When adding features, propose where to add unit tests (suggest `tests/test_geometry.py`, `tests/test_palette.py`).
- If modifying image handling, show the expected lifecycle (open -> ensure palette -> close) and indicate why conversions are safe.
- Prefer explicit, type-annotated functions and small helper functions over large refactors.

## Packaging & distribution

- `pyproject.toml` contains project metadata and the console script entry point.
- Use `uv sync` for dependency management and installation.

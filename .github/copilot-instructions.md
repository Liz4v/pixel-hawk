## Project Overview

cam (Canvas Activity Monitor) is a small watcher for WPlace paint projects. It polls WPlace tile images, stitches cached tiles, and diffs them against project image files a user places in their platform pictures folder. The package entry point is exposed as the console script `cam` (see `pyproject.toml`).

## Quick facts

- **Requires:** Python >= 3.14 (see `pyproject.toml`)
- **Console script:** `cam = "cam.main:main"`
- **Main package:** `src/cam`
- **Key dependencies:** `loguru`, `pillow`, `platformdirs`, `requests`, `ruamel.yaml`
- **Linting:** `ruff` configured with `line-length = 120`

## Where to look for further context

- `pyproject.toml` for packaging/deps and `ruff` config
- `README.md` for user-facing documentation and external resources/links
- `tests/` for unit tests and test patterns

## Development Environment

**PowerShell Version:** On Windows, the default shell is PowerShell 5.1 (Desktop Edition). This version has some syntax and feature limitations compared to modern PowerShell 7+ (Core), so be mindful of these when running commands or scripts.

**Important PowerShell 5.1 Limitations:**
- **Does NOT support `&&` for command chaining** — use semicolons `;` instead
- **Does NOT support `||` for conditional execution** — use proper PowerShell syntax with `;` or `if` statements
- This is Windows PowerShell (Desktop Edition), not PowerShell 7+ (Core)
- Some cmdlets and features differ from modern PowerShell versions

**Command Chaining Examples:**
- ✅ Correct: `cd src; python script.py`
- ❌ Wrong: `cd src && python script.py` (will fail with syntax error)

## Quickstart (developer)

See the `uv` documentation: https://pypi.org/project/uv/

- Use `uv` to manage Python and project dependencies. Example:

```bash
uv sync
```

- Run the watcher locally with the console script or module (via your `uv` environment):

```bash
uv run cam
```

## Where data lives

- The package uses `platformdirs.PlatformDirs("cam")` and exposes `DIRS` from `src/cam/__init__.py`.
- User pictures path: `DIRS.user_pictures_path / "wplace"` — drop project PNGs here.

## How it works (high level)

- The application runs in a unified ~97 second polling loop (60φ = 30(1+√5), chosen to avoid resonance with WPlace's internal timers) that checks both tiles and project files.
- Tile polling uses intelligent temperature-based queue system: `QueueSystem` (in `queues.py`) maintains burning and temperature queues with Zipf distribution sizing. Tiles are selected round-robin across queues, with least-recently-checked tile selected from each queue.
- `TileChecker` (in `ingest.py`) manages tile monitoring: selects tiles via `QueueSystem`, calls `has_tile_changed()` to fetch from WPlace backend, and triggers project diffs when changes are detected.
- `has_tile_changed()` (in `ingest.py`) requests tiles from the WPlace tile backend and updates a cached paletted PNG if there are changes.
- `Project` (in `projects.py`) discovers project PNGs placed under the `wplace` pictures folder. Filenames must include 4 coordinates in format `*_<tx>_<ty>_<px>_<py>.png` (tile x, tile y, pixel x 0-999, pixel y 0-999) and must use the project's palette.
- Invalid files (missing coordinates, bad palette) are tracked as `ProjectShim` instances to avoid repeated load attempts.
- `PALETTE` (in `palette.py`) enforces and converts images to the project palette (first color treated as transparent).
- `ProjectMetadata` (in `metadata.py`) tracks completion history, progress/regress statistics, streaks, and rates. Persists to YAML files alongside project images.
- `Main` (in `main.py`) runs the polling loop: `TileChecker.check_next_tile()` handles tile selection and checking, `check_projects()` scans for new/modified/deleted project files. On tile changes it diffs updated tiles with project images and logs progress.
- Queue system tracks tile metadata (last checked, last modified) and repositions tiles surgically when modification times change. When a tile moves to a hotter queue, coldest tiles cascade down through intervening queues to maintain Zipf distribution sizes.

## File/Module map (where to look)

- `src/cam/__init__.py` — `DIRS` (platform dirs)
- `src/cam/main.py` — application entry, unified polling loop, project load/forget logic
- `src/cam/geometry.py` — `Tile`, `Point`, `Size`, `Rectangle` helpers (tile math)
- `src/cam/ingest.py` — `TileChecker` (tile monitoring orchestration), `has_tile_changed()` (tile download), `stitch_tiles()` (canvas assembly)
- `src/cam/palette.py` — palette enforcement + helper `PALETTE`
- `src/cam/projects.py` — `Project` model (orchestrates diffs), `ProjectShim` shim (invalid files)
- `src/cam/metadata.py` — `ProjectMetadata` (completion tracking, statistics, streaks, YAML persistence)
- `src/cam/queues.py` — `QueueSystem`, temperature-based tile queues with Zipf distribution, tile metadata tracking

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
- Follow existing idioms: use `NamedTuple`/`dataclass`-like shapes, type hints, and explicit resource management (`with` for PIL Images).
- Type annotations: Python 3.14 provides deferred evaluation of annotations by default. Use unquoted type annotations (e.g., `def foo() -> Rectangle:` not `def foo() -> 'Rectangle':`). Forward references and self-references work without quotes.
- Preserve logging via `loguru` rather than replacing with ad-hoc prints.
- Image handling:
  - Use `PALETTE.ensure(image)` for conversion; no palette manipulation outside `palette.py`.
  - Always close PIL `Image` objects; prefer `with Image.open(...) as im:` or the helper patterns already present.
- Time and date: prefer `round(time.time())` for timestamps to get integer seconds, which simplifies metadata and logging. Avoid using raw `time.time()` as well as `datetime` to keep things simple and consistent.
- Project state: Projects are discovered from the filesystem on each polling cycle and kept in memory during runtime (metadata only).
- Error handling: prefer non-fatal logging (warnings/debug) and avoid raising unexpected exceptions in the polling loop.
- Defensive programming: Use assertions for "shouldn't happen" cases that indicate logic errors. These should be tested to ensure they catch bugs during development. Example: `assert condition, "clear error message"` for invariants that must hold.
- File size management:
  - If a Python file exceeds 400 lines, review it for simplification opportunities; if simplification is insufficient, review it to split it into two modules; if splitting is not appropriate, add a comment at the top documenting that these approaches were attempted and why they were not viable.
  - If a Python file is smaller than 20 lines, review it to see if it's still needed; if it is, consider whether it should be merged into another module for simplicity; if it should remain separate, add a comment at the top documenting why it is still needed and why merging was not appropriate.
  - Test files don't follow file size directives. Test files are named after the module they test, prefixed with `test_`, e.g., `test_geometry.py` for `geometry.py`.

## Developer workflow & checks

- Linting: run `ruff` (project defines `line-length = 120`).
- Type checking: run `mypy` (configured in `pyproject.toml` under `[tool.mypy]`).
  - Run type checks: `uv run mypy`
- Formatting: no explicit formatter in repo; follow current style and ruff suggestions.
- Tests: unit tests live under `tests/`. We use `pytest` with `pytest-cov` for coverage.
  - Coverage is configured in `pyproject.toml` under `[tool.pytest.ini_options]`.
  - The project enforces a coverage threshold for all modules.
  - Test assertions: Write tests that verify assertions fire for "shouldn't happen" cases (use `pytest.raises(AssertionError)`).
  - Mocking `DIRS`: The `DIRS` object from `platformdirs` has read-only properties (`user_cache_path`, `user_pictures_path`, etc.). To mock these in tests, create a simple mock class with the needed attributes and monkeypatch the entire `DIRS` object in the module that imports it. Example: `class MockDIRS: user_cache_path = tmp_path / "cache"` then `monkeypatch.setattr(module_under_test, "DIRS", MockDIRS())`. Do NOT try to `setattr` on DIRS properties directly.
  - Run tests: `uv run pytest`

## Running and debugging

- To debug tile fetching behavior, call `has_tile_changed()` directly with a `Tile` object in an interactive script and observe `DIRS.user_cache_path` for generated `tile-*.png` files.
- To debug project parsing, drop a correctly named PNG into `DIRS.user_pictures_path / 'wplace'` and watch the log output from `Main`.

## Notes for Copilot (how to suggest changes)

- Suggest minimal, testable code changes and include brief rationale in the PR description.
- When adding features, propose where to add unit tests (suggest `tests/test_geometry.py`, `tests/test_palette.py`).
- If modifying image handling, show the expected lifecycle (open -> ensure palette -> close) and indicate why conversions are safe.
- Prefer explicit, type-annotated functions and small helper functions over large refactors.

## Packaging & distribution

- `pyproject.toml` contains project metadata and the console script entry point.
- Use `uv sync` for dependency management and installation.

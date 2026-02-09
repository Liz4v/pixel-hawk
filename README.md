# CAM (Canvas Activity Monitor)

A change tracker for WPlace paint projects. It monitors tile changes and tracks progress and attacks on your artwork.

## What it does

cam polls WPlace tile images, stitches cached tiles, and diffs them against project image files you place in your platform pictures folder. It runs a unified ~97 second polling loop (60φ = 30(1+√5), chosen to avoid resonance with WPlace's internal timers) that:

- Uses intelligent temperature-based queue system with Zipf distribution to prioritize hot tiles
- Checks one tile per cycle in round-robin fashion across burning and temperature queues
- Downloads and caches WPlace tiles when they change
- Discovers project PNGs in your `wplace` pictures folder
- Diffs updated tiles against your project images
- Logs pixel placement progress

### Queue system

The watcher maintains multiple temperature-based queues:
- **Burning queue:** Tiles that have never been checked (highest priority)
- **Temperature queues:** Organized by tile modification time (hot to cold)
  - Queue sizes follow Zipf distribution (harmonic series)
  - Recently modified tiles get checked more frequently
  - Tiles graduate from burning → temperature queues after first check
  - Modified tiles surgically reposition to hotter queues with cascade preservation

## Requirements

- Python >= 3.14
- [`uv`](https://pypi.org/project/uv/) for dependency management

## Installation

```powershell
uv sync
```

## Usage

Run the watcher:

```powershell
uv run cam
```

By default, cam uses `./cam-data` in your current working directory. You can customize this:

```powershell
# Use custom directory
uv run cam --cam-home /path/to/cam-data

# Or set environment variable
$env:CAM_HOME = "C:\path\to\cam-data"
uv run cam
```

**Precedence:** CLI flag `--cam-home` > environment variable `CAM_HOME` > default `./cam-data`

### Setting up projects

1. Create a project image using the WPlace palette (first color is treated as transparent)
2. Name your file with coordinates in the format: `project_<tx>_<ty>_<px>_<py>.png` or similar (4 numbers separated by underscores or hyphens: tile x, tile y, pixel x within tile, pixel y within tile)
3. Place it in `<cam-home>/projects/` directory (default: `./cam-data/projects/`)
4. The watcher will automatically discover and track it

### Where data lives

All cam data lives in a unified directory structure under `cam-home` (default: `./cam-data`):

- **`projects/`** — Place your project PNG files here
- **`tiles/`** — Cached tiles from WPlace backend
- **`snapshots/`** — Canvas state snapshots for tracking changes
- **`metadata/`** — Project statistics and completion history (YAML files)
- **`logs/`** — Application logs (`cam.log` with 10 MB rotation and 7-day retention)
- **`data/`** — Reserved for future bot data and state

**Recommendation:** Run cam from a dedicated directory (e.g., create `~/cam-workspace/` and run cam from there) so `./cam-data` stays organized.

## Development

The project uses `ruff` for linting (line-length = 120), `mypy` for type checking, and `pytest` for testing with 95% coverage threshold.

Run tests:
```powershell
uv run pytest
```

Run type checking:
```powershell
uv run mypy
```

## See Also

* [Blue Marble](https://bluemarble.lol/)
* [WPlace Art Exporter](https://gist.github.com/Kottonye/0e460154cb9b3132c940fa2b4be52faf)
* [Skirk Marble](https://github.com/Seris0/Wplace-SkirkMarble)

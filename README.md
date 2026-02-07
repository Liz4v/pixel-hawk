# wwpppp

A small watcher for WPlace paint projects that monitors tile changes and tracks progress on your artwork.

## What it does

wwpppp polls WPlace tile images, stitches cached tiles, and diffs them against project image files you place in your platform pictures folder. It runs a unified ~97 second polling loop (60φ = 30(1+√5), chosen to avoid resonance with WPlace's internal timers) that:

- Checks one tile per cycle in round-robin fashion (to avoid hammering the backend)
- Downloads and caches WPlace tiles when they change
- Discovers project PNGs in your `wplace` pictures folder
- Diffs updated tiles against your project images
- Logs pixel placement progress

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
uv run wwpppp
```

### Setting up projects

1. Create a project image using the WPlace palette (first color is treated as transparent)
2. Name your file with coordinates in the format: `project_<x>_<y>.png` or similar (coordinates must be in filename)
3. Place it in your platform pictures folder under `wplace/` subdirectory
4. The watcher will automatically discover and track it

### Where data lives

- **Project images:** `<user_pictures_path>/wplace/` — place your project PNGs here
- **Tile cache:** `<user_cache_path>/wwpppp/` — cached tiles from WPlace

Platform paths are managed via `platformdirs.PlatformDirs("wwpppp")`.

## Development

The project uses `ruff` for linting (line-length = 120) and `pytest` for testing.

Run tests:
```powershell
uv run pytest
```

## See Also

* [Blue Marble](https://bluemarble.lol/)
* [WPlace Art Exporter](https://gist.github.com/Kottonye/0e460154cb9b3132c940fa2b4be52faf)
* [Skirk Marble](https://github.com/Seris0/Wplace-SkirkMarble)

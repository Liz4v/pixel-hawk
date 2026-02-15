# Pixel Hawk Glossary

This document defines key terminology used throughout the pixel-hawk project. Focus is on concepts users need to understand when working with projects, not implementation details.

## Core Concepts

### pixel-hawk
Pixel Hawk — the application that watches WPlace paint projects, polls tile images, and tracks pixel placement progress.

### WPlace
[WPlace.live](https://wplace.live) is an online collaborative pixel art canvas where users place individual pixels. The canvas is organized into tiles, each containing 1000×1000 pixels.

### Project
A user-created PNG image file representing artwork to be painted on WPlace. Projects must be placed in the platform pictures folder under `wplace/` subdirectory and must use the WPlace palette. Filenames must include 4 coordinates in format `*_<tx>_<ty>_<px>_<py>.png` where tx/ty are tile coordinates and px/py are pixel offsets within the tile.

## Geometry & Coordinates

### Tile
A 1000×1000 pixel grid cell in the WPlace canvas. The canvas consists of a 2048×2048 lattice of tiles. Tiles are identified by (x, y) coordinates in tile space.

### Point
A single pixel location in the canvas, represented by (x, y) coordinates in pixel space. Can be converted to/from the 4-coordinate format (tx, ty, px, py) used in project filenames.

### Rectangle
An axis-aligned rectangular region in pixel space, defined by left, top, right, and bottom coordinates. Uses PIL-style coordinates.

### Size
Width and height dimensions in pixel space.

### Tile Space
The coordinate system using tile coordinates (tx, ty) where each unit represents one 1000×1000 pixel tile.

### Pixel Space
The coordinate system using individual pixel coordinates (x, y) within the full canvas.

### 4-Coordinate Format
Representation used in project filenames: (tx, ty, px, py) where:
- tx: tile x coordinate (0-2047)
- ty: tile y coordinate (0-2047)
- px: pixel x offset within tile (0-999)
- py: pixel y offset within tile (0-999)

## Queue System

### Temperature-Based Queues
Multiple queues organized by tile modification time, from "hot" (recently modified) to "cold" (long ago modified). Hot tiles are checked more frequently than cold tiles.

### Burning Queue
Special queue containing tiles that have never been checked. These tiles have highest priority and are checked first before graduating to temperature queues.

### Zipf Distribution
Statistical distribution used to size the temperature queues. Queue sizes follow the harmonic series (1/1, 1/2, 1/3, ...), with the coldest queue having the most tiles and the hottest queue having the fewest.

### Round-Robin Selection
Queue selection strategy where the system cycles through all queues in order, checking one tile from each queue before returning to the first queue.

### Queue Graduation
The process of a tile moving from the burning queue to a temperature queue after its first check.

## Polling & Timing

### Polling Cycle
The main application loop that runs every ~97 seconds (60φ). Each cycle checks one tile for changes and scans for project file changes.

### 60φ (Golden Ratio Period)
The polling cycle period of 30(1 + √5) ≈ 97.08 seconds, chosen to be maximally dissonant with WPlace's pixel earning speeds (30s standard, 27s for accounts owning flags) to reduce the chance of being automatically flagged as a paint bot. Paint bots would resonate with these timing patterns.

## Palette & Colors

### Palette
The official WPlace color palette consisting of 63 colors, plus a special transparent color. All project images must use exactly these colors.

### Paletted Image
A PNG image using indexed color mode with the WPlace palette, where each pixel stores a palette index (0-63) rather than RGB values.

### Transparent Color
The first color in the palette (index 0, magenta #FF00FF) is not part of the official WPlace palette. We use it to indicate transparency.

### Palette Enforcement
The process of converting an image to use the WPlace palette. Images with colors not in the palette will fail to load. Flexible color conversion is out of scope for this project because this work should be done interactively and there are excellent browser based tools such as [YAWCC](https://yawcc.z1x.us).

### Color Not In Palette
Error raised when an image contains a color that is not part of the WPlace palette.

## Tile Operations

### Tile Ingestion
The process of downloading tiles from the WPlace backend, converting them to paletted PNGs, and caching them locally.

### Tile Change Detection
Using HTTP conditional requests (If-Modified-Since) to detect when a tile has been modified on the server.

### Tile Cache
Local storage of downloaded tiles as paletted PNG files in the user cache directory. Files are named `tile-{x}_{y}.png`.

### Tile Stitching
Assembling multiple cached tiles together to create a larger image covering a project's bounding rectangle. This process also includes cropping the excess pixels to match project size exactly.

### Has Tile Changed
Function that requests a tile from the WPlace backend and updates the local cache if changes are detected. Returns a tuple of (changed, last_modified_time).

### TileChecker
Class that orchestrates tile monitoring: selects tiles via QueueSystem, calls has_tile_changed(), and triggers project diffs when changes are detected.

## Project Lifecycle

### Project Discovery
Scanning the `wplace/` pictures directory for PNG files with valid coordinate information in their filenames.

### Snapshot
A PNG image saved alongside a project that captures the previous canvas state. Used to detect progress/regress by comparing the current state against the previous state.

### Metadata
Project statistics and history stored in YAML files alongside project images. Tracks completion, progress/regress totals, rates, and tile update times.

### Completion Percentage
The percentage of target pixels that are correctly placed on the canvas.

### Remaining Pixels
The number of target pixels that still need to be placed (or are incorrect).

### Max Completion
The best completion state ever achieved for a project (lowest remaining pixel count).

### Diff Status
The current state of a project: NOT_STARTED (no pixels placed), IN_PROGRESS (partially complete), or COMPLETE (fully done).

## Statistics

### Rate Tracking
Calculation of pixels per hour based on recent activity within a measurement window (typically 24 hours).

### Recent Rate Window
The time period used for calculating pixel placement rate, starting from the first change in the current measurement period.

### Tile Update History
24-hour rolling log of which tiles were updated and when, used for monitoring activity patterns.

### Largest Regress Event
The worst griefing incident recorded for a project (most pixels lost in a single check).

## File Structure & Paths

### Configuration (CONFIG)
Configurable directory structure managed by `config.py`. Default pixel-hawk-home is `./pixel-hawk-data` (configurable via `--pixel-hawk-home` or `PIXEL_HAWK_HOME` env var).

### Projects Directory
`<pixel-hawk-home>/projects/` — where users place their project PNG files.

### Tiles Cache
`<pixel-hawk-home>/tiles/` — where cached tiles are stored.

### User Log Path
`<user_log_path>/pixel-hawk.log` — where application logs are written.

### Metadata File
YAML file saved alongside each project as `<project_name>.yaml` containing all metadata and statistics.

### Snapshot File
PNG file saved in `snapshots/{person_id}/{tx}_{ty}_{px}_{py}.png` containing the previous canvas state. Uses the same directory structure and filename format as projects.

## Technical Terms

### Harmonic Series
Mathematical series 1 + 1/2 + 1/3 + 1/4 + ... used to calculate Zipf distribution queue sizes.

### Consecutive Errors
Counter tracking how many polling cycles have failed in a row. Application exits after 3 consecutive errors.

### Missing Tiles
Tiles required by a project that haven't been fetched from the server yet. Projects with missing tiles may show inaccurate completion percentages until all tiles are cached.

### HTTP 304 (Not Modified)
Server response indicating a tile hasn't changed, allowing the client to use its cached version.

### Last-Modified Header
HTTP header indicating when a tile was last modified on the server. Used for cache validation and queue positioning.

### Least-Recently-Checked Tile
Within a queue, the tile that hasn't been checked in the longest time. Used to ensure fair checking coverage.

### Cascade Preservation
When a tile moves to a hotter queue, coldest tiles from intervening queues move down to maintain Zipf distribution sizes.

## Python Conventions

### NamedTuple
Immutable Python type used for geometric primitives (Tile, Point, Size, Rectangle) that provid This is why hot tiles get checked more frequently than cold tiles.

### Missing Tiles
Tiles required by a project that haven't been fetched from the server yet. Projects with missing tiles may show inaccurate completion percentages until all tiles are cached & Formats

### YAML
YAML Ain't Markup Language — human-readable data serialization format used for metadata files saved alongside projects.

### PNG
Portable Network Graphics — lossless image format used for all cached tiles, projects, and snapshots

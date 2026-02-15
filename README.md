# Pixel Hawk

A multi-user change tracker for WPlace paint projects. Monitors tile changes and tracks progress and attacks on artwork for multiple users simultaneously.

## What it does

pixel-hawk polls WPlace tile images, stitches cached tiles, and diffs them against project image files stored in a SQLite database. It runs a unified ~97 second polling loop (60φ = 30(1+√5), chosen to avoid resonance with WPlace's internal timers) that:

- Uses intelligent temperature-based queue system with Zipf distribution to prioritize hot tiles
- Checks one tile per cycle in round-robin fashion across burning and temperature queues
- Downloads and caches WPlace tiles when they change
- Loads projects from SQLite database at startup (database-first architecture)
- Diffs updated tiles against project images
- Tracks watched tiles per person with overlap deduplication
- Logs pixel placement progress with owner attribution

### Multi-user architecture

pixel-hawk supports multiple users tracking the same or different coordinates:
- **Person table** tracks users with auto-increment IDs
- **ProjectInfo table** stores project metadata with owner foreign key
- **Unique constraint** on (owner_id, name) prevents duplicate names per user
- **Watched tiles tracking** counts unique tiles across all active projects per person
- **State management** (active/passive/inactive) for future quota enforcement

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

## First-time setup

On first run, pixel-hawk will initialize the SQLite database with Tortoise ORM migrations:

```powershell
# Initialize database (first time only)
uv run aerich init-db

# Insert your first person (Kiva is the default)
# This is done automatically if you follow the setup below
```

## Usage

Run the watcher:

```powershell
uv run pixel-hawk
```

By default, pixel-hawk uses `./pixel-hawk-data` in your current working directory. You can customize this:

```powershell
# Use custom directory
uv run pixel-hawk --pixel-hawk-home /path/to/pixel-hawk-data

# Or set environment variable
$env:PIXEL_HAWK_HOME = "C:\path\to\pixel-hawk-data"
uv run pixel-hawk
```

**Precedence:** CLI flag `--pixel-hawk-home` > environment variable `PIXEL_HAWK_HOME` > default `./pixel-hawk-data`

### Setting up projects (Database-first workflow)

**IMPORTANT:** pixel-hawk no longer auto-discovers projects from the filesystem. Projects must be created in the database first.

#### Quick setup with helper script

```powershell
uv run python scripts/add_project.py
```

The helper script will guide you through creating a Person (if needed) and a ProjectInfo record, then tell you where to place your PNG file.

#### Manual setup

##### Step 1: Create a person (if not exists)

```python
# In Python REPL or script with pixel-hawk context
from pixel_hawk.models import Person

# Create a new person
person = await Person.create(name="YourName")
# person.id is auto-assigned (e.g., 1, 2, 3...)
```

#### Step 2: Create project in database

```python
from pixel_hawk.geometry import Point, Rectangle, Size
from pixel_hawk.models import ProjectInfo, ProjectState

# Define project bounds
rect = Rectangle.from_point_size(
    Point(x=0, y=0),        # Top-left corner in canvas coordinates
    Size(width=100, height=100)
)

# Create ProjectInfo record
info = await ProjectInfo.from_rect(
    rect=rect,
    owner_id=person.id,     # Use person.id from Step 1
    name="MyArtwork",       # Human-readable name (stored in DB only)
    state=ProjectState.ACTIVE  # Optional: active (default), passive, or inactive
)

# Check the generated filename
print(f"Create file at: projects/{person.id}/{info.filename}")
```

#### Step 3: Create and place the PNG file

1. Create your project image using the WPlace palette (first color is treated as transparent)
2. Save it at: `<pixel-hawk-home>/projects/{person_id}/{tx}_{ty}_{px}_{py}.png`
   - Filename is **coordinates only** (no project name prefix)
   - Example: `projects/1/0_0_500_500.png` for person_id=1
3. The watcher will load it from the database on next startup

### Project states

- **ACTIVE**: Monitored for tile changes (default)
- **PASSIVE**: Loaded but not actively monitored
- **INACTIVE**: Not loaded (for pausing projects without deleting)

### Where data lives

All pixel-hawk data lives in a unified directory structure under `pixel-hawk-home` (default: `./pixel-hawk-data`):

- **`projects/{person_id}/`** — Project PNG files organized by person ID
  - Example: `projects/1/0_0_500_500.png` for person_id=1
  - Filenames are coordinates only: `{tx}_{ty}_{px}_{py}.png`
- **`data/pixel-hawk.db`** — SQLite database (Person, ProjectInfo, HistoryChange tables)
- **`tiles/`** — Cached tiles from WPlace backend
- **`snapshots/{person_id}/`** — Canvas state snapshots organized by person (same structure as projects)
- **`metadata/`** — Legacy YAML files (migrated to SQLite on first load)
- **`logs/`** — Application logs (`pixel-hawk.log` with 10 MB rotation and 7-day retention)

**Development workflow:** The default `./pixel-hawk-data` location is designed to work seamlessly when running pixel-hawk from the project root directory during development. This keeps all data files easily accessible for inspection from your IDE and AI agents, making debugging and analysis straightforward.

**Production deployment:** For production use, set `--pixel-hawk-home` explicitly.

## Database schema

### Person table
- `id`: Auto-increment primary key
- `name`: Unique user name
- `watched_tiles_count`: Cached count of unique tiles watched (updated on startup)

### ProjectInfo table
- `id`: Auto-increment primary key
- `owner_id`: Foreign key to Person
- `name`: Human-readable project name
- `state`: active/passive/inactive
- `x, y, width, height`: Bounding rectangle
- `filename`: Property that returns `{tx}_{ty}_{px}_{py}.png`
- Unique constraint on `(owner_id, name)`
- Progress/regress tracking, completion stats, rate calculations

### HistoryChange table
- Tracks every diff event per project
- Records pixel counts, completion percentage, progress/regress deltas

## Migration from YAML metadata

Legacy YAML metadata files (`.metadata.yaml`) are automatically migrated to SQLite on first load. The YAML file is renamed to `.yaml.migrated` after successful migration.

## Development

The project uses `ruff` for linting (line-length = 120), `ty` for type checking, and `pytest` for testing with 95% coverage threshold.

Run tests:
```powershell
uv run pytest
```

Run type checking:
```powershell
uv run ty check
```

Run linting:
```powershell
uv run ruff check
```

## See Also

* [Blue Marble](https://bluemarble.lol/)
* [WPlace Art Exporter](https://gist.github.com/Kottonye/0e460154cb9b3132c940fa2b4be52faf)
* [Skirk Marble](https://github.com/Seris0/Wplace-SkirkMarble)
* [TWY's Blue Marble](https://github.com/t-wy/Wplace-BlueMarble-Userscripts)

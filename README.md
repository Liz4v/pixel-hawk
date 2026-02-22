# Pixel Hawk

A multi-user change tracker for WPlace paint projects. Monitors tile changes and tracks progress and attacks on artwork.

This is still being built and it's not yet been optimized for being easy to adopt and use. I'm still the only user (that I know of). The rest of this document is LLM-generated, but I'm keeping it around because confusing (but correct) documentation is better than no documentation.

## What it does

pixel-hawk polls WPlace tile images, stitches cached tiles, and diffs them against project image files stored in a SQLite database. It runs a unified ~97 second polling loop (60φ = 30(1+√5), chosen to avoid resonance with WPlace's internal timers) that:

- Uses intelligent temperature-based queue system with Zipf distribution to prioritize hot tiles
- Checks one tile per cycle in round-robin fashion across burning and temperature queues
- Downloads and caches WPlace tiles when they change
- Discovers affected projects on demand via database queries (query-driven architecture)
- Diffs updated tiles against project images
- Tracks watched tiles per person with overlap deduplication
- Logs pixel placement progress with owner attribution

### Multi-user architecture

pixel-hawk supports multiple users tracking the same or different coordinates:
- **Person table** tracks users with auto-increment IDs
- **ProjectInfo table** stores project metadata with owner foreign key
- **Unique constraint** on (owner_id, name) prevents duplicate names per user
- **Watched tiles tracking** counts unique tiles and active projects per person via `update_totals()`
- **State management** (ACTIVE/PASSIVE/INACTIVE IntEnum) for quota enforcement

### Queue system

The watcher maintains multiple temperature-based queues:
- **Burning queue:** Tiles that have never been checked (highest priority)
- **Temperature queues:** Organized by tile modification time (hot to cold)
  - Queue sizes follow Zipf distribution (harmonic series)
  - Recently modified tiles get checked more frequently
  - Tiles graduate from burning → temperature queues after first check
  - Periodic redistribution reassigns heat values based on last_update recency (optimistic writes)

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
uv run hawk
```

By default, pixel-hawk uses `./nest` in your current working directory. You can customize this:

```powershell
# Use custom home directory
uv run hawk --nest /path/to/nest

# Or set environment variable
$env:HAWK_NEST = "C:\path\to\nest"
uv run hawk
```

**Precedence:** CLI flag `--nest` > environment variable `HAWK_NEST` > default `./nest`

### Project states

- **ACTIVE**: Tiles are queued for monitoring; diffs run when tiles change (default)
- **PASSIVE**: Not queued, but diffs run when overlapping tiles are checked for other projects
- **INACTIVE**: Completely excluded from monitoring

### Where data lives

All pixel-hawk data lives in a unified directory structure under `nest` (default: `./nest`):

- **`projects/{person_id}/`** — Project PNG files organized by person ID
  - Example: `projects/1/0_0_500_500.png` for person_id=1
  - Filenames are coordinates only: `{tx}_{ty}_{px}_{py}.png`
- **`data/pixel-hawk.db`** — SQLite database (Person, ProjectInfo, HistoryChange, TileInfo, TileProject tables)
- **`tiles/`** — Cached tiles from WPlace backend
- **`snapshots/{person_id}/`** — Canvas state snapshots organized by person (same structure as projects)
- **`logs/`** — Application logs (`pixel-hawk.log` with 10 MB rotation and 7-day retention)

**Development workflow:** The default `./nest` location is designed to work seamlessly when running pixel-hawk from the project root directory during development. This keeps all data files easily accessible for inspection from your IDE and AI agents, making debugging and analysis straightforward.

**Production deployment:** For production use, set `--nest` explicitly.

### Discord bot

An optional Discord bot runs alongside the polling loop, providing slash commands under the `/hawk` group. Configure by adding a `config.toml` at the nest root:

```toml
[discord]
bot_token = "your-bot-token"
# command_prefix = "hawk"
```

If no token is configured, the bot is silently skipped. The `command_prefix` setting changes the slash command group name (default: `hawk`).

**Commands:**
- `/hawk sa myself <token>` — Grant admin access using a one-time UUID (printed to console and saved to `nest/data/admin-me.txt` on each startup)
- `/hawk list` — List all your projects with state, completion stats, 24h progress/regress, and WPlace links (ephemeral, visible only to you)

## Database schema

### Person table (`person`)
- `id`: Auto-increment primary key
- `name`: User name
- `discord_id`: Optional Discord user ID (unique)
- `access`: Bitmask for bot-level access control (`BotAccess` IntFlag)
- `watched_tiles_count`: Cached count of unique tiles watched
- `active_projects_count`: Cached count of active projects
- Both counts updated via `update_totals()` on startup

### ProjectInfo table (`project`)
- `id`: Randomly assigned primary key (1 to 9999). Assigned by `save_as_new()` with collision retry.
- `owner_id`: Foreign key to Person
- `name`: Human-readable project name
- `state`: ACTIVE (0) / PASSIVE (10) / INACTIVE (20) IntEnum
- `x, y, width, height`: Bounding rectangle
- `filename`: Property that returns `{tx}_{ty}_{px}_{py}.png`
- Unique constraint on `(owner_id, name)`
- Progress/regress tracking, completion stats, rate calculations

### HistoryChange table (`history_change`)
- Tracks every diff event per project
- `status`: DiffStatus IntEnum — NOT_STARTED (0) / IN_PROGRESS (10) / COMPLETE (20)
- Records pixel counts, completion percentage, progress/regress deltas

### TileInfo table (`tile`)
- `id`: Encoded from coordinates as `x * 10000 + y` (manually set, not auto-increment)
- `x, y`: Tile coordinates
- `heat`: Queue assignment (999 = burning, 1-998 = temperature index, 0 = not queued)
- `last_checked`: When tile was last fetched (epoch seconds)
- `last_update`: Parsed from Last-Modified header (epoch seconds)
- `etag`: Raw ETag header for conditional requests
- Composite index on `(heat, last_checked)` for LRU selection

### TileProject table (`tile_project`)
- Junction table for many-to-many tile-project relationships
- `tile_id`: Foreign key to TileInfo
- `project_id`: Foreign key to ProjectInfo
- Unique constraint on `(tile_id, project_id)`

## Rebuilding the database

If the SQLite database is lost or corrupted, you can rebuild it from filesystem artifacts:

```powershell
uv run python scripts/rebuild.py
```

This reconstructs Person, ProjectInfo, TileInfo, and TileProject records by scanning the `projects/` and `tiles/` directories. The script is idempotent — safe to re-run on an existing database. Person and project names will use placeholders; historical data (HistoryChange records, rate tracking) is permanently lost.

## Deployment (Linux/systemd)

For production deployment on a Linux server with systemd:

```bash
git clone https://github.com/Liz4v/pixel-hawk.git ~/pixel-hawk
cd ~/pixel-hawk
bash scripts/install-service.sh
```

The install script detects the current user, repo location, and `uv` path, then generates and installs a systemd service unit. It is idempotent — safe to re-run after updates.

Pushes to `main` are automatically deployed via a self-hosted GitHub Actions runner (see `.github/workflows/deploy.yml`).

After installation, configure the Discord bot by copying `config.example.toml`:

```bash
cp config.example.toml nest/config.toml
# Edit nest/config.toml with your bot token
sudo systemctl restart pixel-hawk
```

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

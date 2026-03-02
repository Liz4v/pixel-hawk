# Pixel Hawk

A multi-user change tracker for WPlace paint projects. Monitors tile changes and tracks progress and attacks on artwork.

This is still being built and it's not yet been optimized for being easy to adopt and use. I'm still the only user (that I know of). The rest of this document is LLM-generated, but I'm keeping it around because confusing (but correct) documentation is better than no documentation.

## What it does

pixel-hawk polls WPlace tile images, stitches cached tiles, and diffs them against project image files stored in a SQLite database. It runs a unified ~97 second polling loop (60φ = 30(1+√5), chosen to avoid resonance with WPlace's internal timers) that:

- Uses intelligent temperature-based queue system with Zipf distribution to prioritize hot tiles
- Checks one tile per cycle in round-robin fashion across burning and temperature queues
- Downloads and caches WPlace tiles when they change
- Discovers affected projects on demand via database queries (query-driven architecture)
- Runs an immediate initial diff when creating or editing a project if tiles are already cached (reports partial coverage when some tiles are missing)
- Diffs updated tiles against project images
- Tracks watched tiles per person with overlap deduplication
- Logs pixel placement progress with owner attribution
- Updates persistent Discord "watch" messages with live project stats after each diff

### Multi-user architecture

pixel-hawk supports multiple users tracking the same or different coordinates:
- **Person table** tracks users with auto-increment IDs
- **ProjectInfo table** stores project metadata with owner foreign key
- **Unique constraint** on (owner_id, name) prevents duplicate names per user
- **Watched tiles tracking** counts unique tiles and active projects per person via `update_totals()`
- **State management** (ACTIVE/PASSIVE/INACTIVE/CREATING IntEnum) for quota enforcement

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

Every project has a state that controls how it interacts with the tile polling system:

- **ACTIVE** (default): The project's tiles are linked and queued for polling. When a tile changes, pixel-hawk diffs it against the project image and logs progress. This is the normal operating state. User tile quotas only count tiles with ACTIVE projects.
- **PASSIVE**: Tiles are linked but not queued on their own. If another ACTIVE project (usually from a different user) shares the same tiles, the passive project piggybacks on those polls and gets diffed too. Useful for low-priority tracking without adding polling overhead.
- **INACTIVE**: Tiles are unlinked entirely. The project is stored in the database but completely excluded from monitoring. No polling, no diffing, no bandwidth cost. Reactivating re-links tiles.
- **CREATING**: A newly uploaded image that hasn't been assigned coordinates yet. No tiles are linked. Setting coordinates auto-transitions the project to ACTIVE.

### Where data lives

All pixel-hawk data lives in a unified directory structure under `nest` (default: `./nest`):

- **`projects/{person_id}/`** — Project PNG files organized by person ID
  - Example: `projects/1/0_0_500_500.png` for person_id=1
  - Filenames are coordinates only: `{tx}_{ty}_{px}_{py}.png`
- **`data/pixel-hawk.db`** — SQLite database (Person, ProjectInfo, HistoryChange, TileInfo, TileProject, GuildConfig, WatchMessage tables)
- **`tiles/`** — Cached tiles from WPlace backend
- **`snapshots/{person_id}/`** — Canvas state snapshots organized by person (same structure as projects)
- **`rejected/`** — Project files that failed to import (invalid palette, etc.)
- **`logs/`** — Application logs (`pixel-hawk.log` with 10 MB rotation and 7-day retention)

**Development workflow:** The default `./nest` location is designed to work seamlessly when running pixel-hawk from the project root directory during development. This keeps all data files easily accessible for inspection from your IDE and AI agents, making debugging and analysis straightforward.

**Production deployment:** For production use, set `HAWK_NEST` in `/etc/pixel-hawk.env` (created by `scripts/install-service.sh`).

### Discord bot

An optional Discord bot runs alongside the polling loop, providing slash commands under the `/hawk` group. Configure via environment variables:

| Variable | Default | Description |
|---|---|---|
| `HAWK_BOT_TOKEN` | *(empty)* | Discord bot token. If empty, the bot is silently skipped. |
| `HAWK_COMMAND_PREFIX` | `hawk` | Slash command group name (e.g. `/hawk new`, `/hawkadmin role`). |

Copy `.env.example` to `.env` and fill in values — `python-dotenv` auto-loads it on startup. For production (systemd), the service loads from `/etc/pixel-hawk.env` (see `scripts/install-service.sh`).

**Guild setup:**
1. Run `/hawkadmin admin @yourself` to make yourself the first admin (only works on a fresh database with no existing users)
2. Run `/hawkadmin role <role>` to set the required Discord role for the server — users with this role can use the regular features of the bot and are auto-enrolled on first command (inheriting guild quota ceilings)

Commands are blocked until a role is configured. Hawk Admins always bypass the role check but are still checked for their quota.

**DM support:** All user commands also work in Discord DMs. Access in DMs requires the user to have previously used a command in a configured guild (which auto-enrolls them). Admins can always use DM commands. Admin commands remain guild-only.

**User commands** (under `/hawk` group):
- `/hawk list` — List all your projects with state, completion stats, 24h progress/regress, and WPlace links (ephemeral, visible only to you)
- `/hawk new` — Upload a new project image
- `/hawk edit` — Edit an existing project (name, coordinates, state, image)
- `/hawk delete` — Permanently delete a project
- `/hawk watch <project_id>` — Post a live-updating status message for a project. The message auto-updates with current stats (completion %, pixel counts, rate, ETA, 24h activity, lifetime totals) every time the watcher detects changes. One watch per project per channel.
- `/hawk unwatch <project_id>` — Stop watching a project in this channel and delete the watch message

**Admin commands** (under `/hawkadmin` group, guild-only, requires **both** bot admin and guild admin permission):
- `/hawkadmin admin <user>` — Grant bot admin access to a user (targeting yourself bootstraps the first admin on a fresh database)
- `/hawkadmin role <name>` — Set the required Discord role for this server
- `/hawkadmin quota <user> [projects] [tiles]` — View or set per-user quota limits (enforces guild ceilings)
- `/hawkadmin guildquota [projects] [tiles]` — View or set guild-level quota ceilings

## Database schema

### Person table (`person`)
- `id`: Auto-increment primary key
- `name`: User name
- `discord_id`: Optional Discord user ID (unique)
- `access`: Bitmask for bot-level access control (`BotAccess` IntFlag)
- `max_active_projects`: Per-user quota limit (default 50)
- `max_watched_tiles`: Per-user quota limit (default 10)
- `watched_tiles_count`: Cached count of unique tiles watched
- `active_projects_count`: Cached count of active projects
- Both counts updated via `update_totals()` on startup

### ProjectInfo table (`project`)
- `id`: Randomly assigned primary key (1 to 9999). Assigned by `save_as_new()` with collision retry.
- `owner_id`: Foreign key to Person
- `name`: Human-readable project name
- `state`: ACTIVE (0) / PASSIVE (10) / INACTIVE (20) / CREATING (30) IntEnum
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

### GuildConfig table (`guild_config`)
- `guild_id`: Discord guild snowflake (primary key, not auto-generated)
- `required_role`: Discord role ID (snowflake stored as string) of the role required to use bot commands in this guild
- `max_active_projects`: Guild-level quota ceiling (default 50)
- `max_watched_tiles`: Guild-level quota ceiling (default 10)

### WatchMessage table (`watch_message`)
- `message_id`: Discord message snowflake (primary key, not auto-generated)
- `project_id`: Foreign key to ProjectInfo (CASCADE delete)
- `channel_id`: Discord channel snowflake
- Unique constraint on `(project_id, channel_id)` — one watch per project per channel

## Deployment (Linux/systemd)

For production deployment on a Linux server with systemd:

```bash
git clone https://github.com/Liz4v/pixel-hawk.git ~/pixel-hawk
cd ~/pixel-hawk
bash scripts/install-service.sh
```

The install script detects the current user, repo location, and `uv` path, then generates and installs a systemd service unit. It is idempotent — safe to re-run after updates.

Pushes to `main` are automatically deployed via a GitHub-hosted runner (see `.github/workflows/deploy.yaml`).

After installation, configure the Discord bot by setting environment variables in `/etc/pixel-hawk.env`:

```bash
sudo nano /etc/pixel-hawk.env
# Set HAWK_BOT_TOKEN=your-bot-token
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

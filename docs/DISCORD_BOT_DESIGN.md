# Discord Bot Design Document

## Overview

A Discord bot that integrates with the cam (Canvas Activity Monitor) to provide real-time project monitoring and status updates through Discord messages. The bot enables project management via Discord commands and maintains living status messages that update as project progress changes.

## Goals

- **Project Management**: Allow trusted users to add, remove, and configure projects through Discord messages
- **Real-time Updates**: Track project progress and automatically edit Discord messages with current status
- **Trusted Environment**: Bot operates only in explicitly trusted servers with appropriate permission controls
- **Seamless Integration**: Leverage existing cam infrastructure for tile monitoring and diff computation

## Architecture

### High-Level Components

```
┌─────────────────────────────────────────────────────────────┐
│                        Discord Bot                           │
│  ┌────────────────┐  ┌──────────────┐  ┌─────────────────┐ │
│  │ Command Handler│  │Status Manager│  │ Project Registry│ │
│  └────────────────┘  └──────────────┘  └─────────────────┘ │
└───────────────────────────┬─────────────────────────────────┘
                            │
                    ┌───────▼────────┐
                    │ Integration    │
                    │ Layer          │
                    └───────┬────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                     cam Core                                 │
│  ┌────────────┐  ┌──────────┐  ┌────────────┐             │
│  │QueueSystem │  │ Projects │  │  Ingest    │             │
│  └────────────┘  └──────────┘  └────────────┘             │
└─────────────────────────────────────────────────────────────┘
```

### Integration Approach

**Option A: Parallel Process**
- Discord bot runs as separate process alongside cam
- Shares data through filesystem (project definitions, status files)
- cam writes progress data, bot reads and updates Discord messages
- Simpler separation of concerns, easier to develop/debug

**Option B: Embedded Integration**
- Discord bot runs within cam's main loop
- Direct access to Project objects and diff results
- No filesystem intermediary needed
- Tighter coupling but more efficient

**Recommended**: Start with Option A for faster iteration, consider Option B later if needed.

## Project Management

### Project Registration

Projects are registered through Discord messages with a command syntax:

```
/cam add <name> <tx> <ty> <px> <py> <image_url>
```

- **name**: Human-readable project identifier
- **tx, ty**: Tile coordinates
- **px, py**: Pixel offset within tile (0-999)
- **image_url**: URL to project PNG (must use project palette)

The bot downloads the image, validates it (palette check), and creates the project file in cam's expected location.

### Project Deregistration

```
/cam remove <name>
```

Removes project file and cleans up associated status messages.

### Project Listing

```
/cam list
```

Shows all registered projects with basic status information.

## Status Message Management

### Message Lifecycle

1. **Creation**: When a project is added, bot creates a status message and stores the message ID
2. **Updates**: As cam detects changes, bot edits the message with new progress data
3. **Cleanup**: When project is removed, bot can either delete or archive the status message

### Status Message Format

```
📊 Project: [Name]
📍 Location: tile(tx, ty) + pixel(px, py)
✅ Progress: 1234/5000 pixels (24.7%)
🔄 Last Update: 2 minutes ago
🎯 Last Change: +15 pixels | 3 minutes ago
```

Progress indicators:
- Green percentage bar for > 90% complete
- Yellow for 50-90%
- Red for < 50%
- Animated emoji when actively changing
- Time-relative updates ("2 minutes ago")

### Update Strategy

**Throttling**: Don't spam Discord API
- Batch updates if multiple projects change simultaneously
- Minimum 30 seconds between updates for the same project
- Rate limit: respect Discord's 5 requests/second limit

**Event-driven**: Update on significant changes
- New pixels matched (progress increase)
- Pixels lost (regression/griefing)
- Project reaches milestones (50%, 75%, 90%, 100%)

## Data Model

### Project Registry (bot-side)

```json
{
  "projects": {
    "project_name": {
      "file_path": "/path/to/wplace/project_name_tx_ty_px_py.png",
      "status_message_id": "1234567890",
      "channel_id": "9876543210",
      "added_by": "user_id",
      "added_at": "2026-02-07T12:00:00Z",
      "last_update": "2026-02-07T12:05:00Z",
      "last_progress": 0.247
    }
  }
}
```

Stored as JSON in bot's data directory.

### Progress Data (cam-side)

cam could write simple status files alongside each project:

```
project_name_tx_ty_px_py.status.json
```

Contains:
```json
{
  "last_checked": "2026-02-07T12:05:30Z",
  "progress": {
    "matched": 1234,
    "total": 5000,
    "percentage": 0.247
  },
  "last_change": {
    "timestamp": "2026-02-07T12:03:15Z",
    "delta": 15
  }
}
```

## Security & Permissions

### Server Whitelist

Bot maintains a whitelist of trusted server IDs. Rejects all commands from non-whitelisted servers.

```python
TRUSTED_SERVERS = [
    123456789,  # Server ID 1
    987654321,  # Server ID 2
]
```

### Permission Model

- **Add/Remove Projects**: Requires specific Discord role (configurable)
- **View Status**: Available to all users in trusted servers
- **Bot Admin Commands**: Server administrators only

### Data Privacy

- Project images stored locally only
- No cloud storage of project data
- Bot does not relay content from WPlace (only progress metrics)

## Technology Stack

### Core Dependencies

- **discord.py**: Discord bot framework
- **aiohttp**: Async HTTP for image downloads
- **Pillow**: Image validation (reuse from cam)
- **ruamel.yaml**: Configuration management (reuse from cam)

### Bot Configuration

Environment variables or config file:
```
DISCORD_BOT_TOKEN=...
DISCORD_TRUSTED_SERVERS=123456789,987654321
DISCORD_REQUIRED_ROLE=ProjectManager
CAM_WPLACE_DIR=/path/to/wplace
```

## Implementation Phases

See `DISCORD_BOT_TASKS.md` for detailed task breakdown.

### Phase 1: Bot Foundation
- Basic Discord bot setup
- Command handling framework
- Server whitelist enforcement

### Phase 2: Project Management
- Add/remove/list commands
- Image download and validation
- File management in wplace directory

### Phase 3: Status Integration
- Read cam progress data
- Create and update status messages
- Rate limiting and throttling

### Phase 4: Polish & Features
- Rich embeds for status messages
- Error handling and user feedback
- Monitoring and logging

## Future Enhancements

- **Regression Alerts**: Notify when projects are griefed (tie into cam's griefing detection task)
- **Multi-Project Dashboards**: Single message showing all project statuses
- **Historical Charts**: Progress over time visualizations
- **Project Templates**: Pre-configured popular projects
- **Collaborative Management**: Multiple users can manage the same project
- **Webhooks**: Allow external services to query project status

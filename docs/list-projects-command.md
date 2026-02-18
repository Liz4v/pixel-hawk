# Add `/hawk list` Discord command

## Context

The Discord bot currently only has `/hawk sa`. We need a `/hawk list` command that lets a linked user list all their projects with name, short stats, and WPlace link.

## Design

### New command: `/hawk projects`

- Registered as a subcommand on the existing `hawk_group` in `HawkBot._register_commands()`
- No arguments — looks up the calling user's Person record via `discord_id`
- Shows **all** projects regardless of state (ACTIVE/PASSIVE/INACTIVE), labeled by state
- Ephemeral response (only visible to the calling user)

### Core logic function: `list_projects(discord_id) -> str | None`

Separated from command handler for testability (same pattern as `grant_admin()`).

- Query `Person` by `discord_id`, return `None` if not found
- Query all `ProjectInfo` for that person, ordered by state then name
- Format each project as a line: `name — state | completion% | last_log_message summary | link`
- Return formatted string, or a "no projects" message if empty

### Response format

Plain text (consistent with existing commands). Each project on its own line:

```
Your projects:

**project_name** [ACTIVE]
  52.3% complete · 1,247px remaining · https://wplace.live/?lat=...&lng=...&zoom=...

**other_project** [PASSIVE]
  Not yet checked
```

Uses data already on `ProjectInfo`: `name`, `state`, `max_completion_percent`, `last_log_message`, and `rectangle.to_link()`. For projects never checked (`last_check == 0`), show "Not yet checked".

### Files to modify

1. **`src/pixel_hawk/interactions.py`** (~25 lines added)
   - Add import: `ProjectInfo, ProjectState`
   - Add `list_projects(discord_id: int) -> str | None` function
   - Add `_projects` command handler method on `HawkBot`
   - Register in `_register_commands()`

2. **`tests/test_interactions.py`** (~60 lines added)
   - `TestListProjects` class:
     - `test_unknown_discord_id_returns_none`
     - `test_no_projects_returns_message`
     - `test_lists_active_project_with_stats`
     - `test_lists_all_states`
     - `test_shows_not_yet_checked_for_unchecked_project`
   - `TestHawkBot` additions:
     - `test_command_tree_has_projects_command` (verify registration)

## Verification

```bash
uv run pytest tests/test_interactions.py -v
uv run ruff check src/pixel_hawk/interactions.py tests/test_interactions.py
uv run ty check
```

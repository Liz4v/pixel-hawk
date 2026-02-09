# Discord Bot Implementation Tasks

## Phase 1: Bot Foundation

### Create Discord bot application and get bot token

**Status:** Not Started
**Priority:** High
**Effort:** 1 hour

**Description:**
Set up the Discord bot application through Discord Developer Portal, configure basic settings, and obtain the bot token for authentication.

**Steps:**
- Create new application at https://discord.com/developers/applications
- Navigate to Bot section and create bot user
- Enable necessary intents (message content, guild members)
- Generate and securely store bot token
- Configure OAuth2 URL with appropriate permissions

**Permissions needed:**
- Send Messages
- Embed Links
- Attach Files
- Read Message History
- Use Slash Commands

---

### Set up project structure for Discord bot

**Status:** Not Started
**Priority:** High
**Effort:** 2 hours

**Description:**
Create the basic project structure for the Discord bot as a new module within the cam package or as a separate entry point.

**Implementation Considerations:**
- Decide on module location: `src/cam/discord_bot/` or separate package
- Create entry point (console script in pyproject.toml: `cam-bot = "cam.discord_bot.main:main"`)
- Set up configuration management (environment variables, config file)
- Add discord.py and aiohttp to dependencies in pyproject.toml
- Create basic bot initialization and startup code

**Files to create:**
- `src/cam/discord_bot/__init__.py`
- `src/cam/discord_bot/main.py`
- `src/cam/discord_bot/config.py`
- `.env.example` (template for configuration)

---

### Implement server whitelist and permission checks

**Status:** Not Started
**Priority:** High
**Effort:** 3 hours

**Description:**
Create middleware to enforce server whitelist and role-based permissions for all bot commands.

**Implementation Considerations:**
- Load trusted server IDs from config
- Create decorator for command permission checks
- Implement role name checking (support multiple approved role names)
- Add helpful error messages when permissions are denied
- Log all permission check failures for security monitoring

**Files to create:**
- `src/cam/discord_bot/permissions.py`

**Tests needed:**
- Test permission decorator with whitelisted/non-whitelisted servers
- Test role checking with various role configurations
- Test error message formatting

---

### Create command handler framework

**Status:** Not Started
**Priority:** High
**Effort:** 3 hours

**Description:**
Set up the command handling system using discord.py's slash commands or prefix commands.

**Implementation Considerations:**
- Choose command style (slash commands recommended for better UX)
- Create base command cog/group for cam commands
- Set up error handling for commands
- Add help/usage documentation
- Implement command logging

**Files to create:**
- `src/cam/discord_bot/commands/__init__.py`
- `src/cam/discord_bot/commands/base.py`

---

## Phase 2: Project Management

### Implement `/cam add` command

**Status:** Not Started
**Priority:** High
**Effort:** 4 hours

**Description:**
Command to register a new project through Discord by downloading an image URL and creating the appropriately named PNG file in the wplace directory.

**Implementation Considerations:**
- Parse command arguments (name, tx, ty, px, py, image_url)
- Validate coordinate ranges (tile coords, pixel coords 0-999)
- Download image from URL using aiohttp
- Validate image palette using cam's `PALETTE.ensure()`
- Generate proper filename: `<name>_<tx>_<ty>_<px>_<py>.png`
- Save to `get_config().projects_dir`
- Create initial status message
- Store project registry entry
- Handle errors gracefully (invalid URL, bad palette, file system errors)

**Files to modify:**
- `src/cam/discord_bot/commands/projects.py` (new file)

**Dependencies:**
- Reuse `PALETTE` from `src/cam/palette.py`
- Reuse `get_config()` from `src/cam/config.py`
- Import `Point`, `Tile` from `src/cam/geometry.py` for validation

**Tests needed:**
- Test with valid project addition
- Test with invalid coordinates
- Test with bad palette image
- Test with unreachable URL
- Test filename generation

---

### Implement secure untrusted project loading system

**Status:** Not Started
**Priority:** High
**Effort:** 6 hours

**Description:**
Create `Project.load_from_untrusted_source()` method to safely validate and sanitize project images from Discord before storing them in the active projects folder. This implements a two-folder security architecture with comprehensive validation.

**Architecture:**
- **Untrusted Input Folder**: Files downloaded from Discord await validation
- **Active Projects Folder**: Validated projects ready for monitoring
- **Rejected Folder**: Failed validation attempts for review

**Security Measures:**

1. **File Size Limits**
   - MAX_FILE_SIZE = 10MB
   - Check before opening with Pillow
   
2. **Image Validation**
   - Verify PNG magic bytes (first 8 bytes)
   - Check dimensions ≤ MAX_IMAGE_DIMENSION (10,000px per side)
   - Set PIL Image.MAX_IMAGE_PIXELS = 100,000,000
   - Verify format == 'PNG'
   - Validate mode compatibility

3. **Coordinate Validation**
   - Tile coordinates: 0 ≤ tx, ty < 2048 (square grid)
   - Pixel coordinates: 0 ≤ px, py < 1000
   - Verify rectangle fits within valid WPlace canvas bounds
   - Confirm dimensions match filename coordinates

4. **Content Sanitization**
   - Convert via PALETTE.ensure() (validates palette)
   - Strip metadata (EXIF, comments, etc.)
   - Re-encode to clean PNG

**Processing Pipeline:**

```python
@classmethod
def load_from_untrusted_source(
    cls,
    untrusted_path: Path,
    *,
    trusted_filename: str | None = None
) -> tuple[Project | ProjectShim | None, str]:
    # 1. Extract and validate coordinates from filename
    # 2. Pre-checks: file size, PNG magic bytes
    # 3. Open with Pillow (catch broad exceptions)
    # 4. Validate dimensions and format
    # 5. Sanitize content (palette conversion, metadata strip)
    # 6. Safe persistence with backup
    # 7. Cleanup untrusted source file
```

**File Operations:**
- On success:
  - If destination exists: rename existing to `.bak` (one version kept)
  - Write sanitized image to temp file in active folder
  - Atomic rename to final filename
  - Delete untrusted source file
  - Return Project instance

- On failure:
  - Log specific failure reason
  - Move to rejected folder (preserve filename or add timestamp)
  - Return error description
  - **TODO**: Implement periodic cleanup of rejected folder

**Exception Handling:**
Catch and handle gracefully:
- OSError (file operations)
- PIL.Image.DecompressionBombError
- PIL.UnidentifiedImageError  
- ColorNotInPalette
- ValueError (dimension/bounds checks)
- AssertionError (invariant violations)

**Constants to Define:**
```python
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_IMAGE_DIMENSION = 10_000
MAX_IMAGE_PIXELS = 100_000_000
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
WPLACE_MAX_TILE_COORD = 2048
WPLACE_PIXELS_PER_TILE = 1000
```

**Implementation Considerations:**
- Return value design: TBD (tuple vs exception vs result object)
- Consider adding dry-run mode for testing
- May need atomic file operations helper
- Should be callable from Discord bot context
- Currently synchronous; consider async wrapper if needed

**Future Enhancements:**
- Rejected folder cleanup policy (age/size based)
- Metrics: acceptance rate, common failure reasons
- Batch processing for multiple uploads
- Progress feedback for large file processing
- Rate limiting per user/server

**Files to modify:**
- `src/cam/projects.py` (add new method and constants)

**Files to create:**
- Helper for folder paths (use `get_config().data_dir`)

**Dependencies:**
- Keep Pillow updated for security patches
- Consider OS-level resource limits for process

**Tests needed:**
- Test with valid untrusted file (successful load)
- Test decompression bomb protection
- Test oversized file rejection
- Test invalid PNG magic bytes
- Test coordinate bounds validation
- Test palette validation failure
- Test dimension mismatch with filename
- Test backup creation on overwrite
- Test atomic file operations
- Test cleanup of untrusted source
- Test rejected folder handling
- Test multiple exception types

**Related Tasks:**
- Must be integrated with `/cam add` command implementation
- Coordinate with project registry persistence
- Consider integration with error reporting system

---

### Implement `/cam remove` command

**Status:** Not Started
**Priority:** High
**Effort:** 2 hours

**Description:**
Command to deregister a project by name, removing the PNG file and cleaning up status messages.

**Implementation Considerations:**
- Look up project by name in registry
- Delete PNG file from wplace directory
- Delete or archive status message
- Remove from project registry
- Handle case where file is already deleted
- Confirm action with user (require confirmation for safety)

**Files to modify:**
- `src/cam/discord_bot/commands/projects.py`

**Tests needed:**
- Test removal of existing project
- Test removal of non-existent project
- Test cleanup of status message

---

### Implement `/cam list` command

**Status:** Not Started
**Priority:** Medium
**Effort:** 2 hours

**Description:**
Command to list all registered projects with basic status information in a clean, formatted message.

**Implementation Considerations:**
- Read project registry
- Format as embed or formatted text message
- Show: name, coordinates, progress (if available), last update time
- Handle empty project list gracefully
- Paginate if many projects (Discord embed limits)

**Files to modify:**
- `src/cam/discord_bot/commands/projects.py`

**Tests needed:**
- Test with no projects
- Test with one project
- Test with many projects

---

### Create project registry persistence

**Status:** Not Started
**Priority:** High
**Effort:** 2 hours

**Description:**
Implement JSON-based storage for project registry data (message IDs, channel IDs, metadata).

**Implementation Considerations:**
- Use `get_config().data_dir` for storage location
- File: `discord_projects.json`
- Atomic writes (write to temp file, then rename)
- Auto-save after registry modifications
- Load on bot startup
- Handle corruption (backup previous version)

**Files to create:**
- `src/cam/discord_bot/registry.py`

**Tests needed:**
- Test save and load
- Test atomic writes
- Test corruption recovery

---

## Phase 3: Status Integration

### Implement progress data reading

**Status:** Not Started
**Priority:** High
**Effort:** 3 hours

**Description:**
Create system to read progress data written by cam, either by directly importing Project class or by reading status JSON files.

**Implementation Considerations:**
- **Option A (direct import)**: Import `Project` class and call `run_diff()` - requires careful threading/async handling
- **Option B (file-based)**: cam writes `.status.json` files, bot reads them - simpler but requires cam changes
- Decide on update frequency (polling interval)
- Handle missing or stale data gracefully
- Cache data to minimize filesystem reads

**Implementation decision needed:**
- If file-based, cam needs to write status files (see cam enhancement task)
- If direct import, need to ensure thread-safety with cam's main loop

**Files to create:**
- `src/cam/discord_bot/progress.py`

---

### Enhance cam to write status files (if using file-based approach)

**Status:** Not Started
**Priority:** High (if file-based approach chosen)
**Effort:** 2 hours

**Description:**
Modify cam's Project class to write JSON status files after each diff computation, containing progress metrics and timestamps.

**Implementation Considerations:**
- Write status file in same directory as project PNG
- Format: `<project_filename>.status.json`
- Include: last checked time, progress (matched/total/percentage), last change time and delta
- Atomic writes
- Only write if progress changed (avoid unnecessary I/O)

**Files to modify:**
- `src/cam/projects.py` (modify `Project.run_diff()`)

**Related Code:**
- `Project.run_diff()` already computes matched/total pixels

---

### Create status message manager

**Status:** Not Started
**Priority:** High
**Effort:** 4 hours

**Description:**
System to create, update, and manage Discord status messages for each project.

**Implementation Considerations:**
- Create rich embeds with project information
- Format progress bars using Unicode block characters
- Calculate time-relative strings ("2 minutes ago")
- Track last update time to prevent spam
- Implement rate limiting (max 1 update per project per 30 seconds)
- Batch updates if multiple projects change simultaneously
- Handle Discord API errors (message deleted, permissions lost)

**Files to create:**
- `src/cam/discord_bot/status_manager.py`

**Features:**
- Progress bar: `[████████░░░░░░░░░░░░] 40%`
- Color-coded embeds (green/yellow/red based on progress)
- Timestamp formatting
- Change indicators (↑ +15 pixels, ↓ -8 pixels)

**Tests needed:**
- Test message formatting
- Test rate limiting
- Test batch updates
- Test error recovery

---

### Implement update loop

**Status:** Not Started
**Priority:** High
**Effort:** 3 hours

**Description:**
Main async loop that periodically checks for progress updates and triggers Discord message updates.

**Implementation Considerations:**
- Run as background task in Discord bot
- Check frequency: every 60 seconds (align with cam's polling cycle)
- For each project: read progress data, compare to last known state, update if changed
- Respect rate limits and throttling
- Log update activity
- Handle exceptions without crashing loop

**Files to modify:**
- `src/cam/discord_bot/main.py`

---

## Phase 4: Polish & Features

### Add rich embed formatting for status messages

**Status:** Not Started
**Priority:** Medium
**Effort:** 2 hours

**Description:**
Enhance status messages with Discord embeds, emoji indicators, and visual polish.

**Features:**
- Thumbnail: small preview of project image
- Color-coded embed borders
- Emoji indicators: 📊📍✅🔄🎯
- Fields for different information sections
- Footer with last update timestamp

---

### Implement error handling and user feedback

**Status:** Not Started
**Priority:** Medium
**Effort:** 3 hours

**Description:**
Comprehensive error handling with helpful user-facing messages for all command failures.

**Implementation Considerations:**
- Catch and format all exceptions
- Provide actionable error messages
- Log errors server-side for debugging
- Add retry logic for transient failures
- Timeout handling for slow operations

---

### Add bot monitoring and health checks

**Status:** Not Started
**Priority:** Low
**Effort:** 2 hours

**Description:**
Monitoring system to track bot health, command usage, and error rates.

**Features:**
- Log all commands with user/server/timestamp
- Track update loop health (last successful update time)
- Expose `/cam status` command for bot health
- Log rate limit hits
- Alert on repeated errors

---

### Create comprehensive test suite

**Status:** Not Started
**Priority:** Medium
**Effort:** 4 hours

**Description:**
Unit and integration tests for all bot components.

**Tests needed:**
- Command parsing and validation
- Permission checks
- Project registry operations
- Status message formatting
- Update loop logic
- Error handling paths

**Files to create:**
- `tests/test_discord_bot.py` (or multiple test files)

---

### Write user documentation

**Status:** Not Started
**Priority:** Medium
**Effort:** 2 hours

**Description:**
User-facing documentation for setting up and using the Discord bot.

**Contents:**
- Setup instructions (bot token, server whitelist)
- Command reference with examples
- Troubleshooting guide
- Permission requirements
- Security considerations

**Files to create:**
- `DISCORD_BOT_README.md`

---

## Future Enhancements

### Regression alerts for griefing detection

**Status:** Backlog
**Priority:** Low

**Description:**
Integrate with cam's griefing detection (when implemented) to send Discord alerts when projects are attacked.

**Features:**
- Mention role when regression detected
- Show before/after comparison
- Link to WPlace coordinates
- Severity indicators

---

### Multi-project dashboard message

**Status:** Backlog
**Priority:** Low

**Description:**
Single message showing condensed status for all projects, automatically updated.

**Features:**
- Table format with all projects
- Sort by progress, last update, or name
- Filter options
- Summary statistics

---

### Historical progress charts

**Status:** Backlog
**Priority:** Low

**Description:**
Generate and post progress charts showing project completion over time.

**Features:**
- Line graph of progress percentage
- Highlight griefing events
- Comparison between multiple projects
- Export as image using matplotlib

---

### Project templates and presets

**Status:** Backlog
**Priority:** Low

**Description:**
Allow users to add projects from a library of templates (popular artworks, common projects).

**Features:**
- `/cam add-template <template_name> <tx> <ty> <px> <py>`
- Template library stored in bot
- Community-submitted templates

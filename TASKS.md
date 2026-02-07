# wwpppp Tasks

## High Priority

### ⚠️ Fix tile polling - only check ONE tile per cycle

**Status:** Open  
**Priority:** High  
**Date Reported:** 2026-02-07

**Problem:**
The application is currently checking ALL tiles every polling cycle. In `main.py`, the `check_tiles()` method iterates over every tile in `self.tiles` (all tiles that have projects on them) and calls `has_tile_changed(tile)` on each one. This results in downloading multiple tiles per cycle, which is:
- Wasteful of bandwidth
- Unnecessarily hammers the WPlace backend
- Defeats the purpose of the 2-minute polling interval

**Expected Behavior:**
The application should check **exactly one tile** per polling cycle, rotating through the indexed tiles in a round-robin fashion or similar strategy.

**Current Code Location:**
- `src/wwpppp/main.py`, lines 28-32 (`check_tiles()` method)
- Iterates: `for tile in list(self.tiles.keys()): ...`

**Proposed Solution:**
1. Add an instance variable to track the current tile index/position in the rotation
2. Modify `check_tiles()` to only check one tile per invocation
3. Advance the tile pointer after each check
4. Handle the case where tiles are added/removed from `self.tiles` during runtime

**Implementation Notes:**
- Consider using a circular iterator or index-based approach
- Must handle edge case where `self.tiles` becomes empty
- Must handle edge case where tiles are added/removed mid-iteration
- The tile rotation state doesn't need to persist across application restarts

**Related Code:**
- `Main.check_tiles()` in `src/wwpppp/main.py`
- `Main._load_tiles()` in `src/wwpppp/main.py` (builds the tile index)
- `has_tile_changed(tile)` in `src/wwpppp/ingest.py` (does the actual HTTP request)

---

## Backlog

### Intelligent tile checking with warm/cold queues

**Status:** Backlog  
**Priority:** Medium

**Description:**
Instead of simple round-robin tile checking, implement a smarter queue-based system that prioritizes tiles based on activity patterns:
- "Hot" queue: tiles that have never been downloaded and are part of new projects
- "Warm" queue: tiles that have changed recently
- "Cold" queue: tiles that haven't changed in a while

This will allow more responsive monitoring of active areas while still keeping an eye on quieter regions.

**Notes:**
- Design to be fleshed out further
- Should still respect the one-tile-per-cycle constraint
- Consider decay mechanisms for moving tiles between queues

---

### Detect and alarm on project regression (griefing/attacks)

**Status:** Backlog  
**Priority:** Medium

**Description:**
When project progress changes are detected, analyze whether the change represents forward progress (more pixels matching the project) or regression (fewer pixels matching). A regression likely indicates that the project is being attacked/griefed and should trigger an alarm or notification.

**Implementation Considerations:**
- Need to track project completion percentage over time
- Define threshold for what constitutes a "significant" regression worth alarming on
- Decide on alarm mechanism (log level, notification, etc.)
- May want to distinguish between minor griefing and coordinated attacks based on regression magnitude

**Related Code:**
- `Project.run_diff()` in `src/wwpppp/projects.py` (where diffs are computed)
- Progress tracking would need to be added to the `Project` class

---

## Completed

(No items yet)

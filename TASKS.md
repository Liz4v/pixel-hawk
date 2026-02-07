# wwpppp Tasks

## High Priority

(No items currently)

---

## Backlog

### Intelligent tile checking with warm/cold queues

**Status:** Backlog
**Priority:** Medium

**Description:**
Instead of simple round-robin tile checking, implement a smarter queue-based system that prioritizes tiles based on activity patterns:
- "Burning" queue: tiles that have never been downloaded and are part of new projects
- "Hot" queue: tiles that have changed recently
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

### ✅ Fix tile polling - only check ONE tile per cycle (2026-02-07)

Implemented round-robin tile checking that processes exactly one tile per polling cycle instead of checking all tiles. Added `current_tile_index` to `Main` class to track rotation position with automatic wraparound, preventing unnecessary bandwidth usage and backend hammering. Includes proper edge case handling for empty/modified tile lists and comprehensive test coverage.

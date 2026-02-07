# CAM Tasks

## High Priority

### Intelligent tile checking with warm/cold queues

**Status:** Active 🔥
**Priority:** High

**Description:**
Instead of simple round-robin tile checking, implement a smarter queue-based system that prioritizes tiles based on activity patterns:
- "Burning" queue: tiles that have never been downloaded and are part of new projects (special queue, outside temperature hierarchy)
- Temperature-based queues: an arbitrary number of queues from hottest to coldest
  - Hottest queue: tiles that have changed most recently
  - Intermediate queues: tiles in various stages of cooldown
  - Coldest queue: tiles that haven't changed in a long time

This will allow more responsive monitoring of active areas while still keeping an eye on quieter regions.

**Queue Structure & Distribution:**
- **Burning queue:** Special queue outside the temperature hierarchy, usually empty except when new projects are added
- **Temperature queues:** Arbitrary number of queues with Zipf distribution of tile counts
  - **Zipf distribution:** Coldest queue has the most tiles, each progressively hotter queue has fewer tiles
  - **Minimum hottest queue size:** At least 5 tiles (unless it's the only non-burning queue)
  - **Example distribution (4 temperature queues, 100 total tiles):**
    - Hottest: 5 tiles
    - Hotter: 13 tiles  
    - Cooler: 25 tiles
    - Coldest: 57 tiles

**Queue Selection Criteria:**
- **Between queues:** Round-robin rotation through all queues (Burning → Hottest → ... → Coldest → repeat), skipping empty queues
  - Note: The burning queue will usually be empty except when new projects are added
- **Queue membership:** Determined by tile's last modification time
  - More recent modifications → hotter queues
  - Older modifications → cooler queues
  - Brand new tiles (never downloaded) → burning queue
- **Within each queue:** Select tile with the oldest "last checked" timestamp
  - This ensures fair coverage within each activity level

**Implementation Requirements:**
- Should still respect the one-tile-per-cycle constraint
- Maintain timestamps for: last modification time, last check time (per tile)
- Calculate queue boundaries dynamically to achieve Zipf distribution of tile counts
- Handle queue rotation with graceful skipping of empty queues
- Support configurable number of temperature queues
- Implement decay mechanisms for moving tiles between queues based on time since last modification

**Implementation Details:**
1. **Number of temperature queues:** Calculate dynamically to maximize queues while keeping hottest queue ≥ 5 tiles and maintaining good Zipf approximation
2. **Zipf distribution calculation:** Use harmonic series approach (queue i gets tile count proportional to 1/i)
3. **Queue boundary updates:** Recalculate only when necessary:
   - When project tile mapping changes (projects added/removed/modified)
   - When the tile checked in current cycle causes queue movement (modification time changed, moving it between queues)
     - If a tile moves to a hotter queue, cascade tiles down to cooler queues to maintain target Zipf distribution sizes
   - Otherwise, skip recalculation to avoid unnecessary overhead
4. **Burning queue graduation:** Tiles leave burning queue after first successful check; server provides last modification timestamp
5. **Startup behavior:** Read existing disk cache with modification metadata; assign cached tiles to temperature queues using regular criteria (no special initialization)

---

## Backlog

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
- `Project.run_diff()` in `src/cam/projects.py` (where diffs are computed)
- Progress tracking would need to be added to the `Project` class

---

## Completed

### ✅ Fix tile polling - only check ONE tile per cycle (2026-02-07)

Implemented round-robin tile checking that processes exactly one tile per polling cycle instead of checking all tiles. Added `current_tile_index` to `Main` class to track rotation position with automatic wraparound, preventing unnecessary bandwidth usage and backend hammering. Includes proper edge case handling for empty/modified tile lists and comprehensive test coverage.

# TODO — post-migration follow-ups

Follow-up work after the Tortoise ORM + Aerich → raw SQL + dataclasses migration.
Ordered by priority within each section.

## Correctness and safety (worth doing soon)

### 1. No schema migration story at all
The current docs claim "`CREATE TABLE IF NOT EXISTS` on startup", which only works
for brand-new databases. The moment you add a column to an existing production DB,
startup silently does nothing, the app crashes on first query, and there is no
recovery path.

A minimal `PRAGMA user_version` + list of upgrade functions is ~30 lines and
handles the additive case (`ALTER TABLE ... ADD COLUMN`) cleanly. Worth doing
*before* the first post-migration schema change, not after.

**Tradeoff:** adds a small amount of code we just deleted, but a real ORM's
migration system is much heavier, so this is still a big net win.

### 2. `save_as_new()` catches exceptions by string matching
[src/pixel_hawk/models/entities.py:272-275](src/pixel_hawk/models/entities.py#L272-L275)
does `if "UNIQUE constraint" in str(e) or "IntegrityError" in type(e).__name__`.
Should be `except sqlite3.IntegrityError:`. Fragile string matching is exactly
what the Zen section in CLAUDE.md warns against.

### 3. Multi-statement operations aren't atomic
`db.execute()` auto-commits each call, so `link_tiles()`
([src/pixel_hawk/models/entities.py:403-428](src/pixel_hawk/models/entities.py#L403-L428))
can crash partway and leave the DB with some tiles created and others missing.

Options:
- Add a `db.transaction()` async context manager that holds `BEGIN`/`COMMIT`/`ROLLBACK`.
- Or wrap the batch in a single SQL statement using `INSERT OR IGNORE` / `executemany`.

The first is more flexible; the second is a smaller change per call site.

## Maintainability

### 4. `entities.py` is 886 lines — over 2x the 400-line soft cap
Violates the file-size convention in CLAUDE.md. Natural split:

- `models/person.py` — `Person` + `BotAccess`
- `models/project.py` — `ProjectInfo` + `ProjectState` + `HistoryChange` + `DiffStatus`
- `models/tile.py` — `TileInfo` + `TileProject`
- `models/discord.py` — `GuildConfig` + `WatchMessage`

Shared helpers like `_where_clause` move to `models/_sql.py` or live in `db.py`.
Low-risk, pure movement.

### 5. N+1 loops in `unlink_tiles()` and `adjust_linked_tiles_heat()`
[src/pixel_hawk/models/entities.py:430-453](src/pixel_hawk/models/entities.py#L430-L453)
both fetch a list of `tile_id`s and then call `TileInfo.get_by_id(tile_id)` per
tile in a loop. For a 4x4 project that's 16 extra round-trips. A single
`WHERE id IN (placeholders)` query fixes both. Low-risk.

### 6. Column-list duplication
Every dataclass has the same list of 20+ column names written three times:
`_from_row`, `save`, `save_as_new`. Drift will silently corrupt data (e.g. add a
field, forget to update `save`, writes succeed but with a stale value).

Options, from lightest to heaviest:

- A one-line `dataclasses.fields(cls)` helper that generates the column list once
  at import time and reuses it in all three methods (~15 lines, no behavior change).
- A tiny `_insert(cls, instance)` / `_update(cls, instance)` base method.
- Don't bother — accept the duplication since we just escaped an ORM and this is
  the cost.

Leaning toward the first option: it keeps the "raw SQL visible at the call site"
feel while eliminating the drift hazard.

## Design cleanup (lower priority)

### 7. `ProjectInfo.owner = field(default_factory=Person)` creates a bogus empty Person
Whenever a `ProjectInfo` is instantiated without an owner, it gets an empty
`Person()`. Then `__post_init__` tries to reconcile `owner_id` vs `owner.id`.
This preserves a Tortoise-style "related object is always present" illusion that
no longer matches reality.

Cleaner: `owner: Person | None = None`, and `fetch_related_owner()` populates it
explicitly. Every call site already loads the owner deliberately or doesn't need it.

### 8. `_from_row_with_owner()` uses `try/except (IndexError, KeyError)`
Uses exception control flow to detect whether a joined row has owner columns.
The caller already knows — it just wrote the JOIN. Passing an explicit flag (or
having `_from_joined_row` as a separate method) is clearer and doesn't rely on
exception control flow.

### 9. `get(**kwargs)` and explicit `get_by_*` methods coexist
`get(**kwargs)` + `_where_clause` lives alongside `get_by_id`,
`filter_by_owner_name`, `filter_by_coords`, etc. Two parallel idioms for filtering.

The `**kwargs` form is flexible but opaque to the type checker; the explicit
form is verbose but self-documenting. Pick one. Bias toward explicit methods for
the common lookups and delete the `**kwargs` versions, since callers are few and
grep-ability wins.

## Docs and tests

### 10. Add DB-layer best practices to CLAUDE.md's "Database layer" section
So future-you (or an agent) doesn't re-introduce the problems above:

- Catch `sqlite3.IntegrityError`, not string-matched exceptions.
- Prefer `WHERE id IN (...)` over loops of `get_by_id`.
- Wrap multi-statement mutations in a transaction.
- When adding a column, update `SCHEMA` *and* add a migration entry.

### 11. Tests for the new db layer
The 493 tests pass, but worth checking whether any target `db.py` directly:

- Nested `database()` context managers (the `_conn` save/restore fix).
- `fetch_int` returning 0 on no-row.
- `_assert_db_writable` raising on a locked DB.

These are the bits most likely to silently break in a refactor.

## Recommended order

If only doing three things:

1. **Add a minimal `user_version`-based migration runner** (safety).
2. **Fix the `save_as_new` exception catch** (correctness, 2-line fix).
3. **Split `entities.py`** (convention compliance, low risk).

Those buy the most safety and future-agent-friendliness for the least risk.
Everything else can wait for the moment it actually hurts.

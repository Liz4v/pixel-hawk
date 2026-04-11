# TODO — post-migration follow-ups

Follow-up work after the Tortoise ORM + Aerich → raw SQL + dataclasses migration,
restructured for multi-agent orchestrated implementation.

## Orchestration

Three tracks. **Run Tracks A and B in parallel** (disjoint file sets until B8).
**Run Track C after both A and B are merged.** Within a track, steps are sequential
— each one is easier once the previous has landed.

```
Track A (db.py)        ────┐
                           ├──► Track C (docs)
Track B (entities split) ──┘
         │
         └── B8 blocks on A1
```

---

## Track A — db.py foundation

**Owner:** one agent.
**Files:** [src/pixel_hawk/models/db.py](src/pixel_hawk/models/db.py),
`tests/models/test_db.py` (new).

### A1. `db.transaction()` async context manager

`db.execute()` auto-commits every call, so multi-statement mutations like
`ProjectInfo.link_tiles` can crash partway and leave the DB half-written.

Add `async def transaction()` (`@asynccontextmanager`) that:
- Issues `BEGIN IMMEDIATE` on entry, `COMMIT` on clean exit, `ROLLBACK` on exception.
- Sets a module-level `_in_transaction: bool` flag while active. `db.execute()`
  checks the flag and skips its own `await conn.commit()` when inside a transaction.
- Is nest-safe: save/restore the prior flag value the same way `database()` already
  saves/restores `_conn`, so a nested `transaction()` is a no-op (SQLite doesn't
  support true nesting without savepoints, and we don't need savepoint semantics).

**API shape** (implicit — callers don't change how they write SQL):

```python
async with db.transaction():
    await db.execute("INSERT INTO tile ...", (...))
    await db.execute("INSERT INTO tile_project ...", (...))
```

**Done when:** CM exists, `db.execute()` respects the flag, a test that raises
mid-block leaves the DB unchanged.

### A2. Migration runner with `PRAGMA user_version`

**Depends on:** A1.

The current `CREATE TABLE IF NOT EXISTS` approach only works for brand-new DBs.
The moment a column is added, the app crashes on first query with no recovery path.

Add:

```python
MIGRATIONS: list[Callable[[aiosqlite.Connection], Awaitable[None]]] = []

async def _run_migrations(conn: aiosqlite.Connection) -> None: ...
```

Called from inside `database()` after `executescript(SCHEMA)`:

1. Read `PRAGMA user_version`.
2. **Bootstrap rule:** if version == 0 **and**
   `SELECT 1 FROM sqlite_master WHERE type='table' AND name='person'` returns a
   row, stamp `PRAGMA user_version = len(MIGRATIONS)` without running any
   migrations. This silently adopts the existing prod DB.
3. Otherwise, for each migration at index `>= version`, run it inside
   `async with db.transaction():` and bump `user_version` after each.

`MIGRATIONS` stays empty on first landing — the runner is infrastructure only.
The first real schema change appends to the list.

**Done when:** empty-DB startup runs zero migrations and stamps to 0; existing-DB
startup stamps to `len(MIGRATIONS)` without running anything; appending a no-op
migration and restarting advances `user_version` by 1.

### A3. db-layer tests

New `tests/models/test_db.py` covering the bits most likely to silently break in
future refactors:

- **Nested `database()`**: opening a second `async with database(db_path=tmp2):`
  inside a first one preserves the outer connection's `_conn` on exit.
- **`fetch_int`**: returns `0` for a query that matches no rows; returns `0` for
  `SELECT NULL`; returns the integer for a normal scalar query.
- **`_assert_db_writable`**: raises when another connection holds an exclusive
  lock on the same file. (Open a second raw `aiosqlite.connect` with
  `BEGIN EXCLUSIVE`, then try `database()` on the same path.)
- **`db.transaction()` (A1)**: commits on clean exit; rolls back on raised
  exception; nested `db.execute()` calls participate in the tx rather than
  auto-committing mid-block.
- **Migration bootstrap (A2)**: empty DB → runs migrations from index 0;
  DB with `person` table at version 0 → stamps without running; appending a
  migration and rerunning runs only the new one.

**Done when:** new test file green, coverage threshold still met.

---

## Track B — entities split and refactor

**Owner:** one agent, sequential internally.
**Files:** delete [src/pixel_hawk/models/entities.py](src/pixel_hawk/models/entities.py);
create `models/person.py`, `models/project.py`, `models/tile.py`, `models/guild.py`,
`models/watch.py`, `models/_sql.py`; import updates across
[src/pixel_hawk/watcher/](src/pixel_hawk/watcher/),
[src/pixel_hawk/interface/](src/pixel_hawk/interface/),
[src/pixel_hawk/main.py](src/pixel_hawk/main.py), and [tests/](tests/).

### B1. Split `entities.py` into per-entity modules

`entities.py` is 886 lines — 2.2× the 400-line soft cap in `CLAUDE.md`. Split:

- `models/person.py` ← `Person`, `BotAccess`
- `models/project.py` ← `ProjectInfo`, `ProjectState`, `HistoryChange`, `DiffStatus`
- `models/tile.py` ← `TileInfo`, `TileProject`
- `models/guild.py` ← `GuildConfig`
- `models/watch.py` ← `WatchMessage`
- `models/_sql.py` ← `_where_clause` (moved verbatim from
  [entities.py:878](src/pixel_hawk/models/entities.py#L878))

Pure mechanical movement. **No behavior changes in this step.** Update every
import site (grep for `from .entities`, `from pixel_hawk.models.entities`,
`models.entities`). Delete `entities.py`.

**Done when:** `uv run pytest` passes; no references to `entities` remain.

### B2. Column-list helper (dedupe `_from_row` / `save` / `save_as_new`)

Every dataclass writes its column list three times. Drift silently corrupts data.

In `models/_sql.py`, add:

```python
def _columns(cls) -> tuple[str, ...]:
    """Persistent column names for a dataclass entity."""
    excluded = getattr(cls, "_EXCLUDE_COLUMNS", frozenset())
    return tuple(f.name for f in dataclasses.fields(cls) if f.name not in excluded)
```

Each entity declares `_EXCLUDE_COLUMNS: frozenset[str] = frozenset({...})` for
non-persisted fields (e.g. `ProjectInfo.owner`, `HistoryChange.project`,
`WatchMessage.project`).

Rewrite each entity's `_from_row`, `save`, and `save_as_new` to build SQL from
`_columns(cls)`. `_from_row` becomes a generic loop mapping row columns →
constructor kwargs, with a per-class adapter dict for enum/bool coercion
(e.g. `{"state": ProjectState, "has_missing_tiles": bool}`).

Alternative rejected: a shared `_insert(cls, instance)` / `_update(cls, instance)`
base — heavier than needed and obscures the raw-SQL-at-call-site feel.

**Done when:** no column name is hand-written more than once per entity; tests pass.

### B3. `save_as_new` exception catch

[entities.py:272-275](src/pixel_hawk/models/entities.py#L272-L275) does
`if "UNIQUE constraint" in str(e) or "IntegrityError" in type(e).__name__`.
Replace with:

```python
except sqlite3.IntegrityError:
    continue
```

Fragile string matching is exactly what `CLAUDE.md`'s Zen-of-Python section warns
against.

**Done when:** only `sqlite3.IntegrityError` is caught; a test forces a collision
and verifies the retry loop still works.

### B4. Batch `WHERE id IN (…)` for N+1 loops

[entities.py:430-453](src/pixel_hawk/models/entities.py#L430-L453) —
`ProjectInfo.unlink_tiles` and `ProjectInfo.adjust_linked_tiles_heat` both fetch a
list of `tile_id`s and then call `TileInfo.get_by_id(tile_id)` per tile. For a 4×4
project that's 16 extra round-trips.

Add `TileInfo.filter_by_ids(tile_ids: list[int]) -> list[TileInfo]` in
`models/tile.py` (empty list on empty input, single `WHERE id IN (…)` query
otherwise). Rewrite both call sites to use it.

**Done when:** neither method calls `get_by_id` in a loop.

### B5. `ProjectInfo.owner` cleanup

[entities.py:152](src/pixel_hawk/models/entities.py#L152) has
`owner: Person = field(default_factory=Person)`, which fabricates an empty
`Person` on every instantiation and then reconciles `owner_id` vs `owner.id` in
`__post_init__`. This preserves a Tortoise-style "related object always present"
illusion that no longer matches reality.

Change to `owner: Person | None = None`. Delete `__post_init__`. Every call site
either already calls `fetch_related_owner()` or doesn't need `.owner`. Where a
loop narrows, hoist a local:

```python
assert info.owner is not None
owner = info.owner
```

Audit call sites in [src/pixel_hawk/watcher/](src/pixel_hawk/watcher/),
[src/pixel_hawk/interface/](src/pixel_hawk/interface/), and
[src/pixel_hawk/main.py](src/pixel_hawk/main.py).

**Done when:** `ProjectInfo()` no longer constructs a throwaway `Person`;
`ty check` passes.

### B6. Explicit `_from_joined_row` method

[entities.py:209-225](src/pixel_hawk/models/entities.py#L209-L225) uses
`try/except (IndexError, KeyError)` to detect whether a row has owner columns.
The caller already knows — it just wrote the JOIN.

Split into two classmethods on `ProjectInfo`:

- `_from_row(row)` — unchanged, no owner.
- `_from_joined_row(row)` — always reads the `owner_*` columns and sets `info.owner`.

Delete the try/except. Callers that write a JOIN use `_from_joined_row`; callers
that don't use `_from_row`.

**Done when:** no exception-control-flow in entity loaders.

### B7. Explicit getters (remove `**kwargs` lookups)

`get(**kwargs)` + `_where_clause` coexists alongside `get_by_id`,
`filter_by_owner_name`, `filter_by_coords`, etc. — two parallel idioms.

Remove `get`, `get_or_none`, `filter`, `count` `**kwargs` classmethods from every
entity. Replace each call site with the matching explicit method
(`get_by_id`, `filter_by_owner`, `count_by_owner`, …). Add new `get_by_*` /
`filter_by_*` / `count_by_*` methods where a call site needs one that doesn't
exist yet — name them after the filter fields.

`_where_clause` **stays** in `models/_sql.py` as a private helper for explicit
methods that want a multi-field WHERE clause internally. It just stops being the
public API.

Grep for `.get(`, `.get_or_none(`, `.filter(`, `.count(` across `src/` and `tests/`
to find call sites.

**Done when:** no entity exposes a `**kwargs`-based lookup; `ty check` is happier
(explicit signatures narrow better).

### B8. Wrap multi-statement mutations in `db.transaction()`

**Depends on:** A1.

Wrap `ProjectInfo.link_tiles`
([entities.py:403-428](src/pixel_hawk/models/entities.py#L403-L428)),
`ProjectInfo.unlink_tiles`
([entities.py:430-443](src/pixel_hawk/models/entities.py#L430-L443)),
and any other multi-statement mutation (audit during this step — look for
methods that issue two or more `db.execute` calls that must succeed together)
in `async with db.transaction():`.

**Done when:** a test that raises mid-`link_tiles` leaves zero rows in `tile` and
`tile_project` for that project.

---

## Track C — docs

**Depends on:** Tracks A and B merged.
**Owner:** one agent.
**File:** [CLAUDE.md](CLAUDE.md), "Database layer: raw SQL + dataclasses" section.

### C1. Database-layer best practices

Add a **Best practices** subsection so future-you (or an agent) doesn't
re-introduce the problems fixed above:

- Catch `sqlite3.IntegrityError`, never string-match exception messages.
- Prefer `WHERE id IN (…)` over per-row `get_by_id` loops.
- Wrap any multi-statement mutation in `async with db.transaction():`.
- When adding a column: update `SCHEMA` **and** append a function to
  `MIGRATIONS`. Explain the `user_version` bootstrap rule.
- Prefer explicit `get_by_*` / `filter_by_*` / `count_by_*` methods over generic
  `**kwargs` lookups. `_where_clause` in `models/_sql.py` is a private helper,
  not a public API.
- Use `_columns(cls)` from `models/_sql.py` when writing SQL for a new entity —
  never hand-duplicate the column list across `_from_row` / `save` / `save_as_new`.
  Declare `_EXCLUDE_COLUMNS` on the class for non-persisted fields.
- `ProjectInfo.owner` is `None` until `fetch_related_owner()` or
  `_from_joined_row()` populates it. Narrow with an assert at the loop boundary.

Also update the **File/Module map** section to reference the split modules
(`person.py`, `project.py`, `tile.py`, `guild.py`, `watch.py`, `_sql.py`)
instead of `entities.py`.

**Done when:** `CLAUDE.md` reflects the new structure and conventions.

---

## End-to-end verification (run after all tracks land)

- `uv run ruff check` — lint clean.
- `uv run ty check` — type-check clean.
- `uv run pytest` — all existing tests pass; new db-layer tests pass; coverage
  threshold met.
- `uv run hawk` against the existing dev DB — migration runner stamps it to
  current version without running migrations; polling loop starts cleanly.
- Manual: create a project via `/hawk new`, confirm `link_tiles` commits cleanly.
  Inject an exception mid-`link_tiles` and confirm rollback leaves no partial
  `tile` / `tile_project` rows.

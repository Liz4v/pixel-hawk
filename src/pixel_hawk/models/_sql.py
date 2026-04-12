"""Private SQL helpers shared by the entity modules.

Not part of the public API — entity modules use these internally to build
queries without duplicating column lists or WHERE-clause construction.
"""

import dataclasses
from typing import Any


def _columns(cls: type) -> tuple[str, ...]:
    """Persistent column names for a dataclass entity.

    Fields listed in the class attribute `_EXCLUDE_COLUMNS` (a `frozenset[str]`)
    are skipped — used for in-memory-only fields such as `ProjectInfo.owner`.
    """
    excluded: frozenset[str] = getattr(cls, "_EXCLUDE_COLUMNS", frozenset())
    return tuple(f.name for f in dataclasses.fields(cls) if f.name not in excluded)


def _where_clause(kwargs: dict[str, Any]) -> tuple[str, tuple]:
    """Build a WHERE clause and value tuple from keyword arguments.

    Private helper for explicit getters that need a multi-field WHERE clause;
    not exposed as a public lookup idiom.
    """
    parts = [f"{key} = ?" for key in kwargs]
    values = tuple(kwargs.values())
    return " AND ".join(parts), values

"""What changed in the session inventory since a client's last read."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class SessionChanges:
    """Sessions written since a cursor, and the cursor to ask from next time.

    ``cursor`` is the oldest transaction still in flight when this was read: a
    write the reader could not see yet carries a transaction id at or above it,
    so asking again from ``cursor`` never misses one. ``ids`` names every session
    whose row, or whose read state for the reader, was written at or after the
    cursor the caller passed in.
    """

    cursor: int
    ids: frozenset[UUID]

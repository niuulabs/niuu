"""Replay sources: a uniform async stream of recorded ``SessionLogEntry``.

Two backings, one contract (:class:`ReplaySource`) — so the pacing/emit driver
is source-agnostic:

* :class:`RepositoryReplaySource` pages the durable
  :class:`SessionEventLogRepository` (the same instance the app already holds —
  no new connection pool).
* :class:`FixtureReplaySource` replays a checked-in ``SessionLogEntry[]`` JSON
  array (the ``GET .../log`` / Phase-1 ``*.frames.json`` shape) for offline/CI.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from volundr.domain.models import SessionLogEntry
from volundr.domain.ports import SessionEventLogRepository


class ReplaySource(Protocol):
    """An async iterator of recorded frames with seq > ``after_seq``."""

    def entries(self, *, after_seq: int = 0) -> AsyncIterator[SessionLogEntry]: ...


class RepositoryReplaySource:
    """DB source: pages the repo (``seq > cursor``, ORDER BY seq ASC, LIMIT page).

    Reuses the app-wide repository — opens NO new asyncpg pool. The cursor
    advances to the last seq of each page; a short page means the log is drained
    (so we avoid one extra empty round-trip).
    """

    def __init__(
        self,
        repo: SessionEventLogRepository,
        session_id: UUID,
        *,
        page: int = 500,
    ) -> None:
        self._repo = repo
        self._sid = session_id
        self._page = page

    async def entries(self, *, after_seq: int = 0) -> AsyncIterator[SessionLogEntry]:
        cursor = after_seq
        while True:
            batch = await self._repo.read_after(self._sid, after_seq=cursor, limit=self._page)
            if not batch:
                return
            for entry in batch:
                yield entry
                cursor = entry.seq
            if len(batch) < self._page:
                return


def _parse_ts(value: object) -> datetime | None:
    """Parse a fixture ts into a datetime (or None).

    Python 3.11+ ``datetime.fromisoformat`` handles both the trailing ``Z`` and
    ``+00:00`` offsets; we normalise ``Z`` defensively for older shapes.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _entry_from_fixture_row(row: dict, *, session_id: UUID) -> SessionLogEntry:
    payload = row.get("payload", {}) or {}
    return SessionLogEntry(
        session_id=session_id,  # route-derived; the fixture's own id is ignored
        seq=int(row["seq"]),
        kind=str(row.get("kind") or payload.get("type", "unknown")),
        payload=payload,
        ts=_parse_ts(row.get("ts")),
        role=row.get("role"),
        request_id=row.get("request_id"),
    )


def load_fixture_entries(path: Path, *, session_id: UUID) -> list[SessionLogEntry]:
    """Load a ``SessionLogEntry[]`` fixture file, sorted by ``seq`` ascending.

    Each row's own ``session_id`` is ignored; we stamp the route-derived
    ``session_id`` (the payload itself is what gets re-emitted, never this id).
    ``kind`` falls back to ``payload.type``; a missing ``ts`` is tolerated.

    Raises:
        ValueError: the file is not a JSON array.
        KeyError: a row is missing the required ``seq``.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"fixture {path} is not a SessionLogEntry[] array")
    rows = [_entry_from_fixture_row(r, session_id=session_id) for r in raw]
    rows.sort(key=lambda e: e.seq)
    return rows


class FixtureReplaySource:
    """In-memory source: replays a pre-loaded list of ``SessionLogEntry``."""

    def __init__(self, entries: list[SessionLogEntry]) -> None:
        self._entries = entries

    async def entries(self, *, after_seq: int = 0) -> AsyncIterator[SessionLogEntry]:
        for entry in self._entries:
            if entry.seq > after_seq:
                yield entry

"""SQLite audit repository adapter for Sleipnir (Pi / single-node mode).

Uses Python's built-in ``sqlite3`` module wrapped in a thread-pool executor so
it integrates cleanly with asyncio without requiring an extra dependency.

All operations acquire a per-instance asyncio lock so concurrent callers
never interleave writes on the shared connection.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import sqlite3
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.audit import AuditQuery, AuditRepository

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sleipnir_events (
    event_id        TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,
    source          TEXT NOT NULL,
    summary         TEXT,
    urgency         REAL,
    domain          TEXT,
    correlation_id  TEXT,
    causation_id    TEXT,
    tenant_id       TEXT,
    payload         TEXT,
    timestamp       TEXT NOT NULL,
    ttl             INTEGER
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_se_type_ts ON sleipnir_events (event_type, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_se_correlation ON sleipnir_events (correlation_id);",
    "CREATE INDEX IF NOT EXISTS idx_se_source_ts ON sleipnir_events (source, timestamp);",
]

_INSERT = """
INSERT OR IGNORE INTO sleipnir_events
    (event_id, event_type, source, summary, urgency, domain,
     correlation_id, causation_id, tenant_id, payload, timestamp, ttl)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_PURGE = """
DELETE FROM sleipnir_events
WHERE ttl IS NOT NULL
  AND datetime(timestamp, '+' || CAST(ttl AS TEXT) || ' seconds') < datetime(?)
"""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_TABLE)
    for idx_sql in _CREATE_INDEXES:
        conn.execute(idx_sql)
    conn.commit()


class SqliteAuditRepository(AuditRepository):
    """SQLite-backed audit repository.

    :param db_path: Path to the SQLite database file.  Use ``":memory:"`` for
        an in-process, ephemeral store (useful in tests).
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_connection(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        conn = await asyncio.get_running_loop().run_in_executor(
            None, partial(_connect, self._db_path)
        )
        await asyncio.get_running_loop().run_in_executor(None, _init_schema, conn)
        self._conn = conn
        return conn

    # ------------------------------------------------------------------
    # AuditRepository interface
    # ------------------------------------------------------------------

    async def append(self, event: SleipnirEvent) -> None:
        conn = await self._ensure_connection()
        params = (
            event.event_id,
            event.event_type,
            event.source,
            event.summary,
            event.urgency,
            event.domain,
            event.correlation_id,
            event.causation_id,
            event.tenant_id,
            json.dumps(event.payload),
            event.timestamp.isoformat(),
            event.ttl,
        )
        async with self._lock:
            await asyncio.get_running_loop().run_in_executor(None, conn.execute, _INSERT, params)
            await asyncio.get_running_loop().run_in_executor(None, conn.commit)

    async def query(self, q: AuditQuery) -> list[SleipnirEvent]:
        conn = await self._ensure_connection()

        sql = "SELECT * FROM sleipnir_events WHERE 1=1"
        params: list[Any] = []

        if q.from_ts is not None:
            sql += " AND timestamp >= ?"
            params.append(q.from_ts.isoformat())
        if q.to_ts is not None:
            sql += " AND timestamp <= ?"
            params.append(q.to_ts.isoformat())
        if q.correlation_id is not None:
            sql += " AND correlation_id = ?"
            params.append(q.correlation_id)
        if q.source is not None:
            sql += " AND source = ?"
            params.append(q.source)

        if q.service is not None:
            sql += " AND substr(source, 1, instr(source || ':', ':') - 1) = ?"
            params.append(q.service)

        # Over-fetch when pattern filtering to ensure we return q.limit results.
        fetch_limit = q.limit if not q.event_type_pattern else q.limit * 10
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(fetch_limit)

        async with self._lock:
            rows = await asyncio.get_running_loop().run_in_executor(
                None, lambda: conn.execute(sql, params).fetchall()
            )

        events = [_row_to_event(row) for row in rows]

        if q.event_type_pattern and q.event_type_pattern != "*":
            events = [e for e in events if fnmatch.fnmatch(e.event_type, q.event_type_pattern)]

        return events[: q.limit]

    async def purge_expired(self) -> int:
        conn = await self._ensure_connection()
        now_iso = datetime.now(UTC).isoformat()
        async with self._lock:
            cursor = await asyncio.get_running_loop().run_in_executor(
                None, conn.execute, _PURGE, (now_iso,)
            )
            count: int = cursor.rowcount
            await asyncio.get_running_loop().run_in_executor(None, conn.commit)
        logger.debug("SQLite audit purge removed %d expired rows", count)
        return count

    async def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._conn is not None:
            await asyncio.get_running_loop().run_in_executor(None, self._conn.close)
            self._conn = None


# ------------------------------------------------------------------
# Row → domain model
# ------------------------------------------------------------------


def _row_to_event(row: sqlite3.Row) -> SleipnirEvent:
    ts_str: str = row["timestamp"]
    ts = datetime.fromisoformat(ts_str)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    payload_raw = row["payload"]
    payload: dict = json.loads(payload_raw) if payload_raw else {}

    return SleipnirEvent(
        event_id=row["event_id"],
        event_type=row["event_type"],
        source=row["source"],
        summary=row["summary"] or "",
        urgency=row["urgency"] if row["urgency"] is not None else 0.0,
        domain=row["domain"] or "code",
        correlation_id=row["correlation_id"],
        causation_id=row["causation_id"],
        tenant_id=row["tenant_id"],
        payload=payload,
        timestamp=ts,
        ttl=row["ttl"],
    )

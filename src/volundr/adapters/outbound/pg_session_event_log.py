"""PostgreSQL adapter for the durable, append-only session event log.

Implements :class:`SessionEventLogRepository`. Writes are idempotent on
(session_id, seq) via ON CONFLICT DO NOTHING, so the producer (skuld) can retry
at-least-once without creating duplicates. Reads are cursor-based (seq) so any
client can resume a full transcript replay.
"""

import json
import logging
from uuid import UUID

import asyncpg

from volundr.domain.models import SessionLogEntry
from volundr.domain.ports import SessionEventLogRepository

logger = logging.getLogger(__name__)


class PostgresSessionEventLog(SessionEventLogRepository):
    """PostgreSQL adapter for the full-fidelity session event log."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def append(self, entries: list[SessionLogEntry]) -> int:
        if not entries:
            return 0
        args = [self._entry_to_args(e) for e in entries]
        await self._pool.executemany(
            """INSERT INTO session_event_log
               (session_id, seq, kind, role, request_id, payload, ts)
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT (session_id, seq) DO NOTHING""",
            args,
        )
        return len(entries)

    async def read_after(
        self,
        session_id: UUID,
        after_seq: int = 0,
        limit: int = 1000,
    ) -> list[SessionLogEntry]:
        rows = await self._pool.fetch(
            """SELECT session_id, seq, kind, role, request_id, payload, ts
               FROM session_event_log
               WHERE session_id = $1 AND seq > $2
               ORDER BY seq ASC
               LIMIT $3""",
            session_id,
            after_seq,
            limit,
        )
        return [self._row_to_entry(r) for r in rows]

    async def latest_seq(self, session_id: UUID) -> int:
        value = await self._pool.fetchval(
            "SELECT MAX(seq) FROM session_event_log WHERE session_id = $1",
            session_id,
        )
        if value is None:
            return 0
        return int(value)

    # -- Internal helpers -----------------------------------------------------

    @staticmethod
    def _entry_to_args(entry: SessionLogEntry) -> tuple:
        return (
            entry.session_id,
            entry.seq,
            entry.kind,
            entry.role,
            entry.request_id,
            json.dumps(entry.payload),
            entry.ts,
        )

    @staticmethod
    def _row_to_entry(row: asyncpg.Record) -> SessionLogEntry:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return SessionLogEntry(
            session_id=row["session_id"],
            seq=row["seq"],
            kind=row["kind"],
            payload=payload,
            ts=row["ts"],
            role=row["role"],
            request_id=row["request_id"],
        )

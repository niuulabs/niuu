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

from volundr.adapters.outbound._jsonb import dumps_jsonb, force_scrub_json, scrub_text
from volundr.domain.models import SessionLogEntry
from volundr.domain.ports import SessionEventLogRepository, _log_entries_conflict

logger = logging.getLogger(__name__)

_INSERT_SQL = """INSERT INTO session_event_log
       (session_id, seq, kind, role, request_id, payload, ts)
       VALUES ($1, $2, $3, $4, $5, $6, $7)
       ON CONFLICT (session_id, seq) DO NOTHING"""


class PostgresSessionEventLog(SessionEventLogRepository):
    """PostgreSQL adapter for the full-fidelity session event log."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def append(self, entries: list[SessionLogEntry]) -> int:
        if not entries:
            return 0
        args = [self._entry_to_args(e) for e in entries]
        try:
            await self._pool.executemany(_INSERT_SQL, args)
        except asyncpg.exceptions.UntranslatableCharacterError:
            # Defensive: a frame still carried a character Postgres can't store
            # past the primary scrub. Force-scrub the serialized payloads and retry
            # once so one bad frame never drops the whole batch (ON CONFLICT keeps
            # this idempotent if some rows already landed).
            logger.warning("session_event_log insert hit untranslatable char; retrying scrubbed")
            scrubbed = [self._force_scrub_args(a) for a in args]
            await self._pool.executemany(_INSERT_SQL, scrubbed)
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

    async def detect_conflicts(self, entries: list[SessionLogEntry]) -> list[int]:
        """Surface seqs whose STORED row differs from the candidate (INV-3c).

        Cold path only — never called from ``append``/``_flush``. One bounded,
        parameterized SELECT per session over exactly the candidate seqs (no N+1,
        no scan). ``append`` (ON CONFLICT DO NOTHING) is untouched, so the
        at-least-once retry stays an idempotent same-payload no-op.
        """
        if not entries:
            return []

        by_session: dict[UUID, dict[int, SessionLogEntry]] = {}
        for entry in entries:
            by_session.setdefault(entry.session_id, {})[entry.seq] = entry

        conflicts: list[int] = []
        for session_id, candidates in by_session.items():
            rows = await self._pool.fetch(
                """SELECT session_id, seq, kind, role, request_id, payload, ts
                   FROM session_event_log
                   WHERE session_id = $1 AND seq = ANY($2::bigint[])""",
                session_id,
                list(candidates),
            )
            for row in rows:
                stored = self._row_to_entry(row)
                candidate = candidates.get(stored.seq)
                if candidate is None:
                    continue
                if _log_entries_conflict(candidate, stored):
                    conflicts.append(stored.seq)
        conflicts.sort()
        return conflicts

    # -- Internal helpers -----------------------------------------------------

    @staticmethod
    def _entry_to_args(entry: SessionLogEntry) -> tuple:
        # Scrub the text columns too — kind/role/request_id are plain text and a
        # NUL there 500s the insert just like the JSONB payload does.
        return (
            entry.session_id,
            entry.seq,
            scrub_text(entry.kind),
            scrub_text(entry.role),
            scrub_text(entry.request_id),
            dumps_jsonb(entry.payload),
            entry.ts,
        )

    @staticmethod
    def _force_scrub_args(args: tuple) -> tuple:
        """Belt-and-suspenders rebuild of one row's args for the retry path: the
        payload ($6) is an already-serialized JSON string, force-scrubbed of any
        residual escaped NUL/surrogate; the text columns re-scrubbed."""
        session_id, seq, kind, role, request_id, payload, ts = args
        return (
            session_id,
            seq,
            scrub_text(kind),
            scrub_text(role),
            scrub_text(request_id),
            force_scrub_json(payload),
            ts,
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

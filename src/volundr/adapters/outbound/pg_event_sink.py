"""PostgreSQL event sink — persists session events to the session_events table.

Implements both EventSink (write-side) and SessionEventRepository (read-side).
"""

import json
import logging
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from volundr.adapters.outbound._jsonb import dumps_jsonb, force_scrub_json, scrub_text
from volundr.domain.models import SessionEvent, SessionEventType
from volundr.domain.ports import EventSink, SessionEventRepository

logger = logging.getLogger(__name__)

_INSERT_SQL = """INSERT INTO session_events
       (id, session_id, event_type, timestamp, data, tokens_in,
        tokens_out, cost, duration_ms, model, sequence)
       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)"""


class PostgresEventSink(EventSink, SessionEventRepository):
    """PostgreSQL adapter for the session event pipeline."""

    def __init__(self, pool: asyncpg.Pool, buffer_size: int = 1):
        self._pool = pool
        self._buffer_size = buffer_size
        self._buffer: list[SessionEvent] = []
        self._healthy = True

    # -- EventSink (write-side) -----------------------------------------------

    async def emit(self, event: SessionEvent) -> None:
        if self._buffer_size <= 1:
            await self._insert_one(event)
            return
        self._buffer.append(event)
        if len(self._buffer) >= self._buffer_size:
            await self.flush()

    async def emit_batch(self, events: list[SessionEvent]) -> None:
        if not events:
            return
        args = [self._event_to_args(e) for e in events]
        try:
            await self._pool.executemany(_INSERT_SQL, args)
        except asyncpg.exceptions.UntranslatableCharacterError:
            # Defensive: never let one bad frame 500 + drop the whole batch.
            logger.warning("session_events insert hit untranslatable char; retrying scrubbed")
            await self._pool.executemany(_INSERT_SQL, [self._force_scrub_args(a) for a in args])

    async def flush(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer[:]
        self._buffer.clear()
        await self.emit_batch(batch)

    async def close(self) -> None:
        await self.flush()

    @property
    def sink_name(self) -> str:
        return "postgres"

    @property
    def healthy(self) -> bool:
        return self._healthy

    # -- SessionEventRepository (read-side) -----------------------------------

    async def get_events(
        self,
        session_id: UUID,
        event_types: list[SessionEventType] | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[SessionEvent]:
        conditions = ["session_id = $1"]
        params: list = [session_id]
        idx = 2

        if event_types:
            placeholders = ", ".join(f"${idx + i}" for i in range(len(event_types)))
            conditions.append(f"event_type IN ({placeholders})")
            params.extend(t.value for t in event_types)
            idx += len(event_types)

        if after:
            conditions.append(f"timestamp > ${idx}")
            params.append(after)
            idx += 1

        if before:
            conditions.append(f"timestamp < ${idx}")
            params.append(before)
            idx += 1

        where = " AND ".join(conditions)
        params.extend([limit, offset])
        query = f"""SELECT * FROM session_events
                    WHERE {where}
                    ORDER BY sequence ASC
                    LIMIT ${idx} OFFSET ${idx + 1}"""

        rows = await self._pool.fetch(query, *params)
        return [self._row_to_event(r) for r in rows]

    async def get_event_counts(self, session_id: UUID) -> dict[str, int]:
        rows = await self._pool.fetch(
            """SELECT event_type, COUNT(*)::int AS cnt
               FROM session_events WHERE session_id = $1
               GROUP BY event_type""",
            session_id,
        )
        return {row["event_type"]: row["cnt"] for row in rows}

    async def get_token_timeline(self, session_id: UUID, bucket_seconds: int = 300) -> list[dict]:
        rows = await self._pool.fetch(
            """SELECT
                 (EXTRACT(EPOCH FROM timestamp)::bigint / $2) * $2 AS bucket,
                 COALESCE(SUM(tokens_in), 0)::int AS tokens_in,
                 COALESCE(SUM(tokens_out), 0)::int AS tokens_out,
                 COALESCE(SUM(cost), 0) AS cost
               FROM session_events
               WHERE session_id = $1
                 AND event_type IN ('message_assistant', 'token_usage')
               GROUP BY bucket ORDER BY bucket""",
            session_id,
            bucket_seconds,
        )
        return [dict(r) for r in rows]

    async def delete_by_session(self, session_id: UUID) -> int:
        result = await self._pool.execute(
            "DELETE FROM session_events WHERE session_id = $1", session_id
        )
        # asyncpg returns "DELETE N"
        return int(result.split()[-1])

    # -- Internal helpers -----------------------------------------------------

    async def _insert_one(self, event: SessionEvent) -> None:
        args = self._event_to_args(event)
        try:
            await self._pool.execute(_INSERT_SQL, *args)
        except asyncpg.exceptions.UntranslatableCharacterError:
            logger.warning("session_events insert hit untranslatable char; retrying scrubbed")
            await self._pool.execute(_INSERT_SQL, *self._force_scrub_args(args))

    @staticmethod
    def _event_to_args(event: SessionEvent) -> tuple:
        # event_type/model are plain text columns — scrub them too, like the
        # JSONB `data` payload, so a NUL there can't 500 the insert.
        return (
            event.id,
            event.session_id,
            scrub_text(event.event_type.value),
            event.timestamp,
            dumps_jsonb(event.data),
            event.tokens_in,
            event.tokens_out,
            float(event.cost) if event.cost is not None else None,
            event.duration_ms,
            scrub_text(event.model),
            event.sequence,
        )

    @staticmethod
    def _force_scrub_args(args: tuple) -> tuple:
        """Belt-and-suspenders rebuild for the retry path: data ($5) is an already-
        serialized JSON string, force-scrubbed; event_type/model re-scrubbed."""
        (
            event_id,
            session_id,
            event_type,
            timestamp,
            data,
            tokens_in,
            tokens_out,
            cost,
            duration_ms,
            model,
            sequence,
        ) = args
        return (
            event_id,
            session_id,
            scrub_text(event_type),
            timestamp,
            force_scrub_json(data),
            tokens_in,
            tokens_out,
            cost,
            duration_ms,
            scrub_text(model),
            sequence,
        )

    @staticmethod
    def _row_to_event(row: asyncpg.Record) -> SessionEvent:
        data = row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        return SessionEvent(
            id=row["id"],
            session_id=row["session_id"],
            event_type=SessionEventType(row["event_type"]),
            timestamp=row["timestamp"],
            data=data,
            sequence=row["sequence"],
            tokens_in=row["tokens_in"],
            tokens_out=row["tokens_out"],
            cost=Decimal(str(row["cost"])) if row["cost"] is not None else None,
            duration_ms=row["duration_ms"],
            model=row["model"],
        )

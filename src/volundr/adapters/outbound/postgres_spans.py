"""PostgreSQL repository for persisted session trace spans."""

import json
from datetime import UTC
from uuid import UUID

import asyncpg

from volundr.domain.models import SessionSpan, SessionSpanStatus
from volundr.domain.ports import SessionSpanRepository


class PostgresSpanRepository(SessionSpanRepository):
    """Persist and query session spans from PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def upsert_span(self, span: SessionSpan) -> SessionSpan:
        row = await self._pool.fetchrow(
            """INSERT INTO session_spans (
                   id,
                   session_id,
                   trace_id,
                   parent_span_id,
                   kind,
                   name,
                   status,
                   started_at,
                   ended_at,
                   duration_ms,
                   actor_type,
                   actor_id,
                   actor_label,
                   source_service,
                   attributes
               ) VALUES (
                   $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15
               )
               ON CONFLICT (id) DO UPDATE SET
                   session_id = EXCLUDED.session_id,
                   trace_id = EXCLUDED.trace_id,
                   parent_span_id = EXCLUDED.parent_span_id,
                   kind = EXCLUDED.kind,
                   name = EXCLUDED.name,
                   status = EXCLUDED.status,
                   started_at = EXCLUDED.started_at,
                   ended_at = EXCLUDED.ended_at,
                   duration_ms = EXCLUDED.duration_ms,
                   actor_type = EXCLUDED.actor_type,
                   actor_id = EXCLUDED.actor_id,
                   actor_label = EXCLUDED.actor_label,
                   source_service = EXCLUDED.source_service,
                   attributes = EXCLUDED.attributes,
                   updated_at = NOW()
               RETURNING *""",
            span.id,
            span.session_id,
            span.trace_id,
            span.parent_span_id,
            span.kind,
            span.name,
            span.status.value,
            span.started_at,
            span.ended_at,
            span.duration_ms,
            span.actor_type,
            span.actor_id,
            span.actor_label,
            span.source_service,
            json.dumps(span.attributes),
        )
        return self._row_to_span(row)

    async def finish_span(
        self,
        span_id: UUID,
        ended_at,
        status: str,
        attributes: dict | None = None,
    ) -> SessionSpan | None:
        row = await self._pool.fetchrow(
            """UPDATE session_spans
               SET ended_at = $2,
                   duration_ms = GREATEST(0, FLOOR(EXTRACT(EPOCH FROM ($2 - started_at)) * 1000))::int,
                   status = $3,
                   attributes = COALESCE(attributes, '{}'::jsonb) || $4::jsonb,
                   updated_at = NOW()
               WHERE id = $1
               RETURNING *""",
            span_id,
            ended_at,
            status,
            json.dumps(attributes or {}),
        )
        if row is None:
            return None
        return self._row_to_span(row)

    async def list_spans(self, session_id: UUID) -> list[SessionSpan]:
        rows = await self._pool.fetch(
            """SELECT *
               FROM session_spans
               WHERE session_id = $1
               ORDER BY started_at ASC, created_at ASC""",
            session_id,
        )
        return [self._row_to_span(row) for row in rows]

    async def delete_by_session(self, session_id: UUID) -> int:
        result = await self._pool.execute(
            "DELETE FROM session_spans WHERE session_id = $1",
            session_id,
        )
        return int(result.split()[-1])

    @staticmethod
    def _row_to_span(row: asyncpg.Record) -> SessionSpan:
        attributes = row["attributes"]
        if isinstance(attributes, str):
            attributes = json.loads(attributes)
        started_at = row["started_at"]
        ended_at = row["ended_at"]
        if started_at is not None and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        if ended_at is not None and ended_at.tzinfo is None:
            ended_at = ended_at.replace(tzinfo=UTC)
        return SessionSpan(
            id=row["id"],
            session_id=row["session_id"],
            trace_id=row["trace_id"],
            parent_span_id=row["parent_span_id"],
            kind=row["kind"],
            name=row["name"],
            status=SessionSpanStatus(row["status"]),
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=row["duration_ms"],
            actor_type=row["actor_type"],
            actor_id=row["actor_id"],
            actor_label=row["actor_label"],
            source_service=row["source_service"],
            attributes=attributes or {},
        )

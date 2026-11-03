"""PostgreSQL repository for persisted session trace spans."""

import json
from datetime import UTC, datetime
from uuid import UUID

import asyncpg

from volundr.domain.models import SessionSpan, SessionSpanStatus
from volundr.domain.ports import SessionSpanRepository

# The union of each session's assistant-turn intervals inside [$2, $3]: clip every span to
# the window, then merge overlaps (a new group starts where no earlier interval reaches).
WORKED_SECONDS_SQL = """
WITH spans AS (
    SELECT sp.session_id, sp.started_at, sp.ended_at,
           lead(sp.started_at) OVER (
               PARTITION BY sp.session_id ORDER BY sp.started_at
           ) AS next_start
    FROM session_spans sp
    WHERE sp.session_id = ANY($1::uuid[]) AND sp.kind = 'turn.assistant'
),
clipped AS (
    SELECT sp.session_id,
           greatest(sp.started_at, $2) AS s,
           least(
               CASE
                   WHEN sp.ended_at IS NOT NULL THEN sp.ended_at
                   -- The latest turn may still be running: to now while its session
                   -- works, else to the session's last activity.
                   WHEN sp.next_start IS NULL THEN
                       CASE WHEN se.status = 'running'
                                 AND se.activity_state IS DISTINCT FROM 'idle'
                            THEN $3 ELSE se.last_active END
                   -- An older turn left open (its end event was lost) lasted as long as
                   -- the last tool call recorded inside it, never past the next turn.
                   ELSE least(coalesce(evidence.last_end, sp.started_at), sp.next_start)
               END,
               $3
           ) AS e
    FROM spans sp
    JOIN sessions se ON se.id = sp.session_id
    LEFT JOIN LATERAL (
        SELECT max(coalesce(t.ended_at, t.started_at)) AS last_end
        FROM session_spans t
        WHERE sp.ended_at IS NULL AND sp.next_start IS NOT NULL
          AND t.session_id = sp.session_id AND t.kind <> 'turn.assistant'
          AND t.started_at >= sp.started_at AND t.started_at < sp.next_start
    ) evidence ON true
    WHERE sp.started_at < $3
      AND coalesce(sp.ended_at, sp.next_start, $3) > $2
),
ordered AS (
    SELECT session_id, s, e,
           max(e) OVER (PARTITION BY session_id ORDER BY s, e
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS reach
    FROM clipped WHERE e > s
),
grouped AS (
    SELECT session_id, s, e,
           sum(CASE WHEN reach IS NULL OR s > reach THEN 1 ELSE 0 END)
               OVER (PARTITION BY session_id ORDER BY s, e) AS grp
    FROM ordered
),
merged AS (
    SELECT session_id, max(e) - min(s) AS span FROM grouped GROUP BY session_id, grp
)
SELECT session_id, floor(extract(epoch FROM sum(span)))::bigint AS seconds
FROM merged GROUP BY session_id
"""


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
                   attributes,
                   w3c_trace_id
               ) VALUES (
                   $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16
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
                   w3c_trace_id = COALESCE(EXCLUDED.w3c_trace_id, session_spans.w3c_trace_id),
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
            span.w3c_trace_id,
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
                   duration_ms = GREATEST(
                       0,
                       FLOOR(EXTRACT(EPOCH FROM ($2 - started_at)) * 1000)
                   )::int,
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

    async def worked_seconds(
        self, session_ids: list[UUID], *, since: datetime, until: datetime
    ) -> dict[UUID, int]:
        if not session_ids:
            return {}
        rows = await self._pool.fetch(WORKED_SECONDS_SQL, session_ids, since, until)
        return {row["session_id"]: int(row["seconds"]) for row in rows}

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
            w3c_trace_id=row["w3c_trace_id"],
        )

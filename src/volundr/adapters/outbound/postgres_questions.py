"""Question state in PostgreSQL (LEX-1932, migration 000097).

The first answer wins through ``INSERT … ON CONFLICT DO NOTHING``: two devices
answering at once both reach the insert and exactly one row is written. Each change
and its event commit in one transaction, so the push gateway never sees an event for
a state that was not stored.

Event ``seq`` values are taken under one transaction-scoped advisory lock, so they
commit in ``seq`` order: a reader that has seen ``seq`` n can never later find an
uncommitted n-1 commit behind it and skip it (``BIGSERIAL`` alone hands out numbers
before commit, in any order).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from volundr.adapters.outbound.postgres_notifications import scope_clause
from volundr.domain.notifications import NotificationScope
from volundr.domain.question_ports import QuestionRepository
from volundr.domain.questions import (
    AnsweredBy,
    QuestionAnswer,
    QuestionEvent,
    QuestionRecord,
    QuestionState,
)

#: The advisory lock that orders event commits (any constant unique to this table).
EVENT_ORDER_LOCK = 1_932_000_097

_INSERT_EVENT = """INSERT INTO forge_question_events
    (notification_id, owner_id, tenant_id, state, answered_device_id,
     answered_device_label, at)
VALUES ($1, $2, $3, $4, $5, $6, $7)"""


def _device(device_id: str | None, label: str | None) -> AnsweredBy | None:
    if device_id is None:
        return None
    return AnsweredBy(device_id=device_id, device_label=label or "")


def row_to_question(row: asyncpg.Record) -> QuestionRecord:
    answer = None
    if row["answered_at"] is not None:
        answers = row["answers"]
        answer = QuestionAnswer(
            request_id=row["answer_request_id"],
            device=_device(row["answered_device_id"], row["answered_device_label"]),
            content=row["answer_content"],
            answers=json.loads(answers) if isinstance(answers, str) else answers,
            at=row["answered_at"],
        )
    return QuestionRecord(
        notification_id=row["notification_id"],
        owner_id=row["owner_id"],
        tenant_id=row["tenant_id"],
        session_id=row["session_id"],
        state=QuestionState(row["state"]),
        answer=answer,
        closed_at=row["closed_at"],
        closed_by_notification_id=row["closed_by_notification_id"],
    )


def row_to_event(row: asyncpg.Record) -> QuestionEvent:
    return QuestionEvent(
        seq=row["seq"],
        notification_id=row["notification_id"],
        owner_id=row["owner_id"],
        tenant_id=row["tenant_id"],
        state=QuestionState(row["state"]),
        answered_by=_device(row["answered_device_id"], row["answered_device_label"]),
        at=row["at"],
    )


class PostgresQuestionRepository(QuestionRepository):
    def __init__(self, pool: asyncpg.Pool, **_extra: object) -> None:
        self._pool = pool

    async def get(self, notification_id: UUID) -> QuestionRecord | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM forge_question_states WHERE notification_id = $1", notification_id
        )
        return row_to_question(row) if row is not None else None

    async def claim_answer(
        self,
        *,
        notification_id: UUID,
        owner_id: str,
        tenant_id: str | None,
        session_id: UUID | None,
        answer: QuestionAnswer,
    ) -> tuple[QuestionRecord, bool]:
        device = answer.device
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """INSERT INTO forge_question_states
                       (notification_id, owner_id, tenant_id, session_id, state,
                        answer_request_id, answered_device_id, answered_device_label,
                        answer_content, answers, answered_at, updated_at)
                   VALUES ($1, $2, $3, $4, 'answered', $5, $6, $7, $8, $9::jsonb, $10, now())
                   ON CONFLICT (notification_id) DO NOTHING
                   RETURNING *""",
                notification_id,
                owner_id,
                tenant_id,
                session_id,
                answer.request_id,
                device.device_id if device else None,
                device.device_label if device else None,
                answer.content,
                json.dumps(answer.answers) if answer.answers is not None else None,
                answer.at,
            )
            if row is None:
                existing = await conn.fetchrow(
                    "SELECT * FROM forge_question_states WHERE notification_id = $1",
                    notification_id,
                )
                return row_to_question(existing), False
            await conn.execute("SELECT pg_advisory_xact_lock($1)", EVENT_ORDER_LOCK)
            await conn.execute(
                _INSERT_EVENT,
                notification_id,
                owner_id,
                tenant_id,
                QuestionState.ANSWERED.value,
                device.device_id if device else None,
                device.device_label if device else None,
                answer.at,
            )
        return row_to_question(row), True

    async def close(
        self,
        *,
        notification_id: UUID,
        owner_id: str,
        tenant_id: str | None,
        session_id: UUID | None,
        state: QuestionState,
        at: datetime,
        closed_by_notification_id: UUID | None,
    ) -> QuestionRecord | None:
        if not state.closed:
            raise ValueError(f"close() takes resolved or withdrawn, not {state.value}")
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """INSERT INTO forge_question_states
                       (notification_id, owner_id, tenant_id, session_id, state,
                        closed_at, closed_by_notification_id, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, now())
                   ON CONFLICT (notification_id) DO UPDATE
                       SET state = EXCLUDED.state,
                           closed_at = EXCLUDED.closed_at,
                           closed_by_notification_id = EXCLUDED.closed_by_notification_id,
                           updated_at = now()
                       WHERE forge_question_states.state = 'answered'
                   RETURNING *""",
                notification_id,
                owner_id,
                tenant_id,
                session_id,
                state.value,
                at,
                closed_by_notification_id,
            )
            if row is None:
                return None
            await conn.execute("SELECT pg_advisory_xact_lock($1)", EVENT_ORDER_LOCK)
            await conn.execute(
                _INSERT_EVENT, notification_id, owner_id, tenant_id, state.value, None, None, at
            )
        return row_to_question(row)

    async def events_after(
        self, scope: NotificationScope, *, after: int, limit: int
    ) -> list[QuestionEvent]:
        params: list[Any] = []
        clause = scope_clause(scope, params)
        params += [after, limit]
        rows = await self._pool.fetch(
            f"""SELECT * FROM forge_question_events
                WHERE {clause} AND seq > ${len(params) - 1}
                ORDER BY seq ASC LIMIT ${len(params)}""",
            *params,
        )
        return [row_to_event(row) for row in rows]

    async def head_seq(self, scope: NotificationScope) -> int:
        params: list[Any] = []
        clause = scope_clause(scope, params)
        value = await self._pool.fetchval(
            f"SELECT COALESCE(MAX(seq), 0) FROM forge_question_events WHERE {clause}", *params
        )
        return int(value)

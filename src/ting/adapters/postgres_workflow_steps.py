"""Postgres ledger of workflow step activations and run events.

``workflow_steps`` holds one row per step activation and ``workflow_run_events`` is
the append-only log of a run (migration ``000054_workflow_steps``). A row is an offer (kind
``offer``, one per delivery to a stage), a member's pickup of an offer (``stage_member``, with
the ``offer_id`` it took up and when) or a gate, wait or end (migration
``000060_workflow_step_offers``). Every write to a
run goes through ``transaction``, which takes a transaction-scoped advisory lock on
the run, so two decisions about the same run never interleave; sequence numbers are
assigned under that lock.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from ting.domain.workflow_steps import (
    FlagKind,
    RunEvent,
    RunState,
    StepFlag,
    StepInput,
    StepKind,
    StepState,
    WaitingOn,
    WorkflowStep,
)
from ting.ports.workflow_steps import LedgerTransaction, WorkflowStepRepository

_LOCK_NAMESPACE = "workflow-steps"

_UPSERT_STEP = """
INSERT INTO workflow_steps (
    id, run_id, node_id, member, kind, attempt, generation, state, session_id,
    inputs, verdict, outcome, note, evidence, waiting_on,
    deadline_at, created_at, activated_at, closed_at, offer_id, picked_up_at, orchestration
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9,
    $10::jsonb, $11, $12::jsonb, $13, $14::jsonb, $15::jsonb,
    $16, $17, $18, $19, $20, $21, $22
)
ON CONFLICT (id) DO UPDATE SET
    generation = EXCLUDED.generation,
    state = EXCLUDED.state,
    session_id = EXCLUDED.session_id,
    inputs = EXCLUDED.inputs,
    verdict = EXCLUDED.verdict,
    outcome = EXCLUDED.outcome,
    note = EXCLUDED.note,
    evidence = EXCLUDED.evidence,
    waiting_on = EXCLUDED.waiting_on,
    deadline_at = EXCLUDED.deadline_at,
    activated_at = EXCLUDED.activated_at,
    closed_at = EXCLUDED.closed_at,
    offer_id = EXCLUDED.offer_id,
    picked_up_at = EXCLUDED.picked_up_at
"""

_APPEND_EVENT = """
INSERT INTO workflow_run_events (
    run_id, seq, event_type, payload, source_activation_id, generation, created_at
)
SELECT $1::uuid, COALESCE(MAX(seq), 0) + 1, $2::text, $3::jsonb, $4::uuid, $5::integer,
       $6::timestamptz
FROM workflow_run_events
WHERE run_id = $1::uuid
RETURNING run_id, seq, event_type, payload, source_activation_id, generation, created_at
"""

_RAISE_FLAG = """
INSERT INTO workflow_step_flags (
    activation_id, kind, run_id, since, raised_at, detail
) VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (activation_id, kind) DO NOTHING
RETURNING activation_id
"""

_RESOLVE_FLAG = """
UPDATE workflow_step_flags
SET resolved_at = $3, resolution = $4
WHERE activation_id = $1 AND kind = $2 AND resolved_at IS NULL
"""

_CLAIM_FLAG_ACTION = """
UPDATE workflow_step_flags
SET action_at = $3
WHERE activation_id = $1 AND kind = $2 AND action_at IS NULL
RETURNING activation_id
"""

_FINISH_FLAG_ACTION = """
UPDATE workflow_step_flags
SET action_done_at = $3, action_error = $4
WHERE activation_id = $1 AND kind = $2 AND action_at IS NOT NULL
"""

_NOTIFY_FLAG = """
UPDATE workflow_step_flags
SET notified_at = $3
WHERE activation_id = $1 AND kind = $2
"""

_SELECT_STEPS = "SELECT * FROM workflow_steps WHERE run_id = $1 ORDER BY created_at, id"
_SELECT_FLAGS = (
    "SELECT run_id, activation_id, kind, since, raised_at, detail, notified_at, resolved_at, "
    "resolution, action_at, action_done_at, action_error FROM workflow_step_flags "
    "WHERE run_id = $1 ORDER BY raised_at, activation_id, kind"
)
_SELECT_EVENTS = (
    "SELECT run_id, seq, event_type, payload, source_activation_id, generation, created_at "
    "FROM workflow_run_events WHERE run_id = $1 ORDER BY seq"
)


class PostgresWorkflowStepRepository(WorkflowStepRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def transaction(self, run_id: UUID) -> AsyncIterator[LedgerTransaction]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                f"{_LOCK_NAMESPACE}:{run_id}",
            )
            yield _PostgresLedgerTransaction(conn, run_id)

    async def find_step(self, activation_id: UUID) -> WorkflowStep | None:
        row = await self._pool.fetchrow("SELECT * FROM workflow_steps WHERE id = $1", activation_id)
        return _step_from_row(row) if row is not None else None

    async def load(self, run_id: UUID) -> RunState:
        step_rows = await self._pool.fetch(_SELECT_STEPS, run_id)
        event_rows = await self._pool.fetch(_SELECT_EVENTS, run_id)
        flag_rows = await self._pool.fetch(_SELECT_FLAGS, run_id)
        return _state_from_rows(run_id, step_rows, event_rows, flag_rows)


class _PostgresLedgerTransaction(LedgerTransaction):
    def __init__(self, conn: Any, run_id: UUID) -> None:
        self._conn = conn
        self._run_id = run_id

    async def load(self) -> RunState:
        step_rows = await self._conn.fetch(_SELECT_STEPS, self._run_id)
        event_rows = await self._conn.fetch(_SELECT_EVENTS, self._run_id)
        flag_rows = await self._conn.fetch(_SELECT_FLAGS, self._run_id)
        return _state_from_rows(self._run_id, step_rows, event_rows, flag_rows)

    async def save_step(self, step: WorkflowStep) -> None:
        await self._conn.execute(
            _UPSERT_STEP,
            step.id,
            step.run_id,
            step.node_id,
            step.member,
            step.kind.value,
            step.attempt,
            step.generation,
            step.state.value,
            step.session_id,
            json.dumps([item.to_json() for item in step.inputs]),
            step.verdict,
            json.dumps(step.outcome),
            step.note,
            json.dumps(step.evidence),
            json.dumps(step.waiting_on.to_json()) if step.waiting_on is not None else None,
            step.deadline_at,
            step.created_at,
            step.activated_at,
            step.closed_at,
            step.offer_id,
            step.picked_up_at,
            step.orchestration,
        )

    async def append_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        generation: int,
        source_activation_id: UUID | None,
        created_at: datetime,
    ) -> RunEvent:
        row = await self._conn.fetchrow(
            _APPEND_EVENT,
            self._run_id,
            event_type,
            json.dumps(payload),
            source_activation_id,
            generation,
            created_at,
        )
        return _event_from_row(row)

    async def raise_flag(self, flag: StepFlag) -> bool:
        row = await self._conn.fetchrow(
            _RAISE_FLAG,
            flag.activation_id,
            flag.kind.value,
            flag.run_id,
            flag.since,
            flag.raised_at,
            flag.detail,
        )
        return row is not None

    async def resolve_flag(
        self, activation_id: UUID, kind: FlagKind, *, at: datetime, resolution: str
    ) -> None:
        await self._conn.execute(_RESOLVE_FLAG, activation_id, kind.value, at, resolution)

    async def claim_flag_action(self, activation_id: UUID, kind: FlagKind, *, at: datetime) -> bool:
        row = await self._conn.fetchrow(_CLAIM_FLAG_ACTION, activation_id, kind.value, at)
        return row is not None

    async def finish_flag_action(
        self, activation_id: UUID, kind: FlagKind, *, at: datetime, error: str
    ) -> None:
        await self._conn.execute(_FINISH_FLAG_ACTION, activation_id, kind.value, at, error)

    async def mark_flag_notified(
        self, activation_id: UUID, kind: FlagKind, *, at: datetime
    ) -> None:
        await self._conn.execute(_NOTIFY_FLAG, activation_id, kind.value, at)


def _state_from_rows(
    run_id: UUID, step_rows: list[Any], event_rows: list[Any], flag_rows: list[Any]
) -> RunState:
    return RunState(
        run_id=run_id,
        steps=tuple(_step_from_row(row) for row in step_rows),
        events=tuple(_event_from_row(row) for row in event_rows),
        flags=tuple(_flag_from_row(row) for row in flag_rows),
    )


def _flag_from_row(row: Any) -> StepFlag:
    return StepFlag(
        run_id=row["run_id"],
        activation_id=row["activation_id"],
        kind=FlagKind(row["kind"]),
        since=row["since"],
        raised_at=row["raised_at"],
        detail=row["detail"],
        notified_at=row["notified_at"],
        resolved_at=row["resolved_at"],
        resolution=row["resolution"],
        action_at=row["action_at"],
        action_done_at=row["action_done_at"],
        action_error=row["action_error"],
    )


def _step_from_row(row: Any) -> WorkflowStep:
    waiting_raw = _json_value(row["waiting_on"])
    return WorkflowStep(
        id=row["id"],
        run_id=row["run_id"],
        node_id=row["node_id"],
        member=row["member"],
        kind=StepKind(row["kind"]),
        attempt=row["attempt"],
        generation=row["generation"],
        state=StepState(row["state"]),
        created_at=row["created_at"],
        session_id=row["session_id"],
        inputs=tuple(StepInput.from_json(item) for item in _json_value(row["inputs"]) or ()),
        verdict=row["verdict"],
        outcome=dict(_json_value(row["outcome"]) or {}),
        note=row["note"],
        evidence=dict(_json_value(row["evidence"]) or {}),
        waiting_on=WaitingOn.from_json(waiting_raw) if waiting_raw else None,
        deadline_at=row["deadline_at"],
        activated_at=row["activated_at"],
        closed_at=row["closed_at"],
        offer_id=row["offer_id"],
        picked_up_at=row["picked_up_at"],
        orchestration=row["orchestration"],
    )


def _event_from_row(row: Any) -> RunEvent:
    return RunEvent(
        run_id=row["run_id"],
        seq=row["seq"],
        event_type=row["event_type"],
        generation=row["generation"],
        created_at=row["created_at"],
        payload=dict(_json_value(row["payload"]) or {}),
        source_activation_id=row["source_activation_id"],
    )


def _json_value(raw: Any) -> Any:
    """A jsonb column as Python: asyncpg returns text unless a codec is registered."""
    if isinstance(raw, str):
        return json.loads(raw)
    return raw

"""Postgres durable ledger for exact remote delivery observations."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import asyncpg

from ting.delivery.domain import DELIVERY_WAIT_FAILURE_PREFIX, DeliveryExecution
from ting.domain.workflow_execution import TERMINAL_EXECUTION_STATES, WorkflowExecutionError
from ting.domain.workflow_wait import (
    WaitObservation,
    WaitObservationStatus,
    WorkflowWait,
    WorkflowWaitRequest,
    WorkflowWaitState,
    delivery_candidate_digest,
)
from ting.ports.workflow_wait import WorkflowWaitRepository


class PostgresWorkflowWaitRepository(WorkflowWaitRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def reserve(
        self,
        execution: DeliveryExecution,
        request: WorkflowWaitRequest,
        *,
        wait_id: UUID,
        request_digest: str,
        candidate_digest: str,
        next_poll_at: datetime,
    ) -> WorkflowWait:
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "SELECT * FROM workflow_executions WHERE id = $1 FOR UPDATE",
                execution.id,
            )
            if current is None:
                raise WorkflowExecutionError("Developer execution no longer exists")
            current_candidate = _json_dict(current.get("integration_candidate"))
            current_allocation = _json_dict(current.get("integration_allocation"))
            current_digest = delivery_candidate_digest(
                integration_candidate=current_candidate,
                integration_allocation=current_allocation,
            )
            if (
                current["revision"] != execution.revision
                or current["current_generation"] != execution.current_generation
                or current_digest != candidate_digest
                or current["cancel_requested"]
                or current["state"] in {state.value for state in TERMINAL_EXECUTION_STATES}
            ):
                raise WorkflowExecutionError(
                    "Developer execution changed while reserving its delivery wait"
                )
            active = await conn.fetchrow(
                """
                SELECT * FROM workflow_waits
                WHERE execution_id = $1 AND execution_generation = $2
                  AND state <> 'notified'
                FOR UPDATE
                """,
                execution.id,
                execution.current_generation,
            )
            if active is not None:
                if active["request_digest"] != request_digest:
                    raise WorkflowExecutionError(
                        "A different delivery observation is already active for this execution"
                    )
                return _wait_from_row(active)
            existing = await conn.fetchrow(
                """
                SELECT * FROM workflow_waits
                WHERE execution_id = $1 AND request_digest = $2
                """,
                execution.id,
                request_digest,
            )
            if existing is not None:
                return _wait_from_row(existing)
            row = await conn.fetchrow(
                """
                INSERT INTO workflow_waits (
                    id, execution_id, execution_generation, execution_revision,
                    candidate_digest, mode, request_digest, request, state, next_poll_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, 'pending', $9)
                RETURNING *
                """,
                wait_id,
                execution.id,
                execution.current_generation,
                execution.revision,
                candidate_digest,
                request.mode.value,
                request_digest,
                json.dumps(request.canonical_payload(), sort_keys=True),
                next_poll_at,
            )
            result = await conn.execute(
                """
                UPDATE workflow_executions
                SET state = 'waiting', suspension_reason = $2,
                    revision = revision + 1, updated_at = NOW()
                WHERE id = $1 AND revision = $3
                """,
                execution.id,
                ("awaiting_checks" if request.mode.value == "checks" else "awaiting_merge"),
                execution.revision,
            )
            _require_updated(result, "Developer execution changed while entering delivery wait")
        return _wait_from_row(row)

    async def list_for_execution(self, execution_id: UUID) -> list[WorkflowWait]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM workflow_waits
            WHERE execution_id = $1
            ORDER BY created_at, id
            """,
            execution_id,
        )
        return [_wait_from_row(row) for row in rows]

    async def claim_due(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[WorkflowWait]:
        rows = await self._pool.fetch(
            """
            WITH due AS (
                SELECT id
                FROM workflow_waits
                WHERE state IN ('pending', 'ready', 'failed')
                  AND (state <> 'pending' OR next_poll_at <= $1)
                  AND (lease_expires_at IS NULL OR lease_expires_at <= $1)
                ORDER BY next_poll_at, created_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT $2
            )
            UPDATE workflow_waits AS waits
            SET lease_owner = $3,
                lease_token = gen_random_uuid(),
                lease_expires_at = $4,
                fencing_generation = waits.fencing_generation + 1,
                updated_at = NOW()
            FROM due
            WHERE waits.id = due.id
            RETURNING waits.*
            """,
            now,
            limit,
            worker_id,
            lease_until,
        )
        return [_wait_from_row(row) for row in rows]

    async def record_pending(
        self,
        wait: WorkflowWait,
        observation: WaitObservation | None,
        *,
        next_poll_at: datetime,
        error: str = "",
    ) -> None:
        result = await self._pool.execute(
            """
            UPDATE workflow_waits
            SET observation = $2::jsonb,
                next_poll_at = $3,
                attempt_count = attempt_count + 1,
                last_error = $4,
                lease_owner = '', lease_token = NULL, lease_expires_at = NULL,
                updated_at = NOW()
            WHERE id = $1 AND state = 'pending'
              AND lease_token = $5 AND fencing_generation = $6
            """,
            wait.id,
            _observation_json(observation),
            next_poll_at,
            error,
            wait.lease_token,
            wait.fencing_generation,
        )
        _require_updated(result, "delivery wait lease is no longer current")

    async def record_terminal(
        self,
        wait: WorkflowWait,
        observation: WaitObservation,
        *,
        expected_execution_revision: int,
        project_execution: bool,
    ) -> WorkflowWait:
        if not observation.terminal:
            raise WorkflowExecutionError("Cannot terminally record a pending delivery observation")
        state = (
            WorkflowWaitState.READY
            if observation.status
            in {
                WaitObservationStatus.CHECKS_PASSED,
                WaitObservationStatus.MERGED,
            }
            else WorkflowWaitState.FAILED
        )
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE workflow_waits
                SET state = $2,
                    observation = $3::jsonb,
                    attempt_count = attempt_count + 1,
                    last_error = '',
                    updated_at = NOW()
                WHERE id = $1 AND state = 'pending'
                  AND lease_token = $4 AND fencing_generation = $5
                RETURNING *
                """,
                wait.id,
                state.value,
                _observation_json(observation),
                wait.lease_token,
                wait.fencing_generation,
            )
            if row is None:
                raise WorkflowExecutionError("delivery wait lease is no longer current")
            if project_execution:
                result = await conn.execute(
                    """
                    UPDATE workflow_executions
                    SET suspension_reason = $2, revision = revision + 1,
                        updated_at = NOW()
                    WHERE id = $1 AND current_generation = $3
                      AND revision = $4
                      AND cancel_requested = FALSE
                      AND state NOT IN ('canceled', 'completed', 'failed')
                    """,
                    wait.execution_id,
                    (
                        "delivery_observed"
                        if state is WorkflowWaitState.READY
                        else f"{DELIVERY_WAIT_FAILURE_PREFIX}{observation.reason}"
                    ),
                    wait.execution_generation,
                    expected_execution_revision,
                )
                _require_updated(
                    result,
                    "Developer execution changed while recording its terminal delivery "
                    "observation; the wait stays pending and is observed again",
                )
        return _wait_from_row(row)

    async def mark_notified(self, wait: WorkflowWait) -> None:
        result = await self._pool.execute(
            """
            UPDATE workflow_waits
            SET state = 'notified', notified_at = NOW(),
                lease_owner = '', lease_token = NULL, lease_expires_at = NULL,
                updated_at = NOW()
            WHERE id = $1 AND state IN ('ready', 'failed')
              AND lease_token = $2 AND fencing_generation = $3
            """,
            wait.id,
            wait.lease_token,
            wait.fencing_generation,
        )
        _require_updated(result, "delivery wait notification lease is no longer current")


def _wait_from_row(row) -> WorkflowWait:
    request_raw = row["request"]
    if isinstance(request_raw, str):
        request_raw = json.loads(request_raw)
    observation_raw = row["observation"]
    if isinstance(observation_raw, str):
        observation_raw = json.loads(observation_raw)
    return WorkflowWait(
        id=row["id"],
        execution_id=row["execution_id"],
        request=WorkflowWaitRequest.model_validate(request_raw),
        request_digest=row["request_digest"],
        execution_generation=int(row["execution_generation"]),
        execution_revision=int(row["execution_revision"]),
        candidate_digest=row["candidate_digest"],
        state=WorkflowWaitState(row["state"]),
        next_poll_at=row["next_poll_at"],
        observation=(WaitObservation.model_validate(observation_raw) if observation_raw else None),
        attempt_count=row["attempt_count"],
        last_error=row["last_error"],
        lease_owner=row["lease_owner"],
        lease_token=row["lease_token"],
        fencing_generation=row["fencing_generation"],
        lease_expires_at=row["lease_expires_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        notified_at=row["notified_at"],
    )


def _observation_json(observation: WaitObservation | None) -> str | None:
    return observation.model_dump_json() if observation is not None else None


def _require_updated(result: str, message: str) -> None:
    if result != "UPDATE 1":
        raise WorkflowExecutionError(message)


def _json_dict(value) -> dict | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value)

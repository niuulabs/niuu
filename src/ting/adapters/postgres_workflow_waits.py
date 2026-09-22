"""Postgres durable ledger for condition-agnostic workflow waits."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from ting.domain.workflow_execution import (
    TERMINAL_EXECUTION_STATES,
    WorkflowExecution,
    WorkflowExecutionError,
)
from ting.domain.workflow_wait import (
    WAIT_FAILURE_PREFIX,
    WAIT_OBSERVED_SUSPENSION_REASON,
    WaitObservation,
    WaitObservationStatus,
    WorkflowWait,
    WorkflowWaitState,
    wait_suspension_reason,
)
from ting.ports.workflow_wait import WorkflowWaitRepository


class PostgresWorkflowWaitRepository(WorkflowWaitRepository):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def reserve(
        self,
        execution: WorkflowExecution,
        *,
        wait_id: UUID,
        node_id: str,
        condition_type: str,
        request: dict[str, Any],
        request_digest: str,
        next_poll_at: datetime,
    ) -> WorkflowWait:
        async with self._pool.acquire() as conn, conn.transaction():
            current = await conn.fetchrow(
                "SELECT * FROM workflow_executions WHERE id = $1 FOR UPDATE",
                execution.id,
            )
            if current is None:
                raise WorkflowExecutionError("Execution no longer exists")
            if (
                current["revision"] != execution.revision
                or current["current_generation"] != execution.current_generation
                or current["cancel_requested"]
                or current["state"] in {state.value for state in TERMINAL_EXECUTION_STATES}
            ):
                raise WorkflowExecutionError("Execution changed while reserving its wait")
            active = await conn.fetchrow(
                """
                SELECT * FROM workflow_waits
                WHERE execution_id = $1 AND execution_generation = $2 AND node_id = $3
                  AND state <> 'notified'
                FOR UPDATE
                """,
                execution.id,
                execution.current_generation,
                node_id,
            )
            if active is not None:
                if active["request_digest"] != request_digest:
                    raise WorkflowExecutionError(
                        "A different wait is already active for this wait node"
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
                    node_id, condition_type, request_digest, request, state, next_poll_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, 'pending', $9)
                RETURNING *
                """,
                wait_id,
                execution.id,
                execution.current_generation,
                execution.revision,
                node_id,
                condition_type,
                request_digest,
                json.dumps(request, sort_keys=True),
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
                wait_suspension_reason(condition_type),
                execution.revision,
            )
            _require_updated(result, "Execution changed while entering its wait")
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
        _require_updated(result, "wait lease is no longer current")

    async def record_terminal(
        self,
        wait: WorkflowWait,
        observation: WaitObservation,
        *,
        expected_execution_revision: int,
        project_execution: bool,
    ) -> WorkflowWait:
        if not observation.terminal:
            raise WorkflowExecutionError("Cannot terminally record a pending wait observation")
        state = (
            WorkflowWaitState.READY
            if observation.status is WaitObservationStatus.SATISFIED
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
                raise WorkflowExecutionError("wait lease is no longer current")
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
                        WAIT_OBSERVED_SUSPENSION_REASON
                        if state is WorkflowWaitState.READY
                        else f"{WAIT_FAILURE_PREFIX}{observation.reason}"
                    ),
                    wait.execution_generation,
                    expected_execution_revision,
                )
                _require_updated(
                    result,
                    "Execution changed while recording its terminal wait observation; the "
                    "wait stays pending and is observed again",
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
        _require_updated(result, "wait notification lease is no longer current")


def _wait_from_row(row) -> WorkflowWait:
    request_raw = row["request"]
    if isinstance(request_raw, str):
        request_raw = json.loads(request_raw)
    observation_raw = row["observation"]
    if isinstance(observation_raw, str):
        observation_raw = json.loads(observation_raw)
    observation = None
    if observation_raw:
        observation = WaitObservation(
            status=WaitObservationStatus(observation_raw["status"]),
            observed_at=datetime.fromisoformat(observation_raw["observedAt"]),
            reason=observation_raw.get("reason", ""),
            detail=observation_raw.get("detail") or {},
        )
    return WorkflowWait(
        id=row["id"],
        execution_id=row["execution_id"],
        node_id=row["node_id"],
        condition_type=row["condition_type"],
        request=dict(request_raw),
        request_digest=row["request_digest"],
        execution_generation=int(row["execution_generation"]),
        execution_revision=int(row["execution_revision"]),
        state=WorkflowWaitState(row["state"]),
        next_poll_at=row["next_poll_at"],
        observation=observation,
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
    return json.dumps(observation.to_dict(), sort_keys=True) if observation is not None else None


def _require_updated(result: str, message: str) -> None:
    if result != "UPDATE 1":
        raise WorkflowExecutionError(message)

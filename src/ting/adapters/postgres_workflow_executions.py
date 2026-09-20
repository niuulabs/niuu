"""PostgreSQL execution ledger for durable, domain-neutral workflow expansion.

Only domain-neutral columns live here: ``workflow_executions``,
``workflow_execution_generations``, ``workflow_execution_children``,
``workflow_child_events``, and ``workflow_child_messages``. A workflow pack
that needs its own persisted fields layers an extension table over this
ledger by subclassing ``PostgresWorkflowExecutionRepository`` and overriding
its row/object mapping hooks — see ``ting.delivery.postgres`` for the one the
code delivery pack uses.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg

from ting.domain.workflow_execution import (
    TERMINAL_CHILD_STATES,
    ChildExecutionState,
    ChildMessage,
    ChildPendingGate,
    ChildPendingQuestion,
    ChildTaskHandle,
    ChildTaskObservation,
    ExecutionBudget,
    ExecutionConflictError,
    ExecutionState,
    ExpansionPolicy,
    FailureKind,
    WorkflowChildExecution,
    WorkflowExecution,
    calculate_join,
)
from ting.ports.workflow_execution import WorkflowExecutionRepository


class PostgresWorkflowExecutionRepository[
    ExecutionT: WorkflowExecution,
    ChildT: WorkflowChildExecution,
](WorkflowExecutionRepository[ExecutionT, ChildT]):
    """Raw-asyncpg ledger with transactional budget and fenced launch writes."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- overridable row/object mapping hooks --------------------------------
    #
    # A specialization overrides these to attach its own extension-table
    # columns without duplicating any of the generic ledger logic below.

    async def _to_execution(
        self, row, *, connection: asyncpg.Connection | None = None
    ) -> ExecutionT:
        del connection
        return _generic_execution_from_row(row)  # type: ignore[return-value]

    async def _to_executions(
        self, rows: list, *, connection: asyncpg.Connection | None = None
    ) -> list[ExecutionT]:
        return [await self._to_execution(row, connection=connection) for row in rows]

    def _to_child(self, row) -> ChildT:
        return _generic_child_from_row(row)  # type: ignore[return-value]

    async def _after_create(self, connection: asyncpg.Connection, execution: ExecutionT) -> None:
        """Extension point: persist a specialization's own row in the same transaction."""

    async def _after_reserve_generation(
        self, connection: asyncpg.Connection, execution_id: UUID
    ) -> None:
        """Extension point: reset a specialization's own per-generation state."""

    # -- lifecycle ------------------------------------------------------------

    async def create(self, execution: ExecutionT) -> ExecutionT:
        try:
            async with self._pool.acquire() as connection, connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO workflow_executions (
                        id, owner_id, tenant_id, name, prompt, workflow_id,
                        workflow_revision, workflow_digest,
                        parent_session_id, parent_node_id, connection_id, state,
                        current_generation, total_budget, reserved_budget, spent_budget,
                        deadline, suspension_reason, cancel_requested, policy, input,
                        revision, launch_key, launch_digest, plan_revision,
                        created_at, updated_at
                    ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                        $17,$18,$19,$20::jsonb,$21::jsonb,$22,$23,$24,$25,$26,$27
                    )
                    """,
                    execution.id,
                    execution.owner_id,
                    execution.tenant_id,
                    execution.name,
                    execution.prompt,
                    execution.workflow_id,
                    execution.workflow_revision,
                    execution.workflow_digest,
                    execution.parent_session_id,
                    execution.parent_node_id,
                    execution.connection_id,
                    execution.state.value,
                    execution.current_generation,
                    execution.budget.total_units,
                    execution.budget.reserved_units,
                    execution.budget.spent_units,
                    execution.deadline,
                    execution.suspension_reason,
                    execution.cancel_requested,
                    json.dumps(_policy_to_json(execution.policy)),
                    json.dumps(execution.input),
                    execution.revision,
                    execution.launch_key,
                    execution.launch_digest,
                    execution.plan_revision,
                    execution.created_at,
                    execution.updated_at,
                )
                await self._after_create(connection, execution)
        except asyncpg.UniqueViolationError as exc:
            raise ExecutionConflictError(f"execution {execution.id} already exists") from exc
        return execution

    async def reserve_parent_launch(self, execution: ExecutionT) -> tuple[ExecutionT, bool]:
        if not execution.launch_key or not execution.launch_digest:
            raise ExecutionConflictError("parent launch requires an idempotency key and digest")
        try:
            await self.create(execution)
            return execution, True
        except ExecutionConflictError:
            row = await self._pool.fetchrow(
                """
                SELECT * FROM workflow_executions
                WHERE owner_id = $1 AND tenant_id = $2 AND launch_key = $3
                """,
                execution.owner_id,
                execution.tenant_id,
                execution.launch_key,
            )
            if row is None:
                raise
            existing = await self._to_execution(row)
            if existing.launch_digest != execution.launch_digest:
                raise ExecutionConflictError(
                    "Idempotency-Key was reused for a different workflow execution launch"
                )
            return existing, False

    async def attach_parent_session(
        self,
        execution_id: UUID,
        *,
        session_id: str,
        connection_id: str,
    ) -> ExecutionT:
        if not session_id.strip():
            raise ExecutionConflictError("parent session id is required")
        row = await self._pool.fetchrow(
            """
            UPDATE workflow_executions
            SET parent_session_id = $2,
                connection_id = $3,
                state = CASE WHEN state = 'pending' THEN 'running' ELSE state END,
                suspension_reason = CASE
                    WHEN suspension_reason = 'launching_parent' THEN ''
                    ELSE suspension_reason
                END,
                revision = revision + 1,
                updated_at = NOW()
            WHERE id = $1 AND (parent_session_id = '' OR parent_session_id = $2)
            RETURNING *
            """,
            execution_id,
            session_id,
            connection_id,
        )
        if row is None:
            raise ExecutionConflictError("a different parent session is already attached")
        return await self._to_execution(row)

    async def get(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> ExecutionT | None:
        row = await self._pool.fetchrow(
            """
            SELECT * FROM workflow_executions
            WHERE id = $1 AND owner_id = $2 AND tenant_id = $3
            """,
            execution_id,
            owner_id,
            tenant_id,
        )
        return await self._to_execution(row) if row is not None else None

    async def get_internal(self, execution_id: UUID) -> ExecutionT | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM workflow_executions WHERE id = $1",
            execution_id,
        )
        return await self._to_execution(row) if row is not None else None

    async def get_by_parent_session(
        self,
        *,
        owner_id: str,
        session_id: str,
    ) -> ExecutionT | None:
        row = await self._pool.fetchrow(
            """
            SELECT * FROM workflow_executions
            WHERE owner_id = $1 AND parent_session_id = $2
              AND state NOT IN ('canceled', 'completed', 'failed')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            owner_id,
            session_id,
        )
        return await self._to_execution(row) if row is not None else None

    async def list(
        self,
        *,
        owner_id: str,
        tenant_id: str,
        state: str = "",
        limit: int,
        cursor: str = "",
    ) -> tuple[list[ExecutionT], str]:
        cursor_time, cursor_id = _decode_cursor(cursor)
        rows = await self._pool.fetch(
            """
            SELECT *
            FROM workflow_executions
            WHERE owner_id = $1 AND tenant_id = $2
              AND ($3 = '' OR state = $3)
              AND (
                  $4::timestamptz IS NULL
                  OR (updated_at, id) < ($4::timestamptz, $5::uuid)
              )
            ORDER BY updated_at DESC, id DESC
            LIMIT $6
            """,
            owner_id,
            tenant_id,
            state,
            cursor_time,
            cursor_id,
            limit + 1,
        )
        selected = rows[:limit]
        next_cursor = ""
        if len(rows) > limit and selected:
            last = selected[-1]
            next_cursor = f"{last['updated_at'].isoformat()}|{last['id']}"
        return await self._to_executions(selected), next_cursor

    async def reserve_generation(
        self,
        execution: ExecutionT,
        children: tuple[ChildT, ...],
        *,
        plan_revision: str,
    ) -> ExecutionT:
        if not children:
            raise ExecutionConflictError("cannot reserve an empty generation")
        if not plan_revision.strip():
            raise ExecutionConflictError("plan_revision is required for generation reservation")
        generation = children[0].generation
        reservation = sum(child.budget_units for child in children)
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                "SELECT * FROM workflow_executions WHERE id = $1 FOR UPDATE",
                execution.id,
            )
            if row is None:
                raise ExecutionConflictError(f"execution {execution.id} does not exist")
            current = await self._to_execution(row, connection=connection)
            if current.revision != execution.revision:
                raise ExecutionConflictError("execution changed while expansion was being prepared")
            if current.cancel_requested or current.state in {
                ExecutionState.CANCELED,
                ExecutionState.COMPLETED,
                ExecutionState.FAILED,
            }:
                raise ExecutionConflictError("execution no longer accepts child reservations")
            if generation != current.current_generation + 1:
                raise ExecutionConflictError("generation is no longer current")
            if reservation > current.budget.available_units:
                raise ExecutionConflictError("budget was consumed by another reservation")
            if current.plan_revision and current.plan_revision != plan_revision:
                raise ExecutionConflictError(
                    "plan_revision differs from the durable execution plan"
                )
            if current.current_generation and not current.plan_revision:
                raise ExecutionConflictError(
                    "existing execution has no durable plan_revision and must be replanned"
                )

            await connection.execute(
                """
                INSERT INTO workflow_execution_generations (
                    execution_id, generation, sealed, sealed_at
                ) VALUES ($1, $2, TRUE, NOW())
                """,
                execution.id,
                generation,
            )
            await connection.executemany(
                """
                INSERT INTO workflow_execution_children (
                    id, execution_id, generation, child_key, attempt, state,
                    dependencies, objective, template_id,
                    template_revision, template_digest, plan_digest, input_digest,
                    input, budget_units, deadline, agent_id,
                    skill_id, intent_id, message_id, created_at, updated_at
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,
                    $15,$16,$17,$18,$19,$20,$21,$22
                )
                """,
                [_child_insert_values(child) for child in children],
            )
            updated_row = await connection.fetchrow(
                """
                UPDATE workflow_executions
                SET current_generation = $2,
                    reserved_budget = reserved_budget + $3,
                    plan_revision = $4,
                    state = 'waiting',
                    suspension_reason = 'awaiting_children',
                    revision = revision + 1,
                    updated_at = NOW()
                WHERE id = $1
                RETURNING *
                """,
                execution.id,
                generation,
                reservation,
                plan_revision,
            )
            await self._after_reserve_generation(connection, execution.id)
            result = await self._to_execution(updated_row, connection=connection)
        return result

    async def list_children(self, execution_id: UUID) -> list[ChildT]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM workflow_execution_children
            WHERE execution_id = $1
            ORDER BY generation, child_key, attempt
            """,
            execution_id,
        )
        return [self._to_child(row) for row in rows]

    async def get_child(self, child_id: UUID) -> ChildT | None:
        row = await self._pool.fetchrow(
            "SELECT * FROM workflow_execution_children WHERE id = $1",
            child_id,
        )
        return self._to_child(row) if row is not None else None

    async def list_reconcilable(self, *, limit: int) -> list[ChildT]:
        rows = await self._pool.fetch(
            """
            SELECT child.*
            FROM workflow_execution_children child
            JOIN workflow_executions execution ON execution.id = child.execution_id
            WHERE child.task_id <> ''
              AND child.state IN ('submitted', 'running', 'waiting', 'blocked', 'canceling')
              AND (
                    execution.state NOT IN ('canceled', 'completed', 'failed')
                    OR (
                        execution.state IN ('canceled', 'failed')
                        AND execution.cancel_requested = TRUE
                        AND child.state = 'canceling'
                    )
              )
            ORDER BY COALESCE(child.last_polled_at, child.created_at), child.id
            LIMIT $1
            """,
            limit,
        )
        return [self._to_child(row) for row in rows]

    async def list_parent_stop_pending(self, *, limit: int) -> list[ExecutionT]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM workflow_executions
            WHERE parent_stop_requested_at IS NOT NULL
              AND parent_stopped_at IS NULL
            ORDER BY parent_stop_attempts, parent_stop_requested_at, id
            LIMIT $1
            """,
            limit,
        )
        return await self._to_executions(rows)

    async def list_deadline_expired_children(self, *, now: datetime, limit: int) -> list[ChildT]:
        rows = await self._pool.fetch(
            """
            SELECT child.*
            FROM workflow_execution_children child
            JOIN workflow_executions execution ON execution.id = child.execution_id
            WHERE child.deadline < $1
              AND child.state NOT IN ('canceled', 'completed', 'failed', 'superseded')
              AND execution.state NOT IN ('canceled', 'completed', 'failed')
            ORDER BY child.deadline, child.id
            LIMIT $2
            """,
            now,
            limit,
        )
        return [self._to_child(row) for row in rows]

    async def list_deadline_expired_executions(
        self, *, now: datetime, limit: int
    ) -> list[ExecutionT]:
        rows = await self._pool.fetch(
            """
            SELECT * FROM workflow_executions
            WHERE deadline < $1
              AND state NOT IN ('canceled', 'completed', 'failed')
            ORDER BY deadline, id
            LIMIT $2
            """,
            now,
            limit,
        )
        return await self._to_executions(rows)

    async def mark_parent_stopped(self, execution_id: UUID) -> None:
        await self._pool.execute(
            """
            UPDATE workflow_executions
            SET parent_stopped_at = COALESCE(parent_stopped_at, NOW()),
                parent_stop_error = '',
                updated_at = NOW()
            WHERE id = $1 AND parent_stop_requested_at IS NOT NULL
            """,
            execution_id,
        )

    async def record_parent_stop_error(
        self,
        execution_id: UUID,
        *,
        error: str,
        max_attempts: int,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        await self._pool.execute(
            """
            UPDATE workflow_executions
            SET parent_stop_attempts = parent_stop_attempts + 1,
                parent_stop_error = $2,
                parent_stopped_at = CASE
                    WHEN parent_stop_attempts + 1 >= $3 THEN NOW()
                    ELSE parent_stopped_at
                END,
                updated_at = NOW()
            WHERE id = $1 AND parent_stop_requested_at IS NOT NULL AND parent_stopped_at IS NULL
            """,
            execution_id,
            error,
            max_attempts,
        )

    async def claim_launches(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_until: datetime,
    ) -> list[ChildT]:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        lease_token = uuid4()
        claimed_rows = []
        async with self._pool.acquire() as connection, connection.transaction():
            executions = await connection.fetch(
                """
                SELECT execution.*
                FROM workflow_executions execution
                WHERE execution.deadline > NOW()
                  AND execution.cancel_requested = FALSE
                  AND execution.state IN ('pending', 'running', 'waiting')
                  AND (execution.policy ->> 'maxActiveChildren')::INTEGER > (
                      SELECT COUNT(*)
                      FROM workflow_execution_children active
                      WHERE active.execution_id = execution.id
                        AND (
                            active.state IN (
                                'submitted', 'running', 'waiting', 'blocked', 'canceling'
                            )
                            OR (
                                active.state = 'launching'
                                AND active.lease_expires_at >= NOW()
                            )
                        )
                  )
                  AND EXISTS (
                      SELECT 1 FROM workflow_execution_children child
                      WHERE child.execution_id = execution.id
                        AND child.state IN ('reserved', 'launching')
                        AND (child.state = 'reserved' OR child.lease_expires_at < NOW())
                        AND child.deadline > NOW()
                  )
                ORDER BY execution.updated_at, execution.id
                FOR UPDATE OF execution SKIP LOCKED
                LIMIT $1
                """,
                limit,
            )
            remaining = limit
            for execution in executions:
                policy = _policy_from_json(execution["policy"])
                active = int(
                    await connection.fetchval(
                        """
                        SELECT COUNT(*)
                        FROM workflow_execution_children
                        WHERE execution_id = $1
                          AND (
                              state IN ('submitted', 'running', 'waiting', 'blocked', 'canceling')
                              OR (state = 'launching' AND lease_expires_at >= NOW())
                          )
                        """,
                        execution["id"],
                    )
                    or 0
                )
                slots = min(policy.max_active_children - active, remaining)
                if slots <= 0:
                    continue
                rows = await connection.fetch(
                    """
                    WITH claimable AS (
                        SELECT child.id
                        FROM workflow_execution_children child
                        WHERE child.execution_id = $1
                          AND child.state IN ('reserved', 'launching')
                          AND (child.state = 'reserved' OR child.lease_expires_at < NOW())
                          AND child.deadline > NOW()
                          AND NOT EXISTS (
                              SELECT 1
                              FROM unnest(child.dependencies) dependency(child_key)
                              WHERE NOT EXISTS (
                                  SELECT 1
                                  FROM workflow_execution_children prerequisite
                                  WHERE prerequisite.execution_id = child.execution_id
                                    AND prerequisite.generation = child.generation
                                    AND prerequisite.child_key = dependency.child_key
                                    AND prerequisite.state = 'completed'
                                    AND prerequisite.attempt = (
                                        SELECT MAX(latest.attempt)
                                        FROM workflow_execution_children latest
                                        WHERE latest.execution_id = prerequisite.execution_id
                                          AND latest.generation = prerequisite.generation
                                          AND latest.child_key = prerequisite.child_key
                                    )
                              )
                          )
                        ORDER BY child.created_at, child.child_key
                        FOR UPDATE OF child SKIP LOCKED
                        LIMIT $2
                    )
                    UPDATE workflow_execution_children child
                    SET state = 'launching', lease_owner = $3, lease_token = $4,
                        lease_expires_at = $5, fencing_generation = fencing_generation + 1,
                        updated_at = NOW()
                    FROM claimable
                    WHERE child.id = claimable.id
                    RETURNING child.*
                    """,
                    execution["id"],
                    slots,
                    worker_id,
                    lease_token,
                    lease_until,
                )
                claimed_rows.extend(rows)
                remaining -= len(rows)
                if remaining <= 0:
                    break
        return [self._to_child(row) for row in claimed_rows]

    async def record_handle(
        self,
        child: ChildT,
        handle: ChildTaskHandle,
        *,
        worker_id: str,
        lease_token: UUID,
        fencing_generation: int,
    ) -> ChildT:
        row = await self._pool.fetchrow(
            """
            UPDATE workflow_execution_children
            SET state = 'submitted', task_id = $2, context_id = $3,
                lease_owner = '', lease_token = NULL, lease_expires_at = NULL,
                updated_at = NOW()
            WHERE id = $1 AND state = 'launching' AND lease_owner = $4
              AND lease_token = $5 AND fencing_generation = $6
            RETURNING *
            """,
            child.id,
            handle.task_id,
            handle.context_id,
            worker_id,
            lease_token,
            fencing_generation,
        )
        if row is None:
            row = await self._pool.fetchrow(
                """
                UPDATE workflow_execution_children child
                SET state = 'canceling', task_id = $2, context_id = $3,
                    lease_owner = '', lease_token = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                FROM workflow_executions execution
                WHERE child.id = $1 AND child.execution_id = execution.id
                  AND child.state = 'canceled' AND child.task_id = ''
                  AND child.fencing_generation = $4
                  AND execution.cancel_requested = TRUE
                  AND execution.state IN ('canceling', 'canceled', 'failed')
                RETURNING child.*
                """,
                child.id,
                handle.task_id,
                handle.context_id,
                fencing_generation,
            )
        if row is None:
            raise ExecutionConflictError("launch lease expired or was fenced")
        return self._to_child(row)

    async def record_observation(
        self,
        child: ChildT,
        observation: ChildTaskObservation,
    ) -> ChildT:
        payload = {
            "taskId": observation.handle.task_id,
            "contextId": observation.handle.context_id,
            "result": observation.result,
            "artifacts": list(observation.artifacts),
            "failureKind": observation.failure_kind.value if observation.failure_kind else None,
            "error": observation.error,
            "pendingQuestions": [item.to_a2a_metadata() for item in observation.pending_questions],
            "pendingGates": [item.to_a2a_metadata() for item in observation.pending_gates],
        }
        async with self._pool.acquire() as connection, connection.transaction():
            parent = await connection.fetchrow(
                """
                SELECT id, cancel_requested FROM workflow_executions
                WHERE id = $1
                FOR UPDATE
                """,
                child.execution_id,
            )
            if parent is None:
                raise ExecutionConflictError("child parent execution no longer exists")
            inserted = await connection.execute(
                """
                INSERT INTO workflow_child_events (child_id, event_id, observed_at, state, payload)
                VALUES ($1,$2,$3,$4,$5::jsonb)
                ON CONFLICT (child_id, event_id) DO NOTHING
                """,
                child.id,
                observation.event_id,
                observation.observed_at,
                observation.state.value,
                json.dumps(payload),
            )
            if inserted == "INSERT 0 0":
                row = await connection.fetchrow(
                    """
                    UPDATE workflow_execution_children
                    SET last_polled_at = NOW(), reconcile_failure_count = 0
                    WHERE id = $1
                    RETURNING *
                    """,
                    child.id,
                )
                if row is None:
                    raise ExecutionConflictError("child no longer exists")
                return self._to_child(row)
            current_row = await connection.fetchrow(
                "SELECT * FROM workflow_execution_children WHERE id = $1 FOR UPDATE",
                child.id,
            )
            if current_row is None:
                raise ExecutionConflictError("child no longer exists")
            current = self._to_child(current_row)
            remote_observed_at = current_row.get("remote_observed_at")
            if current.state in TERMINAL_CHILD_STATES or (
                remote_observed_at is not None and observation.observed_at < remote_observed_at
            ):
                row = await connection.fetchrow(
                    """
                    UPDATE workflow_execution_children
                    SET last_polled_at = NOW(), reconcile_failure_count = 0
                    WHERE id = $1
                    RETURNING *
                    """,
                    child.id,
                )
                return self._to_child(row)
            projected_state = observation.state
            if (
                parent["cancel_requested"]
                and current.state == ChildExecutionState.CANCELING
                and observation.state not in TERMINAL_CHILD_STATES
            ):
                projected_state = ChildExecutionState.CANCELING
            current_failure = current.failure_kind.value if current.failure_kind else None
            next_failure = observation.failure_kind.value if observation.failure_kind else None
            blocker_states = {ChildExecutionState.BLOCKED, ChildExecutionState.FAILED}
            blocker_changed = (
                current.state in blocker_states or projected_state in blocker_states
            ) and (
                current.state.value,
                current_failure,
                current.error,
                current.pending_questions,
                current.pending_gates,
            ) != (
                projected_state.value,
                next_failure,
                observation.error,
                observation.pending_questions,
                observation.pending_gates,
            )
            row = await connection.fetchrow(
                """
                UPDATE workflow_execution_children
                SET state = $2, result = $3::jsonb, artifacts = $4::jsonb,
                    failure_kind = $5, error = $6, remote_observed_at = $7,
                    pending_questions = $8::jsonb, pending_gates = $9::jsonb,
                    last_polled_at = NOW(), updated_at = NOW(), reconcile_failure_count = 0
                WHERE id = $1
                RETURNING *
                """,
                child.id,
                projected_state.value,
                json.dumps(observation.result) if observation.result is not None else None,
                json.dumps(list(observation.artifacts)),
                observation.failure_kind.value if observation.failure_kind else None,
                observation.error,
                observation.observed_at,
                json.dumps([item.to_a2a_metadata() for item in observation.pending_questions]),
                json.dumps([item.to_a2a_metadata() for item in observation.pending_gates]),
            )
            if blocker_changed:
                await connection.execute(
                    """
                    UPDATE workflow_executions
                    SET blocker_revision = blocker_revision + 1,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    child.execution_id,
                )
        return self._to_child(row)

    async def record_gate_report(self, child_id: UUID, report: dict) -> None:
        result = await self._pool.execute(
            """
            UPDATE workflow_execution_children
            SET gate_report = $2::jsonb, gate_validated_at = NOW(), updated_at = NOW()
            WHERE id = $1
            """,
            child_id,
            json.dumps(report),
        )
        if result == "UPDATE 0":
            raise ExecutionConflictError("gate report child no longer exists")

    async def record_reconcile_error(
        self,
        child_id: UUID,
        *,
        error: str,
        failure_kind: str,
        max_consecutive_failures: int,
    ) -> ChildT | None:
        if max_consecutive_failures <= 0:
            raise ValueError("max_consecutive_failures must be positive")
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                UPDATE workflow_execution_children
                SET reconcile_failure_count = reconcile_failure_count + 1,
                    error = $2, last_polled_at = NOW(), updated_at = NOW()
                WHERE id = $1
                  AND state NOT IN ('canceled', 'completed', 'failed', 'superseded')
                RETURNING *
                """,
                child_id,
                error,
            )
            if row is None:
                return None
            if row["reconcile_failure_count"] < max_consecutive_failures:
                return self._to_child(row)
            failed_row = await connection.fetchrow(
                """
                UPDATE workflow_execution_children
                SET state = 'failed', failure_kind = $2, updated_at = NOW()
                WHERE id = $1
                  AND state NOT IN ('canceled', 'completed', 'failed', 'superseded')
                RETURNING *
                """,
                child_id,
                failure_kind,
            )
            if failed_row is None:
                return self._to_child(row)
            await connection.execute(
                """
                UPDATE workflow_executions
                SET blocker_revision = blocker_revision + 1, updated_at = NOW()
                WHERE id = $1
                """,
                failed_row["execution_id"],
            )
            return self._to_child(failed_row)

    async def seal_generation(self, execution_id: UUID, generation: int) -> None:
        result = await self._pool.execute(
            """
            UPDATE workflow_execution_generations
            SET sealed = TRUE, sealed_at = COALESCE(sealed_at, NOW())
            WHERE execution_id = $1 AND generation = $2
            """,
            execution_id,
            generation,
        )
        if result == "UPDATE 0":
            raise ExecutionConflictError("generation does not exist")

    async def join_status(self, execution_id: UUID, generation: int):
        generation_row = await self._pool.fetchrow(
            """
            SELECT sealed FROM workflow_execution_generations
            WHERE execution_id = $1 AND generation = $2
            """,
            execution_id,
            generation,
        )
        if generation_row is None:
            raise ExecutionConflictError("generation does not exist")
        children = await self.list_children(execution_id)
        return calculate_join(generation, sealed=bool(generation_row["sealed"]), children=children)

    async def request_cancel(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> ExecutionT:
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                UPDATE workflow_executions
                SET cancel_requested = TRUE,
                    parent_stop_requested_at = COALESCE(parent_stop_requested_at, NOW()),
                    state = CASE
                        WHEN state IN ('failed', 'canceled') THEN state
                        ELSE 'canceling'
                    END,
                    suspension_reason = CASE
                        WHEN state IN ('failed', 'canceled') THEN suspension_reason
                        ELSE 'canceling_children'
                    END,
                    revision = CASE
                        WHEN cancel_requested THEN revision
                        ELSE revision + 1
                    END,
                    updated_at = NOW()
                WHERE id = $1 AND owner_id = $2 AND tenant_id = $3
                  AND state <> 'completed'
                RETURNING *
                """,
                execution_id,
                owner_id,
                tenant_id,
            )
            if row is None:
                existing = await connection.fetchrow(
                    """
                    SELECT * FROM workflow_executions
                    WHERE id = $1 AND owner_id = $2 AND tenant_id = $3
                    """,
                    execution_id,
                    owner_id,
                    tenant_id,
                )
                if existing is None:
                    raise ExecutionConflictError("execution does not exist")
                return await self._to_execution(existing, connection=connection)
            await connection.execute(
                """
                UPDATE workflow_execution_children
                SET state = CASE
                        WHEN state IN ('reserved', 'launching') THEN 'canceled'
                        ELSE 'canceling'
                    END,
                    lease_owner = '', lease_token = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE execution_id = $1
                  AND state NOT IN ('canceled', 'completed', 'failed', 'superseded')
                """,
                execution_id,
            )
            result = await self._to_execution(row, connection=connection)
        return result

    async def create_retry(
        self,
        child: ChildT,
        retry: ChildT,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> ChildT:
        async with self._pool.acquire() as connection, connection.transaction():
            execution = await connection.fetchrow(
                """
                SELECT * FROM workflow_executions
                WHERE id = $1 AND owner_id = $2 AND tenant_id = $3
                FOR UPDATE
                """,
                child.execution_id,
                owner_id,
                tenant_id,
            )
            if execution is None or execution["cancel_requested"]:
                raise ExecutionConflictError("execution does not accept retries")
            current = await connection.fetchrow(
                "SELECT * FROM workflow_execution_children WHERE id = $1 FOR UPDATE",
                child.id,
            )
            if current is None or int(current["attempt"]) != child.attempt:
                raise ExecutionConflictError("child attempt changed before retry")
            latest_attempt = await connection.fetchval(
                """
                SELECT MAX(attempt) FROM workflow_execution_children
                WHERE execution_id = $1 AND generation = $2 AND child_key = $3
                """,
                child.execution_id,
                child.generation,
                child.key,
            )
            if int(latest_attempt or 0) != child.attempt:
                raise ExecutionConflictError("only the latest child attempt can retry")
            await connection.execute(
                """
                UPDATE workflow_execution_children
                SET state = 'superseded', updated_at = NOW()
                WHERE id = $1
                """,
                child.id,
            )
            await connection.execute(
                """
                INSERT INTO workflow_execution_children (
                    id, execution_id, generation, child_key, attempt, state,
                    dependencies, objective, template_id,
                    template_revision, template_digest, plan_digest, input_digest,
                    input, budget_units, deadline, agent_id,
                    skill_id, intent_id, message_id, created_at, updated_at
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,
                    $15,$16,$17,$18,$19,$20,$21,$22
                )
                """,
                *_child_insert_values(retry),
            )
        return retry

    async def update_execution_state(
        self,
        execution_id: UUID,
        *,
        state: str,
        suspension_reason: str,
        expected_revision: int,
    ) -> None:
        if state != ExecutionState.FAILED.value:
            result = await self._pool.execute(
                """
                UPDATE workflow_executions
                SET state = $2, suspension_reason = $3, revision = revision + 1,
                    updated_at = NOW()
                WHERE id = $1 AND revision = $4
                  AND state NOT IN ('canceled', 'completed', 'failed')
                  AND (state IS DISTINCT FROM $2 OR suspension_reason IS DISTINCT FROM $3)
                """,
                execution_id,
                state,
                suspension_reason,
                expected_revision,
            )
            if result == "UPDATE 0":
                await self._require_state_projection_noop(
                    execution_id,
                    state=state,
                    suspension_reason=suspension_reason,
                    expected_revision=expected_revision,
                )
            return

        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                UPDATE workflow_executions
                SET state = 'failed', suspension_reason = $2,
                    cancel_requested = TRUE, revision = revision + 1,
                    updated_at = NOW()
                WHERE id = $1 AND revision = $3 AND cancel_requested = FALSE
                  AND state NOT IN ('canceled', 'completed', 'failed')
                RETURNING id
                """,
                execution_id,
                suspension_reason,
                expected_revision,
            )
            if row is None:
                await self._require_state_projection_noop(
                    execution_id,
                    state=state,
                    suspension_reason=suspension_reason,
                    expected_revision=expected_revision,
                    connection=connection,
                )
                return
            await connection.execute(
                """
                UPDATE workflow_execution_children
                SET state = CASE
                        WHEN state IN ('reserved', 'launching') THEN 'canceled'
                        ELSE 'canceling'
                    END,
                    lease_owner = '', lease_token = NULL, lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE execution_id = $1
                  AND state NOT IN ('canceled', 'completed', 'failed', 'superseded')
                """,
                execution_id,
            )

    async def _require_state_projection_noop(
        self,
        execution_id: UUID,
        *,
        state: str,
        suspension_reason: str,
        expected_revision: int,
        connection: asyncpg.Connection | None = None,
    ) -> None:
        """Distinguish an idempotent replay from a real conflict after 0 rows matched.

        The write above intentionally skips rows that already hold the target
        state/reason (to avoid bumping revision on a no-op retry), so 0 rows
        updated is ambiguous: it could mean the projection already landed, or
        it could mean the execution moved to a different revision or a
        terminal state underneath this caller. Only the second case is a
        conflict worth raising.
        """
        querier = connection if connection is not None else self._pool
        current = await querier.fetchrow(
            "SELECT state, suspension_reason, revision FROM workflow_executions WHERE id = $1",
            execution_id,
        )
        already_applied = (
            current is not None
            and current["revision"] == expected_revision
            and current["state"] == state
            and current["suspension_reason"] == suspension_reason
        )
        if already_applied:
            return
        raise ExecutionConflictError(
            "workflow execution changed since this projection observed it; "
            "the caller must re-read the execution before writing again"
        )

    async def mark_blocker_notification(
        self,
        execution_id: UUID,
        *,
        blocker_revision: int,
    ) -> None:
        await self._pool.execute(
            """
            UPDATE workflow_executions
            SET blocker_notified_revision = GREATEST(blocker_notified_revision, $2),
                updated_at = NOW()
            WHERE id = $1 AND blocker_revision = $2
            """,
            execution_id,
            blocker_revision,
        )

    async def reserve_message(self, message: ChildMessage) -> ChildMessage:
        await self._pool.execute(
            """
            INSERT INTO workflow_child_messages (
                id, child_id, message_id, answer, metadata, state, created_at, delivered_at
            ) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8)
            ON CONFLICT (message_id) DO NOTHING
            """,
            message.id,
            message.child_id,
            message.message_id,
            message.answer,
            json.dumps(message.metadata),
            message.state,
            message.created_at,
            message.delivered_at,
        )
        row = await self._pool.fetchrow(
            "SELECT * FROM workflow_child_messages WHERE message_id = $1",
            message.message_id,
        )
        if row is None:
            raise ExecutionConflictError("child message reservation disappeared")
        if row["child_id"] != message.child_id or row["answer"] != message.answer:
            raise ExecutionConflictError("messageId was reused for different child content")
        metadata = _json_dict(row["metadata"])
        if metadata != message.metadata:
            raise ExecutionConflictError("messageId was reused for different child metadata")
        return ChildMessage(
            id=row["id"],
            child_id=row["child_id"],
            message_id=row["message_id"],
            answer=row["answer"],
            metadata=metadata,
            state=row["state"],
            created_at=row["created_at"],
            delivered_at=row.get("delivered_at"),
        )

    async def mark_message_delivered(self, message_id: UUID) -> None:
        await self._pool.execute(
            """
            UPDATE workflow_child_messages
            SET state = 'delivered', delivered_at = COALESCE(delivered_at, NOW())
            WHERE id = $1
            """,
            message_id,
        )


def _policy_to_json(policy: ExpansionPolicy) -> dict[str, object]:
    return {
        "coordinatorId": policy.coordinator_id,
        "workflowDependency": policy.workflow_dependency,
        "templateId": str(policy.template_id),
        "templateRevision": policy.template_revision,
        "templateDigest": policy.template_digest,
        "inputSchema": policy.input_schema,
        "resultSchema": policy.result_schema,
        "maxChildren": policy.max_children,
        "maxAttempts": policy.max_attempts,
        "maxActiveChildren": policy.max_active_children,
        "joinMode": policy.join_mode,
    }


def _policy_from_json(value: object) -> ExpansionPolicy:
    raw = json.loads(value) if isinstance(value, str) else dict(value or {})
    return ExpansionPolicy(
        coordinator_id=str(raw["coordinatorId"]),
        workflow_dependency=str(raw["workflowDependency"]),
        template_id=UUID(str(raw["templateId"])),
        template_revision=str(raw["templateRevision"]),
        template_digest=str(raw["templateDigest"]),
        input_schema=dict(raw["inputSchema"]),
        result_schema=dict(raw["resultSchema"]),
        max_children=int(raw["maxChildren"]),
        max_attempts=int(raw["maxAttempts"]),
        max_active_children=int(raw["maxActiveChildren"]),
        join_mode=str(raw["joinMode"]),
    )


def _generic_execution_from_row(row) -> WorkflowExecution:
    return WorkflowExecution(
        id=row["id"],
        name=row["name"],
        prompt=row["prompt"],
        owner_id=row["owner_id"],
        tenant_id=row["tenant_id"],
        workflow_id=row["workflow_id"],
        workflow_revision=row["workflow_revision"],
        workflow_digest=row["workflow_digest"],
        parent_session_id=row["parent_session_id"],
        parent_node_id=row["parent_node_id"],
        connection_id=row.get("connection_id") or "",
        policy=_policy_from_json(row["policy"]),
        budget=ExecutionBudget(
            total_units=int(row["total_budget"]),
            reserved_units=int(row["reserved_budget"]),
            spent_units=int(row["spent_budget"]),
        ),
        deadline=row["deadline"],
        launch_key=row.get("launch_key") or "",
        launch_digest=row.get("launch_digest") or "",
        state=ExecutionState(row["state"]),
        current_generation=int(row["current_generation"]),
        plan_revision=row.get("plan_revision") or "",
        suspension_reason=row["suspension_reason"],
        cancel_requested=bool(row["cancel_requested"]),
        parent_stop_requested_at=row.get("parent_stop_requested_at"),
        parent_stopped_at=row.get("parent_stopped_at"),
        revision=int(row["revision"]),
        blocker_revision=int(row["blocker_revision"]),
        blocker_notified_revision=int(row["blocker_notified_revision"]),
        completed_at=row.get("completed_at"),
        input=_json_dict(row.get("input")),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _child_insert_values(child: WorkflowChildExecution) -> tuple:
    return (
        child.id,
        child.execution_id,
        child.generation,
        child.key,
        child.attempt,
        child.state.value,
        list(child.dependencies),
        child.objective,
        child.template_id,
        child.template_revision,
        child.template_digest,
        child.plan_digest,
        child.input_digest,
        json.dumps(child.input),
        child.budget_units,
        child.deadline,
        child.agent_id,
        child.skill_id,
        child.intent_id,
        child.message_id,
        child.created_at,
        child.updated_at,
    )


def _generic_child_from_row(row) -> WorkflowChildExecution:
    result = row.get("result")
    artifacts = row.get("artifacts") or []
    pending_questions = _json_list(row.get("pending_questions"))
    pending_gates = _json_list(row.get("pending_gates"))
    return WorkflowChildExecution(
        id=row["id"],
        execution_id=row["execution_id"],
        generation=int(row["generation"]),
        key=row["child_key"],
        attempt=int(row["attempt"]),
        state=ChildExecutionState(row["state"]),
        dependencies=tuple(row.get("dependencies") or ()),
        objective=row["objective"],
        template_id=row["template_id"],
        template_revision=row["template_revision"],
        template_digest=row["template_digest"],
        plan_digest=row["plan_digest"],
        input_digest=row["input_digest"],
        input=_json_dict(row["input"]),
        budget_units=int(row["budget_units"]),
        deadline=row["deadline"],
        agent_id=row["agent_id"],
        skill_id=row["skill_id"],
        intent_id=row["intent_id"],
        message_id=row["message_id"],
        task_id=row.get("task_id") or "",
        context_id=row.get("context_id") or "",
        result=_json_dict(result) if result is not None else None,
        artifacts=tuple(json.loads(artifacts) if isinstance(artifacts, str) else artifacts),
        gate_report=(
            _json_dict(row.get("gate_report")) if row.get("gate_report") is not None else None
        ),
        gate_validated_at=row.get("gate_validated_at"),
        failure_kind=FailureKind(row["failure_kind"]) if row.get("failure_kind") else None,
        error=row.get("error") or "",
        pending_questions=tuple(
            ChildPendingQuestion(
                request_id=str(item.get("requestId") or item.get("request_id") or ""),
                persona=str(item.get("persona") or ""),
                question=str(item.get("question") or item.get("summary") or ""),
                reason=str(item.get("reason") or ""),
                recommendation=str(item.get("recommendation") or ""),
                attempted=tuple(str(value) for value in item.get("attempted") or ()),
            )
            for item in pending_questions
        ),
        pending_gates=tuple(
            ChildPendingGate(
                gate_id=str(item.get("gateId") or item.get("gate_id") or ""),
                node_id=str(item.get("nodeId") or item.get("node_id") or ""),
                label=str(item.get("label") or ""),
                condition=str(item.get("condition") or ""),
                instructions=str(item.get("instructions") or ""),
                summary=str(item.get("summary") or ""),
            )
            for item in pending_gates
        ),
        lease_owner=row.get("lease_owner") or "",
        lease_token=row.get("lease_token"),
        fencing_generation=int(row.get("fencing_generation") or 0),
        lease_expires_at=row.get("lease_expires_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _json_dict(value: object) -> dict:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _json_list(value: object) -> list[dict]:
    decoded = json.loads(value) if isinstance(value, str) else list(value or [])
    return [dict(item) for item in decoded]


def _decode_cursor(cursor: str) -> tuple[datetime | None, UUID | None]:
    if not cursor:
        return None, None
    try:
        raw_time, raw_id = cursor.rsplit("|", 1)
        parsed = datetime.fromisoformat(raw_time)
        if parsed.tzinfo is None:
            raise ValueError("timestamp has no timezone")
        return parsed.astimezone(UTC), UUID(raw_id)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid workflow execution cursor") from exc

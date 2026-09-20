"""PostgreSQL ledger for code delivery: the generic ledger plus its extension table.

``delivery_executions`` holds only what delivery adds over the generic
``workflow_executions``/``workflow_execution_children`` tables: repository,
base ref/sha, merge receipt, and the integration candidate/review trail. A
child's own delivery fields (repository, base sha, workspace, requirement
IDs) have no columns of their own — they are read back from the generic
child's ``input`` JSON, which ``ting.delivery.domain.validate_expansion``
requires to carry them.
"""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from ting.adapters.postgres_workflow_executions import (
    PostgresWorkflowExecutionRepository,
    _generic_child_from_row,
    _json_dict,
    _json_list,
    _policy_from_json,
)
from ting.delivery.domain import ChildExecution, DeliveryExecution
from ting.delivery.ports import DeliveryExecutionRepository
from ting.domain.workflow_execution import (
    ExecutionBudget,
    ExecutionConflictError,
    ExecutionState,
    calculate_join,
)


class PostgresDeliveryExecutionRepository(
    PostgresWorkflowExecutionRepository[DeliveryExecution, ChildExecution],
    DeliveryExecutionRepository,
):
    """Layers the delivery extension table over the generic execution ledger."""

    async def _to_execution(
        self,
        row,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> DeliveryExecution:
        querier = connection if connection is not None else self._pool
        delivery_row = await querier.fetchrow(
            "SELECT * FROM delivery_executions WHERE execution_id = $1", row["id"]
        )
        if delivery_row is None:
            raise ExecutionConflictError(
                f"execution {row['id']} is missing its delivery extension row"
            )
        return _delivery_execution_from_rows(row, delivery_row)

    async def _to_executions(
        self,
        rows: list,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> list[DeliveryExecution]:
        if not rows:
            return []
        querier = connection if connection is not None else self._pool
        ids = [row["id"] for row in rows]
        delivery_rows = await querier.fetch(
            "SELECT * FROM delivery_executions WHERE execution_id = ANY($1::uuid[])", ids
        )
        by_id = {delivery_row["execution_id"]: delivery_row for delivery_row in delivery_rows}
        executions = []
        for row in rows:
            delivery_row = by_id.get(row["id"])
            if delivery_row is None:
                raise ExecutionConflictError(
                    f"execution {row['id']} is missing its delivery extension row"
                )
            executions.append(_delivery_execution_from_rows(row, delivery_row))
        return executions

    def _to_child(self, row) -> ChildExecution:
        return _delivery_child_from_row(row)

    async def _after_create(
        self, connection: asyncpg.Connection, execution: DeliveryExecution
    ) -> None:
        await connection.execute(
            """
            INSERT INTO delivery_executions (
                execution_id, repository, base_ref, base_sha,
                merge_receipt, integration_receipts, integration_allocation,
                integration_candidate, integration_review_receipt,
                integration_review_event_id
            ) VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7::jsonb,$8::jsonb,$9::jsonb,$10)
            """,
            execution.id,
            execution.repository,
            execution.base_ref,
            execution.base_sha,
            json.dumps(execution.merge_receipt) if execution.merge_receipt is not None else None,
            json.dumps(list(execution.integration_receipts)),
            (
                json.dumps(execution.integration_allocation)
                if execution.integration_allocation is not None
                else None
            ),
            (
                json.dumps(execution.integration_candidate)
                if execution.integration_candidate is not None
                else None
            ),
            (
                json.dumps(execution.integration_review_receipt)
                if execution.integration_review_receipt is not None
                else None
            ),
            execution.integration_review_event_id,
        )

    async def _after_reserve_generation(
        self, connection: asyncpg.Connection, execution_id: UUID
    ) -> None:
        await connection.execute(
            """
            UPDATE delivery_executions
            SET integration_allocation = NULL,
                integration_receipts = '[]'::jsonb,
                integration_candidate = NULL,
                integration_review_receipt = NULL,
                integration_review_event_id = '',
                updated_at = NOW()
            WHERE execution_id = $1
            """,
            execution_id,
        )

    # -- delivery-only ledger operations -------------------------------------

    async def complete_execution(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
        expected_revision: int,
        merge_receipt: dict,
    ) -> DeliveryExecution:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM workflow_executions WHERE id = $1 AND owner_id = $2 "
                "AND tenant_id = $3 FOR UPDATE",
                execution_id,
                owner_id,
                tenant_id,
            )
            if row is None or row["revision"] != expected_revision:
                raise ExecutionConflictError("Execution changed during publication verification")
            delivery_row = await conn.fetchrow(
                "SELECT * FROM delivery_executions WHERE execution_id = $1 FOR UPDATE",
                execution_id,
            )
            if delivery_row is None:
                raise ExecutionConflictError(
                    f"execution {execution_id} is missing its delivery extension row"
                )
            execution = _delivery_execution_from_rows(row, delivery_row)
            if execution.cancel_requested or execution.state not in {
                ExecutionState.RUNNING,
                ExecutionState.WAITING,
                ExecutionState.BLOCKED,
            }:
                raise ExecutionConflictError("Execution no longer permits completion")
            children = await conn.fetch(
                "SELECT * FROM workflow_execution_children WHERE execution_id = $1 "
                "AND generation = $2 FOR UPDATE",
                execution_id,
                execution.current_generation,
            )
            sealed = await conn.fetchval(
                "SELECT sealed FROM workflow_execution_generations "
                "WHERE execution_id = $1 AND generation = $2",
                execution_id,
                execution.current_generation,
            )
            join = calculate_join(
                execution.current_generation,
                sealed=bool(sealed),
                children=[self._to_child(child) for child in children],
            )
            if not join.ready:
                raise ExecutionConflictError("Child evidence barrier changed during completion")
            if (
                merge_receipt.get("campaign_id") != str(execution.id)
                or merge_receipt.get("repository") != execution.repository
                or merge_receipt.get("base_sha") != execution.base_sha
                or merge_receipt.get("target_branch") != execution.base_ref
                or merge_receipt.get("state") != "merged"
            ):
                raise ExecutionConflictError("Merge receipt does not match this execution")
            updated_row = await conn.fetchrow(
                "UPDATE workflow_executions SET state = 'completed', "
                "suspension_reason = 'delivery_merged', "
                "completed_at = NOW(), updated_at = NOW(), revision = revision + 1 "
                "WHERE id = $1 RETURNING *",
                execution_id,
            )
            updated_delivery_row = await conn.fetchrow(
                "UPDATE delivery_executions SET merge_receipt = $2::jsonb, updated_at = NOW() "
                "WHERE execution_id = $1 RETURNING *",
                execution_id,
                json.dumps(merge_receipt),
            )
            return _delivery_execution_from_rows(updated_row, updated_delivery_row)

    async def record_integration_candidate(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
        expected_revision: int,
        allocation: dict,
        receipts: list[dict],
        candidate: dict,
    ) -> DeliveryExecution:
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                UPDATE workflow_executions
                SET revision = revision + 1, updated_at = NOW()
                WHERE id = $1 AND owner_id = $2 AND tenant_id = $3 AND revision = $4
                  AND cancel_requested = FALSE
                  AND state IN ('running', 'waiting', 'blocked')
                RETURNING *
                """,
                execution_id,
                owner_id,
                tenant_id,
                expected_revision,
            )
            if row is None:
                raise ExecutionConflictError(
                    "Execution changed while recording the inspected integration candidate"
                )
            delivery_row = await connection.fetchrow(
                """
                UPDATE delivery_executions
                SET integration_allocation = $2::jsonb,
                    integration_receipts = $3::jsonb,
                    integration_candidate = $4::jsonb,
                    integration_review_receipt = NULL,
                    integration_review_event_id = '',
                    updated_at = NOW()
                WHERE execution_id = $1
                RETURNING *
                """,
                execution_id,
                json.dumps(allocation),
                json.dumps(receipts),
                json.dumps(candidate),
            )
            if delivery_row is None:
                raise ExecutionConflictError(
                    f"execution {execution_id} is missing its delivery extension row"
                )
        return _delivery_execution_from_rows(row, delivery_row)

    async def record_integration_review(
        self,
        execution_id: UUID,
        *,
        event_id: str,
        candidate_sha: str,
        candidate_tree: str,
        receipt: dict,
    ) -> DeliveryExecution:
        exec_row = await self._pool.fetchrow(
            "SELECT * FROM workflow_executions WHERE id = $1",
            execution_id,
        )
        if exec_row is None:
            raise ExecutionConflictError("Integration review execution was not found")
        delivery_row = await self._pool.fetchrow(
            "SELECT * FROM delivery_executions WHERE execution_id = $1",
            execution_id,
        )
        if delivery_row is None:
            raise ExecutionConflictError(
                f"execution {execution_id} is missing its delivery extension row"
            )
        execution = _delivery_execution_from_rows(exec_row, delivery_row)
        if execution.integration_review_event_id == event_id:
            if execution.integration_review_receipt != receipt:
                raise ExecutionConflictError(
                    "Integration review event was reused for different content"
                )
            return execution
        async with self._pool.acquire() as connection, connection.transaction():
            updated_delivery_row = await connection.fetchrow(
                """
                UPDATE delivery_executions
                SET integration_review_receipt = $5::jsonb,
                    integration_review_event_id = $2,
                    updated_at = NOW()
                WHERE execution_id = $1
                  AND integration_review_event_id = ''
                  AND integration_candidate ->> 'candidate_sha' = $3
                  AND integration_candidate ->> 'candidate_tree' = $4
                  AND EXISTS (
                      SELECT 1 FROM workflow_executions e
                      WHERE e.id = $1 AND e.cancel_requested = FALSE
                        AND e.state IN ('running', 'waiting', 'blocked')
                  )
                RETURNING *
                """,
                execution_id,
                event_id,
                candidate_sha,
                candidate_tree,
                json.dumps(receipt),
            )
            if updated_delivery_row is None:
                raise ExecutionConflictError(
                    "Integration review no longer matches the current inspected candidate"
                )
            updated_exec_row = await connection.fetchrow(
                "UPDATE workflow_executions SET revision = revision + 1, updated_at = NOW() "
                "WHERE id = $1 RETURNING *",
                execution_id,
            )
        return _delivery_execution_from_rows(updated_exec_row, updated_delivery_row)


def _delivery_execution_from_rows(exec_row, delivery_row) -> DeliveryExecution:
    return DeliveryExecution(
        id=exec_row["id"],
        name=exec_row["name"],
        prompt=exec_row["prompt"],
        owner_id=exec_row["owner_id"],
        tenant_id=exec_row["tenant_id"],
        workflow_id=exec_row["workflow_id"],
        workflow_revision=exec_row["workflow_revision"],
        workflow_digest=exec_row["workflow_digest"],
        parent_session_id=exec_row["parent_session_id"],
        parent_node_id=exec_row["parent_node_id"],
        connection_id=exec_row.get("connection_id") or "",
        policy=_policy_from_json(exec_row["policy"]),
        budget=ExecutionBudget(
            total_units=int(exec_row["total_budget"]),
            reserved_units=int(exec_row["reserved_budget"]),
            spent_units=int(exec_row["spent_budget"]),
        ),
        deadline=exec_row["deadline"],
        launch_key=exec_row.get("launch_key") or "",
        launch_digest=exec_row.get("launch_digest") or "",
        state=ExecutionState(exec_row["state"]),
        current_generation=int(exec_row["current_generation"]),
        plan_revision=exec_row.get("plan_revision") or "",
        suspension_reason=exec_row["suspension_reason"],
        cancel_requested=bool(exec_row["cancel_requested"]),
        parent_stop_requested_at=exec_row.get("parent_stop_requested_at"),
        parent_stopped_at=exec_row.get("parent_stopped_at"),
        revision=int(exec_row["revision"]),
        blocker_revision=int(exec_row["blocker_revision"]),
        blocker_notified_revision=int(exec_row["blocker_notified_revision"]),
        completed_at=exec_row.get("completed_at"),
        input=_json_dict(exec_row.get("input")),
        workflow_snapshot=_json_dict(exec_row.get("workflow_snapshot")),
        created_at=exec_row["created_at"],
        updated_at=exec_row["updated_at"],
        repository=delivery_row["repository"],
        base_ref=delivery_row["base_ref"],
        base_sha=delivery_row["base_sha"],
        merge_receipt=(
            _json_dict(delivery_row["merge_receipt"])
            if delivery_row.get("merge_receipt") is not None
            else None
        ),
        integration_receipts=tuple(_json_list(delivery_row.get("integration_receipts"))),
        integration_allocation=(
            _json_dict(delivery_row["integration_allocation"])
            if delivery_row.get("integration_allocation") is not None
            else None
        ),
        integration_candidate=(
            _json_dict(delivery_row["integration_candidate"])
            if delivery_row.get("integration_candidate") is not None
            else None
        ),
        integration_review_receipt=(
            _json_dict(delivery_row["integration_review_receipt"])
            if delivery_row.get("integration_review_receipt") is not None
            else None
        ),
        integration_review_event_id=delivery_row.get("integration_review_event_id") or "",
    )


def _delivery_child_from_row(row) -> ChildExecution:
    base = _generic_child_from_row(row)
    workspace = base.input.get("workspace")
    return ChildExecution(
        id=base.id,
        execution_id=base.execution_id,
        generation=base.generation,
        key=base.key,
        attempt=base.attempt,
        state=base.state,
        dependencies=base.dependencies,
        objective=base.objective,
        template_id=base.template_id,
        template_revision=base.template_revision,
        template_digest=base.template_digest,
        plan_digest=base.plan_digest,
        input_digest=base.input_digest,
        input=base.input,
        budget_units=base.budget_units,
        deadline=base.deadline,
        agent_id=base.agent_id,
        skill_id=base.skill_id,
        intent_id=base.intent_id,
        message_id=base.message_id,
        task_id=base.task_id,
        context_id=base.context_id,
        result=base.result,
        artifacts=base.artifacts,
        gate_report=base.gate_report,
        gate_validated_at=base.gate_validated_at,
        failure_kind=base.failure_kind,
        pending_questions=base.pending_questions,
        pending_gates=base.pending_gates,
        error=base.error,
        lease_owner=base.lease_owner,
        lease_token=base.lease_token,
        fencing_generation=base.fencing_generation,
        lease_expires_at=base.lease_expires_at,
        created_at=base.created_at,
        updated_at=base.updated_at,
        requirement_ids=tuple(str(item) for item in base.input.get("requirementIds") or ()),
        repository=str(base.input.get("repository") or ""),
        base_sha=str(base.input.get("baseSha") or ""),
        workspace=dict(workspace) if isinstance(workspace, dict) else None,
    )

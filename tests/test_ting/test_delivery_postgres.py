"""Tests for the delivery pack's extension over the generic execution ledger.

These prove the two-table contract ``PostgresDeliveryExecutionRepository``
adds over ``PostgresWorkflowExecutionRepository``: the ``delivery_executions``
join on every read, one transaction spanning both tables on every write that
touches both, and that a delivery child's git-bound fields come solely from
the generic child's ``input`` JSON — there are no dedicated columns for them.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.test_ting.test_delivery_execution import PLAN_REVISION, _execution, _proposal
from ting.delivery.domain import make_children
from ting.delivery.postgres import (
    PostgresDeliveryExecutionRepository,
    _delivery_child_from_row,
)
from ting.domain.workflow_execution import (
    ChildExecutionState,
    ExecutionConflictError,
    ExecutionState,
)


def _execution_row(execution) -> dict:
    from ting.adapters.postgres_workflow_executions import _policy_to_json

    return {
        "id": execution.id,
        "owner_id": execution.owner_id,
        "tenant_id": execution.tenant_id,
        "name": execution.name,
        "prompt": execution.prompt,
        "workflow_id": execution.workflow_id,
        "workflow_revision": execution.workflow_revision,
        "workflow_digest": execution.workflow_digest,
        "parent_session_id": execution.parent_session_id,
        "parent_node_id": execution.parent_node_id,
        "connection_id": execution.connection_id,
        "state": execution.state.value,
        "current_generation": execution.current_generation,
        "plan_revision": execution.plan_revision,
        "total_budget": execution.budget.total_units,
        "reserved_budget": execution.budget.reserved_units,
        "spent_budget": execution.budget.spent_units,
        "deadline": execution.deadline,
        "suspension_reason": execution.suspension_reason,
        "cancel_requested": execution.cancel_requested,
        "parent_stop_requested_at": execution.parent_stop_requested_at,
        "parent_stopped_at": execution.parent_stopped_at,
        "policy": _policy_to_json(execution.policy),
        "input": execution.input,
        "revision": execution.revision,
        "blocker_revision": execution.blocker_revision,
        "blocker_notified_revision": execution.blocker_notified_revision,
        "launch_key": execution.launch_key,
        "launch_digest": execution.launch_digest,
        "completed_at": execution.completed_at,
        "created_at": execution.created_at,
        "updated_at": execution.updated_at,
    }


def _delivery_row(execution) -> dict:
    return {
        "execution_id": execution.id,
        "repository": execution.repository,
        "base_ref": execution.base_ref,
        "base_sha": execution.base_sha,
        "workflow_snapshot": execution.workflow_snapshot,
        "merge_receipt": execution.merge_receipt,
        "integration_receipts": list(execution.integration_receipts),
        "integration_allocation": execution.integration_allocation,
        "integration_candidate": execution.integration_candidate,
        "integration_review_receipt": execution.integration_review_receipt,
        "integration_review_event_id": execution.integration_review_event_id,
        "created_at": execution.created_at,
        "updated_at": execution.updated_at,
    }


def _child_row(child) -> dict:
    return {
        "id": child.id,
        "execution_id": child.execution_id,
        "generation": child.generation,
        "child_key": child.key,
        "attempt": child.attempt,
        "state": child.state.value,
        "dependencies": list(child.dependencies),
        "objective": child.objective,
        "template_id": child.template_id,
        "template_revision": child.template_revision,
        "template_digest": child.template_digest,
        "plan_digest": child.plan_digest,
        "input_digest": child.input_digest,
        "input": child.input,
        "budget_units": child.budget_units,
        "deadline": child.deadline,
        "agent_id": child.agent_id,
        "skill_id": child.skill_id,
        "intent_id": child.intent_id,
        "message_id": child.message_id,
        "task_id": child.task_id,
        "context_id": child.context_id,
        "result": child.result,
        "artifacts": list(child.artifacts),
        "gate_report": child.gate_report,
        "gate_validated_at": child.gate_validated_at,
        "failure_kind": child.failure_kind.value if child.failure_kind else None,
        "error": child.error,
        "pending_questions": [item.to_a2a_metadata() for item in child.pending_questions],
        "pending_gates": [item.to_a2a_metadata() for item in child.pending_gates],
        "lease_owner": child.lease_owner,
        "lease_token": child.lease_token,
        "fencing_generation": child.fencing_generation,
        "lease_expires_at": child.lease_expires_at,
        "created_at": child.created_at,
        "updated_at": child.updated_at,
    }


class _ContextManager:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


def test_delivery_child_git_fields_are_derived_solely_from_input_json() -> None:
    """The child row carries no repository/base_sha/workspace columns of its own."""
    execution = _execution()
    child = make_children(execution, 1, (_proposal(execution, "api"),))[0]
    row = _child_row(child)

    assert "repository" not in row
    assert "base_sha" not in row
    assert "workspace" not in row
    assert "requirement_ids" not in row

    rebuilt = _delivery_child_from_row(row)

    assert rebuilt.repository == child.repository
    assert rebuilt.base_sha == child.base_sha
    assert rebuilt.workspace == child.workspace
    assert rebuilt.requirement_ids == child.requirement_ids
    assert rebuilt.repository == row["input"]["repository"]
    assert rebuilt.base_sha == row["input"]["baseSha"]


class _JoinPool:
    """Backs ``_to_execution``/``_to_executions``: two independent tables."""

    def __init__(self, execution, *, missing_delivery=False):
        self.execution_row = _execution_row(execution)
        self.delivery_row = None if missing_delivery else _delivery_row(execution)

    async def fetchrow(self, query, *args):
        if "FROM workflow_executions" in query:
            return self.execution_row
        if "FROM delivery_executions" in query:
            return self.delivery_row
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if "FROM delivery_executions" in query:
            return [self.delivery_row] if self.delivery_row is not None else []
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_get_joins_the_delivery_extension_table() -> None:
    execution = _execution()
    pool = _JoinPool(execution)
    repository = PostgresDeliveryExecutionRepository(pool)

    fetched = await repository.get(
        execution.id, owner_id=execution.owner_id, tenant_id=execution.tenant_id
    )

    assert fetched.id == execution.id
    assert fetched.repository == execution.repository
    assert fetched.base_sha == execution.base_sha


@pytest.mark.asyncio
async def test_get_raises_when_delivery_extension_row_is_missing() -> None:
    execution = _execution()
    pool = _JoinPool(execution, missing_delivery=True)
    repository = PostgresDeliveryExecutionRepository(pool)

    with pytest.raises(ExecutionConflictError, match="missing its delivery extension row"):
        await repository.get(
            execution.id, owner_id=execution.owner_id, tenant_id=execution.tenant_id
        )


@pytest.mark.asyncio
async def test_list_batches_the_delivery_join_and_raises_on_any_gap() -> None:
    execution = _execution()
    other = replace(_execution(), id=uuid4())
    pool = _JoinPool(execution)
    repository = PostgresDeliveryExecutionRepository(pool)

    complete = await repository._to_executions([_execution_row(execution)])
    assert complete[0].repository == execution.repository

    assert await repository._to_executions([]) == []

    class _GapPool(_JoinPool):
        async def fetch(self, query, *args):
            return [_delivery_row(execution)]  # missing `other`'s row

    gapped_repo = PostgresDeliveryExecutionRepository(_GapPool(execution))
    with pytest.raises(ExecutionConflictError, match="missing its delivery extension row"):
        await gapped_repo._to_executions([_execution_row(execution), _execution_row(other)])


class _Transaction(_ContextManager):
    """Plain transaction: only tracks whether a block is currently open."""

    async def __aenter__(self):
        self.value.in_transaction = True
        return self.value

    async def __aexit__(self, *_args):
        self.value.in_transaction = False
        return False


class _TrackedTransaction(_ContextManager):
    """Commit-aware transaction: only marks writes durable on a clean exit."""

    async def __aenter__(self):
        self.value.in_transaction = True
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        self.value.in_transaction = False
        if exc_type is None:
            self.value.committed_workflow = self.value.pending_workflow
            self.value.committed_delivery = self.value.pending_delivery
        else:
            self.value.pending_workflow = None
            self.value.pending_delivery = None
        return False


class _CreateConnection:
    """Tracks whether each insert would actually persist through a commit."""

    def __init__(self, *, fail_delivery: bool = False):
        self.in_transaction = False
        self.fail_delivery = fail_delivery
        self.attempted_workflow_insert = False
        self.pending_workflow = None
        self.pending_delivery = None
        self.committed_workflow = None
        self.committed_delivery = None

    def transaction(self):
        return _TrackedTransaction(self)

    async def execute(self, query, *args):
        assert self.in_transaction, "both writes must run inside the same transaction"
        if "INSERT INTO workflow_executions" in query:
            self.attempted_workflow_insert = True
            self.pending_workflow = args
            return "INSERT 0 1"
        if "INSERT INTO delivery_executions" in query:
            if self.fail_delivery:
                raise RuntimeError("simulated delivery_executions constraint violation")
            self.pending_delivery = args
            return "INSERT 0 1"
        raise AssertionError(query)


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _ContextManager(self.connection)


@pytest.mark.asyncio
async def test_create_writes_both_tables_in_one_transaction() -> None:
    execution = _execution()
    connection = _CreateConnection()
    repository = PostgresDeliveryExecutionRepository(_Pool(connection))

    created = await repository.create(execution)

    assert created is execution
    assert connection.committed_workflow is not None
    assert connection.committed_delivery is not None
    assert connection.in_transaction is False


@pytest.mark.asyncio
async def test_create_rolls_back_both_writes_when_the_delivery_insert_fails() -> None:
    execution = _execution()
    connection = _CreateConnection(fail_delivery=True)
    repository = PostgresDeliveryExecutionRepository(_Pool(connection))

    with pytest.raises(RuntimeError, match="simulated delivery_executions constraint violation"):
        await repository.create(execution)

    # The workflow_executions insert ran against the same connection, but
    # since it shares the delivery insert's transaction, a real Postgres
    # connection rolls both back together. Nothing was ever marked committed.
    assert connection.attempted_workflow_insert is True
    assert connection.committed_workflow is None
    assert connection.committed_delivery is None
    assert connection.in_transaction is False


class _AfterReserveConnection:
    def __init__(self, execution):
        self.execution = _execution_row(execution)
        self.delivery = _delivery_row(execution) | {
            "integration_receipts": [{"stale": True}],
            "integration_allocation": {"stale": True},
            "integration_candidate": {"stale": True},
            "integration_review_receipt": {"stale": True},
            "integration_review_event_id": "stale",
        }
        self.delivery_updates = 0
        self.in_transaction = False
        self.inserted_children = []

    def transaction(self):
        return _Transaction(self)

    async def fetchrow(self, query, *args):
        if "FROM workflow_executions" in query and "FOR UPDATE" in query:
            return dict(self.execution)
        if "FROM delivery_executions" in query:
            return dict(self.delivery)
        if query.lstrip().startswith("UPDATE workflow_executions"):
            updated = dict(self.execution)
            updated.update(
                current_generation=args[1],
                reserved_budget=updated["reserved_budget"] + args[2],
                plan_revision=args[3],
                state="waiting",
                suspension_reason="awaiting_children",
                revision=updated["revision"] + 1,
            )
            self.execution = updated
            return updated
        raise AssertionError(query)

    async def execute(self, query, *args):
        assert self.in_transaction
        if "UPDATE delivery_executions" in query:
            self.delivery_updates += 1
            self.delivery.update(
                integration_allocation=None,
                integration_receipts=[],
                integration_candidate=None,
                integration_review_receipt=None,
                integration_review_event_id="",
            )
            return "UPDATE 1"
        assert "workflow_execution_generations" in query
        return "INSERT 0 1"

    async def executemany(self, query, rows):
        self.inserted_children = list(rows)


@pytest.mark.asyncio
async def test_reserve_generation_clears_stale_integration_state_in_the_same_transaction() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    child = make_children(execution, 1, (_proposal(execution, "api"),))[0]
    connection = _AfterReserveConnection(execution)
    repository = PostgresDeliveryExecutionRepository(_Pool(connection))

    updated = await repository.reserve_generation(execution, (child,), plan_revision=PLAN_REVISION)

    assert updated.current_generation == 1
    assert connection.delivery_updates == 1
    assert connection.in_transaction is False


class _CompletionConnection:
    def __init__(self, execution, child):
        self.execution = _execution_row(execution)
        self.delivery = _delivery_row(execution)
        self.children = [_child_row(child)]
        self.sealed = True
        self.updated = False
        self.in_transaction = False

    def transaction(self):
        return _Transaction(self)

    async def fetchrow(self, query, *args):
        if query.lstrip().startswith("SELECT * FROM workflow_executions"):
            return dict(self.execution)
        if query.lstrip().startswith("SELECT * FROM delivery_executions"):
            return dict(self.delivery)
        if query.lstrip().startswith("UPDATE workflow_executions"):
            assert self.in_transaction
            self.updated = True
            self.execution.update(
                state="completed",
                suspension_reason="delivery_merged",
                completed_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                revision=self.execution["revision"] + 1,
            )
            return dict(self.execution)
        if query.lstrip().startswith("UPDATE delivery_executions"):
            assert self.in_transaction
            self.delivery.update(merge_receipt=json.loads(args[1]), updated_at=datetime.now(UTC))
            return dict(self.delivery)
        raise AssertionError(query)

    async def fetch(self, query, *_args):
        assert "workflow_execution_children" in query
        assert self.in_transaction
        return [dict(child) for child in self.children]

    async def fetchval(self, query, *_args):
        assert "workflow_execution_generations" in query
        assert self.in_transaction
        return self.sealed


def _completion_context():
    execution = replace(_execution(), current_generation=1, state=ExecutionState.WAITING)
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.COMPLETED,
    )
    connection = _CompletionConnection(execution, child)
    repository = PostgresDeliveryExecutionRepository(_Pool(connection))
    receipt = {
        "campaign_id": str(execution.id),
        "repository": execution.repository,
        "base_sha": execution.base_sha,
        "target_branch": execution.base_ref,
        "source_sha": "b" * 40,
        "state": "merged",
    }
    return execution, connection, repository, receipt


@pytest.mark.asyncio
async def test_complete_execution_locks_join_and_updates_both_tables_atomically() -> None:
    execution, connection, repository, receipt = _completion_context()

    completed = await repository.complete_execution(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        expected_revision=execution.revision,
        merge_receipt=receipt,
    )

    assert completed.state == ExecutionState.COMPLETED
    assert completed.merge_receipt == receipt
    assert completed.revision == execution.revision + 1
    assert connection.updated is True
    assert connection.in_transaction is False


@pytest.mark.parametrize("failure", ["stale", "canceled", "child_changed"])
@pytest.mark.asyncio
async def test_complete_execution_rejects_changed_state_under_lock(failure: str) -> None:
    execution, connection, repository, receipt = _completion_context()
    expected_revision = execution.revision
    if failure == "stale":
        expected_revision += 1
    elif failure == "canceled":
        connection.execution["cancel_requested"] = True
    else:
        connection.children[0]["state"] = ChildExecutionState.BLOCKED.value

    with pytest.raises(ExecutionConflictError):
        await repository.complete_execution(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            expected_revision=expected_revision,
            merge_receipt=receipt,
        )

    assert connection.updated is False
    assert connection.in_transaction is False


class _IntegrationConnection:
    """Doubles as both the repository's pool and its acquired connection.

    ``record_integration_candidate``/``record_integration_review`` mix bare
    pool reads (outside any transaction) with a transactional multi-table
    write, so this fake answers both ``self._pool.fetchrow(...)`` and
    ``self._pool.acquire()`` from the same dispatch table.
    """

    def __init__(self, execution):
        self.execution = _execution_row(execution)
        self.delivery = _delivery_row(execution) | {
            "integration_allocation": None,
            "integration_receipts": [],
            "integration_candidate": None,
            "integration_review_receipt": None,
            "integration_review_event_id": "",
        }
        self.in_transaction = False

    def acquire(self):
        return _ContextManager(self)

    def transaction(self):
        return _Transaction(self)

    async def fetchrow(self, query, *args):
        if "UPDATE workflow_executions" in query and "owner_id = $2" in query:
            if (
                args[3] != self.execution["revision"]
                or self.execution["cancel_requested"]
                or self.execution["state"] not in {"running", "waiting", "blocked"}
            ):
                return None
            self.execution.update(revision=self.execution["revision"] + 1)
            return dict(self.execution)
        if "UPDATE delivery_executions" in query and "integration_allocation" in query:
            self.delivery.update(
                integration_allocation=json.loads(args[1]),
                integration_receipts=json.loads(args[2]),
                integration_candidate=json.loads(args[3]),
                integration_review_receipt=None,
                integration_review_event_id="",
            )
            return dict(self.delivery)
        if "UPDATE delivery_executions" in query and "integration_review_receipt" in query:
            candidate = self.delivery["integration_candidate"] or {}
            if (
                self.delivery["integration_review_event_id"]
                or candidate.get("candidate_sha") != args[2]
                or candidate.get("candidate_tree") != args[3]
            ):
                return None
            self.delivery.update(
                integration_review_receipt=json.loads(args[4]),
                integration_review_event_id=args[1],
            )
            return dict(self.delivery)
        if "UPDATE workflow_executions" in query and "owner_id" not in query:
            self.execution.update(revision=self.execution["revision"] + 1)
            return dict(self.execution)
        if query.strip().startswith("SELECT * FROM workflow_executions WHERE id"):
            return dict(self.execution)
        if query.strip().startswith("SELECT * FROM delivery_executions WHERE execution_id"):
            return dict(self.delivery)
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_records_integration_candidate_and_review_with_cas() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    connection = _IntegrationConnection(execution)
    repository = PostgresDeliveryExecutionRepository(connection)
    allocation = {"allocation_id": "integration-1", "worker_id": "integrator"}
    receipt = {"receipt_id": "integration-receipt"}
    candidate = {"candidate_sha": "b" * 40, "candidate_tree": "c" * 40}

    recorded = await repository.record_integration_candidate(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        expected_revision=execution.revision,
        allocation=allocation,
        receipts=[receipt],
        candidate=candidate,
    )
    reviewed = await repository.record_integration_review(
        execution.id,
        event_id="review-event-1",
        candidate_sha="b" * 40,
        candidate_tree="c" * 40,
        receipt={"receipt_id": "review-1"},
    )

    assert recorded.integration_candidate == candidate
    assert reviewed.integration_review_event_id == "review-event-1"
    assert reviewed.integration_review_receipt == {"receipt_id": "review-1"}


@pytest.mark.asyncio
async def test_rejects_stale_integration_candidate_revision_and_review_head() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    connection = _IntegrationConnection(execution)
    repository = PostgresDeliveryExecutionRepository(connection)

    with pytest.raises(ExecutionConflictError):
        await repository.record_integration_candidate(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            expected_revision=execution.revision + 1,
            allocation={},
            receipts=[],
            candidate={},
        )

    connection.delivery["integration_candidate"] = {
        "candidate_sha": "b" * 40,
        "candidate_tree": "c" * 40,
    }
    with pytest.raises(ExecutionConflictError):
        await repository.record_integration_review(
            execution.id,
            event_id="review-event-stale",
            candidate_sha="d" * 40,
            candidate_tree="e" * 40,
            receipt={"receipt_id": "stale"},
        )


@pytest.mark.asyncio
async def test_rejects_integration_candidate_after_cancel() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING, cancel_requested=True)
    repository = PostgresDeliveryExecutionRepository(_IntegrationConnection(execution))

    with pytest.raises(ExecutionConflictError):
        await repository.record_integration_candidate(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            expected_revision=execution.revision,
            allocation={"allocation_id": "integration-1"},
            receipts=[{"receipt_id": "receipt-1"}],
            candidate={"candidate_sha": "b" * 40, "candidate_tree": "c" * 40},
        )

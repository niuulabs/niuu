"""Transactional completion tests for the PostgreSQL developer ledger."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest

from tests.test_ting.test_developer_execution import PLAN_REVISION, _execution, _proposal
from ting.adapters.postgres_developer_executions import (
    PostgresDeveloperExecutionRepository,
    _policy_to_json,
)
from ting.domain.developer_execution import (
    ChildExecutionState,
    ChildPendingQuestion,
    ChildTaskHandle,
    ChildTaskObservation,
    ExecutionConflictError,
    ExecutionState,
    FailureKind,
    make_children,
)


def _execution_row(execution):
    return {
        "id": execution.id,
        "owner_id": execution.owner_id,
        "tenant_id": execution.tenant_id,
        "name": execution.name,
        "prompt": execution.prompt,
        "workflow_id": execution.workflow_id,
        "workflow_revision": execution.workflow_revision,
        "workflow_digest": execution.workflow_digest,
        "repository": execution.repository,
        "base_ref": execution.base_ref,
        "base_sha": execution.base_sha,
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
        "revision": execution.revision,
        "blocker_revision": execution.blocker_revision,
        "blocker_notified_revision": execution.blocker_notified_revision,
        "launch_key": execution.launch_key,
        "launch_digest": execution.launch_digest,
        "merge_receipt": execution.merge_receipt,
        "completed_at": execution.completed_at,
        "created_at": execution.created_at,
        "updated_at": execution.updated_at,
    }


def _child_row(child):
    return {
        "id": child.id,
        "execution_id": child.execution_id,
        "generation": child.generation,
        "child_key": child.key,
        "attempt": child.attempt,
        "state": child.state.value,
        "dependencies": list(child.dependencies),
        "requirement_ids": list(child.requirement_ids),
        "objective": child.objective,
        "template_id": child.template_id,
        "template_revision": child.template_revision,
        "template_digest": child.template_digest,
        "plan_digest": child.plan_digest,
        "input_digest": child.input_digest,
        "input": child.input,
        "workspace": child.workspace,
        "repository": child.repository,
        "base_sha": child.base_sha,
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
        "evidence_report": child.evidence_report,
        "evidence_validated_at": child.evidence_validated_at,
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


class _Connection:
    def __init__(self, execution, child):
        self.execution = _execution_row(execution)
        self.children = [_child_row(child)]
        self.sealed = True
        self.updated = False
        self.in_transaction = False

    def transaction(self):
        connection = self

        class _Transaction(_ContextManager):
            async def __aenter__(self):
                connection.in_transaction = True
                return connection

            async def __aexit__(self, *_args):
                connection.in_transaction = False
                return False

        return _Transaction(self)

    async def fetchrow(self, query, *args):
        if query.lstrip().startswith("SELECT * FROM developer_executions"):
            return dict(self.execution)
        if query.lstrip().startswith("UPDATE developer_executions"):
            assert self.in_transaction
            self.updated = True
            self.execution.update(
                state="completed",
                suspension_reason="delivery_merged",
                merge_receipt=args[1],
                completed_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                revision=self.execution["revision"] + 1,
            )
            return dict(self.execution)
        raise AssertionError(query)

    async def fetch(self, query, *_args):
        assert "developer_execution_children" in query
        assert self.in_transaction
        return [dict(child) for child in self.children]

    async def fetchval(self, query, *_args):
        assert "developer_execution_generations" in query
        assert self.in_transaction
        return self.sealed


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _ContextManager(self.connection)

    async def fetchrow(self, query, *args):
        return await self.connection.fetchrow(query, *args)

    async def fetch(self, query, *args):
        return await self.connection.fetch(query, *args)

    async def execute(self, query, *args):
        return await self.connection.execute(query, *args)


def _context():
    execution = replace(_execution(), current_generation=1, state=ExecutionState.WAITING)
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.COMPLETED,
    )
    connection = _Connection(execution, child)
    repository = PostgresDeveloperExecutionRepository(_Pool(connection))
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
async def test_complete_execution_locks_join_and_updates_atomically() -> None:
    execution, connection, repository, receipt = _context()

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
    execution, connection, repository, receipt = _context()
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


class _ClaimConnection:
    def __init__(self, execution, children, *, active):
        self.execution = _execution_row(execution)
        self.children = [_child_row(child) for child in children]
        self.active = active
        self.claim_limit = None
        self.in_transaction = False

    def transaction(self):
        connection = self

        class _Transaction(_ContextManager):
            async def __aenter__(self):
                connection.in_transaction = True
                return connection

            async def __aexit__(self, *_args):
                connection.in_transaction = False
                return False

        return _Transaction(self)

    async def fetchval(self, query, *_args):
        assert "SELECT COUNT(*)" in query
        assert self.in_transaction
        return self.active

    async def fetch(self, query, *args):
        assert self.in_transaction
        if "SELECT execution.*" in query:
            return [dict(self.execution)]
        assert "WITH claimable" in query
        self.claim_limit = args[1]
        rows = []
        for child in self.children[: self.claim_limit]:
            row = dict(child)
            row.update(
                state="launching",
                lease_owner=args[2],
                lease_token=args[3],
                lease_expires_at=args[4],
                fencing_generation=row["fencing_generation"] + 1,
            )
            rows.append(row)
        return rows


@pytest.mark.asyncio
async def test_claim_launches_holds_parent_lock_and_honors_active_capacity() -> None:
    execution = _execution()
    children = [
        replace(
            make_children(execution, 1, (_proposal(execution, f"child-{index}"),))[0],
            id=uuid4(),
        )
        for index in range(4)
    ]
    connection = _ClaimConnection(execution, children, active=3)
    repository = PostgresDeveloperExecutionRepository(_Pool(connection))

    claimed = await repository.claim_launches(
        worker_id="worker-1",
        limit=4,
        lease_until=datetime.now(UTC),
    )

    assert connection.claim_limit == 1
    assert len(claimed) == 1
    assert claimed[0].state == ChildExecutionState.LAUNCHING
    assert connection.in_transaction is False


@pytest.mark.asyncio
async def test_claim_launches_does_not_exceed_saturated_parent_capacity() -> None:
    execution = _execution()
    child = make_children(execution, 1, (_proposal(execution, "api"),))[0]
    connection = _ClaimConnection(
        execution,
        [child],
        active=execution.policy.max_active_children,
    )
    repository = PostgresDeveloperExecutionRepository(_Pool(connection))

    claimed = await repository.claim_launches(
        worker_id="worker-2",
        limit=10,
        lease_until=datetime.now(UTC),
    )

    assert claimed == []
    assert connection.claim_limit is None


class _IntegrationConnection:
    def __init__(self, execution):
        self.row = _execution_row(execution)
        self.row.update(
            integration_allocation=None,
            integration_receipts=[],
            integration_candidate=None,
            integration_review_receipt=None,
            integration_review_event_id="",
        )

    async def fetchrow(self, query, *args):
        candidate_update = (
            "\n            UPDATE developer_executions\n            SET integration_allocation"
        )
        if query.startswith(candidate_update):
            if (
                args[3] != self.row["revision"]
                or self.row["cancel_requested"]
                or self.row["state"] not in {"running", "waiting", "blocked"}
            ):
                return None
            self.row.update(
                integration_allocation=json.loads(args[4]),
                integration_receipts=json.loads(args[5]),
                integration_candidate=json.loads(args[6]),
                integration_review_receipt=None,
                integration_review_event_id="",
                revision=self.row["revision"] + 1,
            )
            return dict(self.row)
        if query.startswith("SELECT * FROM developer_executions WHERE id"):
            return dict(self.row)
        review_update = (
            "\n            UPDATE developer_executions\n            SET integration_review"
        )
        if query.startswith(review_update):
            candidate = self.row["integration_candidate"] or {}
            if (
                self.row["integration_review_event_id"]
                or candidate.get("candidate_sha") != args[2]
                or candidate.get("candidate_tree") != args[3]
            ):
                return None
            self.row.update(
                integration_review_receipt=json.loads(args[4]),
                integration_review_event_id=args[1],
                revision=self.row["revision"] + 1,
            )
            return dict(self.row)
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_records_integration_candidate_and_review_with_cas() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    connection = _IntegrationConnection(execution)
    repository = PostgresDeveloperExecutionRepository(connection)
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
    repository = PostgresDeveloperExecutionRepository(connection)

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

    connection.row["integration_candidate"] = {
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
    execution = replace(
        _execution(),
        state=ExecutionState.RUNNING,
        cancel_requested=True,
    )
    repository = PostgresDeveloperExecutionRepository(_IntegrationConnection(execution))

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


class _LaunchPool:
    def __init__(self, execution, *, duplicate=False, existing=None):
        self.execution = execution
        self.duplicate = duplicate
        self.existing = existing
        self.insert_args = None

    async def execute(self, query, *args):
        assert "INSERT INTO developer_executions" in query
        if self.duplicate:
            raise asyncpg.UniqueViolationError()
        self.insert_args = args
        return "INSERT 0 1"

    async def fetchrow(self, query, *args):
        assert "launch_key" in query
        assert args == (
            self.execution.owner_id,
            self.execution.tenant_id,
            self.execution.launch_key,
        )
        return self.existing


@pytest.mark.asyncio
async def test_parent_launch_persists_snapshot_and_replays_same_digest() -> None:
    execution = replace(
        _execution(),
        launch_key="ticket-123",
        launch_digest="sha256:" + "d" * 64,
        workflow_snapshot={"schemaVersion": 1, "workflow": {"id": "root"}},
    )
    pool = _LaunchPool(execution)
    repository = PostgresDeveloperExecutionRepository(pool)

    reserved, created = await repository.reserve_parent_launch(execution)

    assert created is True
    assert reserved == execution
    assert json.loads(pool.insert_args[-2]) == execution.workflow_snapshot
    assert pool.insert_args[-1] == execution.plan_revision

    replay_pool = _LaunchPool(
        execution,
        duplicate=True,
        existing=_execution_row(execution)
        | {
            "workflow_snapshot": execution.workflow_snapshot,
            "integration_receipts": [],
            "integration_allocation": None,
            "integration_candidate": None,
            "integration_review_receipt": None,
            "integration_review_event_id": "",
        },
    )
    replayed, replay_created = await PostgresDeveloperExecutionRepository(
        replay_pool
    ).reserve_parent_launch(execution)
    assert replay_created is False
    assert replayed.id == execution.id
    assert replayed.workflow_snapshot == execution.workflow_snapshot


@pytest.mark.asyncio
async def test_parent_launch_rejects_missing_or_colliding_idempotency() -> None:
    execution = _execution()
    repository = PostgresDeveloperExecutionRepository(_LaunchPool(execution))
    with pytest.raises(ExecutionConflictError, match="idempotency"):
        await repository.reserve_parent_launch(execution)

    attempted = replace(
        execution,
        launch_key="ticket-123",
        launch_digest="sha256:" + "d" * 64,
    )
    existing = replace(attempted, launch_digest="sha256:" + "e" * 64)
    pool = _LaunchPool(
        attempted,
        duplicate=True,
        existing=_execution_row(existing)
        | {
            "workflow_snapshot": {},
            "integration_receipts": [],
            "integration_allocation": None,
            "integration_candidate": None,
            "integration_review_receipt": None,
            "integration_review_event_id": "",
        },
    )
    with pytest.raises(ExecutionConflictError, match="different developer execution"):
        await PostgresDeveloperExecutionRepository(pool).reserve_parent_launch(attempted)


class _ReadPool:
    def __init__(self, execution, child=None):
        self.execution = _execution_row(execution) | {
            "workflow_snapshot": execution.workflow_snapshot,
            "integration_receipts": [],
            "integration_allocation": None,
            "integration_candidate": None,
            "integration_review_receipt": None,
            "integration_review_event_id": "",
        }
        self.child = _child_row(child) if child else None
        self.last_args = None
        self.attach_result = self.execution

    async def fetchrow(self, query, *args):
        self.last_args = args
        if "UPDATE developer_executions" in query:
            if self.attach_result is None:
                return None
            updated = dict(self.execution)
            updated.update(
                parent_session_id=args[1],
                connection_id=args[2],
                state="running",
                suspension_reason="",
                revision=updated["revision"] + 1,
            )
            return updated
        if "developer_execution_children" in query:
            return self.child
        return self.execution

    async def fetch(self, query, *args):
        self.last_args = args
        if "developer_execution_children" in query:
            return [self.child] if self.child else []
        second = dict(self.execution)
        second["id"] = uuid4()
        second["updated_at"] = self.execution["updated_at"]
        return [self.execution, second]


@pytest.mark.asyncio
async def test_attach_read_and_page_execution_and_child_state() -> None:
    execution = replace(_execution(), suspension_reason="launching_parent")
    child = make_children(execution, 1, (_proposal(execution, "api"),))[0]
    pool = _ReadPool(execution, child)
    repository = PostgresDeveloperExecutionRepository(pool)

    attached = await repository.attach_parent_session(
        execution.id,
        session_id="parent-session",
        connection_id="forge-1",
    )
    owned = await repository.get(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
    )
    internal = await repository.get_internal(execution.id)
    by_session = await repository.get_by_parent_session(
        owner_id=execution.owner_id,
        session_id="parent-session",
    )
    children = await repository.list_children(execution.id)
    loaded_child = await repository.get_child(child.id)
    page, cursor = await repository.list(
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        limit=1,
    )

    assert attached.parent_session_id == "parent-session"
    assert attached.state == ExecutionState.RUNNING
    assert owned.id == internal.id == by_session.id == execution.id
    assert children[0].id == loaded_child.id == child.id
    assert page[0].id == execution.id
    assert cursor.endswith(str(execution.id))


@pytest.mark.asyncio
async def test_attach_parent_session_rejects_empty_and_conflicting_session() -> None:
    execution = _execution()
    pool = _ReadPool(execution)
    repository = PostgresDeveloperExecutionRepository(pool)
    with pytest.raises(ExecutionConflictError, match="required"):
        await repository.attach_parent_session(execution.id, session_id=" ", connection_id="")
    pool.attach_result = None
    with pytest.raises(ExecutionConflictError, match="different parent session"):
        await repository.attach_parent_session(
            execution.id,
            session_id="parent-2",
            connection_id="forge-1",
        )


class _GenerationConnection:
    def __init__(self, execution):
        self.execution = _execution_row(execution) | {
            "workflow_snapshot": {},
            "integration_receipts": [{"stale": True}],
            "integration_allocation": {"stale": True},
            "integration_candidate": {"stale": True},
            "integration_review_receipt": {"stale": True},
            "integration_review_event_id": "stale",
        }
        self.inserted_children = []
        self.in_transaction = False

    def transaction(self):
        connection = self

        class Transaction(_ContextManager):
            async def __aenter__(self):
                connection.in_transaction = True
                return connection

            async def __aexit__(self, *_args):
                connection.in_transaction = False
                return False

        return Transaction(self)

    async def fetchrow(self, query, *args):
        if query.lstrip().startswith("SELECT"):
            return dict(self.execution)
        if query.lstrip().startswith("UPDATE"):
            updated = dict(self.execution)
            updated.update(
                current_generation=args[1],
                reserved_budget=updated["reserved_budget"] + args[2],
                plan_revision=args[3],
                state="waiting",
                suspension_reason="awaiting_children",
                revision=updated["revision"] + 1,
                integration_receipts=[],
                integration_allocation=None,
                integration_candidate=None,
                integration_review_receipt=None,
                integration_review_event_id="",
            )
            return updated
        raise AssertionError(query)

    async def execute(self, query, *args):
        assert self.in_transaction
        assert "developer_execution_generations" in query
        return "INSERT 0 1"

    async def executemany(self, query, rows):
        assert self.in_transaction
        self.inserted_children = list(rows)


@pytest.mark.asyncio
async def test_reserve_generation_atomically_reserves_budget_and_clears_stale_integration() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    child = make_children(execution, 1, (_proposal(execution, "api"),))[0]
    connection = _GenerationConnection(execution)
    repository = PostgresDeveloperExecutionRepository(_Pool(connection))

    updated = await repository.reserve_generation(
        execution,
        (child,),
        plan_revision=PLAN_REVISION,
    )

    assert updated.current_generation == 1
    assert updated.budget.reserved_units == child.budget_units
    assert updated.plan_revision == PLAN_REVISION
    assert updated.integration_candidate is None
    assert len(connection.inserted_children) == 1
    assert connection.in_transaction is False


@pytest.mark.parametrize("mutation", ["stale", "canceled", "generation", "budget"])
@pytest.mark.asyncio
async def test_reserve_generation_rejects_changed_parent_state(mutation: str) -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    child = make_children(execution, 1, (_proposal(execution, "api"),))[0]
    connection = _GenerationConnection(execution)
    if mutation == "stale":
        connection.execution["revision"] += 1
    elif mutation == "canceled":
        connection.execution["cancel_requested"] = True
    elif mutation == "generation":
        connection.execution["current_generation"] = 1
    else:
        connection.execution["reserved_budget"] = execution.budget.total_units

    with pytest.raises(ExecutionConflictError):
        await PostgresDeveloperExecutionRepository(_Pool(connection)).reserve_generation(
            execution,
            (child,),
            plan_revision=PLAN_REVISION,
        )

    assert connection.inserted_children == []
    assert connection.in_transaction is False


@pytest.mark.asyncio
async def test_reserve_generation_rejects_missing_plan_revision_before_writes() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    child = make_children(execution, 1, (_proposal(execution, "api"),))[0]
    connection = _GenerationConnection(execution)

    with pytest.raises(ExecutionConflictError, match="plan_revision is required"):
        await PostgresDeveloperExecutionRepository(_Pool(connection)).reserve_generation(
            execution,
            (child,),
            plan_revision="",
        )

    assert connection.inserted_children == []
    assert connection.in_transaction is False


class _ChildMutationConnection:
    def __init__(
        self,
        child,
        *,
        replay=False,
        fenced=False,
        cancellation_race=False,
        parent_cancel_requested=False,
    ):
        self.row = _child_row(child)
        self.row["remote_observed_at"] = None
        self.replay = replay
        self.fenced = fenced
        self.cancellation_race = cancellation_race
        self.parent_cancel_requested = parent_cancel_requested
        self.cancellation_query = ""
        self.in_transaction = False
        self.parent_blocker_updates = 0

    def transaction(self):
        connection = self

        class Transaction(_ContextManager):
            async def __aenter__(self):
                connection.in_transaction = True
                return connection

            async def __aexit__(self, *_args):
                connection.in_transaction = False
                return False

        return Transaction(self)

    async def execute(self, query, *args):
        if "INSERT INTO developer_child_events" in query:
            return "INSERT 0 0" if self.replay else "INSERT 0 1"
        if "SET blocker_revision = blocker_revision + 1" in query:
            self.parent_blocker_updates += 1
            return "UPDATE 1"
        raise AssertionError(query)

    async def fetchrow(self, query, *args):
        if "SET state = 'submitted'" in query:
            if self.fenced or self.cancellation_race:
                return None
            self.row.update(
                state="submitted",
                task_id=args[1],
                context_id=args[2],
                lease_owner="",
                lease_token=None,
                lease_expires_at=None,
            )
            return dict(self.row)
        if "SET state = 'canceling'" in query:
            if not self.cancellation_race:
                return None
            self.cancellation_query = query
            self.row.update(
                state="canceling",
                task_id=args[1],
                context_id=args[2],
                lease_owner="",
                lease_token=None,
                lease_expires_at=None,
            )
            return dict(self.row)
        if "SELECT id, cancel_requested FROM developer_executions" in query:
            return {
                "id": self.row["execution_id"],
                "cancel_requested": self.parent_cancel_requested,
            }
        if query.lstrip().startswith("SELECT"):
            return dict(self.row)
        if "SET last_polled_at = NOW()" in query and "SET state = $2" not in query:
            self.row["last_polled_at"] = datetime.now(UTC)
            return dict(self.row)
        if "SET state = $2" in query:
            self.row.update(
                state=args[1],
                result=json.loads(args[2]) if args[2] else None,
                artifacts=json.loads(args[3]),
                failure_kind=args[4],
                error=args[5],
                remote_observed_at=args[6],
                pending_questions=json.loads(args[7]),
                pending_gates=json.loads(args[8]),
                last_polled_at=datetime.now(UTC),
            )
            return dict(self.row)
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_record_handle_is_lease_fenced_and_observation_is_replay_safe() -> None:
    execution = _execution()
    lease_token = uuid4()
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.LAUNCHING,
        lease_owner="worker-1",
        lease_token=lease_token,
        fencing_generation=2,
    )
    connection = _ChildMutationConnection(child)
    repository = PostgresDeveloperExecutionRepository(_Pool(connection))
    submitted = await repository.record_handle(
        child,
        ChildTaskHandle("agent-1", "task-1", "context-1"),
        worker_id="worker-1",
        lease_token=lease_token,
        fencing_generation=2,
    )
    observation = ChildTaskObservation(
        handle=ChildTaskHandle("agent-1", "task-1", "context-1"),
        state=ChildExecutionState.RUNNING,
        result=None,
        artifacts=(),
        observed_at=datetime.now(UTC),
        event_id="event-1",
    )
    projected = await repository.record_observation(submitted, observation)

    assert submitted.state == ChildExecutionState.SUBMITTED
    assert projected.state == ChildExecutionState.RUNNING
    assert connection.in_transaction is False

    replay_connection = _ChildMutationConnection(projected, replay=True)
    replayed = await PostgresDeveloperExecutionRepository(
        _Pool(replay_connection)
    ).record_observation(projected, observation)
    assert replayed.state == ChildExecutionState.RUNNING
    assert replay_connection.row["last_polled_at"] is not None

    fenced = _ChildMutationConnection(child, fenced=True)
    with pytest.raises(ExecutionConflictError, match="fenced"):
        await PostgresDeveloperExecutionRepository(_Pool(fenced)).record_handle(
            child,
            ChildTaskHandle("agent-1", "task-1", "context-1"),
            worker_id="other-worker",
            lease_token=uuid4(),
            fencing_generation=1,
        )

    canceled_during_launch = replace(
        child,
        state=ChildExecutionState.CANCELED,
        lease_owner="",
        lease_token=None,
        lease_expires_at=None,
    )
    raced = _ChildMutationConnection(canceled_during_launch, cancellation_race=True)
    retained = await PostgresDeveloperExecutionRepository(_Pool(raced)).record_handle(
        child,
        ChildTaskHandle("agent-1", "task-late", "context-late"),
        worker_id="worker-1",
        lease_token=lease_token,
        fencing_generation=2,
    )
    assert retained.state == ChildExecutionState.CANCELING
    assert retained.task_id == "task-late"
    assert "execution.cancel_requested = TRUE" in raced.cancellation_query
    assert "child.fencing_generation = $4" in raced.cancellation_query


@pytest.mark.asyncio
async def test_blocker_observation_advances_parent_revision_only_when_changed() -> None:
    execution = _execution()
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.RUNNING,
        task_id="task-1",
    )
    first = ChildTaskObservation(
        handle=ChildTaskHandle("agent-1", "task-1"),
        state=ChildExecutionState.BLOCKED,
        result=None,
        artifacts=(),
        observed_at=datetime.now(UTC),
        event_id="question-1",
        error="Which format?",
        pending_questions=(ChildPendingQuestion(request_id="request-1", question="Which format?"),),
    )
    first_connection = _ChildMutationConnection(child)
    blocked = await PostgresDeveloperExecutionRepository(
        _Pool(first_connection)
    ).record_observation(child, first)
    assert blocked.state == ChildExecutionState.BLOCKED
    assert blocked.pending_questions[0].request_id == "request-1"
    assert first_connection.parent_blocker_updates == 1

    repeated_connection = _ChildMutationConnection(blocked)
    repeated = replace(first, event_id="question-2", observed_at=datetime.now(UTC))
    await PostgresDeveloperExecutionRepository(_Pool(repeated_connection)).record_observation(
        blocked,
        repeated,
    )
    assert repeated_connection.parent_blocker_updates == 0

    changed_connection = _ChildMutationConnection(blocked)
    changed = replace(
        first,
        event_id="question-3",
        observed_at=datetime.now(UTC),
        pending_questions=(ChildPendingQuestion(request_id="request-2", question="Which format?"),),
    )
    await PostgresDeveloperExecutionRepository(_Pool(changed_connection)).record_observation(
        blocked,
        changed,
    )
    assert changed_connection.parent_blocker_updates == 1


@pytest.mark.asyncio
async def test_observation_cannot_overwrite_terminal_or_newer_remote_state() -> None:
    execution = _execution()
    terminal = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.COMPLETED,
    )
    observation = ChildTaskObservation(
        handle=ChildTaskHandle("agent-1", "task-1"),
        state=ChildExecutionState.RUNNING,
        result=None,
        artifacts=(),
        observed_at=datetime.now(UTC),
        event_id="late-event",
    )
    connection = _ChildMutationConnection(terminal)
    kept = await PostgresDeveloperExecutionRepository(_Pool(connection)).record_observation(
        terminal,
        observation,
    )
    assert kept.state == ChildExecutionState.COMPLETED

    active = replace(terminal, state=ChildExecutionState.RUNNING)
    connection = _ChildMutationConnection(active)
    connection.row["remote_observed_at"] = datetime.now(UTC)
    stale = replace(observation, observed_at=execution.created_at, event_id="stale-event")
    kept = await PostgresDeveloperExecutionRepository(_Pool(connection)).record_observation(
        active,
        stale,
    )
    assert kept.state == ChildExecutionState.RUNNING


@pytest.mark.asyncio
async def test_observation_cannot_reverse_durable_child_cancellation() -> None:
    execution = _execution()
    canceling = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.CANCELING,
        task_id="task-api",
    )
    in_flight_observation = ChildTaskObservation(
        handle=ChildTaskHandle("agent-1", "task-api"),
        state=ChildExecutionState.RUNNING,
        result=None,
        artifacts=(),
        observed_at=datetime.now(UTC),
        event_id="running-after-parent-failure",
    )
    connection = _ChildMutationConnection(
        canceling,
        parent_cancel_requested=True,
    )

    kept = await PostgresDeveloperExecutionRepository(_Pool(connection)).record_observation(
        canceling,
        in_flight_observation,
    )

    assert kept.state == ChildExecutionState.CANCELING
    assert connection.row["state"] == ChildExecutionState.CANCELING.value


class _ResultPool:
    def __init__(self, result="UPDATE 1", generation=None, children=()):
        self.result = result
        self.generation = generation
        self.children = list(children)

    async def execute(self, query, *args):
        return self.result

    async def fetchrow(self, query, *args):
        return self.generation

    async def fetch(self, query, *args):
        return self.children


@pytest.mark.asyncio
async def test_reconcilable_children_rotate_by_durable_poll_cursor() -> None:
    class CapturePool:
        query = ""

        async def fetch(self, query, *_args):
            self.query = query
            return []

    pool = CapturePool()
    assert await PostgresDeveloperExecutionRepository(pool).list_reconcilable(limit=5) == []
    assert "COALESCE(child.last_polled_at, child.created_at)" in pool.query
    assert "execution.state IN ('canceled', 'failed')" in pool.query
    assert "execution.cancel_requested = TRUE" in pool.query
    assert "child.state = 'canceling'" in pool.query


@pytest.mark.asyncio
async def test_list_deadline_expired_children_filters_by_deadline_and_terminal_states() -> None:
    class CapturePool:
        query = ""
        args: tuple = ()

        async def fetch(self, query, *args):
            self.query = query
            self.args = args
            return []

    pool = CapturePool()
    now = datetime.now(UTC)

    result = await PostgresDeveloperExecutionRepository(pool).list_deadline_expired_children(
        now=now, limit=25
    )

    assert result == []
    assert pool.args == (now, 25)
    assert "child.deadline < $1" in pool.query
    assert "child.state NOT IN ('canceled', 'completed', 'failed', 'superseded')" in pool.query
    assert "execution.state NOT IN ('canceled', 'completed', 'failed')" in pool.query


@pytest.mark.asyncio
async def test_list_deadline_expired_executions_filters_by_deadline_and_terminal_states() -> None:
    class CapturePool:
        query = ""
        args: tuple = ()

        async def fetch(self, query, *args):
            self.query = query
            self.args = args
            return []

    pool = CapturePool()
    now = datetime.now(UTC)

    result = await PostgresDeveloperExecutionRepository(pool).list_deadline_expired_executions(
        now=now, limit=10
    )

    assert result == []
    assert pool.args == (now, 10)
    assert "deadline < $1" in pool.query
    assert "state NOT IN ('canceled', 'completed', 'failed')" in pool.query


class _FailureTransitionConnection:
    def __init__(self, execution, children):
        self.execution = _execution_row(execution)
        self.children = [_child_row(child) for child in children]
        self.in_transaction = False
        self.failure_update_query = ""
        self.child_update_query = ""

    def transaction(self):
        connection = self

        class Transaction(_ContextManager):
            async def __aenter__(self):
                connection.in_transaction = True
                return connection

            async def __aexit__(self, *_args):
                connection.in_transaction = False
                return False

        return Transaction(self)

    async def fetchrow(self, query, *args):
        assert self.in_transaction
        if "SELECT state, suspension_reason, revision" in query:
            return dict(self.execution)
        self.failure_update_query = query
        if (
            self.execution["state"]
            in {
                ExecutionState.CANCELED.value,
                ExecutionState.COMPLETED.value,
                ExecutionState.FAILED.value,
            }
            or self.execution["cancel_requested"]
        ):
            return None
        self.execution.update(
            state=ExecutionState.FAILED.value,
            suspension_reason=args[1],
            cancel_requested=True,
            revision=self.execution["revision"] + 1,
        )
        return {"id": self.execution["id"]}

    async def execute(self, query, *_args):
        assert self.in_transaction
        self.child_update_query = query
        terminal = {
            ChildExecutionState.CANCELED.value,
            ChildExecutionState.COMPLETED.value,
            ChildExecutionState.FAILED.value,
            ChildExecutionState.SUPERSEDED.value,
        }
        for child in self.children:
            if child["state"] in terminal:
                continue
            child["state"] = (
                ChildExecutionState.CANCELED.value
                if child["state"]
                in {ChildExecutionState.RESERVED.value, ChildExecutionState.LAUNCHING.value}
                else ChildExecutionState.CANCELING.value
            )
            child.update(lease_owner="", lease_token=None, lease_expires_at=None)
        return "UPDATE 2"


@pytest.mark.asyncio
async def test_failure_projection_atomically_preserves_reason_and_cancels_children() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    reserved, submitted, completed = make_children(
        execution,
        1,
        (
            _proposal(execution, "reserved"),
            _proposal(execution, "submitted"),
            _proposal(execution, "completed"),
        ),
    )
    submitted = replace(
        submitted,
        state=ChildExecutionState.SUBMITTED,
        task_id="task-submitted",
    )
    completed = replace(completed, state=ChildExecutionState.COMPLETED)
    connection = _FailureTransitionConnection(
        execution,
        [reserved, submitted, completed],
    )

    await PostgresDeveloperExecutionRepository(_Pool(connection)).update_execution_state(
        execution.id,
        state=ExecutionState.FAILED.value,
        suspension_reason="invalid planning review",
        expected_revision=execution.revision,
    )

    assert connection.execution["state"] == ExecutionState.FAILED.value
    assert connection.execution["suspension_reason"] == "invalid planning review"
    assert connection.execution["cancel_requested"] is True
    assert [child["state"] for child in connection.children] == [
        ChildExecutionState.CANCELED.value,
        ChildExecutionState.CANCELING.value,
        ChildExecutionState.COMPLETED.value,
    ]
    assert "state IN ('reserved', 'launching')" in connection.child_update_query
    assert connection.in_transaction is False


@pytest.mark.parametrize("terminal", [ExecutionState.CANCELED, ExecutionState.COMPLETED])
@pytest.mark.asyncio
async def test_failure_projection_does_not_overwrite_terminal_parent(
    terminal: ExecutionState,
) -> None:
    execution = replace(
        _execution(),
        state=terminal,
        suspension_reason="terminal reason",
    )
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.SUBMITTED,
        task_id="task-api",
    )
    connection = _FailureTransitionConnection(execution, [child])

    with pytest.raises(ExecutionConflictError, match="changed since this projection"):
        await PostgresDeveloperExecutionRepository(_Pool(connection)).update_execution_state(
            execution.id,
            state=ExecutionState.FAILED.value,
            suspension_reason="later failure",
            expected_revision=execution.revision,
        )

    assert connection.execution["state"] == terminal.value
    assert connection.execution["suspension_reason"] == "terminal reason"
    assert connection.children[0]["state"] == ChildExecutionState.SUBMITTED.value
    assert connection.child_update_query == ""


@pytest.mark.asyncio
async def test_failure_projection_does_not_overwrite_concurrent_cancellation() -> None:
    execution = replace(
        _execution(),
        state=ExecutionState.CANCELING,
        suspension_reason="canceling_children",
        cancel_requested=True,
    )
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.CANCELING,
        task_id="task-api",
    )
    connection = _FailureTransitionConnection(execution, [child])

    with pytest.raises(ExecutionConflictError, match="changed since this projection"):
        await PostgresDeveloperExecutionRepository(_Pool(connection)).update_execution_state(
            execution.id,
            state=ExecutionState.FAILED.value,
            suspension_reason="later failure",
            expected_revision=execution.revision,
        )

    assert connection.execution["state"] == ExecutionState.CANCELING.value
    assert connection.execution["suspension_reason"] == "canceling_children"
    assert connection.execution["cancel_requested"] is True
    assert connection.children[0]["state"] == ChildExecutionState.CANCELING.value
    assert connection.child_update_query == ""
    assert "cancel_requested = FALSE" in connection.failure_update_query


class _StateProjectionPool:
    """Backs the non-FAILED `update_execution_state` path: execute, then a
    follow-up fetchrow only when the update matched zero rows."""

    def __init__(self, *, execute_result: str, current_row: dict | None) -> None:
        self.execute_result = execute_result
        self.current_row = current_row
        self.fetchrow_called = False

    async def execute(self, query, *args):
        return self.execute_result

    async def fetchrow(self, query, *args):
        self.fetchrow_called = True
        return self.current_row


@pytest.mark.asyncio
async def test_update_execution_state_noop_when_already_applied_at_expected_revision() -> None:
    """A repeated call with the target values already current is not a conflict."""
    pool = _StateProjectionPool(
        execute_result="UPDATE 0",
        current_row={
            "state": ExecutionState.WAITING.value,
            "suspension_reason": "awaiting_children",
            "revision": 3,
        },
    )
    repository = PostgresDeveloperExecutionRepository(pool)

    await repository.update_execution_state(
        uuid4(),
        state=ExecutionState.WAITING.value,
        suspension_reason="awaiting_children",
        expected_revision=3,
    )

    assert pool.fetchrow_called is True


@pytest.mark.asyncio
async def test_update_execution_state_raises_conflict_on_stale_revision() -> None:
    """A genuinely stale caller (execution moved on) must raise, not silently no-op."""
    pool = _StateProjectionPool(
        execute_result="UPDATE 0",
        current_row={
            "state": ExecutionState.BLOCKED.value,
            "suspension_reason": "child_blocked",
            "revision": 4,
        },
    )
    repository = PostgresDeveloperExecutionRepository(pool)

    with pytest.raises(ExecutionConflictError, match="changed since this projection"):
        await repository.update_execution_state(
            uuid4(),
            state=ExecutionState.WAITING.value,
            suspension_reason="awaiting_children",
            expected_revision=3,
        )


@pytest.mark.asyncio
async def test_update_execution_state_applies_change_on_match() -> None:
    pool = _StateProjectionPool(execute_result="UPDATE 1", current_row=None)
    repository = PostgresDeveloperExecutionRepository(pool)

    await repository.update_execution_state(
        uuid4(),
        state=ExecutionState.WAITING.value,
        suspension_reason="awaiting_children",
        expected_revision=3,
    )

    assert pool.fetchrow_called is False


class _ReconcileErrorConnection:
    def __init__(self, child: dict, execution: dict) -> None:
        self.child = child
        self.execution = execution
        self.in_transaction = False
        self.blocker_bumped = False

    def transaction(self):
        connection = self

        class _Transaction(_ContextManager):
            async def __aenter__(self):
                connection.in_transaction = True
                return connection

            async def __aexit__(self, *_args):
                connection.in_transaction = False
                return False

        return _Transaction(self)

    async def fetchrow(self, query, *args):
        assert self.in_transaction
        query = query.lstrip()
        if "SET reconcile_failure_count = reconcile_failure_count + 1" in query:
            terminal = {"canceled", "completed", "failed", "superseded"}
            if self.child["state"] in terminal:
                return None
            self.child.update(
                reconcile_failure_count=self.child["reconcile_failure_count"] + 1,
                error=args[1],
            )
            return dict(self.child)
        if "SET state = 'failed'" in query:
            terminal = {"canceled", "completed", "failed", "superseded"}
            if self.child["state"] in terminal:
                return None
            self.child.update(state="failed", failure_kind=args[1])
            return dict(self.child)
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def execute(self, query, *_args):
        assert self.in_transaction
        assert "blocker_revision = blocker_revision + 1" in query
        self.blocker_bumped = True
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_record_reconcile_error_below_threshold_records_error_without_failing() -> None:
    execution = _execution()
    child = _child_row(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
    ) | {"reconcile_failure_count": 0}
    connection = _ReconcileErrorConnection(child, _execution_row(execution))
    repository = PostgresDeveloperExecutionRepository(_Pool(connection))

    updated = await repository.record_reconcile_error(
        child["id"],
        error="boom",
        failure_kind="transient",
        max_consecutive_failures=3,
    )

    assert updated is not None
    assert updated.state != ChildExecutionState.FAILED
    assert updated.error == "boom"
    assert connection.child["reconcile_failure_count"] == 1
    assert connection.blocker_bumped is False


@pytest.mark.asyncio
async def test_record_reconcile_error_at_threshold_fails_child_and_bumps_blocker() -> None:
    execution = _execution()
    child = _child_row(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
    ) | {"reconcile_failure_count": 1}
    connection = _ReconcileErrorConnection(child, _execution_row(execution))
    repository = PostgresDeveloperExecutionRepository(_Pool(connection))

    updated = await repository.record_reconcile_error(
        child["id"],
        error="boom again",
        failure_kind="transient",
        max_consecutive_failures=2,
    )

    assert updated is not None
    assert updated.state == ChildExecutionState.FAILED
    assert updated.failure_kind == FailureKind.TRANSIENT
    assert connection.blocker_bumped is True


@pytest.mark.asyncio
async def test_record_reconcile_error_returns_none_for_missing_or_terminal_child() -> None:
    execution = _execution()
    child = _child_row(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
    ) | {"reconcile_failure_count": 0, "state": ChildExecutionState.COMPLETED.value}
    connection = _ReconcileErrorConnection(child, _execution_row(execution))
    repository = PostgresDeveloperExecutionRepository(_Pool(connection))

    result = await repository.record_reconcile_error(
        child["id"],
        error="too late",
        failure_kind="transient",
        max_consecutive_failures=3,
    )

    assert result is None


@pytest.mark.asyncio
async def test_record_reconcile_error_rejects_non_positive_threshold() -> None:
    repository = PostgresDeveloperExecutionRepository(_ResultPool())
    with pytest.raises(ValueError, match="must be positive"):
        await repository.record_reconcile_error(
            uuid4(),
            error="boom",
            failure_kind="transient",
            max_consecutive_failures=0,
        )


@pytest.mark.asyncio
async def test_evidence_seal_and_join_reject_disappeared_rows() -> None:
    execution = _execution()
    repository = PostgresDeveloperExecutionRepository(_ResultPool(result="UPDATE 0"))
    with pytest.raises(ExecutionConflictError, match="evidence child"):
        await repository.record_evidence_report(uuid4(), {"accepted": True})
    with pytest.raises(ExecutionConflictError, match="generation does not exist"):
        await repository.seal_generation(execution.id, 1)
    with pytest.raises(ExecutionConflictError, match="generation does not exist"):
        await repository.join_status(execution.id, 1)

    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.COMPLETED,
    )
    ready = await PostgresDeveloperExecutionRepository(
        _ResultPool(generation={"sealed": True}, children=[_child_row(child)])
    ).join_status(execution.id, 1)
    assert ready.ready


class _CancelConnection:
    def __init__(self, execution, *, update=True, exists=True):
        self.row = _execution_row(execution) | {
            "workflow_snapshot": {},
            "integration_receipts": [],
            "integration_allocation": None,
            "integration_candidate": None,
            "integration_review_receipt": None,
            "integration_review_event_id": "",
        }
        self.update = update
        self.exists = exists
        self.child_update = False
        self.in_transaction = False

    def transaction(self):
        connection = self

        class Transaction(_ContextManager):
            async def __aenter__(self):
                connection.in_transaction = True
                return connection

            async def __aexit__(self, *_args):
                connection.in_transaction = False
                return False

        return Transaction(self)

    async def fetchrow(self, query, *args):
        if query.lstrip().startswith("UPDATE"):
            if not self.update:
                return None
            updated = dict(self.row)
            terminal = updated["state"] in {
                ExecutionState.FAILED.value,
                ExecutionState.CANCELED.value,
            }
            updated.update(
                cancel_requested=True,
                parent_stop_requested_at=(updated["parent_stop_requested_at"] or datetime.now(UTC)),
                state=updated["state"] if terminal else "canceling",
                suspension_reason=(
                    updated["suspension_reason"] if terminal else "canceling_children"
                ),
                revision=(
                    updated["revision"] if updated["cancel_requested"] else updated["revision"] + 1
                ),
            )
            self.row = updated
            return updated
        return dict(self.row) if self.exists else None

    async def execute(self, query, *args):
        self.child_update = True
        return "UPDATE 1"


class _ParentStopConnection:
    def __init__(self, execution):
        self.row = _execution_row(execution)
        self.limit = None
        self.marked = None

    async def fetch(self, query, *args):
        assert "parent_stop_requested_at IS NOT NULL" in query
        assert "parent_stopped_at IS NULL" in query
        self.limit = args[0]
        return [dict(self.row)]

    async def execute(self, query, *args):
        assert "parent_stopped_at = COALESCE(parent_stopped_at, NOW())" in query
        self.marked = args[0]
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_parent_stop_cleanup_lists_and_marks_durable_intent() -> None:
    requested_at = datetime.now(UTC)
    execution = replace(
        _execution(),
        state=ExecutionState.CANCELED,
        cancel_requested=True,
        parent_stop_requested_at=requested_at,
    )
    connection = _ParentStopConnection(execution)
    repository = PostgresDeveloperExecutionRepository(_Pool(connection))

    pending = await repository.list_parent_stop_pending(limit=7)
    await repository.mark_parent_stopped(execution.id)

    assert [item.id for item in pending] == [execution.id]
    assert pending[0].parent_stop_requested_at == requested_at
    assert connection.limit == 7
    assert connection.marked == execution.id


@pytest.mark.asyncio
async def test_list_parent_stop_pending_orders_fresh_rows_before_failing_ones() -> None:
    class CapturePool:
        query = ""

        async def fetch(self, query, *_args):
            self.query = query
            return []

    pool = CapturePool()

    result = await PostgresDeveloperExecutionRepository(pool).list_parent_stop_pending(limit=3)

    assert result == []
    assert "ORDER BY parent_stop_attempts, parent_stop_requested_at, id" in pool.query


@pytest.mark.asyncio
async def test_record_parent_stop_error_increments_and_records_reason() -> None:
    pool = _ResultPool(result="UPDATE 1")

    await PostgresDeveloperExecutionRepository(pool).record_parent_stop_error(
        uuid4(), error="transport unavailable", max_attempts=3
    )


@pytest.mark.asyncio
async def test_record_parent_stop_error_rejects_non_positive_max_attempts() -> None:
    repository = PostgresDeveloperExecutionRepository(_ResultPool())
    with pytest.raises(ValueError, match="must be positive"):
        await repository.record_parent_stop_error(uuid4(), error="boom", max_attempts=0)


@pytest.mark.asyncio
async def test_request_cancel_is_atomic_idempotent_and_owner_scoped() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    connection = _CancelConnection(execution)
    canceled = await PostgresDeveloperExecutionRepository(_Pool(connection)).request_cancel(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
    )
    assert canceled.state == ExecutionState.CANCELING
    assert canceled.cancel_requested
    assert canceled.parent_stop_requested_at is not None
    assert connection.child_update
    assert connection.in_transaction is False

    terminal = replace(execution, state=ExecutionState.COMPLETED)
    unchanged = await PostgresDeveloperExecutionRepository(
        _Pool(_CancelConnection(terminal, update=False))
    ).request_cancel(
        terminal.id,
        owner_id=terminal.owner_id,
        tenant_id=terminal.tenant_id,
    )
    assert unchanged.state == ExecutionState.COMPLETED

    failed = replace(
        execution,
        state=ExecutionState.FAILED,
        suspension_reason="invalid planning review",
    )
    failed_connection = _CancelConnection(failed)
    unchanged_failure = await PostgresDeveloperExecutionRepository(
        _Pool(failed_connection)
    ).request_cancel(
        failed.id,
        owner_id=failed.owner_id,
        tenant_id=failed.tenant_id,
    )
    assert unchanged_failure.state == ExecutionState.FAILED
    assert unchanged_failure.suspension_reason == "invalid planning review"
    assert unchanged_failure.cancel_requested is True
    assert unchanged_failure.parent_stop_requested_at is not None
    assert failed_connection.child_update is True

    previously_canceled = replace(
        execution,
        state=ExecutionState.CANCELED,
        suspension_reason="canceled",
        cancel_requested=True,
    )
    canceled_connection = _CancelConnection(previously_canceled)
    recovered_cancel = await PostgresDeveloperExecutionRepository(
        _Pool(canceled_connection)
    ).request_cancel(
        previously_canceled.id,
        owner_id=previously_canceled.owner_id,
        tenant_id=previously_canceled.tenant_id,
    )
    assert recovered_cancel.state == ExecutionState.CANCELED
    assert recovered_cancel.parent_stop_requested_at is not None

    with pytest.raises(ExecutionConflictError, match="does not exist"):
        await PostgresDeveloperExecutionRepository(
            _Pool(_CancelConnection(execution, update=False, exists=False))
        ).request_cancel(
            execution.id,
            owner_id="foreign-owner",
            tenant_id=execution.tenant_id,
        )

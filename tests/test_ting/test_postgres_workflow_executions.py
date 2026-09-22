"""Transactional tests for the domain-neutral PostgreSQL execution ledger.

These exercise ``PostgresWorkflowExecutionRepository`` directly against plain
``WorkflowExecution``/``WorkflowChildExecution`` objects — no delivery pack
concepts (repository, base ref/sha, workspace, evidence) anywhere. The
delivery extension table and its two-table transactions are covered by
``test_delivery_postgres.py``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import asyncpg
import pytest

from ting.adapters.postgres_workflow_executions import (
    PostgresWorkflowExecutionRepository,
    _policy_to_json,
)
from ting.domain.workflow_execution import (
    ChildExecutionState,
    ChildPendingQuestion,
    ChildTaskHandle,
    ChildTaskObservation,
    ChildTemplate,
    ExecutionBudget,
    ExecutionConflictError,
    ExecutionState,
    ExpansionPolicy,
    FailureKind,
    WorkflowChildProposal,
    WorkflowExecution,
    digest_json,
    make_children,
)

PLAN_REVISION = "approved-plan"


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode()).hexdigest()


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {"attemptId": {"type": "string", "minLength": 1}},
        "required": ["attemptId"],
        "additionalProperties": False,
    }


def _execution(**changes) -> WorkflowExecution:
    now = datetime.now(UTC)
    value = WorkflowExecution(
        id=uuid4(),
        name="Publish translated handbook",
        prompt="Translate, fact-check, and publish the handbook",
        owner_id="editor-1",
        tenant_id="publishing",
        workflow_id=uuid4(),
        workflow_revision=_digest("a"),
        workflow_digest=_digest("a"),
        parent_session_id="session-1",
        parent_node_id="commission-translations",
        connection_id="content-platform",
        policy=ExpansionPolicy(
            coordinator_id="editorial-coordinator",
            templates={
                "translation": ChildTemplate(
                    dependency_alias="translation-assignment",
                    id=uuid4(),
                    revision="v1",
                    digest=_digest("b"),
                ),
            },
            input_schema=_schema(),
            result_schema=_schema(),
            max_children=5,
            max_attempts=3,
            max_active_children=4,
        ),
        budget=ExecutionBudget(total_units=100),
        deadline=now + timedelta(hours=1),
        input={"handbook": "employee-handbook"},
        created_at=now,
        updated_at=now,
    )
    return replace(value, **changes)


def _proposal(execution: WorkflowExecution, key: str, dependencies=()) -> WorkflowChildProposal:
    payload = {"attemptId": f"input-{key}"}
    return WorkflowChildProposal(
        key=key,
        objective=f"Prepare the {key} edition",
        dependencies=tuple(dependencies),
        input=payload,
        input_digest=digest_json(payload),
        plan_digest=digest_json({"planRevision": PLAN_REVISION}),
        budget_units=10,
        deadline=execution.deadline,
        agent_id=f"translator-{key}",
        skill_id="translate-and-fact-check",
    )


def _execution_row(execution: WorkflowExecution) -> dict:
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
        "workflow_snapshot": execution.workflow_snapshot,
        "revision": execution.revision,
        "blocker_revision": execution.blocker_revision,
        "blocker_notified_revision": execution.blocker_notified_revision,
        "launch_key": execution.launch_key,
        "launch_digest": execution.launch_digest,
        "completed_at": execution.completed_at,
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


class _Transaction(_ContextManager):
    async def __aenter__(self):
        self.value.in_transaction = True
        return self.value

    async def __aexit__(self, *_args):
        self.value.in_transaction = False
        return False


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


def test_generic_repository_never_touches_a_delivery_table() -> None:
    """No SQL string built by the generic ledger mentions the delivery extension."""
    import inspect

    source = inspect.getsource(PostgresWorkflowExecutionRepository)
    assert "delivery_executions" not in source


class _CreateConnection:
    def __init__(self):
        self.in_transaction = False
        self.insert_args = None
        self.after_create_calls = 0

    def transaction(self):
        return _Transaction(self)

    async def execute(self, query, *args):
        assert self.in_transaction
        assert "INSERT INTO workflow_executions" in query
        self.insert_args = args
        return "INSERT 0 1"


@pytest.mark.asyncio
async def test_create_round_trips_a_plain_execution_with_no_extension_table() -> None:
    execution = _execution()
    connection = _CreateConnection()
    repository = PostgresWorkflowExecutionRepository(_Pool(connection))

    created = await repository.create(execution)

    assert created is execution
    assert connection.insert_args[0] == execution.id
    assert json.loads(connection.insert_args[-8]) == execution.input
    assert json.loads(connection.insert_args[-7]) == execution.workflow_snapshot
    assert connection.in_transaction is False


@pytest.mark.asyncio
async def test_create_raises_conflict_on_duplicate_id() -> None:
    execution = _execution()

    class _DuplicateConnection(_CreateConnection):
        async def execute(self, query, *args):
            raise asyncpg.UniqueViolationError()

    repository = PostgresWorkflowExecutionRepository(_Pool(_DuplicateConnection()))
    with pytest.raises(ExecutionConflictError):
        await repository.create(execution)


class _LaunchPool:
    def __init__(self, execution, *, duplicate=False, existing=None):
        self.execution = execution
        self.duplicate = duplicate
        self.existing = existing
        self.insert_args = None

    def acquire(self):
        return _ContextManager(self)

    def transaction(self):
        return _Transaction(self)

    async def execute(self, query, *args):
        assert "INSERT INTO workflow_executions" in query
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
async def test_parent_launch_reserves_once_and_replays_the_same_digest() -> None:
    execution = replace(
        _execution(),
        launch_key="ticket-123",
        launch_digest="sha256:" + "d" * 64,
    )
    pool = _LaunchPool(execution)
    repository = PostgresWorkflowExecutionRepository(pool)

    reserved, created = await repository.reserve_parent_launch(execution)

    assert created is True
    assert reserved == execution

    replay_pool = _LaunchPool(execution, duplicate=True, existing=_execution_row(execution))
    replayed, replay_created = await PostgresWorkflowExecutionRepository(
        replay_pool
    ).reserve_parent_launch(execution)
    assert replay_created is False
    assert replayed.id == execution.id


@pytest.mark.asyncio
async def test_parent_launch_rejects_missing_or_colliding_idempotency() -> None:
    execution = _execution()
    repository = PostgresWorkflowExecutionRepository(_LaunchPool(execution))
    with pytest.raises(ExecutionConflictError, match="idempotency"):
        await repository.reserve_parent_launch(execution)

    attempted = replace(execution, launch_key="ticket-123", launch_digest="sha256:" + "d" * 64)
    existing = replace(attempted, launch_digest="sha256:" + "e" * 64)
    pool = _LaunchPool(attempted, duplicate=True, existing=_execution_row(existing))
    with pytest.raises(ExecutionConflictError, match="different workflow execution"):
        await PostgresWorkflowExecutionRepository(pool).reserve_parent_launch(attempted)


class _ReadPool:
    def __init__(self, execution, child=None):
        self.execution = _execution_row(execution)
        self.child = _child_row(child) if child else None
        self.attach_result = self.execution

    async def fetchrow(self, query, *args):
        if "UPDATE workflow_executions" in query:
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
        if "workflow_execution_children" in query:
            return self.child
        return self.execution

    async def fetch(self, query, *args):
        if "workflow_execution_children" in query:
            return [self.child] if self.child else []
        second = dict(self.execution)
        second["id"] = uuid4()
        second["updated_at"] = self.execution["updated_at"]
        return [self.execution, second]


@pytest.mark.asyncio
async def test_attach_read_and_page_execution_and_child_state() -> None:
    execution = replace(
        _execution(),
        suspension_reason="launching_parent",
        workflow_snapshot={"graph": {"nodes": [], "edges": []}, "name": "Publish handbook"},
    )
    child = make_children(execution, 1, (_proposal(execution, "en"),))[0]
    pool = _ReadPool(execution, child)
    repository = PostgresWorkflowExecutionRepository(pool)

    attached = await repository.attach_parent_session(
        execution.id, session_id="parent-session", connection_id="forge-1"
    )
    owned = await repository.get(
        execution.id, owner_id=execution.owner_id, tenant_id=execution.tenant_id
    )
    internal = await repository.get_internal(execution.id)
    by_session = await repository.get_by_parent_session(
        owner_id=execution.owner_id, session_id="parent-session"
    )
    children = await repository.list_children(execution.id)
    loaded_child = await repository.get_child(child.id)
    page, cursor = await repository.list(
        owner_id=execution.owner_id, tenant_id=execution.tenant_id, limit=1
    )

    assert attached.parent_session_id == "parent-session"
    assert attached.state == ExecutionState.RUNNING
    assert owned.id == internal.id == by_session.id == execution.id
    assert owned.input == execution.input
    # A plain, non-delivery execution carries its pinned workflow snapshot
    # through the generic ledger with no delivery extension table involved.
    assert owned.workflow_snapshot == execution.workflow_snapshot
    assert internal.workflow_snapshot == execution.workflow_snapshot
    assert children[0].id == loaded_child.id == child.id
    assert page[0].id == execution.id
    assert cursor.endswith(str(execution.id))


@pytest.mark.asyncio
async def test_attach_parent_session_rejects_empty_and_conflicting_session() -> None:
    execution = _execution()
    pool = _ReadPool(execution)
    repository = PostgresWorkflowExecutionRepository(pool)
    with pytest.raises(ExecutionConflictError, match="required"):
        await repository.attach_parent_session(execution.id, session_id=" ", connection_id="")
    pool.attach_result = None
    with pytest.raises(ExecutionConflictError, match="different parent session"):
        await repository.attach_parent_session(
            execution.id, session_id="parent-2", connection_id="forge-1"
        )


class _GenerationConnection:
    def __init__(self, execution):
        self.execution = _execution_row(execution)
        self.inserted_children = []
        self.in_transaction = False
        self.after_reserve_calls = 0

    def transaction(self):
        return _Transaction(self)

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
            )
            return updated
        raise AssertionError(query)

    async def execute(self, query, *args):
        assert self.in_transaction
        assert "workflow_execution_generations" in query
        return "INSERT 0 1"

    async def executemany(self, query, rows):
        assert self.in_transaction
        self.inserted_children = list(rows)


@pytest.mark.asyncio
async def test_reserve_generation_atomically_reserves_budget() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    child = make_children(execution, 1, (_proposal(execution, "en"),))[0]
    connection = _GenerationConnection(execution)
    repository = PostgresWorkflowExecutionRepository(_Pool(connection))

    updated = await repository.reserve_generation(execution, (child,), plan_revision=PLAN_REVISION)

    assert updated.current_generation == 1
    assert updated.budget.reserved_units == child.budget_units
    assert updated.plan_revision == PLAN_REVISION
    assert len(connection.inserted_children) == 1
    assert connection.in_transaction is False


@pytest.mark.parametrize("mutation", ["stale", "canceled", "generation", "budget"])
@pytest.mark.asyncio
async def test_reserve_generation_rejects_changed_parent_state(mutation: str) -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    child = make_children(execution, 1, (_proposal(execution, "en"),))[0]
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
        await PostgresWorkflowExecutionRepository(_Pool(connection)).reserve_generation(
            execution, (child,), plan_revision=PLAN_REVISION
        )

    assert connection.inserted_children == []
    assert connection.in_transaction is False


@pytest.mark.asyncio
async def test_reserve_generation_rejects_missing_plan_revision_before_writes() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    child = make_children(execution, 1, (_proposal(execution, "en"),))[0]
    connection = _GenerationConnection(execution)

    with pytest.raises(ExecutionConflictError, match="plan_revision is required"):
        await PostgresWorkflowExecutionRepository(_Pool(connection)).reserve_generation(
            execution, (child,), plan_revision=""
        )

    assert connection.inserted_children == []


class _ClaimConnection:
    def __init__(self, execution, children, *, active):
        self.execution = _execution_row(execution)
        self.children = [_child_row(child) for child in children]
        self.active = active
        self.claim_limit = None
        self.claim_query = ""
        self.in_transaction = False

    def transaction(self):
        return _Transaction(self)

    async def fetchval(self, query, *_args):
        assert "SELECT COUNT(*)" in query
        assert self.in_transaction
        return self.active

    async def fetch(self, query, *args):
        assert self.in_transaction
        if "SELECT execution.*" in query:
            self.claim_query = query
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
        replace(make_children(execution, 1, (_proposal(execution, f"child-{i}"),))[0], id=uuid4())
        for i in range(4)
    ]
    connection = _ClaimConnection(execution, children, active=3)
    repository = PostgresWorkflowExecutionRepository(_Pool(connection))

    claimed = await repository.claim_launches(
        worker_id="worker-1", limit=4, lease_until=datetime.now(UTC)
    )

    assert connection.claim_limit == 1
    assert len(claimed) == 1
    assert claimed[0].state == ChildExecutionState.LAUNCHING
    assert connection.in_transaction is False


@pytest.mark.asyncio
async def test_claim_launches_does_not_exceed_saturated_parent_capacity() -> None:
    execution = _execution()
    child = make_children(execution, 1, (_proposal(execution, "en"),))[0]
    connection = _ClaimConnection(execution, [child], active=execution.policy.max_active_children)
    repository = PostgresWorkflowExecutionRepository(_Pool(connection))

    claimed = await repository.claim_launches(
        worker_id="worker-2", limit=10, lease_until=datetime.now(UTC)
    )

    assert claimed == []
    assert connection.claim_limit is None


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
        return _Transaction(self)

    async def execute(self, query, *args):
        if "INSERT INTO workflow_child_events" in query:
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
        if "SELECT id, cancel_requested FROM workflow_executions" in query:
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
        make_children(execution, 1, (_proposal(execution, "en"),))[0],
        state=ChildExecutionState.LAUNCHING,
        lease_owner="worker-1",
        lease_token=lease_token,
        fencing_generation=2,
    )
    connection = _ChildMutationConnection(child)
    repository = PostgresWorkflowExecutionRepository(_Pool(connection))
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
    replayed = await PostgresWorkflowExecutionRepository(
        _Pool(replay_connection)
    ).record_observation(projected, observation)
    assert replayed.state == ChildExecutionState.RUNNING
    assert replay_connection.row["last_polled_at"] is not None

    fenced = _ChildMutationConnection(child, fenced=True)
    with pytest.raises(ExecutionConflictError, match="fenced"):
        await PostgresWorkflowExecutionRepository(_Pool(fenced)).record_handle(
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
    retained = await PostgresWorkflowExecutionRepository(_Pool(raced)).record_handle(
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
        make_children(execution, 1, (_proposal(execution, "en"),))[0],
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
    blocked = await PostgresWorkflowExecutionRepository(_Pool(first_connection)).record_observation(
        child, first
    )
    assert blocked.state == ChildExecutionState.BLOCKED
    assert blocked.pending_questions[0].request_id == "request-1"
    assert first_connection.parent_blocker_updates == 1

    repeated_connection = _ChildMutationConnection(blocked)
    repeated = replace(first, event_id="question-2", observed_at=datetime.now(UTC))
    await PostgresWorkflowExecutionRepository(_Pool(repeated_connection)).record_observation(
        blocked, repeated
    )
    assert repeated_connection.parent_blocker_updates == 0


@pytest.mark.asyncio
async def test_observation_cannot_overwrite_terminal_or_newer_remote_state() -> None:
    execution = _execution()
    terminal = replace(
        make_children(execution, 1, (_proposal(execution, "en"),))[0],
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
    kept = await PostgresWorkflowExecutionRepository(_Pool(connection)).record_observation(
        terminal, observation
    )
    assert kept.state == ChildExecutionState.COMPLETED


@pytest.mark.asyncio
async def test_observation_cannot_reverse_durable_child_cancellation() -> None:
    execution = _execution()
    canceling = replace(
        make_children(execution, 1, (_proposal(execution, "en"),))[0],
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
    connection = _ChildMutationConnection(canceling, parent_cancel_requested=True)

    kept = await PostgresWorkflowExecutionRepository(_Pool(connection)).record_observation(
        canceling, in_flight_observation
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
async def test_gate_report_seal_and_join_reject_disappeared_rows() -> None:
    execution = _execution()
    repository = PostgresWorkflowExecutionRepository(_ResultPool(result="UPDATE 0"))
    with pytest.raises(ExecutionConflictError, match="gate report child"):
        await repository.record_gate_report(uuid4(), {"accepted": True})
    with pytest.raises(ExecutionConflictError, match="generation does not exist"):
        await repository.seal_generation(execution.id, 1)
    with pytest.raises(ExecutionConflictError, match="generation does not exist"):
        await repository.join_status(execution.id, 1)

    child = replace(
        make_children(execution, 1, (_proposal(execution, "en"),))[0],
        state=ChildExecutionState.COMPLETED,
    )
    ready = await PostgresWorkflowExecutionRepository(
        _ResultPool(generation={"sealed": True}, children=[_child_row(child)])
    ).join_status(execution.id, 1)
    assert ready.ready


@pytest.mark.asyncio
async def test_record_gate_report_persists_report_when_child_exists() -> None:
    class _GateReportPool:
        def __init__(self):
            self.args = None

        async def execute(self, query, *args):
            assert "gate_report" in query
            self.args = args
            return "UPDATE 1"

    pool = _GateReportPool()
    child_id = uuid4()
    await PostgresWorkflowExecutionRepository(pool).record_gate_report(child_id, {"accepted": True})
    assert pool.args[0] == child_id
    assert json.loads(pool.args[1]) == {"accepted": True}


@pytest.mark.asyncio
async def test_list_reconcilable_and_deadline_expired_queries() -> None:
    class CapturePool:
        query = ""
        args: tuple = ()

        async def fetch(self, query, *args):
            self.query = query
            self.args = args
            return []

    reconcile_pool = CapturePool()
    assert (
        await PostgresWorkflowExecutionRepository(reconcile_pool).list_reconcilable(limit=5) == []
    )
    assert "COALESCE(child.last_polled_at, child.created_at)" in reconcile_pool.query

    children_pool = CapturePool()
    now = datetime.now(UTC)
    result = await PostgresWorkflowExecutionRepository(
        children_pool
    ).list_deadline_expired_children(now=now, limit=25)
    assert result == []
    assert children_pool.args == (now, 25)
    assert "child.deadline < $1" in children_pool.query

    executions_pool = CapturePool()
    result = await PostgresWorkflowExecutionRepository(
        executions_pool
    ).list_deadline_expired_executions(now=now, limit=10)
    assert result == []
    assert executions_pool.args == (now, 10)
    assert "deadline < $1" in executions_pool.query


class _FailureTransitionConnection:
    def __init__(self, execution, children):
        self.execution = _execution_row(execution)
        self.children = [_child_row(child) for child in children]
        self.in_transaction = False
        self.failure_update_query = ""
        self.child_update_query = ""

    def transaction(self):
        return _Transaction(self)

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
    submitted = replace(submitted, state=ChildExecutionState.SUBMITTED, task_id="task-submitted")
    completed = replace(completed, state=ChildExecutionState.COMPLETED)
    connection = _FailureTransitionConnection(execution, [reserved, submitted, completed])

    await PostgresWorkflowExecutionRepository(_Pool(connection)).update_execution_state(
        execution.id,
        state=ExecutionState.FAILED.value,
        suspension_reason="invalid planning review",
        expected_revision=execution.revision,
    )

    assert connection.execution["state"] == ExecutionState.FAILED.value
    assert connection.execution["cancel_requested"] is True
    assert [child["state"] for child in connection.children] == [
        ChildExecutionState.CANCELED.value,
        ChildExecutionState.CANCELING.value,
        ChildExecutionState.COMPLETED.value,
    ]
    assert connection.in_transaction is False


class _StateProjectionPool:
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
    pool = _StateProjectionPool(
        execute_result="UPDATE 0",
        current_row={
            "state": ExecutionState.WAITING.value,
            "suspension_reason": "awaiting_children",
            "revision": 3,
        },
    )
    repository = PostgresWorkflowExecutionRepository(pool)

    await repository.update_execution_state(
        uuid4(),
        state=ExecutionState.WAITING.value,
        suspension_reason="awaiting_children",
        expected_revision=3,
    )

    assert pool.fetchrow_called is True


@pytest.mark.asyncio
async def test_update_execution_state_raises_conflict_on_stale_revision() -> None:
    pool = _StateProjectionPool(
        execute_result="UPDATE 0",
        current_row={
            "state": ExecutionState.BLOCKED.value,
            "suspension_reason": "child_blocked",
            "revision": 4,
        },
    )
    repository = PostgresWorkflowExecutionRepository(pool)

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
    repository = PostgresWorkflowExecutionRepository(pool)

    await repository.update_execution_state(
        uuid4(),
        state=ExecutionState.WAITING.value,
        suspension_reason="awaiting_children",
        expected_revision=3,
    )

    assert pool.fetchrow_called is False


class _ReconcileErrorConnection:
    def __init__(self, child: dict) -> None:
        self.child = child
        self.in_transaction = False
        self.blocker_bumped = False

    def transaction(self):
        return _Transaction(self)

    async def fetchrow(self, query, *args):
        assert self.in_transaction
        query = query.lstrip()
        if "SET reconcile_failure_count = reconcile_failure_count + 1" in query:
            terminal = {"canceled", "completed", "failed", "superseded"}
            if self.child["state"] in terminal:
                return None
            self.child.update(
                reconcile_failure_count=self.child["reconcile_failure_count"] + 1, error=args[1]
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
    child = _child_row(make_children(execution, 1, (_proposal(execution, "en"),))[0]) | {
        "reconcile_failure_count": 0
    }
    connection = _ReconcileErrorConnection(child)
    repository = PostgresWorkflowExecutionRepository(_Pool(connection))

    updated = await repository.record_reconcile_error(
        child["id"], error="boom", failure_kind="transient", max_consecutive_failures=3
    )

    assert updated is not None
    assert updated.state != ChildExecutionState.FAILED
    assert updated.error == "boom"
    assert connection.blocker_bumped is False


@pytest.mark.asyncio
async def test_record_reconcile_error_at_threshold_fails_child_and_bumps_blocker() -> None:
    execution = _execution()
    child = _child_row(make_children(execution, 1, (_proposal(execution, "en"),))[0]) | {
        "reconcile_failure_count": 1
    }
    connection = _ReconcileErrorConnection(child)
    repository = PostgresWorkflowExecutionRepository(_Pool(connection))

    updated = await repository.record_reconcile_error(
        child["id"], error="boom again", failure_kind="transient", max_consecutive_failures=2
    )

    assert updated is not None
    assert updated.state == ChildExecutionState.FAILED
    assert updated.failure_kind == FailureKind.TRANSIENT
    assert connection.blocker_bumped is True


@pytest.mark.asyncio
async def test_record_reconcile_error_returns_none_for_missing_or_terminal_child() -> None:
    execution = _execution()
    child = _child_row(make_children(execution, 1, (_proposal(execution, "en"),))[0]) | {
        "reconcile_failure_count": 0,
        "state": ChildExecutionState.COMPLETED.value,
    }
    connection = _ReconcileErrorConnection(child)
    repository = PostgresWorkflowExecutionRepository(_Pool(connection))

    result = await repository.record_reconcile_error(
        child["id"], error="too late", failure_kind="transient", max_consecutive_failures=3
    )

    assert result is None


@pytest.mark.asyncio
async def test_record_reconcile_error_rejects_non_positive_threshold() -> None:
    repository = PostgresWorkflowExecutionRepository(_ResultPool())
    with pytest.raises(ValueError, match="must be positive"):
        await repository.record_reconcile_error(
            uuid4(), error="boom", failure_kind="transient", max_consecutive_failures=0
        )


class _CancelConnection:
    def __init__(self, execution, *, update=True, exists=True):
        self.row = _execution_row(execution)
        self.update = update
        self.exists = exists
        self.child_update = False
        self.in_transaction = False

    def transaction(self):
        return _Transaction(self)

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


@pytest.mark.asyncio
async def test_request_cancel_is_atomic_idempotent_and_owner_scoped() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    connection = _CancelConnection(execution)
    canceled = await PostgresWorkflowExecutionRepository(_Pool(connection)).request_cancel(
        execution.id, owner_id=execution.owner_id, tenant_id=execution.tenant_id
    )
    assert canceled.state == ExecutionState.CANCELING
    assert canceled.cancel_requested
    assert connection.child_update
    assert connection.in_transaction is False

    with pytest.raises(ExecutionConflictError, match="does not exist"):
        await PostgresWorkflowExecutionRepository(
            _Pool(_CancelConnection(execution, update=False, exists=False))
        ).request_cancel(execution.id, owner_id="foreign-owner", tenant_id=execution.tenant_id)


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
    repository = PostgresWorkflowExecutionRepository(_Pool(connection))

    pending = await repository.list_parent_stop_pending(limit=7)
    await repository.mark_parent_stopped(execution.id)

    assert [item.id for item in pending] == [execution.id]
    assert connection.limit == 7
    assert connection.marked == execution.id


@pytest.mark.asyncio
async def test_record_parent_stop_error_increments_and_rejects_non_positive() -> None:
    pool = _ResultPool(result="UPDATE 1")
    await PostgresWorkflowExecutionRepository(pool).record_parent_stop_error(
        uuid4(), error="transport unavailable", max_attempts=3
    )

    repository = PostgresWorkflowExecutionRepository(_ResultPool())
    with pytest.raises(ValueError, match="must be positive"):
        await repository.record_parent_stop_error(uuid4(), error="boom", max_attempts=0)


@pytest.mark.asyncio
async def test_create_retry_supersedes_current_attempt_atomically() -> None:
    execution = replace(_execution(), state=ExecutionState.RUNNING)
    child = replace(
        make_children(execution, 1, (_proposal(execution, "en"),))[0],
        state=ChildExecutionState.FAILED,
    )
    from ting.domain.workflow_execution import make_retry

    retry = make_retry(child, attempt_id=child.id, max_attempts=3)

    class _RetryConnection:
        def __init__(self):
            self.in_transaction = False
            self.superseded = False
            self.inserted = None

        def transaction(self):
            return _Transaction(self)

        async def fetchrow(self, query, *args):
            assert self.in_transaction
            if "FROM workflow_executions" in query:
                return _execution_row(execution) | {"cancel_requested": False}
            if "FROM workflow_execution_children" in query:
                return _child_row(child)
            raise AssertionError(query)

        async def fetchval(self, query, *args):
            assert self.in_transaction
            return child.attempt

        async def execute(self, query, *args):
            assert self.in_transaction
            if "SET state = 'superseded'" in query:
                self.superseded = True
                return "UPDATE 1"
            if "INSERT INTO workflow_execution_children" in query:
                self.inserted = args
                return "INSERT 0 1"
            raise AssertionError(query)

    connection = _RetryConnection()
    result = await PostgresWorkflowExecutionRepository(_Pool(connection)).create_retry(
        child, retry, owner_id=execution.owner_id, tenant_id=execution.tenant_id
    )

    assert result is retry
    assert connection.superseded is True
    assert connection.inserted is not None
    assert connection.in_transaction is False


@pytest.mark.asyncio
async def test_reserve_and_deliver_child_message() -> None:
    from ting.domain.workflow_execution import ChildMessage

    message = ChildMessage(
        id=uuid4(),
        child_id=uuid4(),
        message_id="answer-1",
        answer="Use ISO dates",
        metadata={"source": "coordinator"},
        created_at=datetime.now(UTC),
    )

    class _MessagePool:
        def __init__(self):
            self.row = None
            self.delivered = None

        async def execute(self, query, *args):
            if "INSERT INTO workflow_child_messages" in query:
                self.row = {
                    "id": args[0],
                    "child_id": args[1],
                    "message_id": args[2],
                    "answer": args[3],
                    "metadata": args[4],
                    "state": args[5],
                    "created_at": args[6],
                    "delivered_at": args[7],
                }
                return "INSERT 0 1"
            self.delivered = args[0]
            return "UPDATE 1"

        async def fetchrow(self, query, *args):
            return dict(self.row)

    pool = _MessagePool()
    repository = PostgresWorkflowExecutionRepository(pool)
    reserved = await repository.reserve_message(message)
    await repository.mark_message_delivered(message.id)

    assert reserved.message_id == "answer-1"
    assert reserved.metadata == {"source": "coordinator"}
    assert pool.delivered == message.id


@pytest.mark.asyncio
async def test_reserve_message_rejects_reused_message_id_for_different_content() -> None:
    from ting.domain.workflow_execution import ChildMessage

    message = ChildMessage(
        id=uuid4(),
        child_id=uuid4(),
        message_id="answer-1",
        answer="Use ISO dates",
        metadata={},
        created_at=datetime.now(UTC),
    )
    other_child_id = uuid4()

    class _CollidingMessagePool:
        async def execute(self, query, *args):
            return "INSERT 0 0"

        async def fetchrow(self, query, *args):
            return {
                "id": uuid4(),
                "child_id": other_child_id,
                "message_id": message.message_id,
                "answer": "Different answer",
                "metadata": {},
                "state": "reserved",
                "created_at": datetime.now(UTC),
                "delivered_at": None,
            }

    with pytest.raises(ExecutionConflictError, match="reused"):
        await PostgresWorkflowExecutionRepository(_CollidingMessagePool()).reserve_message(message)


# ---------------------------------------------------------------------------
# Ownership scoping — how a plain generic repository and a specialization's
# repository (code delivery's, layered in test_delivery_postgres.py) can share
# these tables without one worker's reconcile double-claiming the other's rows.
# ---------------------------------------------------------------------------


def test_ownership_predicate_is_empty_without_configured_exclusions() -> None:
    repository = PostgresWorkflowExecutionRepository(_Pool(_CreateConnection()))
    assert repository._ownership_predicate(execution_id_column="id") == ""


def test_ownership_predicate_excludes_configured_extension_tables() -> None:
    repository = PostgresWorkflowExecutionRepository(
        _Pool(_CreateConnection()), exclude_extension_tables=("delivery_executions",)
    )
    predicate = repository._ownership_predicate(execution_id_column="child.execution_id")
    assert "NOT EXISTS" in predicate
    assert "FROM delivery_executions" in predicate
    assert "delivery_executions.execution_id = child.execution_id" in predicate


def test_ownership_predicate_rejects_unsafe_table_names() -> None:
    with pytest.raises(ValueError, match="invalid extension table name"):
        PostgresWorkflowExecutionRepository(
            _Pool(_CreateConnection()), exclude_extension_tables=("delivery_executions; DROP",)
        )


@pytest.mark.asyncio
async def test_exclusion_scoped_queries_carry_the_ownership_predicate() -> None:
    """A repository excluding another pack's extension table never reads its rows.

    This is what lets the composition root run the plain generic repository
    alongside a specialization's own (e.g. code delivery's) over the same
    ledger tables: each one's claim/reconcile queries are scoped to the rows
    it actually owns, so a single worker driving both never processes the
    other pack's row with the wrong validator/evidence hooks.
    """

    class CapturePool:
        query = ""

        async def fetch(self, query, *args):
            self.query = query
            return []

    repository = PostgresWorkflowExecutionRepository(
        CapturePool(), exclude_extension_tables=("delivery_executions",)
    )

    pool = repository._pool
    await repository.list_reconcilable(limit=5)
    assert "NOT EXISTS (SELECT 1 FROM delivery_executions" in pool.query
    assert "delivery_executions.execution_id = child.execution_id" in pool.query

    await repository.list_parent_stop_pending(limit=5)
    assert "delivery_executions.execution_id = id" in pool.query

    now = datetime.now(UTC)
    await repository.list_deadline_expired_children(now=now, limit=5)
    assert "delivery_executions.execution_id = child.execution_id" in pool.query

    await repository.list_deadline_expired_executions(now=now, limit=5)
    assert "delivery_executions.execution_id = id" in pool.query


@pytest.mark.asyncio
async def test_claim_launches_carries_the_ownership_predicate() -> None:
    execution = _execution()
    children = [make_children(execution, 1, (_proposal(execution, "en"),))[0]]
    connection = _ClaimConnection(execution, children, active=0)
    repository = PostgresWorkflowExecutionRepository(
        _Pool(connection), exclude_extension_tables=("delivery_executions",)
    )

    await repository.claim_launches(worker_id="worker-1", limit=1, lease_until=datetime.now(UTC))

    assert "NOT EXISTS (SELECT 1 FROM delivery_executions" in connection.claim_query
    assert "delivery_executions.execution_id = execution.id" in connection.claim_query

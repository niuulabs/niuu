"""Proof the generic workflow execution service needs no delivery type.

A plain `WorkflowExecution`/`WorkflowChildExecution` fan-out (an editorial
translation assignment, not code delivery) exercises expansion, launch,
reconciliation with per-child isolation, deadline enforcement, retry, and the
join projection that resumes the parent — all through
`ting.domain.services.workflow_execution.WorkflowExecutionService` with a
recording gateway/continuation and an in-memory repository fake.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest

from niuu.domain.delivery import EvidenceValidationReport
from ting.domain.services.workflow_execution import WorkflowExecutionService
from ting.domain.workflow_execution import (
    ChildExecutionState,
    ChildTaskHandle,
    ChildTaskObservation,
    ExecutionBudget,
    ExecutionState,
    ExpansionPolicy,
    FailureKind,
    WorkflowChildExecution,
    WorkflowChildProposal,
    WorkflowExecution,
    WorkflowExecutionError,
    calculate_join,
    digest_json,
    make_children,
    validate_expansion,
)
from ting.ports.workflow_execution import WorkflowExecutionRepository

PLAN_REVISION = "editorial-plan-1"


def _digest(seed: str) -> str:
    return "sha256:" + sha256(seed.encode()).hexdigest()


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
        prompt="Translate and publish the handbook",
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
            workflow_dependency="translation-assignment",
            template_id=uuid4(),
            template_revision="v1",
            template_digest=_digest("b"),
            input_schema=_schema(),
            result_schema=_schema(),
            max_children=100,
            max_attempts=3,
            max_active_children=4,
        ),
        budget=ExecutionBudget(total_units=100),
        deadline=now + timedelta(hours=1),
        created_at=now,
        updated_at=now,
    )
    return replace(value, **changes)


def _proposal(execution: WorkflowExecution, key: str, dependencies=()) -> WorkflowChildProposal:
    payload = {"attemptId": f"input-{key}"}
    return WorkflowChildProposal(
        key=key,
        objective=f"Translate the {key} edition",
        dependencies=tuple(dependencies),
        input=payload,
        input_digest=digest_json(payload),
        plan_digest=digest_json({"planRevision": PLAN_REVISION}),
        budget_units=10,
        deadline=execution.deadline,
        agent_id="translator-1",
        skill_id=str(execution.policy.template_id),
    )


class InMemoryWorkflowRepository(
    WorkflowExecutionRepository[WorkflowExecution, WorkflowChildExecution]
):
    """A durable ledger fake proving the generic contract needs no delivery type."""

    def __init__(self, execution: WorkflowExecution) -> None:
        self.execution = execution
        self.children: list[WorkflowChildExecution] = []
        self.events: set[str] = set()
        self.sealed = False
        self.reconcile_failures: dict[object, int] = {}

    async def create(self, execution):
        self.execution = execution
        return execution

    async def reserve_parent_launch(self, execution):
        self.execution = execution
        return execution, True

    async def attach_parent_session(self, execution_id, *, session_id, connection_id):
        self.execution = replace(
            self.execution, parent_session_id=session_id, connection_id=connection_id
        )
        return self.execution

    async def get(self, execution_id, *, owner_id, tenant_id):
        if (
            execution_id == self.execution.id
            and owner_id == self.execution.owner_id
            and tenant_id == self.execution.tenant_id
        ):
            return self.execution
        return None

    async def get_internal(self, execution_id):
        return self.execution if execution_id == self.execution.id else None

    async def get_by_parent_session(self, *, owner_id, session_id):
        raise NotImplementedError

    async def list(self, **kwargs):
        return [self.execution], ""

    async def reserve_generation(self, execution, children, *, plan_revision):
        self.children.extend(children)
        self.sealed = True
        self.execution = replace(
            execution,
            current_generation=children[0].generation,
            plan_revision=plan_revision,
            state=ExecutionState.WAITING,
            suspension_reason="awaiting_children",
            budget=replace(
                execution.budget,
                reserved_units=sum(item.budget_units for item in children),
            ),
            revision=execution.revision + 1,
        )
        return self.execution

    async def list_children(self, execution_id):
        return list(self.children)

    async def get_child(self, child_id):
        return next((item for item in self.children if item.id == child_id), None)

    async def list_reconcilable(self, *, limit):
        eligible = {
            ChildExecutionState.SUBMITTED,
            ChildExecutionState.RUNNING,
            ChildExecutionState.WAITING,
            ChildExecutionState.BLOCKED,
            ChildExecutionState.CANCELING,
        }
        return [item for item in self.children if item.task_id and item.state in eligible][:limit]

    async def list_parent_stop_pending(self, *, limit):
        return []

    async def list_deadline_expired_children(self, *, now, limit):
        terminal = {
            ChildExecutionState.CANCELED,
            ChildExecutionState.COMPLETED,
            ChildExecutionState.FAILED,
            ChildExecutionState.SUPERSEDED,
        }
        expired = [
            child for child in self.children if child.deadline < now and child.state not in terminal
        ]
        expired.sort(key=lambda child: (child.deadline, str(child.id)))
        return expired[:limit]

    async def list_deadline_expired_executions(self, *, now, limit):
        return []

    async def mark_parent_stopped(self, execution_id):
        raise NotImplementedError

    async def record_parent_stop_error(self, execution_id, *, error, max_attempts):
        raise NotImplementedError

    async def claim_launches(self, *, worker_id, limit, lease_until):
        claimed = []
        completed = {
            item.key for item in self.children if item.state == ChildExecutionState.COMPLETED
        }
        now = datetime.now(UTC)
        for index, child in enumerate(self.children):
            if child.state != ChildExecutionState.RESERVED:
                continue
            if child.deadline <= now:
                continue
            if not set(child.dependencies) <= completed:
                continue
            updated = replace(
                child,
                state=ChildExecutionState.LAUNCHING,
                lease_owner=worker_id,
                lease_token=uuid4(),
                fencing_generation=child.fencing_generation + 1,
                lease_expires_at=lease_until,
            )
            self.children[index] = updated
            claimed.append(updated)
            if len(claimed) == limit:
                break
        return claimed

    async def record_handle(self, child, handle, **lease):
        current = await self.get_child(child.id)
        if current.lease_token != lease["lease_token"]:
            raise WorkflowExecutionError("fenced")
        updated = replace(
            current,
            state=ChildExecutionState.SUBMITTED,
            task_id=handle.task_id,
            context_id=handle.context_id,
            lease_owner="",
            lease_token=None,
        )
        self._replace(updated)
        return updated

    async def record_observation(self, child, observation):
        current = await self.get_child(child.id)
        self.reconcile_failures.pop(child.id, None)
        if observation.event_id in self.events:
            return current
        self.events.add(observation.event_id)
        if current.state in {
            ChildExecutionState.COMPLETED,
            ChildExecutionState.FAILED,
            ChildExecutionState.CANCELED,
            ChildExecutionState.SUPERSEDED,
        }:
            return current
        updated = replace(
            current,
            state=observation.state,
            result=observation.result,
            artifacts=tuple(observation.artifacts),
            failure_kind=observation.failure_kind,
            error=observation.error,
            pending_questions=tuple(observation.pending_questions),
            pending_gates=tuple(observation.pending_gates),
        )
        self._replace(updated)
        return updated

    async def record_evidence_report(self, child_id, report):
        del child_id, report

    async def record_reconcile_error(
        self, child_id, *, error, failure_kind, max_consecutive_failures
    ):
        current = await self.get_child(child_id)
        terminal = {
            ChildExecutionState.CANCELED,
            ChildExecutionState.COMPLETED,
            ChildExecutionState.FAILED,
            ChildExecutionState.SUPERSEDED,
        }
        if current is None or current.state in terminal:
            return None
        count = self.reconcile_failures.get(child_id, 0) + 1
        self.reconcile_failures[child_id] = count
        updated = replace(current, error=error)
        if count < max_consecutive_failures:
            self._replace(updated)
            return updated
        updated = replace(updated, state=ChildExecutionState.FAILED, failure_kind=failure_kind)
        self._replace(updated)
        return updated

    async def seal_generation(self, execution_id, generation):
        del execution_id, generation
        self.sealed = True

    async def join_status(self, execution_id, generation):
        del execution_id
        return calculate_join(generation, sealed=self.sealed, children=self.children)

    async def request_cancel(self, execution_id, *, owner_id, tenant_id):
        raise NotImplementedError

    async def create_retry(self, child, retry, *, owner_id, tenant_id):
        self._replace(replace(child, state=ChildExecutionState.SUPERSEDED))
        self.children.append(retry)
        return retry

    async def update_execution_state(
        self, execution_id, *, state, suspension_reason, expected_revision
    ):
        del execution_id
        if self.execution.revision != expected_revision:
            raise WorkflowExecutionError("execution changed since this projection observed it")
        self.execution = replace(
            self.execution,
            state=ExecutionState(state),
            suspension_reason=suspension_reason,
            revision=self.execution.revision + 1,
        )

    async def mark_blocker_notification(self, execution_id, *, blocker_revision):
        del execution_id
        self.execution = replace(
            self.execution,
            blocker_notified_revision=max(
                self.execution.blocker_notified_revision, blocker_revision
            ),
        )

    async def reserve_message(self, message):
        raise NotImplementedError

    async def mark_message_delivered(self, message_id):
        raise NotImplementedError

    def _replace(self, updated):
        self.children = [updated if item.id == updated.id else item for item in self.children]


class RecordingGateway:
    def __init__(self) -> None:
        self.attempts: dict[str, str] = {}
        self.cancelled: list[str] = []
        self.failing_task_ids: set[str] = set()

    async def launch_child(self, request, *, auth_token=""):
        del auth_token
        attempt_id = request.work_order["attemptId"]
        task_id = f"task-{request.work_order['childKey']}"
        self.attempts[task_id] = attempt_id
        return ChildTaskHandle(request.agent_id, task_id, "context-1")

    async def get_child(self, handle, *, auth_token=""):
        del auth_token
        if handle.task_id in self.failing_task_ids:
            raise RuntimeError("translator sandbox is unreachable")
        return ChildTaskObservation(
            handle=handle,
            state=ChildExecutionState.COMPLETED,
            result={"attemptId": self.attempts[handle.task_id]},
            observed_at=datetime.now(UTC),
            event_id=f"event-{handle.task_id}",
        )

    async def cancel_child(self, handle, *, auth_token=""):
        del auth_token
        self.cancelled.append(handle.task_id)
        return ChildTaskObservation(
            handle=handle,
            state=ChildExecutionState.CANCELED,
            observed_at=datetime.now(UTC),
            event_id=f"cancel-{handle.task_id}",
        )

    async def reply_child(self, handle, *, answer, metadata, message_id, auth_token=""):
        raise NotImplementedError


class RecordingContinuation:
    def __init__(self) -> None:
        self.resumed: list[tuple] = []
        self.notified: list[tuple] = []

    async def resume_parent(self, execution, *, generation, results):
        self.resumed.append((execution.id, generation, results))

    async def notify_parent(self, execution, *, generation, correlation_revision, children):
        self.notified.append((execution.id, generation, correlation_revision, children))

    async def stop_parent(self, execution):
        raise NotImplementedError


class AcceptingEvidenceVerifier:
    async def validate(self, execution, child, result):
        del execution, child, result
        return EvidenceValidationReport(
            accepted=True, blocking_reasons=(), manifest_digest="a" * 64
        )


class PassthroughReviewAttestor:
    async def attest(self, execution, child, result):
        del execution, child
        return result


def _service(
    repository: InMemoryWorkflowRepository,
    *,
    gateway: RecordingGateway | None = None,
    continuation: RecordingContinuation | None = None,
    max_child_reconcile_failures: int = 5,
) -> WorkflowExecutionService[WorkflowExecution, WorkflowChildProposal, WorkflowChildExecution]:
    return WorkflowExecutionService(
        repository=repository,
        gateway=gateway or RecordingGateway(),
        continuation=continuation or RecordingContinuation(),
        evidence_verifier=AcceptingEvidenceVerifier(),
        review_attestor=PassthroughReviewAttestor(),
        worker_id="worker-1",
        launch_claim_limit=4,
        reconcile_limit=100,
        lease_seconds=30,
        max_child_reconcile_failures=max_child_reconcile_failures,
    )


@pytest.mark.asyncio
async def test_expand_launches_and_join_resumes_the_exact_parent() -> None:
    execution = _execution()
    repository = InMemoryWorkflowRepository(execution)
    gateway = RecordingGateway()
    continuation = RecordingContinuation()
    service = _service(repository, gateway=gateway, continuation=continuation)

    reserved = await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id="editorial-coordinator",
        generation=1,
        plan_revision=PLAN_REVISION,
        children=[_proposal(execution, "fr-CA"), _proposal(execution, "es-MX")],
    )
    assert reserved.current_generation == 1
    assert all(child.state == ChildExecutionState.SUBMITTED for child in repository.children)

    await repository.seal_generation(execution.id, 1)
    result = await service.reconcile(execution.id)

    assert result["projected"] == 2
    assert all(child.state == ChildExecutionState.COMPLETED for child in repository.children)
    assert len(continuation.resumed) == 1
    resumed_id, generation, results = continuation.resumed[0]
    assert resumed_id == execution.id
    assert generation == 1
    assert {item["childKey"] for item in results} == {"fr-CA", "es-MX"}
    assert repository.execution.state == ExecutionState.RUNNING


@pytest.mark.asyncio
async def test_one_failing_child_does_not_stall_reconciliation_of_others() -> None:
    execution = _execution()
    repository = InMemoryWorkflowRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway=gateway)

    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id="editorial-coordinator",
        generation=1,
        plan_revision=PLAN_REVISION,
        children=[_proposal(execution, "fr-CA"), _proposal(execution, "es-MX")],
    )
    await repository.seal_generation(execution.id, 1)
    gateway.failing_task_ids.add("task-fr-CA")

    result = await service.reconcile(execution.id)

    assert result["projected"] == 2
    by_key = {child.key: child for child in repository.children}
    assert by_key["es-MX"].state == ChildExecutionState.COMPLETED
    assert by_key["fr-CA"].state == ChildExecutionState.SUBMITTED
    assert repository.reconcile_failures[by_key["fr-CA"].id] == 1


@pytest.mark.asyncio
async def test_deadline_expired_reserved_child_fails_without_a_gateway_call() -> None:
    execution = _execution()
    repository = InMemoryWorkflowRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway=gateway)

    past = replace(_proposal(execution, "fr-CA"), deadline=execution.deadline - timedelta(hours=2))
    ordered = validate_expansion(
        replace(execution, deadline=execution.deadline + timedelta(hours=3)),
        coordinator_id="editorial-coordinator",
        generation=1,
        children=[past],
    )
    children = make_children(execution, 1, ordered)
    repository.children = list(children)
    repository.sealed = True

    result = await service.reconcile(execution.id)

    assert result["executions"] == 1
    child = repository.children[0]
    assert child.state == ChildExecutionState.FAILED
    assert child.failure_kind == FailureKind.DEADLINE_EXCEEDED
    assert gateway.cancelled == []


@pytest.mark.asyncio
async def test_retry_supersedes_the_blocked_attempt_and_reserves_a_fresh_one() -> None:
    execution = _execution(current_generation=1)
    repository = InMemoryWorkflowRepository(execution)
    child = replace(
        make_children(execution, 1, (_proposal(execution, "fr-CA"),))[0],
        state=ChildExecutionState.FAILED,
        failure_kind=FailureKind.TRANSIENT,
        error="translator sandbox crashed",
    )
    repository.children = [child]
    service = _service(repository)

    retried = await service.retry(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key="fr-CA",
        attempt_id=child.id,
    )

    assert retried.attempt == 2
    assert retried.state == ChildExecutionState.RESERVED
    assert retried.error == ""
    superseded = next(item for item in repository.children if item.id == child.id)
    assert superseded.state == ChildExecutionState.SUPERSEDED

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from niuu.domain.delivery import EvidenceValidationReport
from niuu.ports.workload_identity import IssuedWorkloadToken
from ting.api.workflow_executions import _assert_delivery_claims
from ting.domain.delivery_execution import (
    ChildExecution,
    ChildExecutionState,
    ChildMessage,
    ChildPendingGate,
    ChildPendingQuestion,
    ChildTaskHandle,
    ChildTaskObservation,
    DeliveryExecution,
    ExecutionBudget,
    ExecutionConflictError,
    ExecutionState,
    ExpansionPolicy,
    FailureKind,
    WorkflowExecutionError,
    WorkstreamProposal,
    calculate_join,
    digest_json,
    make_children,
    validate_expansion,
    validate_json_instance,
    validate_json_schema,
)
from ting.domain.services.delivery_execution import (
    DeliveryExecutionCoordinator,
    DeliveryExecutionService,
)
from ting.ports.delivery_execution import DeliveryExecutionRepository


def _digest(seed: str) -> str:
    return "sha256:" + sha256(seed.encode()).hexdigest()


PLAN_REVISION = "approved-plan"


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {"attemptId": {"type": "string", "minLength": 1}},
        "required": ["attemptId"],
        "additionalProperties": False,
    }


def _execution(**changes) -> DeliveryExecution:
    now = datetime.now(UTC)
    value = DeliveryExecution(
        id=uuid4(),
        name="Developer delivery",
        prompt="Implement the ticket",
        owner_id="owner-1",
        tenant_id="tenant-1",
        workflow_id=uuid4(),
        workflow_revision=_digest("a"),
        workflow_digest=_digest("a"),
        repository="https://example.test/repo.git",
        base_ref="main",
        base_sha="a" * 40,
        parent_session_id="session-1",
        parent_node_id="expand",
        connection_id="forge-1",
        policy=ExpansionPolicy(
            coordinator_id="developer-coordinator",
            workflow_dependency="workstream",
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


def _proposal(execution: DeliveryExecution, key: str, dependencies=()) -> WorkstreamProposal:
    payload = {"attemptId": f"input-{key}"}
    return WorkstreamProposal(
        key=key,
        objective=f"Implement {key}",
        requirement_ids=(f"REQ-{key}",),
        dependencies=tuple(dependencies),
        input=payload,
        input_digest=digest_json(payload),
        plan_digest=digest_json({"planRevision": PLAN_REVISION}),
        repository=execution.repository,
        base_sha=execution.base_sha,
        budget_units=10,
        deadline=execution.deadline,
        agent_id="agent-1",
        skill_id=str(execution.policy.template_id),
        workspace={
            "allocation_id": f"allocation-{key}",
            "campaign_id": str(execution.id),
            "workstream_key": key,
            "repository": execution.repository,
            "workspace_path": f"/worktrees/{key}",
            "repository_path": execution.repository,
            "base_sha": execution.base_sha,
            "branch_name": f"workstream/{key}",
            "worker_id": f"worker-{key}",
            "allowed_paths": ["src"],
            "test_contract_ids": [],
        },
    )


@pytest.mark.asyncio
async def test_coordinator_derives_canonical_work_order_digests() -> None:
    execution = _execution()
    proposal = _proposal(execution, "api")

    class Service:
        captured = None
        captured_plan_revision = ""

        async def expand(self, execution_id, **kwargs):
            self.captured = kwargs["workstreams"][0]
            self.captured_plan_revision = kwargs["plan_revision"]
            return replace(execution, current_generation=kwargs["generation"])

    service = Service()
    coordinator = DeliveryExecutionCoordinator(
        service,  # type: ignore[arg-type]
        execution_id=execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
    )
    await coordinator.expand(
        {
            "generation": 1,
            "plan_revision": "approved-plan-v3",
            "workstreams": [
                {
                    "key": proposal.key,
                    "objective": proposal.objective,
                    "requirementIds": list(proposal.requirement_ids),
                    "dependencies": list(proposal.dependencies),
                    "input": proposal.input,
                    "repository": proposal.repository,
                    "baseSha": proposal.base_sha,
                    "budgetUnits": proposal.budget_units,
                    "deadline": proposal.deadline.isoformat(),
                    "agentId": proposal.agent_id,
                    "skillId": proposal.skill_id,
                    "workspace": proposal.workspace,
                }
            ],
        }
    )

    assert service.captured.input_digest == digest_json(proposal.input)
    assert service.captured.plan_digest == digest_json({"planRevision": "approved-plan-v3"})
    assert service.captured_plan_revision == "approved-plan-v3"


def test_expansion_returns_stable_topological_order_and_children() -> None:
    execution = _execution()
    ordered = validate_expansion(
        execution,
        coordinator_id="developer-coordinator",
        generation=1,
        workstreams=[_proposal(execution, "ui", ("api",)), _proposal(execution, "api")],
    )

    assert [item.key for item in ordered] == ["api", "ui"]
    children = make_children(execution, 1, ordered)
    assert [item.key for item in children] == ["api", "ui"]
    assert children[0].message_id.endswith(":api:1")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda e, items: ("other", items), "coordinator identity"),
        (lambda e, items: (e.policy.coordinator_id, []), "at least one"),
        (
            lambda e, items: (
                e.policy.coordinator_id,
                [replace(items[0], dependencies=("missing",))],
            ),
            "unknown dependencies",
        ),
        (
            lambda e, items: (
                e.policy.coordinator_id,
                [
                    replace(items[0], dependencies=(items[1].key,)),
                    replace(items[1], dependencies=(items[0].key,)),
                ],
            ),
            "cycle",
        ),
        (
            lambda e, items: (
                e.policy.coordinator_id,
                [replace(items[0], budget_units=e.budget.total_units + 1)],
            ),
            "available budget",
        ),
    ],
)
def test_expansion_rejects_invalid_proposals(mutate, message: str) -> None:
    execution = _execution()
    identity, proposals = mutate(
        execution,
        [_proposal(execution, "one"), _proposal(execution, "two")],
    )
    with pytest.raises(WorkflowExecutionError, match=message):
        validate_expansion(
            execution,
            coordinator_id=identity,
            generation=1,
            workstreams=proposals,
        )


def _bound_work_order(execution: DeliveryExecution, key: str = "api") -> WorkstreamProposal:
    proposal = _proposal(execution, key)
    allocation = proposal.workspace
    payload = {
        "childKey": proposal.key,
        "objective": proposal.objective,
        "requirementIds": list(proposal.requirement_ids),
        "repository": proposal.repository,
        "baseSha": proposal.base_sha,
        "allowedPaths": list(allocation["allowed_paths"]),
        "expectedOutputs": ["Implemented code and passing tests"],
        "testContractIds": list(allocation["test_contract_ids"]),
        "dependencies": list(proposal.dependencies),
        "workspace": allocation,
    }
    return replace(proposal, input=payload, input_digest=digest_json(payload))


def _bound_work_order_schema() -> dict:
    string = {"type": "string"}
    string_array = {"type": "array", "items": string}
    properties = {
        "childKey": string,
        "objective": string,
        "requirementIds": string_array,
        "repository": string,
        "baseSha": string,
        "allowedPaths": string_array,
        "expectedOutputs": {**string_array, "minItems": 1},
        "testContractIds": string_array,
        "dependencies": string_array,
        "workspace": {"type": "object", "properties": {}, "additionalProperties": True},
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


@pytest.mark.parametrize(
    ("field", "different", "message"),
    [
        ("childKey", "other", "input childKey differs"),
        ("objective", "Different work", "input objective differs"),
        ("requirementIds", ["REQ-other"], "input requirementIds differs"),
        ("repository", "https://example.test/other.git", "input repository differs"),
        ("baseSha", "b" * 40, "input baseSha differs"),
        ("dependencies", ["other"], "input dependencies differs"),
    ],
)
def test_expansion_rejects_conflicting_nested_work_order_bindings(
    field: str, different: object, message: str
) -> None:
    initial = _execution()
    execution = replace(
        initial,
        policy=replace(initial.policy, input_schema=_bound_work_order_schema()),
    )
    proposal = _bound_work_order(execution)
    changed_input = {**proposal.input, field: different}
    conflicting = replace(
        proposal,
        input=changed_input,
        input_digest=digest_json(changed_input),
    )

    with pytest.raises(WorkflowExecutionError, match=message):
        validate_expansion(
            execution,
            coordinator_id=execution.policy.coordinator_id,
            generation=1,
            workstreams=[conflicting],
        )


def test_expansion_requires_expected_outputs_and_exact_nested_workspace() -> None:
    initial = _execution()
    execution = replace(
        initial,
        policy=replace(initial.policy, input_schema=_bound_work_order_schema()),
    )
    proposal = _bound_work_order(execution)
    missing_outputs = dict(proposal.input)
    missing_outputs.pop("expectedOutputs")
    without_outputs = replace(
        proposal,
        input=missing_outputs,
        input_digest=digest_json(missing_outputs),
    )
    with pytest.raises(WorkflowExecutionError, match="missing: expectedOutputs"):
        validate_expansion(
            execution,
            coordinator_id=execution.policy.coordinator_id,
            generation=1,
            workstreams=[without_outputs],
        )

    different_workspace = {**proposal.workspace, "worker_id": "other-worker"}
    changed_input = {**proposal.input, "workspace": different_workspace}
    conflicting = replace(
        proposal,
        input=changed_input,
        input_digest=digest_json(changed_input),
    )
    with pytest.raises(WorkflowExecutionError, match="input workspace differs"):
        validate_expansion(
            execution,
            coordinator_id=execution.policy.coordinator_id,
            generation=1,
            workstreams=[conflicting],
        )


def test_schema_validation_rejects_missing_unknown_and_wrong_types() -> None:
    schema = _schema()
    with pytest.raises(WorkflowExecutionError, match="missing"):
        validate_json_instance({}, schema, field="input")
    with pytest.raises(WorkflowExecutionError, match="unknown fields"):
        validate_json_instance({"attemptId": "x", "extra": True}, schema, field="input")
    with pytest.raises(WorkflowExecutionError, match="must be string"):
        validate_json_instance({"attemptId": 1}, schema, field="input")


def test_join_distinguishes_pending_blocked_failed_and_ready() -> None:
    execution = _execution()
    children = list(make_children(execution, 1, (_proposal(execution, "one"),)))
    assert calculate_join(1, sealed=True, children=children).pending == ("one",)
    children[0] = replace(children[0], state=ChildExecutionState.BLOCKED)
    assert calculate_join(1, sealed=True, children=children).blocked == ("one",)
    children[0] = replace(children[0], state=ChildExecutionState.FAILED)
    assert calculate_join(1, sealed=True, children=children).failed == ("one",)
    children[0] = replace(children[0], state=ChildExecutionState.COMPLETED)
    assert calculate_join(1, sealed=True, children=children).ready is True


def _request_with_auth(execution: DeliveryExecution, child: ChildExecution) -> Request:
    class CampaignRepository:
        async def get_campaign_by_slug(self, slug, *, owner_id=None):
            if slug != child.task_id or owner_id != execution.owner_id:
                return None
            return SimpleNamespace(
                owner_id=execution.owner_id,
                tenant_id=execution.tenant_id,
                session_id=f"forge-{child.task_id}",
            )

    settings = SimpleNamespace(auth=SimpleNamespace(allow_anonymous_dev=False))
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=settings,
            workflow_campaign_repo=CampaignRepository(),
        )
    )
    return Request({"type": "http", "app": app})


def _child_delivery_token(
    execution: DeliveryExecution,
    child: ChildExecution,
    *,
    forge_session_id: str | None = None,
) -> str:
    session_key = "workflow:a2a-" + uuid4().hex
    return jwt.encode(
        {
            "token_use": "valkyrie_build",
            "scopes": ["ting:workflow:coordinate"],
            "workload_workflow_execution_id": str(execution.id),
            "workload_parent_node_id": execution.parent_node_id,
            "workload_coordinator_id": execution.policy.coordinator_id,
            "workload_parent_session_key": session_key,
            "workload_sub": session_key,
            "workload_child_attempt_id": str(child.id),
            "workload_child_task_id": child.task_id,
            "workload_forge_session_id": forge_session_id or f"forge-{child.task_id}",
        },
        key="",
        algorithm="none",
    )


class MemoryRepository(DeliveryExecutionRepository):
    def __init__(self, execution: DeliveryExecution) -> None:
        self.execution = execution
        self.children: list[ChildExecution] = []
        self.events: set[str] = set()
        self.messages: dict[str, ChildMessage] = {}
        self.evidence_reports: dict = {}
        self.sealed = False
        self.reconcile_failures: dict = {}
        self.parent_stop_attempts = 0
        self.parent_stop_error = ""

    async def create(self, execution):
        self.execution = execution
        return execution

    async def reserve_parent_launch(self, execution):
        if self.execution.launch_key == execution.launch_key and execution.launch_key:
            if self.execution.launch_digest != execution.launch_digest:
                raise ExecutionConflictError("different launch")
            return self.execution, False
        self.execution = execution
        return execution, True

    async def attach_parent_session(self, execution_id, *, session_id, connection_id):
        self.execution = replace(
            self.execution,
            parent_session_id=session_id,
            connection_id=connection_id,
            state=ExecutionState.RUNNING,
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
        if self.execution.owner_id == owner_id and self.execution.parent_session_id == session_id:
            return self.execution
        return None

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
            integration_allocation=None,
            integration_receipts=(),
            integration_candidate=None,
            integration_review_receipt=None,
            integration_review_event_id="",
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
        children = [
            item
            for item in self.children
            if item.task_id
            and item.state in eligible
            and (
                self.execution.state
                not in {
                    ExecutionState.CANCELED,
                    ExecutionState.COMPLETED,
                    ExecutionState.FAILED,
                }
                or (self.execution.cancel_requested and item.state == ChildExecutionState.CANCELING)
            )
        ]
        return children[:limit]

    async def list_parent_stop_pending(self, *, limit):
        if (
            self.execution.parent_stop_requested_at is not None
            and self.execution.parent_stopped_at is None
        ):
            return [self.execution][:limit]
        return []

    async def list_deadline_expired_children(self, *, now, limit):
        terminal = {
            ChildExecutionState.CANCELED,
            ChildExecutionState.COMPLETED,
            ChildExecutionState.FAILED,
            ChildExecutionState.SUPERSEDED,
        }
        if self.execution.state in {
            ExecutionState.CANCELED,
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
        }:
            return []
        expired = [
            child for child in self.children if child.deadline < now and child.state not in terminal
        ]
        expired.sort(key=lambda child: (child.deadline, str(child.id)))
        return expired[:limit]

    async def list_deadline_expired_executions(self, *, now, limit):
        if self.execution.deadline < now and self.execution.state not in {
            ExecutionState.CANCELED,
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
        }:
            return [self.execution][:limit]
        return []

    async def mark_parent_stopped(self, execution_id):
        if execution_id == self.execution.id:
            self.execution = replace(
                self.execution,
                parent_stopped_at=datetime.now(UTC),
            )
            self.parent_stop_error = ""

    async def record_parent_stop_error(self, execution_id, *, error, max_attempts):
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        if execution_id != self.execution.id:
            return
        if (
            self.execution.parent_stop_requested_at is None
            or self.execution.parent_stopped_at is not None
        ):
            return
        self.parent_stop_attempts += 1
        self.parent_stop_error = error
        if self.parent_stop_attempts >= max_attempts:
            self.execution = replace(
                self.execution,
                parent_stopped_at=datetime.now(UTC),
            )

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
            raise ExecutionConflictError("fenced")
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
        current_failure = current.failure_kind.value if current.failure_kind else None
        next_failure = observation.failure_kind.value if observation.failure_kind else None
        blocker_states = {ChildExecutionState.BLOCKED, ChildExecutionState.FAILED}
        blocker_changed = (
            current.state in blocker_states or observation.state in blocker_states
        ) and (
            current.state.value,
            current_failure,
            current.error,
            current.pending_questions,
            current.pending_gates,
        ) != (
            observation.state.value,
            next_failure,
            observation.error,
            observation.pending_questions,
            observation.pending_gates,
        )
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
        if blocker_changed:
            self.execution = replace(
                self.execution,
                blocker_revision=self.execution.blocker_revision + 1,
            )
        return updated

    async def record_evidence_report(self, child_id, report):
        self.evidence_reports[child_id] = report

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
        updated = replace(
            updated,
            state=ChildExecutionState.FAILED,
            failure_kind=FailureKind(failure_kind),
        )
        self._replace(updated)
        self.execution = replace(
            self.execution,
            blocker_revision=self.execution.blocker_revision + 1,
        )
        return updated

    async def seal_generation(self, execution_id, generation):
        self.sealed = True

    async def join_status(self, execution_id, generation):
        return calculate_join(generation, sealed=self.sealed, children=self.children)

    async def request_cancel(self, execution_id, *, owner_id, tenant_id):
        if self.execution.state == ExecutionState.COMPLETED:
            return self.execution
        next_state = self.execution.state
        next_reason = self.execution.suspension_reason
        if self.execution.state != ExecutionState.FAILED:
            next_state = ExecutionState.CANCELING
            next_reason = "canceling_children"
        self.execution = replace(
            self.execution,
            state=next_state,
            suspension_reason=next_reason,
            cancel_requested=True,
            parent_stop_requested_at=(self.execution.parent_stop_requested_at or datetime.now(UTC)),
        )
        self.children = [
            (
                replace(item, state=ChildExecutionState.CANCELING)
                if item.task_id
                else replace(item, state=ChildExecutionState.CANCELED)
            )
            if item.state
            not in {
                ChildExecutionState.CANCELED,
                ChildExecutionState.COMPLETED,
                ChildExecutionState.FAILED,
                ChildExecutionState.SUPERSEDED,
            }
            else item
            for item in self.children
        ]
        return self.execution

    async def create_retry(self, child, retry, *, owner_id, tenant_id):
        self._replace(replace(child, state=ChildExecutionState.SUPERSEDED))
        self.children.append(retry)
        return retry

    async def update_execution_state(
        self, execution_id, *, state, suspension_reason, expected_revision
    ):
        next_state = ExecutionState(state)
        already_applied = (
            self.execution.revision == expected_revision
            and self.execution.state == next_state
            and self.execution.suspension_reason == suspension_reason
        )
        blocked = self.execution.revision != expected_revision or self.execution.state in {
            ExecutionState.CANCELED,
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
        }
        if next_state == ExecutionState.FAILED:
            blocked = blocked or self.execution.cancel_requested
        if blocked:
            if already_applied:
                return
            raise ExecutionConflictError(
                "developer execution changed since this projection observed it"
            )
        if already_applied:
            return
        self.execution = replace(
            self.execution,
            state=next_state,
            suspension_reason=suspension_reason,
            cancel_requested=(
                self.execution.cancel_requested or next_state == ExecutionState.FAILED
            ),
            revision=self.execution.revision + 1,
        )
        if next_state != ExecutionState.FAILED:
            return
        self.children = [
            (
                replace(item, state=ChildExecutionState.CANCELING)
                if item.task_id
                else replace(item, state=ChildExecutionState.CANCELED)
            )
            if item.state
            not in {
                ChildExecutionState.CANCELED,
                ChildExecutionState.COMPLETED,
                ChildExecutionState.FAILED,
                ChildExecutionState.SUPERSEDED,
            }
            else item
            for item in self.children
        ]

    async def mark_blocker_notification(self, execution_id, *, blocker_revision):
        if self.execution.blocker_revision == blocker_revision:
            self.execution = replace(
                self.execution,
                blocker_notified_revision=max(
                    self.execution.blocker_notified_revision,
                    blocker_revision,
                ),
            )

    async def reserve_message(self, message):
        existing = self.messages.get(message.message_id)
        if existing and (existing.answer, existing.metadata) != (message.answer, message.metadata):
            raise ExecutionConflictError("different child content")
        self.messages.setdefault(message.message_id, message)
        return self.messages[message.message_id]

    async def mark_message_delivered(self, message_id):
        for key, message in self.messages.items():
            if message.id == message_id:
                self.messages[key] = replace(message, state="delivered")

    async def complete_execution(
        self,
        execution_id,
        *,
        owner_id,
        tenant_id,
        expected_revision,
        merge_receipt,
    ):
        self.execution = replace(
            self.execution,
            state=ExecutionState.COMPLETED,
            merge_receipt=merge_receipt,
            completed_at=datetime.now(UTC),
        )
        return self.execution

    async def record_integration_candidate(
        self,
        execution_id,
        *,
        owner_id,
        tenant_id,
        expected_revision,
        allocation,
        receipts,
        candidate,
    ):
        if (
            execution_id != self.execution.id
            or owner_id != self.execution.owner_id
            or tenant_id != self.execution.tenant_id
            or expected_revision != self.execution.revision
        ):
            raise ExecutionConflictError("changed")
        self.execution = replace(
            self.execution,
            integration_allocation=allocation,
            integration_receipts=tuple(receipts),
            integration_candidate=candidate,
            integration_review_receipt=None,
            integration_review_event_id="",
            revision=self.execution.revision + 1,
        )
        return self.execution

    async def record_integration_review(
        self,
        execution_id,
        *,
        event_id,
        candidate_sha,
        candidate_tree,
        receipt,
    ):
        if self.execution.integration_review_event_id == event_id:
            if self.execution.integration_review_receipt != receipt:
                raise ExecutionConflictError("different review content")
            return self.execution
        candidate = self.execution.integration_candidate or {}
        if (
            execution_id != self.execution.id
            or candidate.get("candidate_sha") != candidate_sha
            or candidate.get("candidate_tree") != candidate_tree
        ):
            raise ExecutionConflictError("changed")
        self.execution = replace(
            self.execution,
            integration_review_receipt=receipt,
            integration_review_event_id=event_id,
            revision=self.execution.revision + 1,
        )
        return self.execution

    def _replace(self, updated):
        self.children = [updated if item.id == updated.id else item for item in self.children]


class RecordingGateway:
    def __init__(self) -> None:
        self.attempts: dict[str, str] = {}
        self.replies: list[str] = []
        self.reply_metadata: list[dict] = []
        self.cancelled: list[str] = []
        self.auth_tokens: list[str] = []

    async def launch_child(self, request, *, auth_token=""):
        self.auth_tokens.append(auth_token)
        attempt_id = request.work_order["attemptId"]
        task_id = f"task-{request.work_order['childKey']}"
        self.attempts[task_id] = attempt_id
        return ChildTaskHandle(request.agent_id, task_id, "context-1")

    async def get_child(self, handle, *, auth_token=""):
        self.auth_tokens.append(auth_token)
        return ChildTaskObservation(
            handle=handle,
            state="completed",
            result={"attemptId": self.attempts[handle.task_id]},
            artifacts=({"name": "evidence.json"},),
            observed_at=datetime.now(UTC),
            event_id=f"event-{handle.task_id}",
        )

    async def cancel_child(self, handle, *, auth_token=""):
        self.auth_tokens.append(auth_token)
        self.cancelled.append(handle.task_id)
        return ChildTaskObservation(
            handle=handle,
            state="canceled",
            result=None,
            observed_at=datetime.now(UTC),
            event_id=f"cancel-{handle.task_id}",
        )

    async def reply_child(self, handle, *, answer, metadata, message_id, auth_token=""):
        self.auth_tokens.append(auth_token)
        self.replies.append(message_id)
        self.reply_metadata.append(metadata)
        return ChildTaskObservation(
            handle=handle,
            state="running",
            result=None,
            observed_at=datetime.now(UTC),
            event_id=f"reply-{message_id}",
        )


class RecordingContinuation:
    def __init__(self) -> None:
        self.calls = []
        self.notifications = []
        self.stops = []

    async def resume_parent(self, execution, *, generation, results):
        self.calls.append((execution.id, generation, results))

    async def stop_parent(self, execution):
        self.stops.append(execution.id)

    async def notify_parent(
        self,
        execution,
        *,
        event_type,
        generation,
        correlation_revision,
        children,
    ):
        self.notifications.append(
            (
                execution.id,
                event_type,
                generation,
                correlation_revision,
                children,
            )
        )


class AcceptingEvidenceVerifier:
    async def validate(self, execution, child, result):
        return EvidenceValidationReport(
            accepted=True,
            blocking_reasons=(),
            manifest_digest="a" * 64,
        )


class PassthroughReviewAttestor:
    async def attest(self, execution, child, result):
        return result


def _service(
    repository,
    gateway=None,
    continuation=None,
    token_issuer=None,
    max_child_reconcile_failures=5,
    max_parent_stop_failures=5,
):
    return DeliveryExecutionService(
        repository=repository,
        gateway=gateway or RecordingGateway(),
        continuation=continuation or RecordingContinuation(),
        evidence_verifier=AcceptingEvidenceVerifier(),
        review_attestor=PassthroughReviewAttestor(),
        token_issuer=token_issuer,
        worker_id="worker-1",
        launch_claim_limit=4,
        reconcile_limit=100,
        lease_seconds=30,
        max_child_reconcile_failures=max_child_reconcile_failures,
        max_parent_stop_failures=max_parent_stop_failures,
    )


class RecordingTokenIssuer:
    enabled = True

    def __init__(self) -> None:
        self.calls = []

    def issue_token(self, **kwargs):
        self.calls.append(kwargs)
        return IssuedWorkloadToken(token="owner-child-token", expires_at=9999999999)


@pytest.mark.asyncio
async def test_child_a2a_calls_use_fresh_owner_bound_workload_token() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    issuer = RecordingTokenIssuer()
    service = _service(repository, gateway, token_issuer=issuer)

    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    await service.reconcile(execution.id)

    assert gateway.auth_tokens == ["owner-child-token", "owner-child-token"]
    assert [call["principal"].user_id for call in issuer.calls] == [
        execution.owner_id,
        execution.owner_id,
    ]
    assert all(call["principal"].tenant_id == execution.tenant_id for call in issuer.calls)
    assert all(call["principal"].roles == ["volundr:developer"] for call in issuer.calls)
    assert all(call["claims"]["scopes"] == ["ting:workflow:launch"] for call in issuer.calls)
    assert "child_task_id" not in issuer.calls[0]["claims"]
    assert issuer.calls[1]["claims"]["child_task_id"] == "task-api"


@pytest.mark.asyncio
async def test_service_launch_reconcile_and_resume_exact_parent() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    continuation = RecordingContinuation()
    service = _service(repository, gateway, continuation)

    saved = await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    assert saved.state == ExecutionState.WAITING
    assert saved.plan_revision == PLAN_REVISION
    assert repository.children[0].state == ChildExecutionState.SUBMITTED

    assert await service.reconcile(execution.id) == {"projected": 1, "executions": 1}
    assert repository.children[0].state == ChildExecutionState.COMPLETED
    assert repository.execution.suspension_reason == "children_contract_valid"
    assert continuation.calls[0][0:2] == (execution.id, 1)
    await service.reconcile(execution.id)
    assert len(continuation.calls) == 1


@pytest.mark.parametrize(
    "delivery_suspension_reason",
    ["awaiting_checks", "awaiting_merge", "delivery_observed", "delivery_wait_failed: stale head"],
)
@pytest.mark.asyncio
async def test_manual_reconcile_does_not_reproject_join_during_delivery_wait(
    delivery_suspension_reason: str,
) -> None:
    """A manual reconcile must not clobber delivery-wait state.

    Once the children join is satisfied, `join_status` stays ready forever
    for that generation. If the execution has since moved on to awaiting or
    observing remote delivery, `_project_join` must not re-publish
    `resume_parent` or overwrite the delivery suspension reason.
    """
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    continuation = RecordingContinuation()
    service = _service(repository, gateway, continuation)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    await service.reconcile(execution.id)
    assert repository.execution.suspension_reason == "children_contract_valid"
    assert len(continuation.calls) == 1

    repository.execution = replace(
        repository.execution,
        state=ExecutionState.WAITING,
        suspension_reason=delivery_suspension_reason,
        revision=repository.execution.revision + 1,
    )

    await service._project_join(execution.id)

    assert len(continuation.calls) == 1
    assert repository.execution.state == ExecutionState.WAITING
    assert repository.execution.suspension_reason == delivery_suspension_reason


@pytest.mark.parametrize(
    ("execution_changes", "plan_revision", "message"),
    [
        ({}, "another-plan", "planDigest does not match plan_revision"),
        (
            {"current_generation": 1, "plan_revision": ""},
            PLAN_REVISION,
            "has no durable plan_revision",
        ),
        (
            {"current_generation": 1, "plan_revision": "older-plan"},
            PLAN_REVISION,
            "differs from the durable execution plan",
        ),
    ],
)
@pytest.mark.asyncio
async def test_expansion_requires_the_exact_durable_plan_revision(
    execution_changes: dict,
    plan_revision: str,
    message: str,
) -> None:
    execution = replace(_execution(), **execution_changes)
    repository = MemoryRepository(execution)
    service = _service(repository)

    with pytest.raises(WorkflowExecutionError, match=message):
        await service.expand(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            coordinator_id=execution.policy.coordinator_id,
            generation=execution.current_generation + 1,
            plan_revision=plan_revision,
            workstreams=[_proposal(execution, "api")],
        )

    assert repository.children == []


@pytest.mark.asyncio
async def test_one_poisoned_child_does_not_stall_reconciliation_of_others() -> None:
    """A child whose gateway call always raises must not stop
    the rest of the batch from being reconciled in the same pass, and must
    eventually become durably, visibly failed rather than pending forever."""

    class PoisonOneChildGateway(RecordingGateway):
        async def get_child(self, handle, *, auth_token=""):
            if handle.task_id == "task-api":
                raise RuntimeError("gateway unavailable for api")
            return await super().get_child(handle, auth_token=auth_token)

    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = PoisonOneChildGateway()
    continuation = RecordingContinuation()
    service = _service(repository, gateway, continuation, max_child_reconcile_failures=2)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api"), _proposal(execution, "ui")],
    )
    api_child = next(child for child in repository.children if child.key == "api")

    result = await service.reconcile(execution.id)

    # Both children were attempted in the same pass despite "api" raising.
    assert result == {"projected": 2, "executions": 1}
    ui_child = next(child for child in repository.children if child.key == "ui")
    assert ui_child.state == ChildExecutionState.COMPLETED
    api_child = next(child for child in repository.children if child.key == "api")
    assert api_child.state == ChildExecutionState.SUBMITTED
    assert "gateway unavailable for api" in api_child.error
    assert repository.reconcile_failures[api_child.id] == 1

    # A second failure reaches the configured maximum and terminally fails it.
    await service.reconcile(execution.id)
    api_child = next(child for child in repository.children if child.key == "api")
    assert api_child.state == ChildExecutionState.FAILED
    assert api_child.failure_kind == FailureKind.TRANSIENT
    assert repository.execution.state == ExecutionState.BLOCKED
    assert repository.execution.suspension_reason == "child_failed"


@pytest.mark.asyncio
async def test_deadline_expired_reserved_child_fails_without_gateway_call() -> None:
    """A never-launched child past its own deadline must be failed, not
    left stuck 'reserved' (unclaimable, but never terminal) forever."""
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway)
    past_deadline = datetime.now(UTC) - timedelta(seconds=1)
    proposal = replace(_proposal(execution, "api"), deadline=past_deadline)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[proposal],
    )
    api_child = repository.children[0]
    assert api_child.state == ChildExecutionState.RESERVED
    assert not api_child.task_id

    await service.reconcile(execution.id)

    api_child = repository.children[0]
    assert api_child.state == ChildExecutionState.FAILED
    assert api_child.failure_kind == FailureKind.DEADLINE_EXCEEDED
    assert gateway.cancelled == []
    assert repository.execution.state == ExecutionState.BLOCKED
    assert repository.execution.suspension_reason == "child_failed"


@pytest.mark.asyncio
async def test_deadline_expired_launched_child_cancels_then_fails() -> None:
    """A launched/running child past its own deadline is canceled through
    the gateway first, then failed with DEADLINE_EXCEEDED, instead of being
    polled forever."""
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    api_child = repository.children[0]
    assert api_child.state == ChildExecutionState.SUBMITTED
    repository._replace(replace(api_child, deadline=datetime.now(UTC) - timedelta(seconds=1)))

    await service.reconcile(execution.id)

    api_child = repository.children[0]
    assert gateway.cancelled == ["task-api"]
    assert api_child.state == ChildExecutionState.FAILED
    assert api_child.failure_kind == FailureKind.DEADLINE_EXCEEDED
    assert "deadline elapsed" in api_child.error


@pytest.mark.asyncio
async def test_deadline_expired_child_is_not_failed_while_remote_cancel_fails() -> None:
    """Ting must not report dead an agent it could not stop. A failed remote
    cancel is recorded and retried; only the configured maximum fails the child."""

    class UncancellableGateway(RecordingGateway):
        async def cancel_child(self, handle, *, auth_token=""):
            raise RuntimeError("A2A unavailable")

        async def get_child(self, handle, *, auth_token=""):
            return ChildTaskObservation(
                handle=handle,
                state="running",
                result=None,
                observed_at=datetime.now(UTC),
                event_id=f"running-{handle.task_id}",
            )

    execution = _execution()
    repository = MemoryRepository(execution)
    service = _service(repository, UncancellableGateway())
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    api_child = repository.children[0]
    repository._replace(replace(api_child, deadline=datetime.now(UTC) - timedelta(seconds=1)))

    await service.reconcile(execution.id)

    api_child = repository.children[0]
    assert api_child.state not in {ChildExecutionState.FAILED, ChildExecutionState.CANCELED}
    assert repository.reconcile_failures[api_child.id] == 1

    for _ in range(service._max_child_reconcile_failures - 1):
        await service.reconcile(execution.id)

    api_child = repository.children[0]
    assert api_child.state == ChildExecutionState.FAILED
    assert api_child.failure_kind == FailureKind.DEADLINE_EXCEEDED


@pytest.mark.asyncio
async def test_execution_deadline_expired_fails_execution_and_cancels_children() -> None:
    """An execution past its own deadline is failed, cascading the
    existing FAILED-path cancellation of its live children."""
    execution = _execution(deadline=datetime.now(UTC) + timedelta(hours=1))
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    repository.execution = replace(
        repository.execution, deadline=datetime.now(UTC) - timedelta(seconds=1)
    )

    await service.reconcile(execution.id)

    assert repository.execution.state == ExecutionState.FAILED
    assert repository.execution.suspension_reason == "deadline_exceeded"
    assert repository.execution.cancel_requested is True
    assert repository.children[0].state in {
        ChildExecutionState.CANCELED,
        ChildExecutionState.CANCELING,
    }


@pytest.mark.asyncio
async def test_completed_child_with_invalid_result_blocks_join() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    continuation = RecordingContinuation()
    service = _service(repository, gateway, continuation)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    gateway.attempts[repository.children[0].task_id] = "wrong-attempt"
    await service.reconcile(execution.id)

    assert repository.children[0].state == ChildExecutionState.BLOCKED
    assert repository.children[0].failure_kind.value == "contract_invalid"
    assert repository.execution.state == ExecutionState.BLOCKED
    assert continuation.notifications[0][1:4] == (
        "developer.children.blocked",
        1,
        1,
    )
    assert repository.execution.blocker_notified_revision == 1
    assert continuation.notifications[0][4][0]["childKey"] == "api"

    await service._project_join(execution.id)
    assert len(continuation.notifications) == 1


@pytest.mark.asyncio
async def test_message_is_idempotent_and_replies_to_blocked_child() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    repository._replace(
        replace(
            repository.children[0],
            state=ChildExecutionState.BLOCKED,
            pending_questions=(
                ChildPendingQuestion(request_id="question-1", question="Which interface?"),
                ChildPendingQuestion(request_id="question-2", question="Which format?"),
            ),
        )
    )
    attempt_id = repository.children[0].id

    with pytest.raises(WorkflowExecutionError, match="metadata.requestId is required"):
        await service.message(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key="api",
            attempt_id=attempt_id,
            answer="Ambiguous answer.",
            metadata={},
            message_id="reply-missing-request",
        )
    with pytest.raises(WorkflowExecutionError, match="exact outstanding question"):
        await service.message(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key="api",
            attempt_id=attempt_id,
            answer="Wrong answer.",
            metadata={"requestId": "another-question"},
            message_id="reply-wrong-request",
        )

    await service.message(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key="api",
        attempt_id=attempt_id,
        answer="Use the public interface.",
        metadata={"requestId": "question-1"},
        message_id="reply-1",
    )
    await service.message(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key="api",
        attempt_id=attempt_id,
        answer="Use the public interface.",
        metadata={"requestId": "question-1"},
        message_id="reply-1",
    )

    assert gateway.replies == ["reply-1"]
    assert gateway.reply_metadata == [{"requestId": "question-1"}]
    assert repository.children[0].state == ChildExecutionState.RUNNING


@pytest.mark.asyncio
async def test_gate_reply_requires_exact_outstanding_gate() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    repository._replace(
        replace(
            repository.children[0],
            state=ChildExecutionState.BLOCKED,
            pending_gates=(
                ChildPendingGate(gate_id="gate-1", node_id="review", label="Approve review"),
            ),
        )
    )
    child = repository.children[0]

    with pytest.raises(WorkflowExecutionError, match="metadata.gateId is required"):
        await service.message(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key=child.key,
            attempt_id=child.id,
            answer="Approved.",
            metadata={"gateDecision": "approve"},
            message_id="gate-missing-id",
        )
    with pytest.raises(WorkflowExecutionError, match="metadata.gateDecision is required"):
        await service.message(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key=child.key,
            attempt_id=child.id,
            answer="Approved.",
            metadata={"gateId": "gate-1"},
            message_id="gate-missing-decision",
        )
    with pytest.raises(WorkflowExecutionError, match="not both"):
        await service.message(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key=child.key,
            attempt_id=child.id,
            answer="Approved.",
            metadata={
                "requestId": "request-1",
                "gateId": "gate-1",
                "gateDecision": "approve",
            },
            message_id="gate-mixed-identity",
        )

    reply = await service.message(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key=child.key,
        attempt_id=child.id,
        answer="Approved.",
        metadata={"gateDecision": "approve", "gateId": "gate-1"},
        message_id="gate-exact-id",
    )
    assert reply.state == ChildExecutionState.RUNNING


@pytest.mark.asyncio
async def test_delivered_reply_survives_observation_projection_crash() -> None:
    class CrashAfterDeliveryRepository(MemoryRepository):
        def __init__(self, execution):
            super().__init__(execution)
            self.fail_projection = True
            self.order: list[str] = []

        async def mark_message_delivered(self, message_id):
            self.order.append("delivered")
            await super().mark_message_delivered(message_id)

        async def record_observation(self, child, observation):
            self.order.append("observation")
            if self.fail_projection:
                self.fail_projection = False
                raise RuntimeError("projection interrupted")
            return await super().record_observation(child, observation)

    class PollRecoveryGateway(RecordingGateway):
        async def get_child(self, handle, *, auth_token=""):
            return ChildTaskObservation(
                handle=handle,
                state="running",
                result=None,
                observed_at=datetime.now(UTC),
                event_id="poll-after-reply",
            )

    execution = _execution()
    repository = CrashAfterDeliveryRepository(execution)
    gateway = PollRecoveryGateway()
    service = _service(repository, gateway)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    repository._replace(
        replace(
            repository.children[0],
            state=ChildExecutionState.BLOCKED,
            pending_questions=(ChildPendingQuestion(request_id="request-1"),),
        )
    )
    child = repository.children[0]
    reply = {
        "answer": "Use main.",
        "metadata": {"requestId": "request-1"},
        "message_id": "reply-1",
    }

    with pytest.raises(RuntimeError, match="projection interrupted"):
        await service.message(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key=child.key,
            attempt_id=child.id,
            **reply,
        )
    assert repository.order == ["delivered", "observation"]
    assert repository.messages["reply-1"].state == "delivered"

    replay = await service.message(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key=child.key,
        attempt_id=child.id,
        **reply,
    )
    assert replay.state == ChildExecutionState.BLOCKED
    assert gateway.replies == ["reply-1"]

    await service.reconcile(execution.id)
    assert repository.children[0].state == ChildExecutionState.RUNNING


@pytest.mark.asyncio
async def test_retry_preserves_budget_and_supersedes_old_attempt() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    service = _service(repository)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    repository._replace(replace(repository.children[0], state=ChildExecutionState.FAILED))
    attempt_id = repository.children[0].id
    reserved = repository.execution.budget.reserved_units

    retry = await service.retry(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key="api",
        attempt_id=attempt_id,
    )

    assert retry.attempt == 2
    assert repository.children[0].state == ChildExecutionState.SUPERSEDED
    assert repository.execution.budget.reserved_units == reserved


@pytest.mark.asyncio
async def test_retry_stops_blocked_a2a_writer_before_reusing_workspace() -> None:
    class ActiveGateway(RecordingGateway):
        async def get_child(self, handle, *, auth_token=""):
            return ChildTaskObservation(
                handle=handle,
                state="running",
                result=None,
                observed_at=datetime.now(UTC),
                event_id="active-before-cancel",
            )

    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = ActiveGateway()
    service = _service(repository, gateway)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    blocked = replace(repository.children[0], state=ChildExecutionState.BLOCKED)
    repository._replace(blocked)

    retry = await service.retry(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key="api",
        attempt_id=blocked.id,
    )

    assert gateway.cancelled == [blocked.task_id]
    assert repository.children[0].state == ChildExecutionState.SUPERSEDED
    assert retry.attempt == 2
    assert retry.workspace == blocked.workspace
    assert retry.id != blocked.id


@pytest.mark.asyncio
async def test_retry_reuses_workspace_when_blocked_child_is_already_remote_terminal() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    blocked = replace(
        repository.children[0],
        state=ChildExecutionState.BLOCKED,
        failure_kind=FailureKind.POLICY_REJECTED,
        error="evidence veto",
    )
    repository._replace(blocked)

    retry = await service.retry(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key="api",
        attempt_id=blocked.id,
    )

    assert gateway.cancelled == []
    assert repository.events == set()
    assert repository.children[0].state == ChildExecutionState.SUPERSEDED
    assert retry.workspace == blocked.workspace


@pytest.mark.asyncio
async def test_retry_does_not_create_attempt_when_blocked_writer_remains_active() -> None:
    class NonterminalCancelGateway(RecordingGateway):
        async def get_child(self, handle, *, auth_token=""):
            return ChildTaskObservation(
                handle=handle,
                state="running",
                result=None,
                observed_at=datetime.now(UTC),
                event_id="active-before-cancel",
            )

        async def cancel_child(self, handle, *, auth_token=""):
            return ChildTaskObservation(
                handle=handle,
                state="running",
                result=None,
                observed_at=datetime.now(UTC),
                event_id="cancel-pending",
            )

    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = NonterminalCancelGateway()
    service = _service(repository, gateway)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    blocked = replace(repository.children[0], state=ChildExecutionState.BLOCKED)
    repository._replace(blocked)

    with pytest.raises(WorkflowExecutionError, match="still active"):
        await service.retry(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key="api",
            attempt_id=blocked.id,
        )

    assert len(repository.children) == 1
    assert repository.children[0].state == ChildExecutionState.RUNNING


@pytest.mark.asyncio
async def test_retry_does_not_create_attempt_when_a2a_cancel_fails() -> None:
    class FailingCancelGateway(RecordingGateway):
        async def get_child(self, handle, *, auth_token=""):
            return ChildTaskObservation(
                handle=handle,
                state="running",
                result=None,
                observed_at=datetime.now(UTC),
                event_id="active-before-cancel",
            )

        async def cancel_child(self, handle, *, auth_token=""):
            raise RuntimeError("A2A unavailable")

    execution = _execution()
    repository = MemoryRepository(execution)
    service = _service(repository, FailingCancelGateway())
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    blocked = replace(repository.children[0], state=ChildExecutionState.BLOCKED)
    repository._replace(blocked)

    with pytest.raises(WorkflowExecutionError, match="could not be stopped"):
        await service.retry(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key="api",
            attempt_id=blocked.id,
        )

    assert repository.children == [blocked]


@pytest.mark.asyncio
async def test_retry_routes_only_to_exact_current_generation_attempt() -> None:
    execution = replace(_execution(), current_generation=2, state=ExecutionState.BLOCKED)
    old = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        attempt=2,
        state=ChildExecutionState.FAILED,
    )
    current = replace(
        make_children(execution, 2, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.FAILED,
    )
    repository = MemoryRepository(execution)
    repository.children = [old, current]
    service = _service(repository)

    retry = await service.retry(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key="api",
        attempt_id=current.id,
    )

    assert retry.generation == 2
    assert retry.attempt == 2
    assert repository.children[0].state == ChildExecutionState.FAILED
    with pytest.raises(WorkflowExecutionError, match="no longer current"):
        await service.retry(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key="api",
            attempt_id=current.id,
        )


@pytest.mark.asyncio
async def test_message_routes_only_to_exact_current_generation_attempt() -> None:
    execution = replace(_execution(), current_generation=2, state=ExecutionState.BLOCKED)
    old = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        attempt=2,
        state=ChildExecutionState.BLOCKED,
        task_id="old-task",
        pending_questions=(ChildPendingQuestion(request_id="old-question"),),
    )
    current = replace(
        make_children(execution, 2, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.BLOCKED,
        task_id="current-task",
        pending_questions=(ChildPendingQuestion(request_id="current-question"),),
    )
    repository = MemoryRepository(execution)
    repository.children = [old, current]
    gateway = RecordingGateway()
    service = _service(repository, gateway)

    await service.message(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key="api",
        attempt_id=current.id,
        answer="Continue with the current attempt.",
        metadata={"requestId": "current-question"},
        message_id="reply-current",
    )

    assert gateway.replies == ["reply-current"]
    assert repository.children[0].state == ChildExecutionState.BLOCKED
    with pytest.raises(WorkflowExecutionError, match="no longer current"):
        await service.message(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key="api",
            attempt_id=old.id,
            answer="Do not route backward.",
            metadata={"requestId": "old-question"},
            message_id="reply-old",
        )


@pytest.mark.asyncio
async def test_a2a_input_required_notifies_parent_and_accepts_exact_reply() -> None:
    class QuestionGateway(RecordingGateway):
        async def get_child(self, handle, *, auth_token=""):
            return ChildTaskObservation(
                handle=handle,
                state="TASK_STATE_INPUT_REQUIRED",
                result=None,
                observed_at=datetime.now(UTC),
                event_id="question-1",
                error="Confirm the expected output format.",
                pending_questions=(
                    ChildPendingQuestion(
                        request_id="request-question-1",
                        persona="developer-coder",
                        question="Confirm the expected output format.",
                    ),
                ),
            )

    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = QuestionGateway()
    continuation = RecordingContinuation()
    service = _service(repository, gateway, continuation)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )

    await service.reconcile(execution.id)

    assert repository.execution.state == ExecutionState.BLOCKED
    child = repository.children[0]
    assert child.state == ChildExecutionState.BLOCKED
    assert continuation.notifications[0][1] == "developer.children.blocked"
    assert continuation.notifications[0][4][0]["detail"] == ("Confirm the expected output format.")
    assert continuation.notifications[0][4][0]["pendingQuestions"] == [
        {
            "requestId": "request-question-1",
            "persona": "developer-coder",
            "question": "Confirm the expected output format.",
            "reason": "",
            "recommendation": "",
            "attempted": [],
        }
    ]
    reply = await service.message(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key=child.key,
        attempt_id=child.id,
        answer="Use the approved plan's output format.",
        metadata={"requestId": "request-question-1"},
        message_id="reply-question-1",
    )
    assert reply.state == ChildExecutionState.RUNNING
    assert gateway.replies == ["reply-question-1"]


@pytest.mark.asyncio
async def test_later_parallel_child_question_notifies_once_and_accepts_exact_reply() -> None:
    class ParallelQuestionGateway(RecordingGateway):
        def __init__(self) -> None:
            super().__init__()
            self.polls: dict[str, int] = {}

        async def get_child(self, handle, *, auth_token=""):
            self.polls[handle.task_id] = self.polls.get(handle.task_id, 0) + 1
            if handle.task_id == "task-api":
                return ChildTaskObservation(
                    handle=handle,
                    state="TASK_STATE_INPUT_REQUIRED",
                    result=None,
                    observed_at=datetime.now(UTC),
                    event_id="question-api-1",
                    error="Which API output format should I use?",
                    pending_questions=(
                        ChildPendingQuestion(
                            request_id="request-api-1",
                            question="Which API output format should I use?",
                        ),
                    ),
                )
            if self.polls[handle.task_id] == 1:
                return ChildTaskObservation(
                    handle=handle,
                    state="running",
                    result=None,
                    observed_at=datetime.now(UTC),
                    event_id="progress-ui-1",
                )
            return ChildTaskObservation(
                handle=handle,
                state="TASK_STATE_INPUT_REQUIRED",
                result=None,
                observed_at=datetime.now(UTC),
                event_id="question-ui-1",
                error="Which UI layout should I use?",
                pending_questions=(
                    ChildPendingQuestion(
                        request_id="request-ui-1",
                        question="Which UI layout should I use?",
                    ),
                ),
            )

    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = ParallelQuestionGateway()
    continuation = RecordingContinuation()
    service = _service(repository, gateway, continuation)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api"), _proposal(execution, "ui")],
    )

    await service.reconcile(execution.id)
    assert len(continuation.notifications) == 1
    assert continuation.notifications[0][3] == 1
    assert [item["childKey"] for item in continuation.notifications[0][4]] == ["api"]

    await service.reconcile(execution.id)
    assert len(continuation.notifications) == 2
    assert continuation.notifications[1][3] == 2
    assert [item["childKey"] for item in continuation.notifications[1][4]] == ["api", "ui"]

    await service.reconcile(execution.id)
    assert len(continuation.notifications) == 2
    assert repository.execution.blocker_notified_revision == 2

    ui = next(child for child in repository.children if child.key == "ui")
    reply = await service.message(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        child_key=ui.key,
        attempt_id=ui.id,
        answer="Use the compact approved layout.",
        metadata={"requestId": "request-ui-1"},
        message_id="reply-question-ui-1",
    )
    assert reply.state == ChildExecutionState.RUNNING
    assert gateway.replies == ["reply-question-ui-1"]


@pytest.mark.asyncio
async def test_blocker_notification_retry_reuses_durable_revision() -> None:
    class QuestionGateway(RecordingGateway):
        async def get_child(self, handle, *, auth_token=""):
            return ChildTaskObservation(
                handle=handle,
                state="TASK_STATE_INPUT_REQUIRED",
                result=None,
                observed_at=datetime.now(UTC),
                event_id="question-stable",
                error="Confirm the contract.",
            )

    class FlakyContinuation(RecordingContinuation):
        def __init__(self) -> None:
            super().__init__()
            self.attempted_revisions: list[int] = []

        async def notify_parent(self, execution, **kwargs):
            self.attempted_revisions.append(kwargs["correlation_revision"])
            if len(self.attempted_revisions) == 1:
                raise RuntimeError("parent transport unavailable")
            await super().notify_parent(execution, **kwargs)

    execution = _execution()
    repository = MemoryRepository(execution)
    continuation = FlakyContinuation()
    service = _service(repository, QuestionGateway(), continuation)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )

    # The transport failure during join projection is isolated and
    # recorded (logged); it must not raise out of reconcile().
    await service.reconcile(execution.id)
    assert continuation.attempted_revisions == [1]
    assert repository.execution.blocker_notified_revision == 0

    await service._project_join(execution.id)
    assert continuation.attempted_revisions == [1, 1]
    assert repository.execution.blocker_notified_revision == 1


@pytest.mark.asyncio
async def test_new_blocker_during_child_snapshot_does_not_ack_stale_snapshot() -> None:
    class RacingRepository(MemoryRepository):
        inject_blocker = True

        async def list_children(self, execution_id):
            snapshot = await super().list_children(execution_id)
            if not self.inject_blocker:
                return snapshot
            self.inject_blocker = False
            ui = next(child for child in self.children if child.key == "ui")
            self._replace(
                replace(
                    ui,
                    state=ChildExecutionState.BLOCKED,
                    error="A later parallel question.",
                )
            )
            self.execution = replace(
                self.execution,
                blocker_revision=self.execution.blocker_revision + 1,
            )
            return snapshot

    execution = _execution()
    repository = RacingRepository(
        replace(
            execution,
            state=ExecutionState.WAITING,
            current_generation=1,
            suspension_reason="awaiting_children",
            blocker_revision=1,
        )
    )
    api, ui = make_children(
        repository.execution,
        1,
        (_proposal(execution, "api"), _proposal(execution, "ui")),
    )
    repository.children = [
        replace(
            api,
            state=ChildExecutionState.BLOCKED,
            task_id="task-api",
            error="The first question.",
        ),
        replace(ui, state=ChildExecutionState.RUNNING, task_id="task-ui"),
    ]
    repository.sealed = True
    continuation = RecordingContinuation()
    service = _service(repository, continuation=continuation)

    await service._project_join(execution.id)
    assert continuation.notifications == []
    assert repository.execution.blocker_revision == 2
    assert repository.execution.blocker_notified_revision == 0

    await service._project_join(execution.id)
    assert continuation.notifications[0][3] == 2
    assert [item["childKey"] for item in continuation.notifications[0][4]] == ["api", "ui"]
    assert repository.execution.blocker_notified_revision == 2


@pytest.mark.asyncio
async def test_cancel_before_expansion_reaches_terminal_state() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    continuation = RecordingContinuation()
    service = _service(repository, gateway, continuation)

    result = await service.cancel(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
    )

    assert result.state == ExecutionState.CANCELED
    assert repository.execution.suspension_reason == "canceled"
    assert repository.execution.parent_stop_requested_at is not None
    assert repository.execution.parent_stopped_at is not None
    assert continuation.stops == [execution.id]
    assert gateway.cancelled == []
    assert repository.children == []

    await service.reconcile(execution.id)
    assert continuation.stops == [execution.id]


@pytest.mark.asyncio
async def test_parent_stop_failure_remains_pending_and_retries() -> None:
    class FlakyStopContinuation(RecordingContinuation):
        async def stop_parent(self, execution):
            self.stops.append(execution.id)
            if len(self.stops) == 1:
                raise RuntimeError("parent stop unavailable")

    execution = _execution()
    repository = MemoryRepository(execution)
    continuation = FlakyStopContinuation()
    service = _service(repository, continuation=continuation)

    result = await service.cancel(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
    )

    assert result.state == ExecutionState.CANCELED
    assert repository.execution.parent_stop_requested_at is not None
    assert repository.execution.parent_stopped_at is None
    assert continuation.stops == [execution.id]

    await service.reconcile(execution.id)
    assert repository.execution.parent_stopped_at is not None
    assert continuation.stops == [execution.id, execution.id]


@pytest.mark.asyncio
async def test_parent_stop_gives_up_visibly_after_max_consecutive_failures() -> None:
    """A permanently failing parent stop must not retry forever; past
    the configured maximum it is durably, visibly given up on."""

    class AlwaysFailingStopContinuation(RecordingContinuation):
        async def stop_parent(self, execution):
            self.stops.append(execution.id)
            raise RuntimeError("parent stop unavailable")

    execution = _execution()
    repository = MemoryRepository(execution)
    continuation = AlwaysFailingStopContinuation()
    service = _service(repository, continuation=continuation, max_parent_stop_failures=2)

    result = await service.cancel(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
    )

    assert result.state == ExecutionState.CANCELED
    assert repository.execution.parent_stopped_at is None
    assert repository.parent_stop_attempts == 1
    assert "parent stop unavailable" in repository.parent_stop_error

    # A second consecutive failure reaches the configured maximum.
    await service.reconcile(execution.id)

    assert repository.parent_stop_attempts == 2
    assert repository.execution.parent_stopped_at is not None
    assert "parent stop unavailable" in repository.parent_stop_error
    assert continuation.stops == [execution.id, execution.id]

    # Gives up: it is no longer retried on later reconcile cycles.
    await service.reconcile(execution.id)
    assert continuation.stops == [execution.id, execution.id]


@pytest.mark.asyncio
async def test_explicit_reconcile_recovers_cancel_without_remote_tasks() -> None:
    execution = _execution(state=ExecutionState.CANCELING, cancel_requested=True)
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway)

    result = await service.reconcile(execution.id)

    assert result == {"projected": 0, "executions": 1}
    assert repository.execution.state == ExecutionState.CANCELED
    assert gateway.cancelled == []


@pytest.mark.asyncio
async def test_cancel_propagates_to_launched_child() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    service = _service(repository, gateway)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )

    await service.cancel(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
    )

    assert gateway.cancelled == ["task-api"]
    assert repository.children[0].state == ChildExecutionState.CANCELED


@pytest.mark.asyncio
async def test_parent_stops_even_when_child_cancellation_transport_fails() -> None:
    class FailingCancelGateway(RecordingGateway):
        async def cancel_child(self, handle, *, auth_token=""):
            raise RuntimeError("child transport unavailable")

    execution = _execution()
    repository = MemoryRepository(execution)
    continuation = RecordingContinuation()
    service = _service(repository, FailingCancelGateway(), continuation)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )

    # The failing child cancellation is isolated per-item and recorded
    # durably; it must not raise out of cancel()/reconcile().
    await service.cancel(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
    )

    assert continuation.stops == [execution.id]
    assert repository.execution.parent_stopped_at is not None
    assert repository.children[0].state == ChildExecutionState.CANCELING
    assert repository.children[0].error.endswith("child transport unavailable")
    assert repository.reconcile_failures[repository.children[0].id] == 1


@pytest.mark.asyncio
async def test_failed_parent_cancels_live_children_without_losing_failure() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = RecordingGateway()
    continuation = RecordingContinuation()
    service = _service(repository, gateway, continuation)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )

    await repository.update_execution_state(
        execution.id,
        state=ExecutionState.FAILED.value,
        suspension_reason="invalid planning review",
        expected_revision=repository.execution.revision,
    )
    assert repository.execution.state == ExecutionState.FAILED
    assert repository.execution.suspension_reason == "invalid planning review"
    assert repository.execution.cancel_requested is True
    assert repository.children[0].state == ChildExecutionState.CANCELING

    result = await service.reconcile(execution.id)

    assert result == {"projected": 1, "executions": 1}
    assert gateway.cancelled == ["task-api"]
    assert repository.children[0].state == ChildExecutionState.CANCELED
    assert repository.execution.state == ExecutionState.FAILED
    assert repository.execution.suspension_reason == "invalid planning review"
    assert continuation.calls == []
    assert continuation.notifications == []
    assert continuation.stops == []


@pytest.mark.asyncio
async def test_failed_parent_retries_cancel_while_remote_child_is_active() -> None:
    class PendingCancelGateway(RecordingGateway):
        async def cancel_child(self, handle, *, auth_token=""):
            self.cancelled.append(handle.task_id)
            return ChildTaskObservation(
                handle=handle,
                state=ChildExecutionState.RUNNING,
                result=None,
                observed_at=datetime.now(UTC),
                event_id=f"cancel-pending-{len(self.cancelled)}",
            )

    execution = _execution()
    repository = MemoryRepository(execution)
    gateway = PendingCancelGateway()
    service = _service(repository, gateway)
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    await repository.update_execution_state(
        execution.id,
        state=ExecutionState.FAILED.value,
        suspension_reason="parent failed",
        expected_revision=repository.execution.revision,
    )

    await service.reconcile(execution.id)
    await service.reconcile(execution.id)

    assert gateway.cancelled == ["task-api", "task-api"]
    assert repository.children[0].state == ChildExecutionState.CANCELING
    assert repository.execution.state == ExecutionState.FAILED
    assert repository.execution.suspension_reason == "parent failed"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"total_units": 0},
        {"total_units": 1, "reserved_units": -1},
        {"total_units": 1, "reserved_units": 1, "spent_units": 1},
    ],
)
def test_budget_rejects_invalid_counters(kwargs) -> None:
    with pytest.raises(WorkflowExecutionError):
        ExecutionBudget(**kwargs)


@pytest.mark.parametrize(
    "changes",
    [
        {"coordinator_id": ""},
        {"workflow_dependency": ""},
        {"template_revision": ""},
        {"max_children": 0},
        {"max_children": 1, "max_active_children": 2},
        {"join_mode": "any"},
    ],
)
def test_policy_rejects_invalid_contract(changes) -> None:
    policy = _execution().policy
    with pytest.raises(WorkflowExecutionError):
        replace(policy, **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"owner_id": ""},
        {"workflow_digest": "bad"},
        {"launch_key": "key"},
        {"launch_key": "key", "launch_digest": "bad"},
        {"deadline": datetime.now()},
        {"revision": 0},
    ],
)
def test_execution_rejects_invalid_identity_and_time(changes) -> None:
    with pytest.raises(WorkflowExecutionError):
        replace(_execution(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"key": ""},
        {"requirement_ids": ()},
        {"dependencies": ("api",), "key": "api"},
        {"input_digest": "bad"},
        {"input": {"attemptId": "changed"}},
        {"budget_units": 0},
        {"deadline": datetime.now()},
        {"agent_id": ""},
        {"workspace": {}},
    ],
)
def test_proposal_rejects_invalid_work_order(changes) -> None:
    execution = _execution()
    with pytest.raises(WorkflowExecutionError):
        replace(_proposal(execution, "api"), **changes)


def test_expansion_rejects_state_generation_capacity_and_binding() -> None:
    execution = _execution()
    proposal = _proposal(execution, "api")
    cases = [
        (replace(execution, state=ExecutionState.COMPLETED), 1, [proposal]),
        (execution, 2, [proposal]),
        (
            replace(
                execution,
                policy=replace(execution.policy, max_children=1, max_active_children=1),
            ),
            1,
            [proposal, replace(_proposal(execution, "ui"), key="ui")],
        ),
        (execution, 1, [proposal, proposal]),
        (execution, 1, [replace(proposal, repository="other")]),
        (
            execution,
            1,
            [
                replace(
                    proposal,
                    workspace={**proposal.workspace, "campaign_id": str(uuid4())},
                )
            ],
        ),
        (execution, 1, [replace(proposal, deadline=execution.deadline + timedelta(seconds=1))]),
    ]
    for current, generation, workstreams in cases:
        with pytest.raises(WorkflowExecutionError):
            validate_expansion(
                current,
                coordinator_id=current.policy.coordinator_id,
                generation=generation,
                workstreams=workstreams,
            )


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {"type": "object", "unsupported": True},
        {"type": "object", "properties": [], "required": []},
        {"type": "object", "properties": {}, "required": ["missing"]},
        {"type": "object", "properties": {"x": "bad"}, "required": []},
        {"type": "object", "properties": {"x": {"type": "unknown"}}},
        {"type": "object", "properties": {"x": {"type": "array"}}},
    ],
)
def test_json_schema_rejects_unsupported_shapes(schema) -> None:
    with pytest.raises(WorkflowExecutionError):
        validate_json_schema(schema, field="schema")


@pytest.mark.asyncio
async def test_retry_and_message_reject_invalid_targets() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    service = _service(repository)
    with pytest.raises(WorkflowExecutionError, match="unknown child"):
        await service.retry(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key="missing",
            attempt_id=uuid4(),
        )
    with pytest.raises(WorkflowExecutionError, match="required"):
        await service.message(
            execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            child_key="missing",
            attempt_id=uuid4(),
            answer="",
            metadata={},
            message_id="",
        )


class RejectingEvidenceVerifier:
    async def validate(self, execution, child, result):
        return EvidenceValidationReport(
            accepted=False,
            blocking_reasons=("review veto",),
            manifest_digest="b" * 64,
        )


@pytest.mark.asyncio
async def test_evidence_veto_blocks_join_and_is_persisted() -> None:
    execution = _execution()
    repository = MemoryRepository(execution)
    service = DeliveryExecutionService(
        repository=repository,
        gateway=RecordingGateway(),
        continuation=RecordingContinuation(),
        evidence_verifier=RejectingEvidenceVerifier(),
        review_attestor=PassthroughReviewAttestor(),
        worker_id="worker",
        launch_claim_limit=1,
        reconcile_limit=10,
        lease_seconds=10,
    )
    await service.expand(
        execution.id,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        coordinator_id=execution.policy.coordinator_id,
        generation=1,
        plan_revision=PLAN_REVISION,
        workstreams=[_proposal(execution, "api")],
    )
    await service.reconcile(execution.id)
    assert repository.children[0].failure_kind.value == "policy_rejected"
    assert repository.evidence_reports[repository.children[0].id]["accepted"] is False


@pytest.mark.asyncio
async def test_child_delivery_token_only_authorizes_current_verification_attempt() -> None:
    execution = replace(_execution(), current_generation=1, state=ExecutionState.WAITING)
    repository = MemoryRepository(execution)
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.RUNNING,
        task_id="a2a-child-task",
    )
    repository.children = [child]
    token = _child_delivery_token(execution, child)

    await _assert_delivery_claims(
        _request_with_auth(execution, child),
        token,
        execution,
        repository,
        operation="run_verification",
    )

    with pytest.raises(HTTPException, match="cannot authorize"):
        await _assert_delivery_claims(
            _request_with_auth(execution, child),
            token,
            execution,
            repository,
            operation="conditional_merge",
        )

    repository.children.append(
        replace(
            child,
            id=uuid4(),
            attempt=2,
            task_id="a2a-retry",
            state=ChildExecutionState.RUNNING,
        )
    )
    with pytest.raises(HTTPException, match="no longer active"):
        await _assert_delivery_claims(
            _request_with_auth(execution, child),
            token,
            execution,
            repository,
            operation="validate_evidence",
        )


@pytest.mark.asyncio
async def test_child_delivery_token_is_bound_to_persisted_a2a_task() -> None:
    execution = replace(_execution(), current_generation=1, state=ExecutionState.WAITING)
    repository = MemoryRepository(execution)
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.RUNNING,
        task_id="a2a-child-task",
    )
    repository.children = [child]
    token = _child_delivery_token(execution, replace(child, task_id="other-task"))

    with pytest.raises(HTTPException, match="lineage is invalid"):
        await _assert_delivery_claims(
            _request_with_auth(execution, child),
            token,
            execution,
            repository,
            operation="run_verification",
        )


@pytest.mark.asyncio
async def test_child_delivery_token_is_bound_to_actual_forge_session() -> None:
    execution = replace(_execution(), current_generation=1, state=ExecutionState.WAITING)
    repository = MemoryRepository(execution)
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        state=ChildExecutionState.RUNNING,
        task_id="a2a-child-task",
    )
    repository.children = [child]

    with pytest.raises(HTTPException, match="child Forge session"):
        await _assert_delivery_claims(
            _request_with_auth(execution, child),
            _child_delivery_token(execution, child, forge_session_id="forged-session"),
            execution,
            repository,
            operation="run_verification",
        )

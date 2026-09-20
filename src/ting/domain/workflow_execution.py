"""Reusable durable lifecycle for dynamically expanded workflows.

This module owns the mechanics shared by any workflow that fans out into a
bounded child DAG: generations, budget reservation, attempt limits, stable
ordering, and fan-in readiness.  Domain contracts (for example code delivery
or editorial assignments) specialize these models and add their own checks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from niuu.domain.json_schema import (
    PortableSchemaError,
    validate_object_instance,
    validate_object_schema,
)


class WorkflowExecutionError(ValueError):
    """Base error for invalid durable workflow lifecycle operations."""


class ExecutionConflictError(WorkflowExecutionError):
    """An idempotency key, generation, or attempt is no longer current."""


class ExecutionState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    CANCELING = "canceling"
    CANCELED = "canceled"
    COMPLETED = "completed"
    FAILED = "failed"


class ChildExecutionState(StrEnum):
    RESERVED = "reserved"
    LAUNCHING = "launching"
    SUBMITTED = "submitted"
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    CANCELING = "canceling"
    CANCELED = "canceled"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class FailureKind(StrEnum):
    """Domain-neutral reasons a child attempt can end up blocked or failed."""

    TRANSIENT = "transient"
    INVALID_INPUT = "invalid_input"
    MISSING_CREDENTIALS = "missing_credentials"
    POLICY_REJECTED = "policy_rejected"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CONTRACT_INVALID = "contract_invalid"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    REMOTE_FAILED = "remote_failed"


TERMINAL_EXECUTION_STATES = frozenset(
    {ExecutionState.CANCELED, ExecutionState.COMPLETED, ExecutionState.FAILED}
)
TERMINAL_CHILD_STATES = frozenset(
    {
        ChildExecutionState.CANCELED,
        ChildExecutionState.COMPLETED,
        ChildExecutionState.FAILED,
        ChildExecutionState.SUPERSEDED,
    }
)
SUCCESSFUL_CHILD_STATES = frozenset({ChildExecutionState.COMPLETED})
RETRYABLE_CHILD_STATES = frozenset(
    {
        ChildExecutionState.BLOCKED,
        ChildExecutionState.FAILED,
        ChildExecutionState.CANCELED,
    }
)

CHILDREN_JOINED_SUSPENSION_REASON = "children_contract_valid"
"""Suspension reason set once a satisfied children join has been resumed onto the parent."""


@dataclass(frozen=True)
class ExecutionBudget:
    total_units: int
    reserved_units: int = 0
    spent_units: int = 0

    def __post_init__(self) -> None:
        if self.total_units <= 0:
            raise WorkflowExecutionError("budget totalUnits must be greater than zero")
        if self.reserved_units < 0 or self.spent_units < 0:
            raise WorkflowExecutionError("budget counters cannot be negative")
        if self.reserved_units + self.spent_units > self.total_units:
            raise WorkflowExecutionError("reserved and spent budget exceed totalUnits")

    @property
    def available_units(self) -> int:
        return self.total_units - self.reserved_units - self.spent_units


@dataclass(frozen=True)
class ExpansionPolicy:
    coordinator_id: str
    workflow_dependency: str
    template_id: UUID
    template_revision: str
    template_digest: str
    input_schema: dict[str, Any]
    result_schema: dict[str, Any]
    max_children: int
    max_attempts: int
    max_active_children: int
    join_mode: str = "all"

    def __post_init__(self) -> None:
        if not self.coordinator_id.strip():
            raise WorkflowExecutionError("allowedCoordinator must be a non-empty identity")
        if not self.workflow_dependency.strip():
            raise WorkflowExecutionError("workflowDependency must be a non-empty alias")
        if not self.template_revision.strip() or not is_digest(self.template_digest):
            raise WorkflowExecutionError("child template revision and sha256 digest are required")
        if self.max_children <= 0 or self.max_attempts <= 0 or self.max_active_children <= 0:
            raise WorkflowExecutionError("expansion limits must be greater than zero")
        if self.max_active_children > self.max_children:
            raise WorkflowExecutionError("maxActiveChildren cannot exceed maxChildren")
        if self.join_mode != "all":
            raise WorkflowExecutionError("schema v2 supports only joinMode='all'")
        validate_json_schema(self.input_schema, field="inputSchema")
        validate_json_schema(self.result_schema, field="resultSchema")


@dataclass(frozen=True, kw_only=True)
class WorkflowExecution:
    """Domain-neutral durable parent execution."""

    id: UUID
    name: str
    prompt: str
    owner_id: str
    tenant_id: str
    workflow_id: UUID
    workflow_revision: str
    workflow_digest: str
    parent_session_id: str
    parent_node_id: str
    connection_id: str
    policy: ExpansionPolicy
    budget: ExecutionBudget
    deadline: datetime
    launch_key: str = ""
    launch_digest: str = ""
    state: ExecutionState = ExecutionState.PENDING
    current_generation: int = 0
    plan_revision: str = ""
    suspension_reason: str = ""
    cancel_requested: bool = False
    parent_stop_requested_at: datetime | None = None
    parent_stopped_at: datetime | None = None
    revision: int = 1
    blocker_revision: int = 0
    blocker_notified_revision: int = 0
    completed_at: datetime | None = None
    context: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.prompt.strip():
            raise WorkflowExecutionError("execution name and prompt are required")
        if not self.owner_id.strip():
            raise WorkflowExecutionError("execution owner is required")
        if not is_digest(self.workflow_digest):
            raise WorkflowExecutionError("workflow digest must be sha256:<64 lowercase hex>")
        if bool(self.launch_key) != bool(self.launch_digest):
            raise WorkflowExecutionError("launch key and digest must be supplied together")
        if self.launch_digest and not is_digest(self.launch_digest):
            raise WorkflowExecutionError("launch digest must be sha256:<64 lowercase hex>")
        if self.deadline.tzinfo is None:
            raise WorkflowExecutionError("execution deadline must be timezone-aware")
        if (
            self.revision <= 0
            or self.current_generation < 0
            or self.blocker_revision < 0
            or self.blocker_notified_revision < 0
            or self.blocker_notified_revision > self.blocker_revision
        ):
            raise WorkflowExecutionError("execution revision and generation are invalid")


@dataclass(frozen=True, kw_only=True)
class WorkflowChildProposal:
    """Domain-neutral proposal for one child in an expansion generation."""

    key: str
    objective: str
    dependencies: tuple[str, ...]
    input: dict[str, Any]
    input_digest: str
    plan_digest: str
    budget_units: int
    deadline: datetime
    agent_id: str
    skill_id: str
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.objective.strip():
            raise WorkflowExecutionError("child key and objective are required")
        if self.key in self.dependencies:
            raise WorkflowExecutionError(f"child {self.key!r} cannot depend on itself")
        if not is_digest(self.input_digest) or not is_digest(self.plan_digest):
            raise WorkflowExecutionError(
                f"child {self.key!r} inputDigest and planDigest must be sha256 digests"
            )
        if digest_json(self.input) != self.input_digest:
            raise WorkflowExecutionError(f"child {self.key!r} inputDigest does not match input")
        if self.budget_units <= 0:
            raise WorkflowExecutionError(f"child {self.key!r} budgetUnits must be positive")
        if self.deadline.tzinfo is None:
            raise WorkflowExecutionError(f"child {self.key!r} deadline must be timezone-aware")
        if not self.agent_id.strip() or not self.skill_id.strip():
            raise WorkflowExecutionError(f"child {self.key!r} agentId and skillId are required")


@dataclass(frozen=True, kw_only=True)
class WorkflowChildExecution:
    """Domain-neutral durable child attempt."""

    id: UUID
    execution_id: UUID
    generation: int
    key: str
    attempt: int
    state: ChildExecutionState
    dependencies: tuple[str, ...]
    objective: str
    template_id: UUID
    template_revision: str
    template_digest: str
    plan_digest: str
    input_digest: str
    input: dict[str, Any]
    budget_units: int
    deadline: datetime
    agent_id: str
    skill_id: str
    intent_id: UUID
    message_id: str
    task_id: str = ""
    context_id: str = ""
    result: dict[str, Any] | None = None
    artifacts: tuple[dict[str, Any], ...] = ()
    failure_kind: str | None = None
    pending_questions: tuple[ChildPendingQuestion, ...] = ()
    pending_gates: tuple[ChildPendingGate, ...] = ()
    error: str = ""
    lease_owner: str = ""
    lease_token: UUID | None = None
    fencing_generation: int = 0
    lease_expires_at: datetime | None = None
    context: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class JoinStatus:
    generation: int
    sealed: bool
    ready: bool
    pending: tuple[str, ...]
    blocked: tuple[str, ...]
    failed: tuple[str, ...]


@dataclass(frozen=True)
class ChildLaunchRequest:
    """One idempotent outbound launch for a reserved child attempt."""

    intent_id: UUID
    message_id: str
    agent_id: str
    skill_id: str
    work_order: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ChildTaskHandle:
    """A remote child task's addressable identity."""

    agent_id: str
    task_id: str
    context_id: str = ""


@dataclass(frozen=True)
class ChildPendingQuestion:
    """A child's outstanding request for a coordinator answer."""

    request_id: str
    persona: str = ""
    question: str = ""
    reason: str = ""
    recommendation: str = ""
    attempted: tuple[str, ...] = ()

    def to_a2a_metadata(self) -> dict[str, Any]:
        return {
            "requestId": self.request_id,
            "persona": self.persona,
            "question": self.question,
            "reason": self.reason,
            "recommendation": self.recommendation,
            "attempted": list(self.attempted),
        }


@dataclass(frozen=True)
class ChildPendingGate:
    """A child's outstanding request for a coordinator gate decision."""

    gate_id: str
    node_id: str = ""
    label: str = ""
    condition: str = ""
    instructions: str = ""
    summary: str = ""

    def to_a2a_metadata(self) -> dict[str, Any]:
        return {
            "gateId": self.gate_id,
            "nodeId": self.node_id,
            "label": self.label,
            "condition": self.condition,
            "instructions": self.instructions,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class ChildTaskObservation:
    """One remote observation of a child task, normalized to durable states."""

    handle: ChildTaskHandle
    state: ChildExecutionState
    observed_at: datetime
    event_id: str
    result: dict[str, Any] | None = None
    artifacts: tuple[dict[str, Any], ...] = ()
    failure_kind: FailureKind | None = None
    error: str = ""
    pending_questions: tuple[ChildPendingQuestion, ...] = ()
    pending_gates: tuple[ChildPendingGate, ...] = ()


@dataclass(frozen=True)
class ChildMessage:
    """An idempotent coordinator follow-up reply addressed to one child attempt."""

    id: UUID
    child_id: UUID
    message_id: str
    answer: str
    metadata: dict[str, Any]
    state: str = "reserved"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    delivered_at: datetime | None = None


def validate_expansion(
    execution: WorkflowExecution,
    *,
    coordinator_id: str,
    generation: int,
    children: list[WorkflowChildProposal],
) -> tuple[WorkflowChildProposal, ...]:
    """Validate generic expansion invariants and return a stable DAG order."""
    if execution.state in TERMINAL_EXECUTION_STATES or execution.cancel_requested:
        raise WorkflowExecutionError("a terminal or canceling execution cannot expand")
    if coordinator_id != execution.policy.coordinator_id:
        raise WorkflowExecutionError("coordinator identity is not allowed by the expansion node")
    if generation != execution.current_generation + 1:
        raise ExecutionConflictError(
            f"generation must be {execution.current_generation + 1}, got {generation}"
        )
    if not children:
        raise WorkflowExecutionError("an expansion must contain at least one child")
    if len(children) > execution.policy.max_children:
        raise WorkflowExecutionError("expansion exceeds maxChildren")
    by_key = {item.key: item for item in children}
    if len(by_key) != len(children):
        raise WorkflowExecutionError("child keys must be unique within a generation")
    for item in children:
        missing = set(item.dependencies) - set(by_key)
        if missing:
            raise WorkflowExecutionError(
                f"child {item.key!r} has unknown dependencies: {', '.join(sorted(missing))}"
            )
        if item.deadline > execution.deadline:
            raise WorkflowExecutionError(f"child {item.key!r} deadline exceeds the parent deadline")
        validate_json_instance(item.input, execution.policy.input_schema, field=f"{item.key}.input")
    required_budget = sum(item.budget_units for item in children)
    if required_budget > execution.budget.available_units:
        raise WorkflowExecutionError("expansion exceeds the execution's available budget")
    return _topological_order(by_key)


def make_children(
    execution: WorkflowExecution,
    generation: int,
    ordered: tuple[WorkflowChildProposal, ...],
    *,
    message_namespace: str = "workflow-execution",
) -> tuple[WorkflowChildExecution, ...]:
    """Create first attempts for a validated generic expansion."""
    now = datetime.now(UTC)
    return tuple(
        WorkflowChildExecution(
            id=uuid4(),
            execution_id=execution.id,
            generation=generation,
            key=proposal.key,
            attempt=1,
            state=ChildExecutionState.RESERVED,
            dependencies=proposal.dependencies,
            objective=proposal.objective,
            template_id=execution.policy.template_id,
            template_revision=execution.policy.template_revision,
            template_digest=execution.policy.template_digest,
            plan_digest=proposal.plan_digest,
            input_digest=proposal.input_digest,
            input=proposal.input,
            budget_units=proposal.budget_units,
            deadline=proposal.deadline,
            agent_id=proposal.agent_id,
            skill_id=proposal.skill_id,
            intent_id=uuid4(),
            message_id=(f"{message_namespace}:{execution.id}:{generation}:{proposal.key}:1"),
            context=proposal.context,
            created_at=now,
            updated_at=now,
        )
        for proposal in ordered
    )


def calculate_join(
    generation: int,
    *,
    sealed: bool,
    children: list[WorkflowChildExecution],
) -> JoinStatus:
    """Calculate an all-children fan-in barrier from the latest attempts."""
    current: dict[str, WorkflowChildExecution] = {}
    for child in children:
        if child.generation != generation:
            continue
        previous = current.get(child.key)
        if previous is None or child.attempt > previous.attempt:
            current[child.key] = child
    pending = tuple(
        sorted(
            key
            for key, child in current.items()
            if child.state not in TERMINAL_CHILD_STATES
            and child.state != ChildExecutionState.BLOCKED
        )
    )
    blocked = tuple(
        sorted(key for key, child in current.items() if child.state == ChildExecutionState.BLOCKED)
    )
    failed = tuple(
        sorted(
            key
            for key, child in current.items()
            if child.state in {ChildExecutionState.FAILED, ChildExecutionState.CANCELED}
        )
    )
    ready = bool(sealed and current and not pending and not blocked and not failed)
    ready = ready and all(child.state in SUCCESSFUL_CHILD_STATES for child in current.values())
    return JoinStatus(
        generation=generation,
        sealed=sealed,
        ready=ready,
        pending=pending,
        blocked=blocked,
        failed=failed,
    )


def make_retry(
    child: WorkflowChildExecution,
    *,
    attempt_id: UUID,
    max_attempts: int,
    message_namespace: str = "workflow-execution",
) -> WorkflowChildExecution:
    """Validate and construct the next durable attempt from the current child."""
    if child.id != attempt_id:
        raise WorkflowExecutionError("child attempt is no longer current")
    if child.attempt >= max_attempts:
        raise WorkflowExecutionError("child attempt limit is exhausted")
    if child.state not in RETRYABLE_CHILD_STATES:
        raise WorkflowExecutionError("only a blocked or failed child can be retried")
    next_attempt = child.attempt + 1
    now = datetime.now(UTC)
    return replace(
        child,
        id=uuid4(),
        attempt=next_attempt,
        state=ChildExecutionState.RESERVED,
        intent_id=uuid4(),
        message_id=(
            f"{message_namespace}:{child.execution_id}:{child.generation}:"
            f"{child.key}:{next_attempt}"
        ),
        task_id="",
        context_id="",
        result=None,
        artifacts=(),
        failure_kind=None,
        pending_questions=(),
        pending_gates=(),
        error="",
        lease_owner="",
        lease_token=None,
        fencing_generation=0,
        lease_expires_at=None,
        created_at=now,
        updated_at=now,
    )


def validate_child_result(
    child: WorkflowChildExecution,
    result: dict[str, Any],
    schema: dict[str, Any],
) -> None:
    if child.state == ChildExecutionState.SUPERSEDED:
        raise ExecutionConflictError("a superseded child cannot satisfy the current join")
    validate_json_instance(result, schema, field=f"result for {child.key}")
    result_attempt = str(result.get("attemptId") or result.get("attempt_id") or "")
    if result_attempt and result_attempt != str(child.id):
        raise ExecutionConflictError("child result belongs to another attempt")


def digest_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def validate_json_schema(schema: dict[str, Any], *, field: str) -> None:
    try:
        validate_object_schema(schema, field=field)
    except PortableSchemaError as exc:
        raise WorkflowExecutionError(str(exc)) from exc


def validate_json_instance(value: object, schema: dict[str, Any], *, field: str) -> None:
    try:
        validate_object_instance(value, schema, field=field)
    except PortableSchemaError as exc:
        raise WorkflowExecutionError(str(exc)) from exc


def is_digest(value: str) -> bool:
    if not value.startswith("sha256:") or len(value) != 71:
        return False
    return all(character in "0123456789abcdef" for character in value[7:])


def _topological_order(
    by_key: dict[str, WorkflowChildProposal],
) -> tuple[WorkflowChildProposal, ...]:
    remaining = {key: set(item.dependencies) for key, item in by_key.items()}
    ordered: list[WorkflowChildProposal] = []
    while remaining:
        ready = sorted(key for key, dependencies in remaining.items() if not dependencies)
        if not ready:
            raise WorkflowExecutionError("child dependencies contain a cycle")
        for key in ready:
            ordered.append(by_key[key])
            remaining.pop(key)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return tuple(ordered)

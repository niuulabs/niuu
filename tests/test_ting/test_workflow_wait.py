"""Tests for the generic, condition-agnostic durable workflow wait.

These cover the domain model, the reconciliation service, the dynamic
observer registry, and configuration -- all without knowing what any
condition_type means. Genericity is proven end to end with the ``timer``
observer against a plain, non-delivery ``WorkflowExecution``; Forge-specific
observer behaviour is covered separately in
``tests/test_ting/test_delivery_wait_observers.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest

from ting.adapters.timer_wait_observer import TimerWaitObserver
from ting.config import WorkflowExecutionConfig
from ting.domain.services.workflow_wait import WorkflowWaitService
from ting.domain.workflow_execution import (
    ChildTemplate,
    ExecutionBudget,
    ExecutionState,
    ExpansionPolicy,
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
    is_wait_suspension_reason,
    wait_suspension_reason,
    workflow_wait_digest,
)
from ting.main import _build_wait_observers, _create_runtime_bound_adapter
from ting.ports.workflow_wait import WaitConditionObserver


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode()).hexdigest()


def _generic_execution(**changes) -> WorkflowExecution:
    """A plain, non-delivery execution: publishing a translated handbook."""
    now = datetime.now(UTC)
    schema = {"type": "object", "additionalProperties": True}
    value = WorkflowExecution(
        id=uuid4(),
        name="Publish translated handbook",
        prompt="Translate, fact-check, and publish the handbook",
        owner_id="editor-1",
        tenant_id="publishing",
        workflow_id=uuid4(),
        workflow_revision="2026-09-19",
        workflow_digest=_digest("editorial-workflow"),
        parent_session_id="editorial-session-1",
        parent_node_id="publication-wait",
        connection_id="content-platform",
        policy=ExpansionPolicy(
            coordinator_id="editorial-coordinator",
            templates={
                "translation": ChildTemplate(
                    dependency_alias="translation-assignment",
                    id=uuid4(),
                    revision="translation-v2",
                    digest=_digest("translation-template"),
                ),
            },
            input_schema=schema,
            result_schema=schema,
            max_children=5,
            max_attempts=2,
            max_active_children=2,
        ),
        budget=ExecutionBudget(total_units=12),
        deadline=now + timedelta(hours=6),
        state=ExecutionState.RUNNING,
        current_generation=2,
        created_at=now,
        updated_at=now,
    )
    return replace(value, **changes)


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


def test_wait_observation_terminal_and_serialization() -> None:
    observed_at = datetime.now(UTC)
    observation = WaitObservation(
        status=WaitObservationStatus.SATISFIED,
        observed_at=observed_at,
        reason="done",
        detail={"foo": "bar"},
    )
    assert observation.terminal
    assert observation.to_dict() == {
        "status": "satisfied",
        "observedAt": observed_at.isoformat(),
        "reason": "done",
        "detail": {"foo": "bar"},
    }


def test_wait_observation_pending_is_not_terminal() -> None:
    observation = WaitObservation(
        status=WaitObservationStatus.PENDING, observed_at=datetime.now(UTC)
    )
    assert not observation.terminal


def test_wait_observation_rejects_negative_retry_after() -> None:
    with pytest.raises(WorkflowExecutionError, match="retry_after_seconds"):
        WaitObservation(
            status=WaitObservationStatus.PENDING,
            observed_at=datetime.now(UTC),
            retry_after_seconds=-1,
        )


def test_wait_observation_rejects_non_json_detail() -> None:
    with pytest.raises(WorkflowExecutionError, match="JSON-compatible"):
        WaitObservation(
            status=WaitObservationStatus.PENDING,
            observed_at=datetime.now(UTC),
            detail={"bad": float("nan")},
        )


def _wait(**changes) -> WorkflowWait:
    defaults = dict(
        id=uuid4(),
        execution_id=uuid4(),
        node_id="wait-1",
        condition_type="test.condition",
        request={"a": 1},
        request_digest="a" * 64,
        execution_generation=1,
        execution_revision=1,
        state=WorkflowWaitState.PENDING,
        next_poll_at=datetime.now(UTC),
    )
    defaults.update(changes)
    return WorkflowWait(**defaults)


def test_workflow_wait_rejects_blank_node_id() -> None:
    with pytest.raises(WorkflowExecutionError, match="node_id"):
        _wait(node_id="  ")


def test_workflow_wait_rejects_blank_condition_type() -> None:
    with pytest.raises(WorkflowExecutionError, match="condition_type"):
        _wait(condition_type="")


def test_workflow_wait_rejects_blank_request_digest() -> None:
    with pytest.raises(WorkflowExecutionError, match="request_digest"):
        _wait(request_digest="")


def test_workflow_wait_to_dict_carries_node_and_condition() -> None:
    wait = _wait()
    payload = wait.to_dict()
    assert payload["nodeId"] == "wait-1"
    assert payload["conditionType"] == "test.condition"
    assert payload["request"] == {"a": 1}
    assert payload["observation"] is None


def test_wait_suspension_reason_roundtrip() -> None:
    reason = wait_suspension_reason("forge.checks")
    assert reason == "awaiting:forge.checks"
    assert is_wait_suspension_reason(reason)


def test_observed_and_failure_suspension_reasons_are_recognized() -> None:
    assert is_wait_suspension_reason(WAIT_OBSERVED_SUSPENSION_REASON)
    assert is_wait_suspension_reason(f"{WAIT_FAILURE_PREFIX}boom")


def test_unrelated_suspension_reason_is_not_recognized() -> None:
    assert not is_wait_suspension_reason("blocked")


def test_workflow_wait_digest_binds_node_condition_request_and_generation() -> None:
    base = dict(
        node_id="wait-1", condition_type="forge.checks", request={"a": 1}, execution_generation=1
    )
    digest = workflow_wait_digest(**base)
    assert digest == workflow_wait_digest(**base)
    assert digest != workflow_wait_digest(**{**base, "node_id": "wait-2"})
    assert digest != workflow_wait_digest(**{**base, "condition_type": "forge.merge"})
    assert digest != workflow_wait_digest(**{**base, "request": {"a": 2}})
    assert digest != workflow_wait_digest(**{**base, "execution_generation": 2})


# ---------------------------------------------------------------------------
# Service test doubles
# ---------------------------------------------------------------------------


class MemoryWaitRepository:
    def __init__(self):
        self.waits: dict = {}

    async def reserve(
        self,
        execution,
        *,
        wait_id,
        node_id,
        condition_type,
        request,
        request_digest,
        next_poll_at,
    ):
        existing = self.waits.get(wait_id)
        if existing is not None:
            return existing
        wait = WorkflowWait(
            id=wait_id,
            execution_id=execution.id,
            node_id=node_id,
            condition_type=condition_type,
            request=request,
            request_digest=request_digest,
            execution_generation=execution.current_generation,
            execution_revision=execution.revision,
            state=WorkflowWaitState.PENDING,
            next_poll_at=next_poll_at,
        )
        self.waits[wait_id] = wait
        return wait

    async def list_for_execution(self, execution_id):
        return [wait for wait in self.waits.values() if wait.execution_id == execution_id]

    async def claim_due(self, *, worker_id, limit, lease_until, now):
        claimed = []
        for wait_id, wait in list(self.waits.items()):
            if wait.state is WorkflowWaitState.NOTIFIED:
                continue
            if wait.state is WorkflowWaitState.PENDING and wait.next_poll_at > now:
                continue
            if wait.lease_expires_at is not None and wait.lease_expires_at > now:
                continue
            claimed_wait = replace(
                wait,
                lease_owner=worker_id,
                lease_token=uuid4(),
                fencing_generation=wait.fencing_generation + 1,
                lease_expires_at=lease_until,
            )
            self.waits[wait_id] = claimed_wait
            claimed.append(claimed_wait)
            if len(claimed) == limit:
                break
        return claimed

    async def record_pending(self, wait, observation, *, next_poll_at, error=""):
        self.waits[wait.id] = replace(
            wait,
            observation=observation,
            next_poll_at=next_poll_at,
            attempt_count=wait.attempt_count + 1,
            last_error=error,
            lease_owner="",
            lease_token=None,
            lease_expires_at=None,
        )

    async def record_terminal(
        self,
        wait,
        observation,
        *,
        expected_execution_revision,
        project_execution,
    ):
        del expected_execution_revision, project_execution
        state = (
            WorkflowWaitState.READY
            if observation.status is WaitObservationStatus.SATISFIED
            else WorkflowWaitState.FAILED
        )
        terminal = replace(
            wait,
            state=state,
            observation=observation,
            attempt_count=wait.attempt_count + 1,
        )
        self.waits[wait.id] = terminal
        return terminal

    async def mark_notified(self, wait):
        self.waits[wait.id] = replace(
            wait,
            state=WorkflowWaitState.NOTIFIED,
            notified_at=datetime.now(UTC),
            lease_owner="",
            lease_token=None,
            lease_expires_at=None,
        )


class MemoryExecutionRepository:
    def __init__(self, execution):
        self.execution = execution

    async def get_internal(self, execution_id):
        return self.execution if execution_id == self.execution.id else None


class MultiExecutionRepository:
    """Serves several executions and can be told to poison lookups for one."""

    def __init__(self, executions: dict) -> None:
        self.executions = executions
        self.poison_execution_id = None

    async def get_internal(self, execution_id):
        if execution_id == self.poison_execution_id:
            raise RuntimeError("execution lookup unavailable")
        return self.executions.get(execution_id)


class SequenceObserver(WaitConditionObserver):
    """Returns a fixed sequence of observations, ignoring the request."""

    def __init__(self, observations, *, condition_type: str = "test.condition") -> None:
        self.observations = list(observations)
        self.calls = 0
        self._condition_type = condition_type

    @property
    def condition_type(self) -> str:
        return self._condition_type

    def validate(self, request, execution) -> None:
        return None

    async def observe(self, wait, execution):
        del wait, execution
        self.calls += 1
        return self.observations.pop(0)


class SelectiveObserver(WaitConditionObserver):
    """Raises for one execution's waits, returns a fixed observation for others."""

    def __init__(self, *, poison_execution_id, observation) -> None:
        self.poison_execution_id = poison_execution_id
        self.observation = observation
        self.calls = 0

    @property
    def condition_type(self) -> str:
        return "test.condition"

    def validate(self, request, execution) -> None:
        return None

    async def observe(self, wait, execution):
        del wait
        self.calls += 1
        if execution.id == self.poison_execution_id:
            raise RuntimeError("observer unavailable")
        return self.observation


class ValidatingObserver(WaitConditionObserver):
    """Rejects a request missing a required field before anything is persisted."""

    def __init__(self) -> None:
        self.validated: list[dict] = []

    @property
    def condition_type(self) -> str:
        return "strict.condition"

    def validate(self, request, execution) -> None:
        self.validated.append(request)
        if "requiredField" not in request:
            raise ValueError("requiredField is required")

    async def observe(self, wait, execution):
        raise NotImplementedError


class RecordingContinuation:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.calls = []

    async def notify_wait_observation(self, execution, wait, observation):
        self.calls.append((execution, wait, observation))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary continuation failure")


def _observation(status: WaitObservationStatus) -> WaitObservation:
    return WaitObservation(status=status, observed_at=datetime.now(UTC), reason=status.value)


def _service(
    execution,
    repository,
    observers,
    continuation,
    *,
    execution_repository=None,
    max_consecutive_failures=5,
):
    return WorkflowWaitService(
        repository=repository,
        execution_repository=execution_repository or MemoryExecutionRepository(execution),
        observers=observers,
        continuation=continuation,
        worker_id="worker-1",
        claim_limit=10,
        lease_seconds=30,
        poll_interval_seconds=5,
        max_consecutive_failures=max_consecutive_failures,
    )


# ---------------------------------------------------------------------------
# WorkflowWaitService construction
# ---------------------------------------------------------------------------


def test_service_rejects_blank_worker_id() -> None:
    with pytest.raises(ValueError, match="worker identity"):
        WorkflowWaitService(
            repository=MemoryWaitRepository(),
            execution_repository=MemoryExecutionRepository(_generic_execution()),
            observers={},
            continuation=RecordingContinuation(),
            worker_id="  ",
            claim_limit=10,
            lease_seconds=30,
            poll_interval_seconds=5,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("claim_limit", 0), ("lease_seconds", 0), ("poll_interval_seconds", 0)],
)
def test_service_rejects_non_positive_reconciliation_bounds(field, value) -> None:
    kwargs = dict(
        repository=MemoryWaitRepository(),
        execution_repository=MemoryExecutionRepository(_generic_execution()),
        observers={},
        continuation=RecordingContinuation(),
        worker_id="worker-1",
        claim_limit=10,
        lease_seconds=30,
        poll_interval_seconds=5,
    )
    kwargs[field] = value
    with pytest.raises(ValueError, match="reconciliation bounds"):
        WorkflowWaitService(**kwargs)


def test_service_rejects_non_positive_max_consecutive_failures() -> None:
    with pytest.raises(ValueError, match="max_consecutive_failures"):
        WorkflowWaitService(
            repository=MemoryWaitRepository(),
            execution_repository=MemoryExecutionRepository(_generic_execution()),
            observers={},
            continuation=RecordingContinuation(),
            worker_id="worker-1",
            claim_limit=10,
            lease_seconds=30,
            poll_interval_seconds=5,
            max_consecutive_failures=0,
        )


def test_service_rejects_observer_registry_key_mismatch() -> None:
    mismatched = SequenceObserver([], condition_type="actual.condition")
    with pytest.raises(ValueError, match="do not match their own condition_type"):
        WorkflowWaitService(
            repository=MemoryWaitRepository(),
            execution_repository=MemoryExecutionRepository(_generic_execution()),
            observers={"declared.condition": mismatched},
            continuation=RecordingContinuation(),
            worker_id="worker-1",
            claim_limit=10,
            lease_seconds=30,
            poll_interval_seconds=5,
        )


# ---------------------------------------------------------------------------
# request_wait
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_wait_binds_generation_and_lists_exact_request() -> None:
    execution = _generic_execution()
    repository = MemoryWaitRepository()
    observer = SequenceObserver([])
    service = _service(execution, repository, {"test.condition": observer}, RecordingContinuation())
    request = {"a": 1}

    wait = await service.request_wait(
        execution,
        node_id="wait-1",
        condition_type="test.condition",
        request=request,
        allowed_condition_types=frozenset({"test.condition"}),
    )
    same = await service.request_wait(
        execution,
        node_id="wait-1",
        condition_type="test.condition",
        request=request,
        allowed_condition_types=frozenset({"test.condition"}),
    )

    assert same.id == wait.id
    assert wait.execution_generation == execution.current_generation
    assert await service.list_waits(execution) == [wait]
    assert wait.to_dict()["nodeId"] == "wait-1"
    assert wait.to_dict()["conditionType"] == "test.condition"
    assert wait.to_dict()["request"] == request
    assert wait.to_dict()["generation"] == execution.current_generation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [{"state": ExecutionState.COMPLETED}, {"cancel_requested": True}],
)
async def test_request_wait_rejects_terminal_or_canceling_execution(changes) -> None:
    execution = _generic_execution(**changes)
    service = _service(
        execution,
        MemoryWaitRepository(),
        {"test.condition": SequenceObserver([])},
        RecordingContinuation(),
    )

    with pytest.raises(WorkflowExecutionError, match="cannot register"):
        await service.request_wait(
            execution,
            node_id="wait-1",
            condition_type="test.condition",
            request={},
            allowed_condition_types=frozenset({"test.condition"}),
        )


@pytest.mark.asyncio
async def test_request_wait_rejects_blank_node_id() -> None:
    execution = _generic_execution()
    service = _service(
        execution,
        MemoryWaitRepository(),
        {"test.condition": SequenceObserver([])},
        RecordingContinuation(),
    )

    with pytest.raises(WorkflowExecutionError, match="node_id"):
        await service.request_wait(
            execution,
            node_id="  ",
            condition_type="test.condition",
            request={},
            allowed_condition_types=frozenset({"test.condition"}),
        )


@pytest.mark.asyncio
async def test_request_wait_rejects_condition_not_declared_by_node() -> None:
    """The node's own declared conditions gate what a wait can request against it."""
    execution = _generic_execution()
    service = _service(
        execution,
        MemoryWaitRepository(),
        {"test.condition": SequenceObserver([])},
        RecordingContinuation(),
    )

    with pytest.raises(WorkflowExecutionError, match="does not declare condition_type"):
        await service.request_wait(
            execution,
            node_id="wait-1",
            condition_type="test.condition",
            request={},
            allowed_condition_types=frozenset({"other.condition"}),
        )


@pytest.mark.asyncio
async def test_request_wait_rejects_unregistered_condition_type() -> None:
    """A condition the node allows but that has no configured observer is still rejected."""
    execution = _generic_execution()
    service = _service(execution, MemoryWaitRepository(), {}, RecordingContinuation())

    with pytest.raises(WorkflowExecutionError, match="No wait observer is registered"):
        await service.request_wait(
            execution,
            node_id="wait-1",
            condition_type="unregistered.condition",
            request={},
            allowed_condition_types=frozenset({"unregistered.condition"}),
        )


@pytest.mark.asyncio
async def test_request_wait_rejects_malformed_request_before_persisting() -> None:
    execution = _generic_execution()
    observer = ValidatingObserver()
    repository = MemoryWaitRepository()
    service = _service(
        execution, repository, {"strict.condition": observer}, RecordingContinuation()
    )

    with pytest.raises(WorkflowExecutionError, match="invalid for condition_type"):
        await service.request_wait(
            execution,
            node_id="wait-1",
            condition_type="strict.condition",
            request={},
            allowed_condition_types=frozenset({"strict.condition"}),
        )

    assert observer.validated == [{}]
    assert await service.list_waits(execution) == []


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_then_terminal_observation_is_persisted_before_notification() -> None:
    execution = _generic_execution()
    repository = MemoryWaitRepository()
    observer = SequenceObserver(
        [_observation(WaitObservationStatus.PENDING), _observation(WaitObservationStatus.SATISFIED)]
    )
    continuation = RecordingContinuation()
    service = _service(execution, repository, {"test.condition": observer}, continuation)
    wait = await service.request_wait(
        execution,
        node_id="wait-1",
        condition_type="test.condition",
        request={},
        allowed_condition_types=frozenset({"test.condition"}),
    )

    assert await service.reconcile() == 1
    pending = repository.waits[wait.id]
    assert pending.state is WorkflowWaitState.PENDING
    assert pending.attempt_count == 1
    assert continuation.calls == []
    repository.waits[wait.id] = replace(pending, next_poll_at=datetime.now(UTC))

    assert await service.reconcile() == 1
    assert repository.waits[wait.id].state is WorkflowWaitState.NOTIFIED
    assert continuation.calls[0][1].state is WorkflowWaitState.READY
    assert continuation.calls[0][2].status is WaitObservationStatus.SATISFIED


@pytest.mark.asyncio
async def test_terminal_notification_retries_without_repolling_after_restart() -> None:
    execution = _generic_execution()
    repository = MemoryWaitRepository()
    observer = SequenceObserver([_observation(WaitObservationStatus.SATISFIED)])
    continuation = RecordingContinuation(fail_once=True)
    service = _service(execution, repository, {"test.condition": observer}, continuation)
    wait = await service.request_wait(
        execution,
        node_id="wait-1",
        condition_type="test.condition",
        request={},
        allowed_condition_types=frozenset({"test.condition"}),
    )

    # The notify failure on an already-terminal wait is isolated and does not
    # raise out of reconcile(); the natural lease-expiry retry (below) replays
    # the notification.
    assert await service.reconcile() == 1
    assert repository.waits[wait.id].state is WorkflowWaitState.READY
    assert observer.calls == 1

    repository.waits[wait.id] = replace(
        repository.waits[wait.id],
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    restarted = _service(execution, repository, {"test.condition": observer}, continuation)
    assert await restarted.reconcile() == 1
    assert observer.calls == 1
    assert len(continuation.calls) == 2
    assert repository.waits[wait.id].state is WorkflowWaitState.NOTIFIED


@pytest.mark.asyncio
async def test_one_poisoned_wait_does_not_stall_reconciliation_of_others() -> None:
    """A wait whose observer call always raises must not stop other due waits in
    the same batch, and must eventually become durably, visibly failed rather
    than pending forever."""
    poisoned_execution = _generic_execution()
    healthy_execution = _generic_execution()
    repository = MemoryWaitRepository()
    execution_repository = MultiExecutionRepository(
        {
            poisoned_execution.id: poisoned_execution,
            healthy_execution.id: healthy_execution,
        }
    )
    observer = SelectiveObserver(
        poison_execution_id=poisoned_execution.id,
        observation=_observation(WaitObservationStatus.SATISFIED),
    )
    continuation = RecordingContinuation()
    service = _service(
        poisoned_execution,
        repository,
        {"test.condition": observer},
        continuation,
        execution_repository=execution_repository,
        max_consecutive_failures=2,
    )
    poisoned_wait = await service.request_wait(
        poisoned_execution,
        node_id="wait-1",
        condition_type="test.condition",
        request={},
        allowed_condition_types=frozenset({"test.condition"}),
    )
    healthy_wait = await service.request_wait(
        healthy_execution,
        node_id="wait-1",
        condition_type="test.condition",
        request={},
        allowed_condition_types=frozenset({"test.condition"}),
    )

    assert await service.reconcile() == 2

    # The healthy wait completed in the same pass despite the poisoned one.
    assert repository.waits[healthy_wait.id].state is WorkflowWaitState.NOTIFIED
    assert repository.waits[poisoned_wait.id].state is WorkflowWaitState.PENDING
    assert repository.waits[poisoned_wait.id].attempt_count == 1
    assert "observer unavailable" in repository.waits[poisoned_wait.id].last_error

    # Force the poisoned wait due again; the second consecutive failure
    # reaches the configured maximum and terminally fails it.
    repository.waits[poisoned_wait.id] = replace(
        repository.waits[poisoned_wait.id],
        next_poll_at=datetime.now(UTC),
    )

    assert await service.reconcile() == 1

    terminal = repository.waits[poisoned_wait.id]
    assert terminal.state in {WorkflowWaitState.FAILED, WorkflowWaitState.NOTIFIED}
    assert terminal.observation is not None
    assert terminal.observation.terminal


@pytest.mark.asyncio
async def test_superseded_generation_is_audited_without_waking_parent() -> None:
    execution = _generic_execution()
    repository = MemoryWaitRepository()
    observer = SequenceObserver([])
    continuation = RecordingContinuation()
    execution_repository = MemoryExecutionRepository(execution)
    service = _service(
        execution,
        repository,
        {"test.condition": observer},
        continuation,
        execution_repository=execution_repository,
    )
    wait = await service.request_wait(
        execution,
        node_id="wait-1",
        condition_type="test.condition",
        request={},
        allowed_condition_types=frozenset({"test.condition"}),
    )
    execution_repository.execution = replace(execution, current_generation=3)

    assert await service.reconcile() == 1
    recovered = repository.waits[wait.id]
    assert recovered.state is WorkflowWaitState.NOTIFIED
    assert recovered.observation is not None
    assert recovered.observation.status is WaitObservationStatus.FAILED
    assert "superseded" in recovered.observation.reason
    assert observer.calls == 0
    assert continuation.calls == []


@pytest.mark.asyncio
async def test_execution_deadline_elapsed_terminally_fails_the_wait() -> None:
    execution = _generic_execution(deadline=datetime.now(UTC) - timedelta(seconds=1))
    repository = MemoryWaitRepository()
    observer = SequenceObserver([])
    continuation = RecordingContinuation()
    service = _service(execution, repository, {"test.condition": observer}, continuation)
    wait = await service.request_wait(
        execution,
        node_id="wait-1",
        condition_type="test.condition",
        request={},
        allowed_condition_types=frozenset({"test.condition"}),
    )

    assert await service.reconcile() == 1
    recovered = repository.waits[wait.id]
    assert recovered.observation.status is WaitObservationStatus.FAILED
    assert "deadline elapsed" in recovered.observation.reason
    assert observer.calls == 0


@pytest.mark.asyncio
async def test_cancel_race_after_terminal_observation_suppresses_parent_notification() -> None:
    execution = _generic_execution()
    execution_repository = MemoryExecutionRepository(execution)
    repository = MemoryWaitRepository()

    class CancelingObserver(WaitConditionObserver):
        @property
        def condition_type(self) -> str:
            return "test.condition"

        def validate(self, request, execution) -> None:
            return None

        async def observe(self, wait, execution):
            execution_repository.execution = replace(execution, cancel_requested=True)
            return _observation(WaitObservationStatus.SATISFIED)

    continuation = RecordingContinuation()
    service = _service(
        execution,
        repository,
        {"test.condition": CancelingObserver()},
        continuation,
        execution_repository=execution_repository,
    )
    wait = await service.request_wait(
        execution,
        node_id="wait-1",
        condition_type="test.condition",
        request={},
        allowed_condition_types=frozenset({"test.condition"}),
    )

    assert await service.reconcile() == 1
    assert repository.waits[wait.id].state is WorkflowWaitState.NOTIFIED
    assert continuation.calls == []


@pytest.mark.asyncio
async def test_remote_error_is_visible_and_retried_without_false_observation() -> None:
    execution = _generic_execution()
    repository = MemoryWaitRepository()

    class RecoveringObserver(WaitConditionObserver):
        def __init__(self) -> None:
            self.calls = 0

        @property
        def condition_type(self) -> str:
            return "test.condition"

        def validate(self, request, execution) -> None:
            return None

        async def observe(self, wait, execution):
            del wait, execution
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("remote condition source unavailable")
            return _observation(WaitObservationStatus.SATISFIED)

    observer = RecoveringObserver()
    continuation = RecordingContinuation()
    service = _service(execution, repository, {"test.condition": observer}, continuation)
    wait = await service.request_wait(
        execution,
        node_id="wait-1",
        condition_type="test.condition",
        request={},
        allowed_condition_types=frozenset({"test.condition"}),
    )

    assert await service.reconcile() == 1
    failed_poll = repository.waits[wait.id]
    assert failed_poll.state is WorkflowWaitState.PENDING
    assert failed_poll.observation is None
    assert failed_poll.last_error == "RuntimeError: remote condition source unavailable"
    repository.waits[wait.id] = replace(failed_poll, next_poll_at=datetime.now(UTC))

    assert await service.reconcile() == 1
    assert observer.calls == 2
    assert repository.waits[wait.id].state is WorkflowWaitState.NOTIFIED
    assert len(continuation.calls) == 1


@pytest.mark.asyncio
async def test_terminal_or_canceled_execution_never_receives_recovered_notification() -> None:
    execution = _generic_execution(state=ExecutionState.FAILED, cancel_requested=True)
    repository = MemoryWaitRepository()
    wait = WorkflowWait(
        id=uuid4(),
        execution_id=execution.id,
        node_id="wait-1",
        condition_type="test.condition",
        request={},
        request_digest="d" * 64,
        execution_generation=execution.current_generation,
        execution_revision=execution.revision,
        state=WorkflowWaitState.READY,
        next_poll_at=datetime.now(UTC),
        observation=_observation(WaitObservationStatus.SATISFIED),
    )
    repository.waits[wait.id] = wait
    continuation = RecordingContinuation()
    service = _service(
        execution, repository, {"test.condition": SequenceObserver([])}, continuation
    )

    assert await service.reconcile() == 1
    assert repository.waits[wait.id].state is WorkflowWaitState.NOTIFIED
    assert continuation.calls == []


@pytest.mark.asyncio
async def test_retry_after_seconds_from_observation_overrides_poll_interval() -> None:
    execution = _generic_execution()
    repository = MemoryWaitRepository()

    class SlowRetryObserver(WaitConditionObserver):
        @property
        def condition_type(self) -> str:
            return "test.condition"

        def validate(self, request, execution) -> None:
            return None

        async def observe(self, wait, execution):
            return WaitObservation(
                status=WaitObservationStatus.PENDING,
                observed_at=datetime.now(UTC),
                retry_after_seconds=3600,
            )

    service = _service(
        execution, repository, {"test.condition": SlowRetryObserver()}, RecordingContinuation()
    )
    wait = await service.request_wait(
        execution,
        node_id="wait-1",
        condition_type="test.condition",
        request={},
        allowed_condition_types=frozenset({"test.condition"}),
    )
    before = datetime.now(UTC)

    assert await service.reconcile() == 1

    updated = repository.waits[wait.id]
    assert updated.next_poll_at - before > timedelta(minutes=59)


# ---------------------------------------------------------------------------
# Genericity proof: a plain, non-delivery execution reconciled through the
# timer observer, registered by configuration alone.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timer_condition_reaches_satisfied_through_the_generic_service() -> None:
    execution = _generic_execution()
    repository = MemoryWaitRepository()
    continuation = RecordingContinuation()
    service = WorkflowWaitService(
        repository=repository,
        execution_repository=MemoryExecutionRepository(execution),
        observers={"timer": TimerWaitObserver(poll_interval_seconds=30)},
        continuation=continuation,
        worker_id="worker-1",
        claim_limit=10,
        lease_seconds=30,
        poll_interval_seconds=5,
    )
    wait = await service.request_wait(
        execution,
        node_id="publication-wait",
        condition_type="timer",
        request={"after_seconds": 3600},
        allowed_condition_types=frozenset({"timer"}),
    )
    assert "until" in wait.request

    assert await service.reconcile() == 1
    pending = repository.waits[wait.id]
    assert pending.state is WorkflowWaitState.PENDING
    assert pending.observation.status is WaitObservationStatus.PENDING
    assert continuation.calls == []

    # Force the timer's own deadline into the past and make the wait due again.
    repository.waits[wait.id] = replace(
        pending,
        request={
            **pending.request,
            "until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        },
        next_poll_at=datetime.now(UTC),
    )

    assert await service.reconcile() == 1
    terminal = repository.waits[wait.id]
    assert terminal.state is WorkflowWaitState.NOTIFIED
    assert continuation.calls[0][2].status is WaitObservationStatus.SATISFIED
    assert continuation.calls[0][1].condition_type == "timer"


# ---------------------------------------------------------------------------
# Configuration and the dynamic observer registry
# ---------------------------------------------------------------------------


def test_workflow_execution_config_wait_defaults_are_dynamic_and_neutral() -> None:
    config = WorkflowExecutionConfig()
    assert config.wait_repository_adapter.endswith("PostgresWorkflowWaitRepository")
    assert config.wait_observers == []
    assert config.admission_roles == ["volundr:developer"]


def test_workflow_execution_config_rejects_empty_admission_roles() -> None:
    with pytest.raises(ValueError):
        WorkflowExecutionConfig(admission_roles=[])


def test_runtime_bound_adapter_reserves_runtime_dependencies() -> None:
    adapter = _create_runtime_bound_adapter(
        "types.SimpleNamespace",
        {"configured": 1},
        {"runtime": 2},
        label="Test adapter",
    )
    assert (adapter.configured, adapter.runtime) == (1, 2)
    with pytest.raises(ValueError, match="cannot override runtime dependencies: pool"):
        _create_runtime_bound_adapter(
            "types.SimpleNamespace",
            {"pool": "configured"},
            {"pool": "runtime"},
            label="Test adapter",
        )


class _FakeConfiguredObserver(WaitConditionObserver):
    """Registered purely through configuration, never imported by name anywhere
    in the engine -- proving a brand new condition_type needs no code change."""

    def __init__(self, *, greeting: str = "hi") -> None:
        self.greeting = greeting

    @property
    def condition_type(self) -> str:
        return "fake.condition"

    def validate(self, request, execution) -> None:
        return None

    async def observe(self, wait, execution):
        raise NotImplementedError


class _FakeInfrastructureObserver(WaitConditionObserver):
    """Declares a runtime dependency, to prove it is injected only when asked for."""

    def __init__(self, *, volundr_factory) -> None:
        self.volundr_factory = volundr_factory

    @property
    def condition_type(self) -> str:
        return "fake.infra"

    def validate(self, request, execution) -> None:
        return None

    async def observe(self, wait, execution):
        raise NotImplementedError


def test_wait_observer_registry_is_config_driven() -> None:
    entries = [
        {
            "condition_type": "fake.condition",
            "adapter": "tests.test_ting.test_workflow_wait._FakeConfiguredObserver",
            "greeting": "hello",
        }
    ]

    observers = _build_wait_observers(entries, runtime_kwargs={})

    assert set(observers) == {"fake.condition"}
    observer = observers["fake.condition"]
    assert isinstance(observer, _FakeConfiguredObserver)
    assert observer.greeting == "hello"


def test_wait_observer_registry_injects_runtime_kwargs_only_when_declared() -> None:
    factory = object()
    entries = [
        {
            "condition_type": "fake.infra",
            "adapter": "tests.test_ting.test_workflow_wait._FakeInfrastructureObserver",
        },
        {
            "condition_type": "timer",
            "adapter": "ting.adapters.timer_wait_observer.TimerWaitObserver",
        },
    ]

    observers = _build_wait_observers(
        entries, runtime_kwargs={"volundr_factory": factory, "policy_id": "unused"}
    )

    assert observers["fake.infra"].volundr_factory is factory
    assert isinstance(observers["timer"], TimerWaitObserver)


def test_wait_observer_registry_rejects_duplicate_condition_type() -> None:
    entries = [
        {
            "condition_type": "timer",
            "adapter": "ting.adapters.timer_wait_observer.TimerWaitObserver",
        },
        {
            "condition_type": "timer",
            "adapter": "ting.adapters.timer_wait_observer.TimerWaitObserver",
        },
    ]
    with pytest.raises(ValueError, match="Duplicate wait observer condition_type"):
        _build_wait_observers(entries, runtime_kwargs={})


def test_wait_observer_registry_rejects_missing_condition_type_or_adapter() -> None:
    with pytest.raises(ValueError, match="requires condition_type and adapter"):
        _build_wait_observers(
            [{"adapter": "ting.adapters.timer_wait_observer.TimerWaitObserver"}], runtime_kwargs={}
        )
    with pytest.raises(ValueError, match="requires condition_type and adapter"):
        _build_wait_observers([{"condition_type": "timer"}], runtime_kwargs={})


def test_wait_observer_registry_rejects_mismatched_condition_type() -> None:
    entries = [
        {
            "condition_type": "other.condition",
            "adapter": "tests.test_ting.test_workflow_wait._FakeConfiguredObserver",
        }
    ]
    with pytest.raises(ValueError, match="does not match its configured condition_type"):
        _build_wait_observers(entries, runtime_kwargs={})


def test_wait_observer_registry_rejects_adapter_not_implementing_the_port() -> None:
    with pytest.raises(TypeError, match="must implement WaitConditionObserver"):
        _build_wait_observers(
            [{"condition_type": "not-an-observer", "adapter": "types.SimpleNamespace"}],
            runtime_kwargs={},
        )

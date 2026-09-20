from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.test_ting.test_delivery_execution import _execution
from ting.adapters.parent_workflow_continuation import VolundrParentWorkflowContinuation
from ting.config import WorkflowExecutionConfig
from ting.domain.delivery_execution import ExecutionState, WorkflowExecutionError
from ting.domain.services.workflow_wait import WorkflowWaitService
from ting.domain.workflow_wait import (
    WaitObservation,
    WaitObservationStatus,
    WorkflowWait,
    WorkflowWaitRequest,
    WorkflowWaitState,
    delivery_candidate_digest,
)
from ting.main import _create_runtime_bound_adapter


def _bound_execution(**changes):
    execution = _execution(
        state=ExecutionState.RUNNING,
        current_generation=2,
        integration_candidate={
            "repository": "https://example.test/repo.git",
            "base_sha": "a" * 40,
            "candidate_sha": "b" * 40,
            "candidate_tree": "c" * 40,
            "integration_allocation_id": "integration-2",
        },
        integration_allocation={"allocation_id": "integration-2"},
        workflow_snapshot={
            "graph": {
                "nodes": [{"id": "delivery-publication-wait", "kind": "wait"}],
                "edges": [
                    {
                        "id": "delivery-wait-publish",
                        "source": "delivery-publication-wait",
                        "target": "delivery-publish",
                        "label": "developer.delivery.observed -> developer.delivery.observed",
                    },
                ],
            }
        },
    )
    return replace(execution, **changes)


def _request(**changes):
    request = WorkflowWaitRequest(
        mode="checks",
        repository="https://example.test/repo.git",
        reviewNumber=8,
        expectedHeadSha="b" * 40,
        expectedBaseSha="a" * 40,
        expectedTargetBranch="main",
        policyId="developer-integration",
    )
    return request.model_copy(update=changes)


def _observation(
    request: WorkflowWaitRequest,
    status: WaitObservationStatus,
) -> WaitObservation:
    return WaitObservation(
        status=status,
        repository=request.repository,
        review_number=request.review_number,
        expected_head_sha=request.expected_head_sha,
        expected_base_sha=request.expected_base_sha,
        expected_target_branch=request.expected_target_branch,
        observed_at=datetime.now(UTC),
        reason=status.value,
    )


def test_delivery_wait_adapters_are_dynamic_and_runtime_dependencies_are_reserved() -> None:
    config = WorkflowExecutionConfig()
    assert config.delivery_wait_repository_adapter.endswith("PostgresWorkflowWaitRepository")
    assert config.delivery_wait_observer_adapter.endswith("ForgeWaitConditionObserver")
    assert config.admission_roles == ["volundr:developer"]

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


def test_workflow_execution_config_rejects_empty_admission_roles() -> None:
    with pytest.raises(ValueError):
        WorkflowExecutionConfig(admission_roles=[])


class MemoryWaitRepository:
    def __init__(self):
        self.waits: dict = {}

    async def reserve(
        self,
        execution,
        request,
        *,
        wait_id,
        request_digest,
        candidate_digest,
        next_poll_at,
    ):
        existing = self.waits.get(wait_id)
        if existing is not None:
            return existing
        wait = WorkflowWait(
            id=wait_id,
            execution_id=execution.id,
            request=request,
            request_digest=request_digest,
            execution_generation=execution.current_generation,
            execution_revision=execution.revision,
            candidate_digest=candidate_digest,
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
            if observation.status
            in {
                WaitObservationStatus.CHECKS_PASSED,
                WaitObservationStatus.MERGED,
            }
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


class SequenceObserver:
    def __init__(self, observations):
        self.observations = list(observations)
        self.calls = 0

    async def observe(self, execution, request):
        del execution, request
        self.calls += 1
        return self.observations.pop(0)


class RecordingContinuation:
    def __init__(self, *, fail_once=False):
        self.fail_once = fail_once
        self.calls = []

    async def notify_delivery_observation(self, execution, wait, observation):
        self.calls.append((execution, wait, observation))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary continuation failure")


def _service(
    execution,
    repository,
    observer,
    continuation,
    *,
    execution_repository=None,
    max_consecutive_failures=5,
):
    return WorkflowWaitService(
        repository=repository,
        execution_repository=execution_repository or MemoryExecutionRepository(execution),
        observer=observer,
        continuation=continuation,
        policy_id="developer-integration",
        worker_id="worker-1",
        claim_limit=10,
        lease_seconds=30,
        poll_interval_seconds=5,
        max_consecutive_failures=max_consecutive_failures,
    )


@pytest.mark.asyncio
async def test_request_wait_binds_generation_candidate_and_lists_exact_request() -> None:
    execution = _bound_execution()
    repository = MemoryWaitRepository()
    service = _service(execution, repository, SequenceObserver([]), RecordingContinuation())

    wait = await service.request_wait(execution, _request())
    same = await service.request_wait(execution, _request())

    assert same.id == wait.id
    assert wait.execution_generation == 2
    assert wait.candidate_digest == delivery_candidate_digest(
        integration_candidate=execution.integration_candidate,
        integration_allocation=execution.integration_allocation,
    )
    assert await service.list_waits(execution) == [wait]
    assert wait.to_dict()["request"]["expectedHeadSha"] == "b" * 40
    assert "provider_operation_id" not in wait.request.canonical_payload()
    assert "providerOperationId" not in wait.to_dict()["request"]
    assert wait.to_dict()["generation"] == 2


@pytest.mark.asyncio
async def test_new_merge_wait_requires_durable_provider_operation_id() -> None:
    execution = _bound_execution()
    service = _service(
        execution,
        MemoryWaitRepository(),
        SequenceObserver([]),
        RecordingContinuation(),
    )
    request = WorkflowWaitRequest.model_validate(
        {
            **_request().model_dump(mode="json"),
            "mode": "merge",
            "method": "squash",
        }
    )

    with pytest.raises(WorkflowExecutionError, match="provider operation ID"):
        await service.request_wait(execution, request)

    persisted = await service.request_wait(
        execution,
        request.model_copy(update={"provider_operation_id": "operation-7"}),
    )
    assert persisted.request.provider_operation_id == "operation-7"
    assert persisted.to_dict()["request"]["providerOperationId"] == "operation-7"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"policy_id": "weaker"}, "policy"),
        ({"repository": "https://example.test/other.git"}, "repository"),
        ({"expected_head_sha": "c" * 40}, "head"),
        ({"expected_base_sha": "d" * 40}, "base"),
        ({"expected_target_branch": "release"}, "target branch"),
    ],
)
async def test_request_wait_rejects_unbound_identity(change, message) -> None:
    execution = _bound_execution()
    service = _service(
        execution,
        MemoryWaitRepository(),
        SequenceObserver([]),
        RecordingContinuation(),
    )

    with pytest.raises(WorkflowExecutionError, match=message):
        await service.request_wait(execution, _request(**change))


@pytest.mark.asyncio
async def test_pending_then_terminal_observation_is_persisted_before_notification() -> None:
    execution = _bound_execution()
    request = _request()
    repository = MemoryWaitRepository()
    observer = SequenceObserver(
        [
            _observation(request, WaitObservationStatus.CHECKS_PENDING),
            _observation(request, WaitObservationStatus.CHECKS_PASSED),
        ]
    )
    continuation = RecordingContinuation()
    service = _service(execution, repository, observer, continuation)
    wait = await service.request_wait(execution, request)

    assert await service.reconcile() == 1
    pending = repository.waits[wait.id]
    assert pending.state is WorkflowWaitState.PENDING
    assert pending.attempt_count == 1
    assert continuation.calls == []
    repository.waits[wait.id] = replace(pending, next_poll_at=datetime.now(UTC))

    assert await service.reconcile() == 1
    assert repository.waits[wait.id].state is WorkflowWaitState.NOTIFIED
    assert continuation.calls[0][1].state is WorkflowWaitState.READY
    assert continuation.calls[0][2].status is WaitObservationStatus.CHECKS_PASSED


@pytest.mark.asyncio
async def test_terminal_notification_retries_without_repolling_after_restart() -> None:
    execution = _bound_execution()
    request = _request()
    repository = MemoryWaitRepository()
    observer = SequenceObserver([_observation(request, WaitObservationStatus.CHECKS_PASSED)])
    continuation = RecordingContinuation(fail_once=True)
    service = _service(execution, repository, observer, continuation)
    wait = await service.request_wait(execution, request)

    # The notify failure on an already-terminal wait is isolated and
    # does not raise out of reconcile(); the natural lease-expiry retry
    # (below) replays the notification.
    assert await service.reconcile() == 1
    assert repository.waits[wait.id].state is WorkflowWaitState.READY
    assert observer.calls == 1

    repository.waits[wait.id] = replace(
        repository.waits[wait.id],
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    restarted = _service(execution, repository, observer, continuation)
    assert await restarted.reconcile() == 1
    assert observer.calls == 1
    assert len(continuation.calls) == 2
    assert repository.waits[wait.id].state is WorkflowWaitState.NOTIFIED


class SelectiveObserver:
    """Raises for one execution's waits, returns a fixed observation for others."""

    def __init__(self, *, poison_execution_id, observation) -> None:
        self.poison_execution_id = poison_execution_id
        self.observation = observation
        self.calls = 0

    async def observe(self, execution, request):
        del request
        self.calls += 1
        if execution.id == self.poison_execution_id:
            raise RuntimeError("observer unavailable")
        return self.observation


@pytest.mark.asyncio
async def test_one_poisoned_wait_does_not_stall_reconciliation_of_others() -> None:
    """A wait whose observer call always raises must not stop
    other due waits in the same batch, and must eventually become durably,
    visibly failed rather than pending forever."""
    poisoned_execution = _bound_execution()
    healthy_execution = _bound_execution()
    repository = MemoryWaitRepository()
    execution_repository = MultiExecutionRepository(
        {
            poisoned_execution.id: poisoned_execution,
            healthy_execution.id: healthy_execution,
        }
    )
    observer = SelectiveObserver(
        poison_execution_id=poisoned_execution.id,
        observation=_observation(_request(), WaitObservationStatus.CHECKS_PASSED),
    )
    continuation = RecordingContinuation()
    service = _service(
        poisoned_execution,
        repository,
        observer,
        continuation,
        execution_repository=execution_repository,
        max_consecutive_failures=2,
    )
    poisoned_wait = await service.request_wait(poisoned_execution, _request())
    healthy_wait = await service.request_wait(healthy_execution, _request())

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
    assert terminal.state in {
        WorkflowWaitState.FAILED,
        WorkflowWaitState.NOTIFIED,
    }
    assert terminal.observation is not None
    assert terminal.observation.terminal


@pytest.mark.asyncio
async def test_superseded_generation_is_audited_without_waking_parent() -> None:
    execution = _bound_execution()
    repository = MemoryWaitRepository()
    observer = SequenceObserver([])
    continuation = RecordingContinuation()
    execution_repository = MemoryExecutionRepository(execution)
    service = WorkflowWaitService(
        repository=repository,
        execution_repository=execution_repository,
        observer=observer,
        continuation=continuation,
        policy_id="developer-integration",
        worker_id="worker-1",
        claim_limit=10,
        lease_seconds=30,
        poll_interval_seconds=5,
    )
    wait = await service.request_wait(execution, _request())
    execution_repository.execution = replace(execution, current_generation=3)

    assert await service.reconcile() == 1
    recovered = repository.waits[wait.id]
    assert recovered.state is WorkflowWaitState.NOTIFIED
    assert recovered.observation is not None
    assert recovered.observation.status is WaitObservationStatus.STALE_CANDIDATE
    assert observer.calls == 0
    assert continuation.calls == []


@pytest.mark.asyncio
async def test_cancel_race_after_terminal_observation_suppresses_parent_notification() -> None:
    execution = _bound_execution()
    execution_repository = MemoryExecutionRepository(execution)
    repository = MemoryWaitRepository()
    request = _request()

    class CancelingObserver:
        async def observe(self, execution, request):
            execution_repository.execution = replace(execution, cancel_requested=True)
            return _observation(
                request,
                WaitObservationStatus.CHECKS_PASSED,
            )

    continuation = RecordingContinuation()
    service = WorkflowWaitService(
        repository=repository,
        execution_repository=execution_repository,
        observer=CancelingObserver(),
        continuation=continuation,
        policy_id="developer-integration",
        worker_id="worker-1",
        claim_limit=10,
        lease_seconds=30,
        poll_interval_seconds=5,
    )
    wait = await service.request_wait(execution, request)

    assert await service.reconcile() == 1
    assert repository.waits[wait.id].state is WorkflowWaitState.NOTIFIED
    assert continuation.calls == []


@pytest.mark.asyncio
async def test_remote_error_is_visible_and_retried_without_false_observation() -> None:
    execution = _bound_execution()
    request = _request()
    repository = MemoryWaitRepository()

    class RecoveringObserver:
        def __init__(self):
            self.calls = 0

        async def observe(self, execution, request):
            del execution
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Forge unavailable")
            return _observation(
                request,
                WaitObservationStatus.CHECKS_PASSED,
            )

    observer = RecoveringObserver()
    continuation = RecordingContinuation()
    service = _service(execution, repository, observer, continuation)
    wait = await service.request_wait(execution, request)

    assert await service.reconcile() == 1
    failed_poll = repository.waits[wait.id]
    assert failed_poll.state is WorkflowWaitState.PENDING
    assert failed_poll.observation is None
    assert failed_poll.last_error == "RuntimeError: Forge unavailable"
    assert failed_poll.to_dict()["lastError"] == "RuntimeError: Forge unavailable"
    repository.waits[wait.id] = replace(failed_poll, next_poll_at=datetime.now(UTC))

    assert await service.reconcile() == 1
    assert observer.calls == 2
    assert repository.waits[wait.id].state is WorkflowWaitState.NOTIFIED
    assert len(continuation.calls) == 1


@pytest.mark.asyncio
async def test_terminal_or_canceled_execution_never_receives_recovered_notification() -> None:
    execution = _bound_execution(state=ExecutionState.FAILED, cancel_requested=True)
    request = _request()
    repository = MemoryWaitRepository()
    wait = WorkflowWait(
        id=uuid4(),
        execution_id=execution.id,
        request=request,
        request_digest="d" * 64,
        execution_generation=execution.current_generation,
        execution_revision=execution.revision,
        candidate_digest=delivery_candidate_digest(
            integration_candidate=execution.integration_candidate,
            integration_allocation=execution.integration_allocation,
        ),
        state=WorkflowWaitState.READY,
        next_poll_at=datetime.now(UTC),
        observation=_observation(
            request,
            WaitObservationStatus.CHECKS_PASSED,
        ),
    )
    repository.waits[wait.id] = wait
    continuation = RecordingContinuation()
    service = _service(execution, repository, SequenceObserver([]), continuation)

    assert await service.reconcile() == 1
    assert repository.waits[wait.id].state is WorkflowWaitState.NOTIFIED
    assert continuation.calls == []


@pytest.mark.asyncio
async def test_terminal_continuation_carries_exact_stable_wait_binding() -> None:
    class Adapter:
        def __init__(self):
            self.calls = []

        async def publish_workflow_event(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    adapter = Adapter()

    class Factory:
        async def for_connection(self, owner_id, connection_id):
            assert (owner_id, connection_id) == ("owner-1", "forge-1")
            return adapter

    execution = _bound_execution()
    request = _request()
    wait = WorkflowWait(
        id=uuid4(),
        execution_id=execution.id,
        request=request,
        request_digest="d" * 64,
        execution_generation=execution.current_generation,
        execution_revision=execution.revision,
        candidate_digest="e" * 64,
        state=WorkflowWaitState.READY,
        next_poll_at=datetime.now(UTC),
    )
    observation = _observation(
        request,
        WaitObservationStatus.CHECKS_PASSED,
    )
    continuation = VolundrParentWorkflowContinuation(volundr_factory=Factory())

    await continuation.notify_delivery_observation(execution, wait, observation)
    await continuation.notify_delivery_observation(execution, wait, observation)

    first_args, first_kwargs = adapter.calls[0]
    second_args, second_kwargs = adapter.calls[1]
    assert first_args[:2] == (execution.parent_session_id, "developer.delivery.observed")
    assert first_kwargs["payload"]["generation"] == execution.current_generation
    assert first_kwargs["payload"]["candidateDigest"] == "e" * 64
    assert first_kwargs["payload"]["expectedHeadSha"] == request.expected_head_sha
    assert first_kwargs["payload"]["reviewNumber"] == request.review_number
    assert first_kwargs["request_id"] == second_kwargs["request_id"]
    assert first_args == second_args

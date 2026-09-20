"""Deterministic reconciliation of durable remote delivery observations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from ting.domain.developer_delivery_wait import (
    DeveloperDeliveryObservation,
    DeveloperDeliveryObservationStatus,
    DeveloperDeliveryWait,
    DeveloperDeliveryWaitMode,
    DeveloperDeliveryWaitRequest,
    DeveloperDeliveryWaitState,
    developer_delivery_candidate_digest,
    developer_delivery_wait_digest,
)
from ting.domain.developer_execution import (
    TERMINAL_EXECUTION_STATES,
    DeveloperExecution,
    DeveloperExecutionError,
)
from ting.ports.developer_delivery_wait import (
    DeveloperDeliveryObserver,
    DeveloperDeliveryWaitRepository,
)
from ting.ports.developer_execution import (
    DeveloperExecutionRepository,
    ParentWorkflowContinuation,
)


class DeveloperDeliveryWaitService:
    """Persist exact waits and poll them without model participation."""

    def __init__(
        self,
        *,
        repository: DeveloperDeliveryWaitRepository,
        execution_repository: DeveloperExecutionRepository,
        observer: DeveloperDeliveryObserver,
        continuation: ParentWorkflowContinuation,
        policy_id: str,
        worker_id: str,
        claim_limit: int,
        lease_seconds: float,
        poll_interval_seconds: float,
    ) -> None:
        if not policy_id.strip() or not worker_id.strip():
            raise ValueError("Delivery wait policy and worker identities are required")
        if claim_limit <= 0 or lease_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("Delivery wait reconciliation bounds must be positive")
        self._repository = repository
        self._execution_repository = execution_repository
        self._observer = observer
        self._continuation = continuation
        self._policy_id = policy_id
        self._worker_id = worker_id
        self._claim_limit = claim_limit
        self._lease_seconds = lease_seconds
        self._poll_interval_seconds = poll_interval_seconds

    async def request_wait(
        self,
        execution: DeveloperExecution,
        request: DeveloperDeliveryWaitRequest,
    ) -> DeveloperDeliveryWait:
        if execution.state in TERMINAL_EXECUTION_STATES or execution.cancel_requested:
            raise DeveloperExecutionError("Developer execution cannot register a delivery wait")
        if request.policy_id != self._policy_id:
            raise DeveloperExecutionError(
                "Delivery wait policy must match the configured integration policy"
            )
        if (
            request.mode is DeveloperDeliveryWaitMode.MERGE
            and request.provider_operation_id is None
        ):
            raise DeveloperExecutionError(
                "Merge delivery waits require the provider operation ID returned by Forge"
            )
        if request.repository != execution.repository:
            raise DeveloperExecutionError("Delivery wait repository differs from the execution")
        candidate = execution.integration_candidate or {}
        candidate_sha = str(candidate.get("candidate_sha") or "")
        if not candidate_sha or request.expected_head_sha != candidate_sha:
            raise DeveloperExecutionError(
                "Delivery wait head must match the current inspected integration candidate"
            )
        if request.expected_base_sha != execution.base_sha:
            raise DeveloperExecutionError("Delivery wait base must match the execution base")
        if request.expected_target_branch != execution.base_ref:
            raise DeveloperExecutionError(
                "Delivery wait target branch must match the execution base ref"
            )
        if (
            candidate.get("repository") != request.repository
            or candidate.get("base_sha") != request.expected_base_sha
        ):
            raise DeveloperExecutionError(
                "Delivery wait base identity must match the current inspected candidate"
            )
        candidate_digest = developer_delivery_candidate_digest(
            integration_candidate=execution.integration_candidate,
            integration_allocation=execution.integration_allocation,
        )
        request_digest = developer_delivery_wait_digest(
            request,
            execution_generation=execution.current_generation,
            candidate_digest=candidate_digest,
        )
        now = datetime.now(UTC)
        wait_id = uuid5(
            NAMESPACE_URL,
            f"niuulabs:developer-execution:{execution.id}:delivery-wait:{request_digest}",
        )
        return await self._repository.reserve(
            execution,
            request,
            wait_id=wait_id,
            request_digest=request_digest,
            candidate_digest=candidate_digest,
            next_poll_at=now,
        )

    async def list_waits(self, execution: DeveloperExecution) -> list[DeveloperDeliveryWait]:
        return await self._repository.list_for_execution(execution.id)

    async def reconcile(self) -> int:
        now = datetime.now(UTC)
        waits = await self._repository.claim_due(
            worker_id=self._worker_id,
            limit=self._claim_limit,
            lease_until=now + timedelta(seconds=self._lease_seconds),
            now=now,
        )
        processed = 0
        for wait in waits:
            await self._reconcile_wait(wait, now=now)
            processed += 1
        return processed

    async def _reconcile_wait(self, wait: DeveloperDeliveryWait, *, now: datetime) -> None:
        execution = await self._execution_repository.get_internal(wait.execution_id)
        if execution is None:
            raise DeveloperExecutionError("Delivery wait execution no longer exists")
        binding_current = _wait_binding_is_current(execution, wait)
        if wait.state in {DeveloperDeliveryWaitState.READY, DeveloperDeliveryWaitState.FAILED}:
            if wait.observation is None or not wait.observation.terminal:
                raise DeveloperExecutionError("Terminal delivery wait is missing its observation")
            if (
                execution.state in TERMINAL_EXECUTION_STATES
                or execution.cancel_requested
                or not binding_current
            ):
                await self._repository.mark_notified(wait)
                return
            await self._notify(execution, wait, wait.observation)
            return
        if not binding_current:
            observation = _failure_observation(
                wait.request,
                DeveloperDeliveryObservationStatus.STALE_CANDIDATE,
                now,
                "Delivery wait was superseded by a newer execution generation or candidate",
            )
        elif now >= execution.deadline:
            status = (
                DeveloperDeliveryObservationStatus.CHECKS_FAILED
                if wait.request.mode is DeveloperDeliveryWaitMode.CHECKS
                else DeveloperDeliveryObservationStatus.MERGE_FAILED
            )
            observation = _failure_observation(
                wait.request,
                status,
                now,
                "Developer execution deadline elapsed before remote delivery completed",
            )
        elif execution.state in TERMINAL_EXECUTION_STATES or execution.cancel_requested:
            status = (
                DeveloperDeliveryObservationStatus.CHECKS_FAILED
                if wait.request.mode is DeveloperDeliveryWaitMode.CHECKS
                else DeveloperDeliveryObservationStatus.MERGE_FAILED
            )
            observation = _failure_observation(
                wait.request,
                status,
                now,
                f"Developer execution is {execution.state.value}",
            )
        else:
            try:
                observation = await self._observer.observe(execution, wait.request)
            except Exception as exc:
                await self._repository.record_pending(
                    wait,
                    None,
                    next_poll_at=now + timedelta(seconds=self._poll_interval_seconds),
                    error=f"{type(exc).__name__}: {exc}",
                )
                return
        if not observation.terminal:
            await self._repository.record_pending(
                wait,
                observation,
                next_poll_at=now + timedelta(seconds=self._poll_interval_seconds),
            )
            return
        terminal = await self._repository.record_terminal(
            wait,
            observation,
            expected_execution_revision=execution.revision,
            project_execution=binding_current,
        )
        current = await self._execution_repository.get_internal(wait.execution_id)
        if current is None:
            raise DeveloperExecutionError("Delivery wait execution no longer exists")
        if (
            current.state in TERMINAL_EXECUTION_STATES
            or current.cancel_requested
            or not _wait_binding_is_current(current, wait)
        ):
            await self._repository.mark_notified(terminal)
            return
        await self._notify(current, terminal, observation)

    async def _notify(
        self,
        execution: DeveloperExecution,
        wait: DeveloperDeliveryWait,
        observation: DeveloperDeliveryObservation,
    ) -> None:
        await self._continuation.notify_delivery_observation(execution, wait, observation)
        await self._repository.mark_notified(wait)


def _wait_binding_is_current(
    execution: DeveloperExecution,
    wait: DeveloperDeliveryWait,
) -> bool:
    return (
        execution.current_generation == wait.execution_generation
        and developer_delivery_candidate_digest(
            integration_candidate=execution.integration_candidate,
            integration_allocation=execution.integration_allocation,
        )
        == wait.candidate_digest
    )


def _failure_observation(
    request: DeveloperDeliveryWaitRequest,
    status: DeveloperDeliveryObservationStatus,
    observed_at: datetime,
    reason: str,
) -> DeveloperDeliveryObservation:
    return DeveloperDeliveryObservation(
        status=status,
        repository=request.repository,
        review_number=request.review_number,
        expected_head_sha=request.expected_head_sha,
        expected_base_sha=request.expected_base_sha,
        expected_target_branch=request.expected_target_branch,
        observed_at=observed_at,
        reason=reason,
    )

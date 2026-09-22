"""Deterministic reconciliation of durable, condition-agnostic workflow waits."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from ting.domain.workflow_execution import (
    TERMINAL_EXECUTION_STATES,
    WorkflowExecution,
    WorkflowExecutionError,
)
from ting.domain.workflow_wait import (
    WaitObservation,
    WaitObservationStatus,
    WorkflowWait,
    WorkflowWaitState,
    workflow_wait_digest,
)
from ting.ports.workflow_wait import WaitConditionObserver, WaitContinuation, WorkflowWaitRepository

logger = logging.getLogger(__name__)


class _ExecutionLookup(Protocol):
    """Trusted, unscoped execution lookup used for background reconciliation."""

    async def get_internal(self, execution_id: UUID) -> WorkflowExecution | None: ...


class WorkflowWaitService:
    """Persist exact waits and poll them without model participation.

    ``observers`` maps a ``condition_type`` to the observer registered for it
    (see ``ting.ports.workflow_wait.WaitConditionObserver``); dispatch is a
    plain dictionary lookup keyed by the wait's own ``condition_type``; this
    service never branches on what that string names.
    """

    def __init__(
        self,
        *,
        repository: WorkflowWaitRepository,
        execution_repository: _ExecutionLookup,
        observers: dict[str, WaitConditionObserver],
        continuation: WaitContinuation[WorkflowExecution],
        worker_id: str,
        claim_limit: int,
        lease_seconds: float,
        poll_interval_seconds: float,
        max_consecutive_failures: int = 5,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("Workflow wait worker identity is required")
        if claim_limit <= 0 or lease_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("Workflow wait reconciliation bounds must be positive")
        if max_consecutive_failures <= 0:
            raise ValueError("Workflow wait max_consecutive_failures must be positive")
        mismatched = [
            condition_type
            for condition_type, observer in observers.items()
            if observer.condition_type != condition_type
        ]
        if mismatched:
            raise ValueError(
                f"Wait observer registry key(s) do not match their own condition_type: "
                f"{', '.join(sorted(mismatched))}"
            )
        self._repository = repository
        self._execution_repository = execution_repository
        self._observers = dict(observers)
        self._continuation = continuation
        self._worker_id = worker_id
        self._claim_limit = claim_limit
        self._lease_seconds = lease_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._max_consecutive_failures = max_consecutive_failures

    async def request_wait(
        self,
        execution: WorkflowExecution,
        *,
        node_id: str,
        condition_type: str,
        request: dict[str, Any],
        allowed_condition_types: frozenset[str],
    ) -> WorkflowWait:
        if execution.state in TERMINAL_EXECUTION_STATES or execution.cancel_requested:
            raise WorkflowExecutionError("Execution cannot register a new wait")
        if not node_id.strip():
            raise WorkflowExecutionError("wait node_id is required")
        if condition_type not in allowed_condition_types:
            raise WorkflowExecutionError(
                f"Wait node {node_id!r} does not declare condition_type {condition_type!r}"
            )
        observer = self._observers.get(condition_type)
        if observer is None:
            raise WorkflowExecutionError(
                f"No wait observer is registered for condition_type {condition_type!r}"
            )
        try:
            observer.validate(request, execution)
        except Exception as exc:
            raise WorkflowExecutionError(
                f"Wait request is invalid for condition_type {condition_type!r}: {exc}"
            ) from exc
        request_digest = workflow_wait_digest(
            node_id=node_id,
            condition_type=condition_type,
            request=request,
            execution_generation=execution.current_generation,
        )
        now = datetime.now(UTC)
        wait_id = uuid5(
            NAMESPACE_URL,
            f"niuulabs:workflow-execution:{execution.id}:wait:{request_digest}",
        )
        return await self._repository.reserve(
            execution,
            wait_id=wait_id,
            node_id=node_id,
            condition_type=condition_type,
            request=request,
            request_digest=request_digest,
            next_poll_at=now,
        )

    async def list_waits(self, execution: WorkflowExecution) -> list[WorkflowWait]:
        return await self._repository.list_for_execution(execution.id)

    async def reconcile(self) -> int:
        """Poll every due wait, isolating one item's failure from the rest.

        A wait that keeps raising is durably recorded (attempt/error) and,
        past the configured maximum, terminally failed instead of retrying
        forever at the head of the claim ordering.
        """
        now = datetime.now(UTC)
        waits = await self._repository.claim_due(
            worker_id=self._worker_id,
            limit=self._claim_limit,
            lease_until=now + timedelta(seconds=self._lease_seconds),
            now=now,
        )
        processed = 0
        for wait in waits:
            try:
                await self._reconcile_wait(wait, now=now)
            except Exception as exc:
                logger.exception(
                    "Wait reconciliation failed; recording durable failure",
                    extra={"workflow_wait_id": str(wait.id)},
                )
                await self._handle_wait_failure(
                    wait, now=now, error_text=f"{type(exc).__name__}: {exc}"
                )
            processed += 1
        return processed

    async def _handle_wait_failure(
        self,
        wait: WorkflowWait,
        *,
        now: datetime,
        error_text: str,
    ) -> None:
        """Durably record one polling failure, escalating past the maximum.

        Called both for exceptions that escape ``_reconcile_wait`` entirely
        (e.g. the execution lookup itself failing) and for an observer
        exception caught inline while still polling.
        """
        if wait.state is not WorkflowWaitState.PENDING:
            # Notify failures on an already-terminal wait are swallowed by
            # `_notify_safely` and never reach here; this is defense in depth
            # for any other exception raised once the wait is terminal. The
            # existing lease-expiry retry already replays a terminal wait
            # without re-polling the observer, so nothing further to record.
            return
        if wait.attempt_count + 1 < self._max_consecutive_failures:
            await self._repository.record_pending(
                wait,
                None,
                next_poll_at=now + timedelta(seconds=self._poll_interval_seconds),
                error=error_text,
            )
            return
        observation = _failure_observation(
            now, f"Wait reconciliation failed repeatedly: {error_text}"
        )
        try:
            execution = await self._execution_repository.get_internal(wait.execution_id)
            project_execution = execution is not None and _binding_is_current(execution, wait)
            terminal = await self._repository.record_terminal(
                wait,
                observation,
                expected_execution_revision=(execution.revision if execution else 0),
                project_execution=project_execution,
            )
            current = await self._execution_repository.get_internal(wait.execution_id)
            if (
                current is None
                or current.state in TERMINAL_EXECUTION_STATES
                or current.cancel_requested
                or not _binding_is_current(current, wait)
            ):
                await self._repository.mark_notified(terminal)
                return
            await self._notify_safely(current, terminal, observation)
        except Exception:
            logger.exception(
                "Wait terminal failure transition also failed; the wait stays "
                "pending and will retry next cycle",
                extra={"workflow_wait_id": str(wait.id)},
            )
            await self._repository.record_pending(
                wait,
                None,
                next_poll_at=now + timedelta(seconds=self._poll_interval_seconds),
                error=error_text,
            )

    async def _reconcile_wait(self, wait: WorkflowWait, *, now: datetime) -> None:
        execution = await self._execution_repository.get_internal(wait.execution_id)
        if execution is None:
            raise WorkflowExecutionError("Wait execution no longer exists")
        binding_current = _binding_is_current(execution, wait)
        if wait.state in {WorkflowWaitState.READY, WorkflowWaitState.FAILED}:
            if wait.observation is None or not wait.observation.terminal:
                raise WorkflowExecutionError("Terminal wait is missing its observation")
            if (
                execution.state in TERMINAL_EXECUTION_STATES
                or execution.cancel_requested
                or not binding_current
            ):
                await self._repository.mark_notified(wait)
                return
            await self._notify_safely(execution, wait, wait.observation)
            return
        if not binding_current:
            observation = _failure_observation(
                now, "Wait was superseded by a newer execution generation"
            )
        elif now >= execution.deadline:
            observation = _failure_observation(
                now, "Execution deadline elapsed before the wait condition was observed"
            )
        elif execution.state in TERMINAL_EXECUTION_STATES or execution.cancel_requested:
            observation = _failure_observation(now, f"Execution is {execution.state.value}")
        else:
            observer = self._observers.get(wait.condition_type)
            if observer is None:
                raise WorkflowExecutionError(
                    f"No wait observer is registered for condition_type {wait.condition_type!r}"
                )
            try:
                observation = await observer.observe(wait, execution)
            except Exception as exc:
                logger.exception(
                    "Wait observer failed; recording durable failure",
                    extra={"workflow_wait_id": str(wait.id)},
                )
                await self._handle_wait_failure(
                    wait, now=now, error_text=f"{type(exc).__name__}: {exc}"
                )
                return
        if not observation.terminal:
            poll_delay = (
                observation.retry_after_seconds
                if observation.retry_after_seconds is not None
                else self._poll_interval_seconds
            )
            await self._repository.record_pending(
                wait,
                observation,
                next_poll_at=now + timedelta(seconds=poll_delay),
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
            raise WorkflowExecutionError("Wait execution no longer exists")
        if (
            current.state in TERMINAL_EXECUTION_STATES
            or current.cancel_requested
            or not _binding_is_current(current, wait)
        ):
            await self._repository.mark_notified(terminal)
            return
        await self._notify_safely(current, terminal, observation)

    async def _notify(
        self,
        execution: WorkflowExecution,
        wait: WorkflowWait,
        observation: WaitObservation,
    ) -> None:
        await self._continuation.notify_wait_observation(execution, wait, observation)
        await self._repository.mark_notified(wait)

    async def _notify_safely(
        self,
        execution: WorkflowExecution,
        wait: WorkflowWait,
        observation: WaitObservation,
    ) -> None:
        """Notify the parent, leaving the lease held for a natural retry on failure.

        The wait is already terminal at this point (`record_terminal` already
        persisted it). A failure here must not be treated as a fresh polling
        failure — it is isolated so the batch continues, and the wait's
        held lease naturally expires and is reclaimed to retry the notify
        alone, without re-polling the observer.
        """
        try:
            await self._notify(execution, wait, observation)
        except Exception:
            logger.exception(
                "Wait parent notification failed; the held lease will expire "
                "and retry the notification without re-polling",
                extra={"workflow_wait_id": str(wait.id)},
            )


def _binding_is_current(execution: WorkflowExecution, wait: WorkflowWait) -> bool:
    return execution.current_generation == wait.execution_generation


def _failure_observation(observed_at: datetime, reason: str) -> WaitObservation:
    return WaitObservation(
        status=WaitObservationStatus.FAILED,
        observed_at=observed_at,
        reason=reason,
    )

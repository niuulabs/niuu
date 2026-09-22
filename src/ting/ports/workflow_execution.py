"""Persistence and outbound boundaries for the reusable expanded-workflow lifecycle."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from ting.domain.workflow_execution import (
    ChildLaunchRequest,
    ChildMessage,
    ChildTaskHandle,
    ChildTaskObservation,
    JoinStatus,
    WorkflowChildExecution,
    WorkflowExecution,
)


class WorkflowExecutionRepository[
    ExecutionT: WorkflowExecution,
    ChildT: WorkflowChildExecution,
](ABC):
    """Atomic ledger operations required by any expanded workflow domain."""

    @abstractmethod
    async def create(self, execution: ExecutionT) -> ExecutionT:
        """Create an execution, rejecting duplicate identifiers."""

    @abstractmethod
    async def reserve_parent_launch(self, execution: ExecutionT) -> tuple[ExecutionT, bool]:
        """Reserve a parent launch or return the matching idempotent reservation."""

    @abstractmethod
    async def attach_parent_session(
        self,
        execution_id: UUID,
        *,
        session_id: str,
        connection_id: str,
    ) -> ExecutionT:
        """Attach the recovered/spawned parent session exactly once."""

    @abstractmethod
    async def get(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> ExecutionT | None:
        """Fetch one owner-and-tenant-scoped execution."""

    @abstractmethod
    async def get_internal(self, execution_id: UUID) -> ExecutionT | None:
        """Fetch an execution for trusted background reconciliation."""

    @abstractmethod
    async def get_by_parent_session(
        self,
        *,
        owner_id: str,
        session_id: str,
    ) -> ExecutionT | None:
        """Fetch an active execution from its exact parent runtime session."""

    @abstractmethod
    async def list(
        self,
        *,
        owner_id: str,
        tenant_id: str,
        state: str = "",
        limit: int,
        cursor: str = "",
    ) -> tuple[list[ExecutionT], str]:
        """List executions with an opaque cursor."""

    @abstractmethod
    async def reserve_generation(
        self,
        execution: ExecutionT,
        children: tuple[ChildT, ...],
        *,
        plan_revision: str,
    ) -> ExecutionT:
        """Atomically reserve a generation and its child budgets."""

    @abstractmethod
    async def list_children(self, execution_id: UUID) -> list[ChildT]:
        """List every child attempt in stable order."""

    @abstractmethod
    async def get_child(self, child_id: UUID) -> ChildT | None:
        """Fetch one child attempt."""

    @abstractmethod
    async def list_reconcilable(self, *, limit: int) -> list[ChildT]:
        """List launched child tasks needing remote reconciliation."""

    @abstractmethod
    async def list_parent_stop_pending(self, *, limit: int) -> list[ExecutionT]:
        """List owner-requested parent runtime stops that still need delivery."""

    @abstractmethod
    async def list_deadline_expired_children(self, *, now: datetime, limit: int) -> list[ChildT]:
        """List non-terminal children whose own deadline has already elapsed."""

    @abstractmethod
    async def list_deadline_expired_executions(
        self, *, now: datetime, limit: int
    ) -> list[ExecutionT]:
        """List non-terminal executions whose own deadline has already elapsed."""

    @abstractmethod
    async def mark_parent_stopped(self, execution_id: UUID) -> None:
        """Record successful idempotent termination of the parent runtime."""

    @abstractmethod
    async def record_parent_stop_error(
        self,
        execution_id: UUID,
        *,
        error: str,
        max_attempts: int,
    ) -> None:
        """Durably record one failed parent-stop attempt.

        Advances the attempt count so a permanently failing stop no longer
        sorts first in `list_parent_stop_pending`'s ordering and starves
        fresher rows. Past ``max_attempts`` the row is marked visibly,
        terminally given up on (``parent_stopped_at`` set, ``parent_stop_error``
        holding the reason) instead of retrying forever.
        """

    @abstractmethod
    async def claim_launches(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_until: datetime,
    ) -> list[ChildT]:
        """Lease eligible launch intents with fencing generations."""

    @abstractmethod
    async def record_handle(
        self,
        child: ChildT,
        handle: ChildTaskHandle,
        *,
        worker_id: str,
        lease_token: UUID,
        fencing_generation: int,
    ) -> ChildT:
        """Persist a launched task's handle if the launch lease is current."""

    @abstractmethod
    async def record_observation(
        self,
        child: ChildT,
        observation: ChildTaskObservation,
    ) -> ChildT:
        """Persist one idempotent remote task observation."""

    @abstractmethod
    async def record_gate_report(self, child_id: UUID, report: dict) -> None:
        """Persist the trusted result-gate decision and manifest for audit."""

    @abstractmethod
    async def record_reconcile_error(
        self,
        child_id: UUID,
        *,
        error: str,
        failure_kind: str,
        max_consecutive_failures: int,
    ) -> ChildT | None:
        """Durably record a reconcile-loop exception for one child attempt.

        Advances the child's poll ordering (so a poisoned item cannot sort
        first forever) and increments its consecutive-failure counter. Once
        the counter reaches ``max_consecutive_failures`` the child is moved
        to its terminal ``failed`` state with ``failure_kind`` and ``error``
        so join logic treats it as failed instead of pending forever.
        Returns ``None`` if the child no longer exists or is already
        terminal — this is not itself an error.
        """

    @abstractmethod
    async def seal_generation(self, execution_id: UUID, generation: int) -> None:
        """Seal the expected child set for a generation."""

    @abstractmethod
    async def join_status(self, execution_id: UUID, generation: int) -> JoinStatus:
        """Read the durable fan-in barrier."""

    @abstractmethod
    async def request_cancel(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> ExecutionT:
        """Atomically stop spawning and mark nonterminal children canceling."""

    @abstractmethod
    async def create_retry(
        self,
        child: ChildT,
        retry: ChildT,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> ChildT:
        """Supersede the current attempt and reserve its bounded retry atomically."""

    @abstractmethod
    async def update_execution_state(
        self,
        execution_id: UUID,
        *,
        state: str,
        suspension_reason: str,
        expected_revision: int,
    ) -> None:
        """Persist an orchestration state projection from a known revision.

        Raises ``ExecutionConflictError`` when the execution has moved past
        ``expected_revision`` (or out of a state this projection may write
        into) instead of silently overwriting whatever is current.
        """

    @abstractmethod
    async def mark_blocker_notification(
        self,
        execution_id: UUID,
        *,
        blocker_revision: int,
    ) -> None:
        """Record successful delivery of one durable blocker snapshot."""

    @abstractmethod
    async def reserve_message(self, message: ChildMessage) -> ChildMessage:
        """Reserve an idempotent coordinator follow-up to a child."""

    @abstractmethod
    async def mark_message_delivered(self, message_id: UUID) -> None:
        """Record successful remote delivery of a coordinator follow-up."""


class ChildWorkflowGateway(ABC):
    """Narrow outbound child-task port; the implementation owns the runtime."""

    @abstractmethod
    async def launch_child(
        self, request: ChildLaunchRequest, *, auth_token: str = ""
    ) -> ChildTaskHandle:
        """Launch exactly one idempotent child task."""

    @abstractmethod
    async def get_child(
        self, handle: ChildTaskHandle, *, auth_token: str = ""
    ) -> ChildTaskObservation:
        """Read one child task without model polling."""

    @abstractmethod
    async def cancel_child(
        self, handle: ChildTaskHandle, *, auth_token: str = ""
    ) -> ChildTaskObservation:
        """Request cancellation of a child task."""

    @abstractmethod
    async def reply_child(
        self,
        handle: ChildTaskHandle,
        *,
        answer: str,
        metadata: dict,
        message_id: str,
        auth_token: str = "",
    ) -> ChildTaskObservation:
        """Reply to a blocked child through its existing task messaging."""


class SessionContinuation[ExecutionT: WorkflowExecution](ABC):
    """Resume the exact suspended parent session after a durable join."""

    @abstractmethod
    async def resume_parent(
        self,
        execution: ExecutionT,
        *,
        generation: int,
        results: list[dict],
    ) -> None:
        """Deliver an idempotently identified continuation to the parent session.

        The event type is the pinned graph's own outgoing edge from the
        expanding subworkflow node, not a caller-supplied literal.
        """

    @abstractmethod
    async def notify_parent(
        self,
        execution: ExecutionT,
        *,
        generation: int,
        correlation_revision: int,
        children: list[dict],
    ) -> None:
        """Deliver a durable child-blocked notification to the exact parent session.

        The event type is the pinned graph's own ``blockedEvent`` declaration
        for the expanding subworkflow node, not a caller-supplied literal.
        """

    @abstractmethod
    async def stop_parent(self, execution: ExecutionT) -> None:
        """Idempotently stop the exact parent runtime after owner cancellation."""

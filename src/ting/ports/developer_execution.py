"""Ports for Ting-owned durable developer workflow execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ting.domain.developer_execution import (
    ChildExecution,
    ChildLaunchRequest,
    ChildMessage,
    ChildTaskHandle,
    ChildTaskObservation,
    DeveloperExecution,
    JoinStatus,
)
from ting.ports.workflow_execution import WorkflowExecutionRepository

if TYPE_CHECKING:
    from ting.domain.developer_delivery_wait import (
        DeveloperDeliveryObservation,
        DeveloperDeliveryWait,
    )


class DeveloperExecutionRepository(
    WorkflowExecutionRepository[DeveloperExecution, ChildExecution],
    ABC,
):
    @abstractmethod
    async def create(self, execution: DeveloperExecution) -> DeveloperExecution:
        """Create an execution, rejecting duplicate identifiers."""

    @abstractmethod
    async def reserve_parent_launch(
        self,
        execution: DeveloperExecution,
    ) -> tuple[DeveloperExecution, bool]:
        """Reserve a parent launch or return the matching idempotent reservation."""

    @abstractmethod
    async def attach_parent_session(
        self,
        execution_id: UUID,
        *,
        session_id: str,
        connection_id: str,
    ) -> DeveloperExecution:
        """Attach the recovered/spawned parent session exactly once."""

    @abstractmethod
    async def get(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> DeveloperExecution | None:
        """Fetch one owner-and-tenant-scoped execution."""

    @abstractmethod
    async def get_internal(self, execution_id: UUID) -> DeveloperExecution | None:
        """Fetch an execution for trusted background reconciliation."""

    @abstractmethod
    async def get_by_parent_session(
        self,
        *,
        owner_id: str,
        session_id: str,
    ) -> DeveloperExecution | None:
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
    ) -> tuple[list[DeveloperExecution], str]:
        """List executions with an opaque cursor."""

    @abstractmethod
    async def reserve_generation(
        self,
        execution: DeveloperExecution,
        children: tuple[ChildExecution, ...],
        *,
        plan_revision: str,
    ) -> DeveloperExecution:
        """Atomically reserve a generation and its child budgets."""

    @abstractmethod
    async def list_children(self, execution_id: UUID) -> list[ChildExecution]:
        """List all child attempts in stable order."""

    @abstractmethod
    async def get_child(self, child_id: UUID) -> ChildExecution | None:
        """Fetch a child attempt."""

    @abstractmethod
    async def list_reconcilable(self, *, limit: int) -> list[ChildExecution]:
        """List launched child tasks needing remote reconciliation."""

    @abstractmethod
    async def list_parent_stop_pending(self, *, limit: int) -> list[DeveloperExecution]:
        """List owner-requested parent runtime stops that still need delivery."""

    @abstractmethod
    async def mark_parent_stopped(self, execution_id: UUID) -> None:
        """Record successful idempotent termination of the parent runtime."""

    @abstractmethod
    async def claim_launches(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_until: datetime,
    ) -> list[ChildExecution]:
        """Lease eligible launch intents with fencing generations."""

    @abstractmethod
    async def record_handle(
        self,
        child: ChildExecution,
        handle: ChildTaskHandle,
        *,
        worker_id: str,
        lease_token: UUID,
        fencing_generation: int,
    ) -> ChildExecution:
        """Persist an A2A handle if the launch lease is current."""

    @abstractmethod
    async def record_observation(
        self,
        child: ChildExecution,
        observation: ChildTaskObservation,
    ) -> ChildExecution:
        """Persist one idempotent remote task observation."""

    @abstractmethod
    async def record_evidence_report(self, child_id: UUID, report: dict) -> None:
        """Persist the trusted verifier decision and manifest for audit."""

    @abstractmethod
    async def seal_generation(self, execution_id: UUID, generation: int) -> None:
        """Seal the expected child set for the generation."""

    @abstractmethod
    async def join_status(self, execution_id: UUID, generation: int) -> JoinStatus:
        """Calculate the durable fan-in barrier."""

    @abstractmethod
    async def request_cancel(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> DeveloperExecution:
        """Atomically stop spawning and mark nonterminal children canceling."""

    @abstractmethod
    async def create_retry(
        self,
        child: ChildExecution,
        retry: ChildExecution,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> ChildExecution:
        """Supersede the current attempt and reserve a bounded retry atomically."""

    @abstractmethod
    async def update_execution_state(
        self,
        execution_id: UUID,
        *,
        state: str,
        suspension_reason: str,
    ) -> None:
        """Persist an orchestration state projection."""

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
    async def complete_execution(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
        expected_revision: int,
        merge_receipt: dict,
    ) -> DeveloperExecution:
        """Atomically persist verified publication after an unchanged successful join."""

    @abstractmethod
    async def record_integration_candidate(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
        expected_revision: int,
        allocation: dict,
        receipts: list[dict],
        candidate: dict,
    ) -> DeveloperExecution:
        """Persist a Forge-inspected integration candidate under optimistic locking."""

    @abstractmethod
    async def record_integration_review(
        self,
        execution_id: UUID,
        *,
        event_id: str,
        candidate_sha: str,
        candidate_tree: str,
        receipt: dict,
    ) -> DeveloperExecution:
        """Persist one idempotent review of the current inspected integration candidate."""

    @abstractmethod
    async def mark_message_delivered(self, message_id: UUID) -> None:
        """Record successful remote delivery of a coordinator follow-up."""


class ChildWorkflowGateway(ABC):
    """Narrow outbound child-task port; the implementation belongs to Ravn."""

    @abstractmethod
    async def launch_child(
        self, request: ChildLaunchRequest, *, auth_token: str = ""
    ) -> ChildTaskHandle:
        """Launch exactly one idempotent A2A child task."""

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
        """Reply to an INPUT_REQUIRED child through existing A2A messaging."""


class ParentWorkflowContinuation(ABC):
    """Resume the exact suspended parent session after a durable join."""

    @abstractmethod
    async def resume_parent(
        self,
        execution: DeveloperExecution,
        *,
        generation: int,
        results: list[dict],
    ) -> None:
        """Deliver an idempotently identified continuation to the parent session."""

    @abstractmethod
    async def notify_parent(
        self,
        execution: DeveloperExecution,
        *,
        event_type: str,
        generation: int,
        correlation_revision: int,
        children: list[dict],
    ) -> None:
        """Deliver a durable child-state notification to the exact parent session."""

    @abstractmethod
    async def notify_delivery_observation(
        self,
        execution: DeveloperExecution,
        wait: DeveloperDeliveryWait,
        observation: DeveloperDeliveryObservation,
    ) -> None:
        """Deliver one persisted Forge observation to the exact parent session."""

    @abstractmethod
    async def stop_parent(self, execution: DeveloperExecution) -> None:
        """Idempotently stop the exact parent runtime after owner cancellation."""

"""Ports for Ting-owned durable code delivery execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from uuid import UUID

from ting.delivery.domain import ChildExecution, DeliveryExecution
from ting.ports.workflow_execution import SessionContinuation, WorkflowExecutionRepository

if TYPE_CHECKING:
    from ting.domain.workflow_wait import (
        WaitObservation,
        WorkflowWait,
    )


class DeliveryExecutionRepository(
    WorkflowExecutionRepository[DeliveryExecution, ChildExecution],
    ABC,
):
    """Delivery-only ledger operations layered over the generic execution ledger."""

    @abstractmethod
    async def complete_execution(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
        expected_revision: int,
        merge_receipt: dict,
    ) -> DeliveryExecution:
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
    ) -> DeliveryExecution:
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
    ) -> DeliveryExecution:
        """Persist one idempotent review of the current inspected integration candidate."""


class ParentWorkflowContinuation(SessionContinuation[DeliveryExecution], ABC):
    """Resume the exact suspended parent session, including delivery observations."""

    @abstractmethod
    async def notify_delivery_observation(
        self,
        execution: DeliveryExecution,
        wait: WorkflowWait,
        observation: WaitObservation,
    ) -> None:
        """Deliver one persisted Forge observation to the exact parent session."""

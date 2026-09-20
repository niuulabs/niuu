"""Ports for durable Forge observation waits."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from ting.delivery.domain import DeliveryExecution
from ting.domain.workflow_wait import (
    WaitObservation,
    WorkflowWait,
    WorkflowWaitRequest,
)


class WorkflowWaitRepository(ABC):
    @abstractmethod
    async def reserve(
        self,
        execution: DeliveryExecution,
        request: WorkflowWaitRequest,
        *,
        wait_id: UUID,
        request_digest: str,
        candidate_digest: str,
        next_poll_at: datetime,
    ) -> WorkflowWait:
        """Reserve one exact idempotent wait, rejecting a different active wait."""

    @abstractmethod
    async def list_for_execution(self, execution_id: UUID) -> list[WorkflowWait]:
        """List the immutable wait history for one execution."""

    @abstractmethod
    async def claim_due(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[WorkflowWait]:
        """Lease due polls and terminal notifications with fencing tokens."""

    @abstractmethod
    async def record_pending(
        self,
        wait: WorkflowWait,
        observation: WaitObservation | None,
        *,
        next_poll_at: datetime,
        error: str = "",
    ) -> None:
        """Persist a nonterminal observation and release the current lease."""

    @abstractmethod
    async def record_terminal(
        self,
        wait: WorkflowWait,
        observation: WaitObservation,
        *,
        expected_execution_revision: int,
        project_execution: bool,
    ) -> WorkflowWait:
        """Persist terminal evidence before any continuation is delivered."""

    @abstractmethod
    async def mark_notified(self, wait: WorkflowWait) -> None:
        """Mark a terminal observation delivered under the current lease."""


class WaitConditionObserver(ABC):
    @abstractmethod
    async def observe(
        self,
        execution: DeliveryExecution,
        request: WorkflowWaitRequest,
    ) -> WaitObservation:
        """Read and classify one exact remote review without taking action."""

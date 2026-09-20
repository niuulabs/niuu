"""Ports for durable Forge observation waits."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from ting.domain.developer_delivery_wait import (
    DeveloperDeliveryObservation,
    DeveloperDeliveryWait,
    DeveloperDeliveryWaitRequest,
)
from ting.domain.developer_execution import DeveloperExecution


class DeveloperDeliveryWaitRepository(ABC):
    @abstractmethod
    async def reserve(
        self,
        execution: DeveloperExecution,
        request: DeveloperDeliveryWaitRequest,
        *,
        wait_id: UUID,
        request_digest: str,
        candidate_digest: str,
        next_poll_at: datetime,
    ) -> DeveloperDeliveryWait:
        """Reserve one exact idempotent wait, rejecting a different active wait."""

    @abstractmethod
    async def list_for_execution(self, execution_id: UUID) -> list[DeveloperDeliveryWait]:
        """List the immutable wait history for one execution."""

    @abstractmethod
    async def claim_due(
        self,
        *,
        worker_id: str,
        limit: int,
        lease_until: datetime,
        now: datetime,
    ) -> list[DeveloperDeliveryWait]:
        """Lease due polls and terminal notifications with fencing tokens."""

    @abstractmethod
    async def record_pending(
        self,
        wait: DeveloperDeliveryWait,
        observation: DeveloperDeliveryObservation | None,
        *,
        next_poll_at: datetime,
        error: str = "",
    ) -> None:
        """Persist a nonterminal observation and release the current lease."""

    @abstractmethod
    async def record_terminal(
        self,
        wait: DeveloperDeliveryWait,
        observation: DeveloperDeliveryObservation,
        *,
        expected_execution_revision: int,
        project_execution: bool,
    ) -> DeveloperDeliveryWait:
        """Persist terminal evidence before any continuation is delivered."""

    @abstractmethod
    async def mark_notified(self, wait: DeveloperDeliveryWait) -> None:
        """Mark a terminal observation delivered under the current lease."""


class DeveloperDeliveryObserver(ABC):
    @abstractmethod
    async def observe(
        self,
        execution: DeveloperExecution,
        request: DeveloperDeliveryWaitRequest,
    ) -> DeveloperDeliveryObservation:
        """Read and classify one exact remote review without taking action."""

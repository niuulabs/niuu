"""Persistence port for ticket coordinators."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from ting.domain.ticket_coordinator import TicketCoordinator


class TicketCoordinatorRepository(ABC):
    @abstractmethod
    async def get(self, saga_id: UUID, issue_id: str) -> TicketCoordinator | None:
        """Return the coordinator recorded for a ticket, active or closed."""

    @abstractmethod
    async def get_by_session(self, session_id: str) -> TicketCoordinator | None:
        """Return the active coordinator running in a session, if any."""

    @abstractmethod
    async def list_active_owner_ids(self) -> list[str]:
        """Return the owners with an active coordinator, whose sessions must be followed."""

    @abstractmethod
    async def list_for_saga(self, saga_id: UUID, *, owner_id: str) -> list[TicketCoordinator]:
        """Return the owner's coordinators for one project."""

    @abstractmethod
    async def save(self, coordinator: TicketCoordinator) -> TicketCoordinator:
        """Record the ticket's coordinator, replacing any earlier one."""

    @abstractmethod
    async def claim_advance(
        self,
        saga_id: UUID,
        issue_id: str,
        *,
        finished_campaign_id: str,
        workflow_id: str,
        requested_at: datetime,
    ) -> TicketCoordinator | None:
        """Atomically claim the one start a finished run gets; ``None`` if already claimed."""

    @abstractmethod
    async def close(self, saga_id: UUID, issue_id: str) -> None:
        """Mark a ticket's coordinator closed."""

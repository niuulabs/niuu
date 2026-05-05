"""Saga repository port — persistence for saga references only.

The tracker (Linear, etc.) is the source of truth for project structure
(milestones, issues, names, statuses). This repo stores only the link
between a tracker project and Tyr's execution context.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from tyr.domain.models import Phase, Raid, RaidStatus, Saga, SagaStatus


class SagaRepository(ABC):
    """Abstract persistence for saga references."""

    @abstractmethod
    async def save_saga(self, saga: Saga, *, conn: Any | None = None) -> None:
        """Persist a saga reference. Uses *conn* when inside a transaction."""
        ...

    @abstractmethod
    async def save_phase(self, phase: Phase, *, conn: Any | None = None) -> None:
        """Persist a phase (insert-or-update). Uses *conn* when inside a transaction."""
        ...

    @abstractmethod
    async def save_raid(self, raid: Raid, *, conn: Any | None = None) -> None:
        """Persist a raid. Uses *conn* when inside a transaction."""
        ...

    @abstractmethod
    async def list_sagas(self, *, owner_id: str | None = None) -> list[Saga]:
        """List all saga references, optionally filtered by owner."""
        ...

    @abstractmethod
    async def get_saga(self, saga_id: UUID, *, owner_id: str | None = None) -> Saga | None:
        """Get a saga by ID, optionally scoped to an owner."""
        ...

    @abstractmethod
    async def get_saga_by_slug(self, slug: str) -> Saga | None:
        """Get a saga by its slug. Returns None if no saga with that slug exists."""
        ...

    @abstractmethod
    async def delete_saga(self, saga_id: UUID, *, owner_id: str | None = None) -> bool:
        """Delete a saga, optionally scoped to an owner. Returns True if deleted."""
        ...

    @abstractmethod
    async def update_saga_status(self, saga_id: UUID, status: SagaStatus) -> None:
        """Update the status of a saga."""
        ...

    async def update_saga_workflow(
        self,
        saga_id: UUID,
        *,
        workflow_id: UUID | None,
        workflow_version: str | None,
        workflow_snapshot: dict[str, Any] | None,
        owner_id: str | None = None,
    ) -> None:
        """Update the workflow assignment and snapshot for a saga."""
        raise NotImplementedError(f"{type(self).__name__}.update_saga_workflow not implemented")

    @abstractmethod
    async def count_by_status(self) -> dict[str, int]:
        """Return a count of raids grouped by status.

        All RaidStatus values are always present in the result, with zero counts
        for statuses that have no raids.
        """
        ...

    async def get_phase(self, phase_id: UUID) -> Phase | None:
        """Get a single phase by ID. Returns None if not found.

        Subclasses should override this method.
        """
        raise NotImplementedError(f"{type(self).__name__}.get_phase not implemented")

    async def get_raid(self, raid_id: UUID) -> Raid | None:
        """Get a single raid by ID. Returns None if not found.

        Subclasses should override this method.  The default raises
        ``NotImplementedError`` so that callers fail clearly when the method
        is missing in test stubs that don't need it.
        """
        raise NotImplementedError(f"{type(self).__name__}.get_raid not implemented")

    async def get_raids_by_phase(self, phase_id: UUID) -> list[Raid]:
        """Return all raids belonging to *phase_id*, ordered by creation time.

        Subclasses should override this method.
        """
        raise NotImplementedError(f"{type(self).__name__}.get_raids_by_phase not implemented")

    async def get_phases_by_saga(self, saga_id: UUID) -> list[Phase]:
        """Return all phases belonging to *saga_id*, ordered by phase number.

        Subclasses should override this method.
        """
        raise NotImplementedError(f"{type(self).__name__}.get_phases_by_saga not implemented")

    async def update_raid_outcome(
        self,
        raid_id: UUID,
        outcome: dict[str, Any],
        event_type: str,
        status: RaidStatus,
    ) -> None:
        """Store a structured outcome on *raid_id* and transition its status.

        Subclasses should override this method.
        """
        raise NotImplementedError(f"{type(self).__name__}.update_raid_outcome not implemented")

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[Any]:
        """Yield a transactional connection.

        The default implementation yields ``None`` (no-op), suitable for
        in-memory or mock repositories. The Postgres adapter overrides this
        to acquire a real connection and start a transaction.
        """
        yield None

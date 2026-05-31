"""Dispatcher repository port — persistence for per-user dispatcher state."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ting.domain.models import DispatcherState


class DispatcherRepository(ABC):
    """Abstract persistence for dispatcher state."""

    @abstractmethod
    async def get_or_create(self, owner_id: str) -> DispatcherState:
        """Return the dispatcher state for *owner_id*, creating a default row if none exists."""
        ...

    @abstractmethod
    async def update(self, owner_id: str, **fields: object) -> DispatcherState:
        """Partial-update the dispatcher state for *owner_id*.

        Only the keys present in *fields* are written; the rest are untouched.
        Returns the full state after the update.
        """
        ...

    @abstractmethod
    async def list_active_owner_ids(self) -> list[str]:
        """Return owner_ids of all dispatchers with running=True."""
        ...

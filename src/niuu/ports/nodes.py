"""Port for registered Guild node persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.models import RegisteredNode


class NodeRepository(ABC):
    """Persistence port for machines joined to Guild."""

    @abstractmethod
    async def create(
        self,
        *,
        node_id: str,
        name: str,
        public_key: str,
        tenant_id: str,
        created_by: str,
    ) -> RegisteredNode:
        """Persist a newly joined node."""

    @abstractmethod
    async def get(self, node_id: str) -> RegisteredNode | None:
        """Look up a node by id."""

    @abstractmethod
    async def touch_heartbeat(self, node_id: str) -> RegisteredNode | None:
        """Update ``last_seen_at`` to now; return the refreshed node."""

    @abstractmethod
    async def record_request(self, node_id: str, *, timestamp: int) -> None:
        """Advance the strictly-increasing replay watermark for a node.

        Called once a signed request's signature has verified, so a captured
        request can never be replayed with the same or an earlier timestamp.
        """

    @abstractmethod
    async def delete(self, node_id: str) -> None:
        """Remove a node's registration (``niuu leave``)."""

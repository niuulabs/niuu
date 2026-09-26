"""Port for registered Guild node persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.models import RegisteredNode


class NodeRepository(ABC):
    """Persistence port for machines joined to Guild.

    Node creation happens only through ``GuildJoinRepository.consume_and_register``
    (one atomic transaction with pairing-code consumption and instance
    registration) — this port does not expose a standalone ``create``.
    """

    @abstractmethod
    async def get(self, node_id: str) -> RegisteredNode | None:
        """Look up a node by id."""

    @abstractmethod
    async def list_for_tenant(self, tenant_id: str) -> list[RegisteredNode]:
        """List nodes registered under *tenant_id*, for admin review/revocation."""

    @abstractmethod
    async def touch_heartbeat(self, node_id: str) -> RegisteredNode | None:
        """Update ``last_seen_at`` to now; return the refreshed node."""

    @abstractmethod
    async def try_advance_watermark(self, node_id: str, *, timestamp_ms: int) -> bool:
        """Atomically advance the replay-protection watermark.

        A single conditional ``UPDATE ... WHERE last_request_at IS NULL OR
        last_request_at < $timestamp_ms`` — the only correct way to do this:
        checking the current watermark and writing a new one as two separate
        steps has a race where two concurrent requests can both pass the
        check before either writes. Returns ``False`` when no row matched
        (unknown node, or a timestamp that does not strictly increase), in
        which case the caller must reject the request as a possible replay.
        """

    @abstractmethod
    async def delete(self, node_id: str) -> None:
        """Remove a node's registration. Its instances cascade at the database
        layer (``niuu_instances.node_id ... ON DELETE CASCADE``)."""

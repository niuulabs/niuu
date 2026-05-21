"""MeshPort — point-to-point and broadcast transport between Ravn peers (NIU-517).

Discovery (who peers are, realm verification, capability handshake) is handled
separately by DiscoveryPort (NIU-538). MeshPort is the transport layer: once
you know a peer exists and is trusted, this is how you talk to it.

``announce`` and ``discover`` are explicitly NOT on this port.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from ravn.domain.events import RavnEvent


class PeerNotFoundError(Exception):
    """Raised by ``send()`` when the target peer is not in the verified peer table.

    This is a trust error, not a transport error — the peer must be discovered
    and verified by DiscoveryPort before MeshPort will route to it.
    """

    def __init__(self, peer_id: str) -> None:
        super().__init__(f"Peer {peer_id!r} not found in verified peer table")
        self.peer_id = peer_id


@runtime_checkable
class MeshPort(Protocol):
    """Point-to-point and broadcast transport between Ravn peers.

    Implementations:
    - ``NngMeshAdapter``      — Pi mode, nng PUB/SUB + REQ/REP, no broker
    - ``SleipnirMeshAdapter`` — infra mode, RabbitMQ topic exchange + RPC
    - ``CompositeMeshAdapter``— tries infra first, falls back to nng
    """

    async def publish(self, event: RavnEvent, topic: str) -> None:
        """Broadcast an event to all subscribers of the given topic."""
        ...

    async def subscribe(
        self,
        topic: str,
        handler: Callable[[RavnEvent], Awaitable[None]],
    ) -> None:
        """Register an async handler for events on a topic."""
        ...

    async def unsubscribe(self, topic: str) -> None:
        """Deregister handler for a topic."""
        ...

    async def send(
        self,
        target_peer_id: str,
        message: dict,
        *,
        timeout_s: float = 10.0,
    ) -> dict:
        """Send a message directly to a specific peer and await its reply.

        This is the cascade delegation primitive. When Ting dispatches a run
        to a specific Ravn, it goes through this method. The reply contains
        the task receipt or an error.

        Raises
        ------
        TimeoutError
            If the peer does not reply within ``timeout_s``.
        PeerNotFoundError
            If ``target_peer_id`` is not in the verified peer table.
        """
        ...

    async def start(self) -> None:
        """Start the transport (open sockets, connect to broker)."""
        ...

    async def stop(self) -> None:
        """Graceful shutdown."""
        ...

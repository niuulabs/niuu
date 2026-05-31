"""DiscoveryPort — realm-scoped flock peer detection (NIU-538).

Discovery is orthogonal to transport (MeshPort).  This port covers:

- **Announcing** this Ravn's presence to the network
- **Scanning** for candidates and running the trust handshake
- **Maintaining** a live, verified peer table
- **Watching** for join/leave events

``peers()`` is intentionally synchronous because the mesh adapters (NngMeshAdapter,
SleipnirMeshAdapter) call it from synchronous helper methods during ``send()``
and ``_connect_sub_to_peers()``.  Implementations maintain an in-memory peer
table that is updated asynchronously by background tasks.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from ravn.domain.models import RavnCandidate, RavnIdentity, RavnPeer

PeerCallback = Callable[[RavnPeer], None]


@runtime_checkable
class DiscoveryPort(Protocol):
    """Realm-scoped flock peer detection and trust verification.

    Implementations
    ---------------
    - ``MdnsDiscoveryAdapter``      — Pi mode, mDNS + HMAC handshake (zeroconf)
    - ``SleipnirDiscoveryAdapter``  — infra mode, pub/sub + SPIFFE JWT validation
    - ``K8sDiscoveryAdapter``       — infra mode, pod label selector query
    - ``CompositeDiscoveryAdapter`` — merges multiple backends
    """

    async def start(self) -> None:
        """Start background tasks (announce, heartbeat, eviction)."""
        raise NotImplementedError

    async def stop(self) -> None:
        """Graceful shutdown — cancel background tasks, unregister services."""
        raise NotImplementedError

    async def announce(self) -> None:
        """(Re-)announce this Ravn's presence to the network."""
        raise NotImplementedError

    async def scan(self) -> list[RavnCandidate]:
        """Return unverified candidates visible on the network right now."""
        raise NotImplementedError

    async def watch(self, on_join: PeerCallback, on_leave: PeerCallback) -> None:
        """Register callbacks fired when verified peers join or leave the flock.

        Returns immediately — callbacks are fired from background tasks.
        Multiple callers may register multiple callbacks.
        """
        raise NotImplementedError

    async def handshake(self, candidate: RavnCandidate) -> RavnPeer | None:
        """Run the trust handshake with *candidate*.

        Returns a verified ``RavnPeer`` on success, ``None`` if the candidate
        is from a different realm (silently ignored — no error, DEBUG log only).
        """
        raise NotImplementedError

    def peers(self) -> dict[str, RavnPeer]:
        """Return the current verified peer table, keyed by ``peer_id``.

        Synchronous — returns the cached in-memory table.  Updated by
        background tasks as peers join, send heartbeats, and are evicted.
        """
        raise NotImplementedError

    async def own_identity(self) -> RavnIdentity:
        """Return this Ravn instance's own identity."""
        raise NotImplementedError

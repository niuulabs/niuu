"""CompositeDiscoveryAdapter — merges multiple discovery backends (NIU-538).

Runs all configured backends simultaneously.  The merged peer table is the
union of all verified peers from all backends.  Callbacks fire once per
unique peer_id (join fires on first-seen, leave when evicted from all backends).

Typical deployments:
- Pi + cluster: mDNS (for LAN Pi peers) + Sleipnir (for infra peers)
- Infra cold-start: K8s (initial pod listing) + Sleipnir (live pub/sub)
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from ravn.domain.models import RavnCandidate, RavnIdentity, RavnPeer
from ravn.ports.discovery import PeerCallback

logger = logging.getLogger(__name__)


class CompositeDiscoveryAdapter:
    """Merges peer tables from multiple discovery backends.

    Parameters
    ----------
    backends:
        List of backend adapters.  Any object satisfying the DiscoveryPort
        interface is accepted.
    """

    def __init__(self, backends: list[object]) -> None:
        self._backends = backends
        self._on_join: list[PeerCallback] = []
        self._on_leave: list[PeerCallback] = []
        # Count of backends that have seen a given peer (for leave eviction)
        self._peer_backend_count: dict[str, int] = {}

    # ------------------------------------------------------------------
    # DiscoveryPort interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all backends in parallel and wire join/leave callbacks."""
        for backend in self._backends:
            await backend.watch(  # type: ignore[attr-defined]
                on_join=self._make_join_callback(backend),
                on_leave=self._make_leave_callback(backend),
            )
        await asyncio.gather(
            *(backend.start() for backend in self._backends),  # type: ignore[attr-defined]
            return_exceptions=True,
        )
        logger.info("composite_discovery: started %d backends", len(self._backends))

    async def stop(self) -> None:
        """Stop all backends in parallel."""
        await asyncio.gather(
            *(backend.stop() for backend in self._backends),  # type: ignore[attr-defined]
            return_exceptions=True,
        )
        logger.info("composite_discovery: stopped")

    async def announce(self) -> None:
        """Re-announce via all backends."""
        await asyncio.gather(
            *(backend.announce() for backend in self._backends),  # type: ignore[attr-defined]
            return_exceptions=True,
        )

    async def scan(self) -> list[RavnCandidate]:
        """Return all candidates from all backends (deduplicated by peer_id)."""
        results = await asyncio.gather(
            *(backend.scan() for backend in self._backends),  # type: ignore[attr-defined]
            return_exceptions=True,
        )
        seen: dict[str, RavnCandidate] = {}
        for result in results:
            if isinstance(result, list):
                for candidate in result:
                    seen.setdefault(candidate.peer_id, candidate)
        return list(seen.values())

    async def watch(self, on_join: PeerCallback, on_leave: PeerCallback) -> None:
        """Register callbacks on the composite adapter (non-blocking)."""
        self._on_join.append(on_join)
        self._on_leave.append(on_leave)

    async def handshake(self, candidate: RavnCandidate) -> RavnPeer | None:
        """Delegate handshake to the first backend that succeeds."""
        for backend in self._backends:
            with suppress(Exception):
                peer = await backend.handshake(candidate)  # type: ignore[attr-defined]
                if peer is not None:
                    return peer
        return None

    def peers(self) -> dict[str, RavnPeer]:
        """Return the merged verified peer table from all backends."""
        merged: dict[str, RavnPeer] = {}
        for backend in self._backends:
            with suppress(Exception):
                merged.update(backend.peers())  # type: ignore[attr-defined]
        return merged

    async def own_identity(self) -> RavnIdentity:
        """Return this Ravn's identity from the first backend."""
        for backend in self._backends:
            try:
                return await backend.own_identity()  # type: ignore[attr-defined]
            except Exception:
                pass
        raise RuntimeError("composite_discovery: no backend could return own_identity")

    # ------------------------------------------------------------------
    # Internal — callback wiring
    # ------------------------------------------------------------------

    def _make_join_callback(self, backend: object) -> PeerCallback:
        def on_join(peer: RavnPeer) -> None:
            count = self._peer_backend_count.get(peer.peer_id, 0)
            self._peer_backend_count[peer.peer_id] = count + 1
            if count == 0:
                # First backend to see this peer — propagate
                for cb in self._on_join:
                    with suppress(Exception):
                        cb(peer)

        return on_join

    def _make_leave_callback(self, backend: object) -> PeerCallback:
        def on_leave(peer: RavnPeer) -> None:
            count = self._peer_backend_count.get(peer.peer_id, 0)
            if count <= 1:
                self._peer_backend_count.pop(peer.peer_id, None)
                # Last backend dropped this peer — propagate leave
                for cb in self._on_leave:
                    with suppress(Exception):
                        cb(peer)
            else:
                self._peer_backend_count[peer.peer_id] = count - 1

        return on_leave

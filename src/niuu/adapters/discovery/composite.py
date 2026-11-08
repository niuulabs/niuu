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
from typing import Any

from niuu.adapters.discovery._failures import DiscoveryError, notify, report_failure
from niuu.mesh.identity import MeshCandidate, MeshIdentity, MeshPeer
from niuu.ports.discovery import PeerCallback

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
        """Start all backends in parallel and wire join/leave callbacks.

        Every backend is one the operator configured, so one that cannot start is fatal:
        the backends that did start are stopped again and the failures of all of them are
        raised together.
        """
        for backend in self._backends:
            await backend.watch(  # type: ignore[attr-defined]
                on_join=self._make_join_callback(backend),
                on_leave=self._make_leave_callback(backend),
            )
        results = await asyncio.gather(
            *(backend.start() for backend in self._backends),  # type: ignore[attr-defined]
            return_exceptions=True,
        )
        failures = self._failures(results)
        if failures:
            started = [b for b, r in zip(self._backends, results, strict=True) if r is None]
            await asyncio.gather(
                *(b.stop() for b in started),  # type: ignore[attr-defined]
                return_exceptions=True,
            )
            raise DiscoveryError(
                "discovery could not start: " + "; ".join(failures) + ". Fix the failing "
                "adapters or remove them from the discovery adapters"
            )
        logger.info("composite_discovery: started %d backends", len(self._backends))

    async def stop(self) -> None:
        """Stop all backends in parallel; a backend that fails to stop is reported."""
        results = await asyncio.gather(
            *(backend.stop() for backend in self._backends),  # type: ignore[attr-defined]
            return_exceptions=True,
        )
        for failure in self._failures(results):
            report_failure("composite", "stop", failure)
        logger.info("composite_discovery: stopped")

    async def announce(self) -> None:
        """Re-announce via all backends; a backend that cannot announce fails the call."""
        results = await asyncio.gather(
            *(backend.announce() for backend in self._backends),  # type: ignore[attr-defined]
            return_exceptions=True,
        )
        failures = self._failures(results)
        if failures:
            raise DiscoveryError("discovery could not announce: " + "; ".join(failures))

    async def scan(self) -> list[MeshCandidate]:
        """Return all candidates from all backends (deduplicated by peer_id).

        A backend whose scan failed is reported; the others' candidates are still returned,
        which is why the failure must not be quiet: the list is then not the whole picture.
        """
        results = await asyncio.gather(
            *(backend.scan() for backend in self._backends),  # type: ignore[attr-defined]
            return_exceptions=True,
        )
        for failure in self._failures(results):
            report_failure("composite", "scan", failure)
        seen: dict[str, MeshCandidate] = {}
        for result in results:
            if isinstance(result, list):
                for candidate in result:
                    seen.setdefault(candidate.peer_id, candidate)
        return list(seen.values())

    def _failures(self, results: list[Any]) -> list[str]:
        """A line per backend whose result is an exception."""
        return [
            f"{type(backend).__name__}: {type(result).__name__}: {result}"
            for backend, result in zip(self._backends, results, strict=True)
            if isinstance(result, BaseException)
        ]

    async def watch(self, on_join: PeerCallback, on_leave: PeerCallback) -> None:
        """Register callbacks on the composite adapter (non-blocking)."""
        self._on_join.append(on_join)
        self._on_leave.append(on_leave)

    async def handshake(self, candidate: MeshCandidate) -> MeshPeer | None:
        """Delegate handshake to the first backend that succeeds."""
        for backend in self._backends:
            try:
                peer = await backend.handshake(candidate)  # type: ignore[attr-defined]
            except Exception as exc:  # another backend may still verify it; this one is reported
                report_failure(
                    "composite",
                    "handshake",
                    f"{type(backend).__name__}: {type(exc).__name__}: {exc}",
                )
                continue
            if peer is not None:
                return peer
        return None

    def peers(self) -> dict[str, MeshPeer]:
        """Return the merged verified peer table from all backends."""
        merged: dict[str, MeshPeer] = {}
        for backend in self._backends:
            merged.update(backend.peers())  # type: ignore[attr-defined]
        return merged

    async def own_identity(self) -> MeshIdentity:
        """Return this Ravn's identity from the first backend."""
        if not self._backends:
            raise DiscoveryError("composite discovery has no backend, so it has no identity")
        return await self._backends[0].own_identity()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Internal — callback wiring
    # ------------------------------------------------------------------

    def _make_join_callback(self, backend: object) -> PeerCallback:
        def on_join(peer: MeshPeer) -> None:
            count = self._peer_backend_count.get(peer.peer_id, 0)
            self._peer_backend_count[peer.peer_id] = count + 1
            if count == 0:
                # First backend to see this peer — propagate
                notify("composite", "join", self._on_join, peer)

        return on_join

    def _make_leave_callback(self, backend: object) -> PeerCallback:
        def on_leave(peer: MeshPeer) -> None:
            count = self._peer_backend_count.get(peer.peer_id, 0)
            if count <= 1:
                self._peer_backend_count.pop(peer.peer_id, None)
                # Last backend dropped this peer — propagate leave
                notify("composite", "leave", self._on_leave, peer)
            else:
                self._peer_backend_count[peer.peer_id] = count - 1

        return on_leave

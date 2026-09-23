"""Shared mesh participant base (NIU-631).

``MeshParticipant`` encapsulates the lifecycle shared by all mesh-capable
services (Ravn, Skuld, future agents):

- mesh adapter start / stop
- discovery adapter start / stop
- event publishing via the mesh

Both Ravn and Skuld compose a ``MeshParticipant`` to gain flock membership
without duplicating the wiring logic.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("niuu.mesh.participant")


class MeshParticipant:
    """Lifecycle wrapper for a single flock member.

    Parameters
    ----------
    mesh:
        A ``MeshPort`` implementation (or ``None`` to disable mesh).
    discovery:
        Optional ``DiscoveryPort`` implementation for peer discovery.
    peer_id:
        This participant's identity string.  Used for logging only — the mesh
        adapter carries the authoritative peer ID.
    """

    def __init__(
        self,
        mesh: Any | None,
        discovery: Any | None = None,
        peer_id: str = "",
    ) -> None:
        self._mesh = mesh
        self._discovery = discovery
        self._peer_id = peer_id
        self._running = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mesh(self) -> Any | None:
        """The underlying mesh adapter."""
        return self._mesh

    @property
    def discovery(self) -> Any | None:
        """The underlying discovery adapter."""
        return self._discovery

    @property
    def peer_id(self) -> str:
        return self._peer_id

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start discovery, then the mesh.

        Raises whatever failed to start. When the mesh fails, the discovery
        adapter that already started is stopped again, so a participant never
        announces itself to peers while its mesh is down.
        """
        if self._running:
            return

        if self._discovery is not None:
            await self._discovery.start()
            logger.debug("participant(%s): discovery started", self._peer_id)

        if self._mesh is not None:
            try:
                await self._mesh.start()
            except Exception:
                if self._discovery is not None:
                    await self._discovery.stop()
                raise
            logger.debug("participant(%s): mesh started", self._peer_id)

        self._running = True

    async def stop(self) -> None:
        """Stop the mesh, then discovery; raise if either failed to stop.

        Both are always attempted, so a mesh that fails to stop does not leave
        discovery announcing a departed participant.
        """
        if not self._running:
            return

        self._running = False
        failures: list[Exception] = []

        if self._mesh is not None:
            try:
                await self._mesh.stop()
                logger.debug("participant(%s): mesh stopped", self._peer_id)
            except Exception as exc:
                failures.append(exc)

        if self._discovery is not None:
            try:
                await self._discovery.stop()
                logger.debug("participant(%s): discovery stopped", self._peer_id)
            except Exception as exc:
                failures.append(exc)

        if failures:
            raise ExceptionGroup(f"participant({self._peer_id}): stop failed", failures)

    # ------------------------------------------------------------------
    # Mesh operations
    # ------------------------------------------------------------------

    async def publish(self, event: Any, topic: str) -> None:
        """Publish an event to the mesh.

        No-op when no mesh adapter is configured.
        """
        if self._mesh is None:
            return
        await self._mesh.publish(event, topic=topic)

    async def subscribe(self, topic: str, handler: Any) -> None:
        """Subscribe to a topic on the mesh.

        No-op when no mesh adapter is configured.
        """
        if self._mesh is None:
            return
        await self._mesh.subscribe(topic, handler)

    async def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from a topic on the mesh.

        No-op when no mesh adapter is configured.
        """
        if self._mesh is None:
            return
        await self._mesh.unsubscribe(topic)

    def set_rpc_handler(self, handler: Any) -> None:
        """Register an RPC request handler on the mesh.

        No-op when no mesh adapter is configured.
        """
        if self._mesh is None:
            return
        self._mesh.set_rpc_handler(handler)

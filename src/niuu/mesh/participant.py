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
        """Start mesh and discovery adapters."""
        if self._running:
            return

        if self._discovery is not None:
            try:
                await self._discovery.start()
                logger.debug("participant(%s): discovery started", self._peer_id)
            except Exception as exc:
                logger.error(
                    "participant(%s): discovery start failed: %r",
                    self._peer_id,
                    exc,
                    exc_info=True,
                )

        if self._mesh is not None:
            try:
                await self._mesh.start()
                logger.debug("participant(%s): mesh started", self._peer_id)
            except Exception as exc:
                logger.error(
                    "participant(%s): mesh start failed: %r",
                    self._peer_id,
                    exc,
                    exc_info=True,
                )

        self._running = True

    async def stop(self) -> None:
        """Stop mesh and discovery adapters."""
        if not self._running:
            return

        if self._mesh is not None:
            try:
                await self._mesh.stop()
                logger.debug("participant(%s): mesh stopped", self._peer_id)
            except Exception as exc:
                logger.warning("participant(%s): mesh stop error: %r", self._peer_id, exc)

        if self._discovery is not None:
            try:
                await self._discovery.stop()
                logger.debug("participant(%s): discovery stopped", self._peer_id)
            except Exception as exc:
                logger.warning("participant(%s): discovery stop error: %r", self._peer_id, exc)

        self._running = False

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

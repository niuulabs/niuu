"""CompositeMeshAdapter — all-active multi-transport mesh.

Runs ALL configured mesh transports simultaneously. This is NOT a failover
chain — events are fanned out to all transports on publish, and the first
successful response wins on send.

**Pub/sub**: ``publish()`` broadcasts to ALL transports (fire-and-forget).
``subscribe()`` registers the handler on ALL transports, so events arrive
regardless of which transport the sender used.

**RPC (send)**: Tries each transport in order until one succeeds. The order
is determined by the config list order.

**Why all-active?**: In mixed environments (some peers reachable via nng,
others via webhook), you don't know which transport will reach which peer.
By running all transports, you maximize connectivity without manual routing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ravn.domain.events import RavnEvent
from ravn.ports.mesh import PeerNotFoundError

logger = logging.getLogger(__name__)


class CompositeMeshAdapter:
    """All-active multi-transport mesh adapter.

    Runs all configured transports simultaneously. Publish fans out to all,
    subscribe registers on all, send tries each until success.

    Parameters
    ----------
    transports:
        List of mesh adapters (must implement MeshPort protocol).
    own_peer_id:
        This Ravn's unique peer identifier.
    **kwargs:
        Ignored — allows forward compatibility with new config fields.
    """

    def __init__(
        self,
        transports: list[Any],
        own_peer_id: str = "",
        **kwargs: Any,  # noqa: ARG002 — ignored
    ) -> None:
        self._transports = transports
        self._own_peer_id = own_peer_id
        self._rpc_handler: Callable[[dict], Awaitable[dict]] | None = None

    # ------------------------------------------------------------------
    # MeshPort interface
    # ------------------------------------------------------------------

    async def publish(self, event: RavnEvent, topic: str) -> None:
        """Broadcast event to all transports (fan-out)."""
        if not self._transports:
            return

        # Fan out to all transports concurrently
        tasks = [self._safe_publish(transport, event, topic) for transport in self._transports]
        await asyncio.gather(*tasks)

    async def subscribe(
        self,
        topic: str,
        handler: Callable[[RavnEvent], Awaitable[None]],
    ) -> None:
        """Register handler on ALL transports."""
        for transport in self._transports:
            try:
                await transport.subscribe(topic, handler)
            except Exception as exc:
                logger.debug(
                    "composite_mesh: subscribe failed on %s: %s",
                    type(transport).__name__,
                    exc,
                )

    async def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from ALL transports."""
        for transport in self._transports:
            try:
                await transport.unsubscribe(topic)
            except Exception as exc:
                logger.debug(
                    "composite_mesh: unsubscribe failed on %s: %s",
                    type(transport).__name__,
                    exc,
                )

    async def send(
        self,
        target_peer_id: str,
        message: dict,
        *,
        timeout_s: float | None = None,
    ) -> dict:
        """Send message to peer, trying each transport until success.

        Transports are tried in config order. The first successful response
        is returned. If all transports fail, raises the last exception.
        """
        if not self._transports:
            raise PeerNotFoundError(target_peer_id)

        last_error: Exception | None = None

        for transport in self._transports:
            try:
                return await transport.send(target_peer_id, message, timeout_s=timeout_s)
            except PeerNotFoundError:
                # Peer not in this transport's discovery — try next
                last_error = PeerNotFoundError(target_peer_id)
                continue
            except TimeoutError as exc:
                # Timeout on this transport — try next
                last_error = exc
                logger.debug(
                    "composite_mesh: send timeout on %s for peer %s",
                    type(transport).__name__,
                    target_peer_id,
                )
                continue
            except Exception as exc:
                # Other error — log and try next
                last_error = exc
                logger.debug(
                    "composite_mesh: send failed on %s: %s",
                    type(transport).__name__,
                    exc,
                )
                continue

        # All transports failed
        if last_error is not None:
            raise last_error
        raise PeerNotFoundError(target_peer_id)

    async def start(self) -> None:
        """Start all transports."""
        for transport in self._transports:
            try:
                await transport.start()
            except Exception as exc:
                logger.warning(
                    "composite_mesh: start failed on %s: %s",
                    type(transport).__name__,
                    exc,
                )

        # Propagate RPC handler to all transports
        if self._rpc_handler is not None:
            self._set_rpc_handler_on_all(self._rpc_handler)

        transport_names = [type(t).__name__ for t in self._transports]
        logger.info(
            "composite_mesh: started peer=%s transports=%s",
            self._own_peer_id,
            transport_names,
        )

    async def stop(self) -> None:
        """Stop all transports."""
        for transport in self._transports:
            try:
                await transport.stop()
            except Exception as exc:
                logger.debug(
                    "composite_mesh: stop failed on %s: %s",
                    type(transport).__name__,
                    exc,
                )

        logger.info("composite_mesh: stopped peer=%s", self._own_peer_id)

    def set_rpc_handler(self, handler: Callable[[dict], Awaitable[dict]]) -> None:
        """Register RPC handler on all transports."""
        self._rpc_handler = handler
        self._set_rpc_handler_on_all(handler)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _safe_publish(
        self,
        transport: Any,
        event: RavnEvent,
        topic: str,
    ) -> None:
        """Publish to a transport, catching exceptions."""
        try:
            await transport.publish(event, topic)
        except Exception as exc:
            logger.debug(
                "composite_mesh: publish failed on %s: %s",
                type(transport).__name__,
                exc,
            )

    def _set_rpc_handler_on_all(
        self,
        handler: Callable[[dict], Awaitable[dict]],
    ) -> None:
        """Set RPC handler on all transports that support it."""
        for transport in self._transports:
            if hasattr(transport, "set_rpc_handler"):
                try:
                    transport.set_rpc_handler(handler)
                except Exception as exc:
                    logger.debug(
                        "composite_mesh: set_rpc_handler failed on %s: %s",
                        type(transport).__name__,
                        exc,
                    )

    @property
    def transports(self) -> list[Any]:
        """Return list of active transports (for introspection)."""
        return list(self._transports)

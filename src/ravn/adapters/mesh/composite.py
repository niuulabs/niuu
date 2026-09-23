"""CompositeMeshAdapter — all-active multi-transport mesh.

Runs ALL configured mesh transports simultaneously. This is NOT a failover
chain: every configured transport is expected to work, and a failure on one
transport is raised, never absorbed by the others.

**Pub/sub**: ``publish()`` broadcasts to ALL transports concurrently.
``subscribe()`` registers the handler on ALL transports, so events arrive
regardless of which transport the sender used.

**RPC (send)**: a request goes to the first transport, in config order, that
knows the target peer. ``PeerNotFoundError`` from a transport is a routing
answer ("that peer is not on this transport"), so the next transport is
asked. Any other failure (a timeout, a transport fault) is raised as-is: the
request may already have reached the peer, and mesh RPCs such as
``task_dispatch`` and ``work_request`` are not idempotent, so re-sending it
over another transport would run the work twice. A transport without a peer
table (for example a Sleipnir adapter built without discovery) never raises
``PeerNotFoundError`` and therefore claims every peer that reaches it.

**Failures**: ``publish``, ``subscribe``, ``unsubscribe`` and ``stop`` attempt
every transport, then raise an ``ExceptionGroup`` holding one exception per
failed transport, each annotated with that transport's name. ``start`` stops
at the first transport that fails, stops the transports it already started,
and raises that failure.

**Why all-active?**: In mixed environments (some peers reachable via nng,
others via webhook), you don't know which transport will reach which peer.
By running all transports, you maximize connectivity without manual routing.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from ravn.domain.events import RavnEvent
from ravn.ports.mesh import PeerNotFoundError

logger = logging.getLogger(__name__)


class CompositeMeshAdapter:
    """All-active multi-transport mesh adapter.

    Runs all configured transports simultaneously. Publish fans out to all,
    subscribe registers on all, send routes to the first transport that knows
    the peer. A failure on any transport is raised.

    Parameters
    ----------
    transports:
        List of mesh adapters (must implement MeshPort protocol). Must not be
        empty.
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
        if not transports:
            raise ValueError(
                "CompositeMeshAdapter needs at least one transport; "
                "configure mesh.adapters or disable the mesh"
            )
        self._transports = transports
        self._own_peer_id = own_peer_id
        self._rpc_handler: Callable[[dict], Awaitable[dict]] | None = None

    # ------------------------------------------------------------------
    # MeshPort interface
    # ------------------------------------------------------------------

    async def publish(self, event: RavnEvent, topic: str) -> None:
        """Broadcast *event* on every transport concurrently.

        Raises an ``ExceptionGroup`` when any transport failed, even if others
        delivered the event: a partial broadcast is not a published event.
        """
        results = await asyncio.gather(
            *(transport.publish(event, topic) for transport in self._transports),
            return_exceptions=True,
        )
        failures: list[Exception] = []
        for transport, result in zip(self._transports, results, strict=True):
            if isinstance(result, Exception):
                failures.append(_attributed(result, transport))
                continue
            if isinstance(result, BaseException):
                raise result
        self._raise_failures(f"publish to topic {topic!r}", failures)

    async def subscribe(
        self,
        topic: str,
        handler: Callable[[RavnEvent], Awaitable[None]],
    ) -> None:
        """Register *handler* on every transport."""
        await self._on_every_transport(
            f"subscribe to topic {topic!r}",
            lambda transport: transport.subscribe(topic, handler),
        )

    async def unsubscribe(self, topic: str) -> None:
        """Unsubscribe *topic* on every transport."""
        await self._on_every_transport(
            f"unsubscribe from topic {topic!r}",
            lambda transport: transport.unsubscribe(topic),
        )

    async def send(
        self,
        target_peer_id: str,
        message: dict,
        *,
        timeout_s: float | None = None,
    ) -> dict:
        """Send *message* over the first transport, in config order, that knows the peer.

        Raises
        ------
        PeerNotFoundError
            No configured transport knows *target_peer_id*.
        TimeoutError
            The transport that knows the peer got no reply in time. The
            request is not re-sent over another transport.
        Exception
            Whatever the routed transport raised.
        """
        for transport in self._transports:
            try:
                return await transport.send(target_peer_id, message, timeout_s=timeout_s)
            except PeerNotFoundError:
                continue
        raise PeerNotFoundError(target_peer_id)

    async def start(self) -> None:
        """Start every transport, in config order.

        When a transport fails to start, the transports already started are
        stopped again and the failure is raised: the composite never runs on a
        subset of its configured transports.
        """
        started: list[Any] = []
        for transport in self._transports:
            try:
                await transport.start()
            except Exception as exc:
                _attributed(exc, transport)
                await self._stop_each(reversed(started), "rollback stop")
                raise
            started.append(transport)

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
        """Stop every transport, then raise if any of them failed to stop."""
        await self._stop_each(self._transports, "stop")
        logger.info("composite_mesh: stopped peer=%s", self._own_peer_id)

    def set_rpc_handler(self, handler: Callable[[dict], Awaitable[dict]]) -> None:
        """Register RPC handler on all transports."""
        self._rpc_handler = handler
        self._set_rpc_handler_on_all(handler)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _on_every_transport(
        self,
        operation: str,
        call: Callable[[Any], Awaitable[None]],
    ) -> None:
        """Run *call* on every transport in config order; raise if any failed."""
        failures: list[Exception] = []
        for transport in self._transports:
            try:
                await call(transport)
            except Exception as exc:
                failures.append(_attributed(exc, transport))
        self._raise_failures(operation, failures)

    async def _stop_each(self, transports: Iterable[Any], operation: str) -> None:
        failures: list[Exception] = []
        for transport in transports:
            try:
                await transport.stop()
            except Exception as exc:
                failures.append(_attributed(exc, transport))
        self._raise_failures(operation, failures)

    def _raise_failures(self, operation: str, failures: list[Exception]) -> None:
        if not failures:
            return
        raise ExceptionGroup(
            f"composite_mesh: {operation} failed on {len(failures)} of "
            f"{len(self._transports)} transports (peer={self._own_peer_id})",
            failures,
        )

    def _set_rpc_handler_on_all(
        self,
        handler: Callable[[dict], Awaitable[dict]],
    ) -> None:
        """Set RPC handler on all transports that support it."""
        for transport in self._transports:
            if hasattr(transport, "set_rpc_handler"):
                transport.set_rpc_handler(handler)

    @property
    def transports(self) -> list[Any]:
        """Return list of active transports (for introspection)."""
        return list(self._transports)


def _attributed(exc: Exception, transport: Any) -> Exception:
    """Name the transport an exception came from, so a grouped failure is traceable."""
    exc.add_note(f"mesh transport: {type(transport).__name__}")
    return exc

"""SleipnirMeshAdapter — transport-agnostic mesh via Sleipnir event bus.

Uses Sleipnir's publisher/subscriber ports, so the underlying transport
(nng, RabbitMQ, NATS, Redis) is determined by Sleipnir config, not here.

**Pub/sub** — uses SleipnirPublisher/SleipnirSubscriber directly.

**RPC (send)** — implemented as a pattern on top of pub/sub:
1. Publishes request to ``ravn.mesh.rpc.<target_peer_id>``
2. Subscribes to ``ravn.mesh.rpc.reply.<own_peer_id>.<nonce>``
3. Awaits reply with matching correlation_id
4. Unsubscribes from reply topic

This works regardless of underlying transport.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from ravn.domain.events import RavnEvent, RavnEventType
from ravn.ports.mesh import PeerNotFoundError
from sleipnir.ports.events import SleipnirPublisher, SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)

# Event type prefixes for mesh communication
_MESH_EVENT_PREFIX = "ravn.mesh"
_RPC_REQUEST_PREFIX = "ravn.mesh.rpc"
_RPC_REPLY_PREFIX = "ravn.mesh.rpc.reply"


def _sanitize_for_event_type(s: str) -> str:
    """Sanitize a string for use in Sleipnir event types.

    Sleipnir event type segments require lowercase alphanumeric + underscores only.
    Converts hyphens to underscores inside a segment.
    """
    return s.lower().replace("-", "_")


def _sanitize_topic(topic: str) -> str:
    """Sanitize a dotted topic while preserving segment boundaries."""
    return ".".join(_sanitize_for_event_type(segment) for segment in topic.split("."))


def _ravn_to_sleipnir(event: RavnEvent, topic: str, source_peer_id: str) -> dict:
    """Convert RavnEvent to SleipnirEvent dict for publishing."""
    from sleipnir.domain.events import SleipnirEvent

    # Use topic as part of the event type
    event_type = f"{_MESH_EVENT_PREFIX}.{_sanitize_topic(topic)}"

    return SleipnirEvent(
        event_type=event_type,
        source=f"ravn:{source_peer_id}",
        payload={
            "ravn_event": event.payload,
            "ravn_type": str(event.type),
            "ravn_source": event.source,
            "ravn_urgency": event.urgency,
            "ravn_session_id": event.session_id,
            "ravn_task_id": event.task_id,
            "ravn_root_correlation_id": event.root_correlation_id,
        },
        summary=f"Mesh event: {topic}",
        urgency=event.urgency,
        domain="code",
        timestamp=event.timestamp,
        correlation_id=event.correlation_id,
    )


def _sleipnir_to_ravn(sleipnir_event: Any) -> RavnEvent:
    """Convert SleipnirEvent back to RavnEvent."""
    payload = sleipnir_event.payload
    ravn_type_str = payload.get("ravn_type", "response")

    # Map string back to RavnEventType
    try:
        ravn_type = RavnEventType(ravn_type_str)
    except ValueError:
        ravn_type = RavnEventType.RESPONSE

    return RavnEvent(
        type=ravn_type,
        source=payload.get("ravn_source", sleipnir_event.source),
        payload=payload.get("ravn_event", {}),
        timestamp=sleipnir_event.timestamp,
        urgency=payload.get("ravn_urgency", sleipnir_event.urgency),
        correlation_id=sleipnir_event.correlation_id or "",
        session_id=payload.get("ravn_session_id", ""),
        task_id=payload.get("ravn_task_id"),
        root_correlation_id=payload.get("ravn_root_correlation_id", ""),
    )


class SleipnirMeshAdapter:
    """Transport-agnostic mesh adapter using Sleipnir event bus.

    The underlying transport (nng, RabbitMQ, NATS, Redis) is determined
    by how the SleipnirPublisher/SleipnirSubscriber are configured.

    Parameters
    ----------
    publisher:
        Sleipnir publisher port for sending events.
    subscriber:
        Sleipnir subscriber port for receiving events.
    own_peer_id:
        This Ravn's unique peer identifier.
    discovery:
        Injected DiscoveryPort for peer verification.
    rpc_timeout_s:
        Default timeout for RPC calls.
    """

    def __init__(
        self,
        publisher: SleipnirPublisher,
        subscriber: SleipnirSubscriber,
        own_peer_id: str,
        discovery: object | None = None,
        rpc_timeout_s: float = 10.0,
    ) -> None:
        self._publisher = publisher
        self._subscriber = subscriber
        self._own_peer_id = own_peer_id
        self._discovery = discovery
        self._rpc_timeout_s = rpc_timeout_s

        self._subscriptions: dict[str, Subscription] = {}
        self._rpc_handler: Callable[[dict], Awaitable[dict]] | None = None

        # Pending RPC responses: correlation_id -> (event, response_dict)
        self._pending_rpc: dict[str, asyncio.Future[dict]] = {}
        self._rpc_subscription: Subscription | None = None

    # ------------------------------------------------------------------
    # MeshPort interface
    # ------------------------------------------------------------------

    async def publish(self, event: RavnEvent, topic: str) -> None:
        """Broadcast *event* to all subscribers of *topic*."""
        sleipnir_event = _ravn_to_sleipnir(event, topic, self._own_peer_id)
        try:
            await self._publisher.publish(sleipnir_event)
        except Exception as exc:
            logger.debug("sleipnir_mesh: publish failed: %s", exc)

    async def subscribe(
        self,
        topic: str,
        handler: Callable[[RavnEvent], Awaitable[None]],
    ) -> None:
        """Register *handler* for events on *topic*."""
        event_type_pattern = f"{_MESH_EVENT_PREFIX}.{_sanitize_topic(topic)}"

        async def _wrapped_handler(sleipnir_event: Any) -> None:
            try:
                ravn_event = _sleipnir_to_ravn(sleipnir_event)
                await handler(ravn_event)
            except Exception as exc:
                logger.warning("sleipnir_mesh: handler for %r raised: %s", topic, exc)

        subscription = await self._subscriber.subscribe([event_type_pattern], _wrapped_handler)
        self._subscriptions[topic] = subscription

    async def unsubscribe(self, topic: str) -> None:
        """Remove subscription for *topic*."""
        subscription = self._subscriptions.pop(topic, None)
        if subscription is not None:
            await subscription.unsubscribe()

    async def send(
        self,
        target_peer_id: str,
        message: dict,
        *,
        timeout_s: float | None = None,
    ) -> dict:
        """Send *message* to *target_peer_id* and await reply.

        Implements RPC as a pattern on top of pub/sub:
        1. Subscribe to reply topic with unique nonce
        2. Publish request with correlation_id
        3. Wait for reply
        4. Unsubscribe and return
        """
        self._assert_peer_trusted(target_peer_id)
        timeout = timeout_s if timeout_s is not None else self._rpc_timeout_s

        # Sleipnir requires event type segments to start with a letter
        nonce = "n" + uuid.uuid4().hex[:8]
        safe_own_id = _sanitize_for_event_type(self._own_peer_id)
        safe_target_id = _sanitize_for_event_type(target_peer_id)
        correlation_id = f"{self._own_peer_id}.{nonce}"
        reply_topic = f"{_RPC_REPLY_PREFIX}.{safe_own_id}.{nonce}"

        # Create future for the response
        response_future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending_rpc[correlation_id] = response_future

        # Subscribe to reply topic
        async def _reply_handler(sleipnir_event: Any) -> None:
            if sleipnir_event.correlation_id == correlation_id:
                reply_data = sleipnir_event.payload.get("rpc_response", {})
                if not response_future.done():
                    response_future.set_result(reply_data)

        reply_subscription = await self._subscriber.subscribe([reply_topic], _reply_handler)

        try:
            # Publish request
            from sleipnir.domain.events import SleipnirEvent

            request_event = SleipnirEvent(
                event_type=f"{_RPC_REQUEST_PREFIX}.{safe_target_id}",
                source=f"ravn:{self._own_peer_id}",
                payload={
                    "rpc_request": message,
                    "reply_topic": reply_topic,
                },
                summary=f"RPC request to {target_peer_id}",
                urgency=0.5,
                domain="code",
                timestamp=datetime.now(UTC),
                correlation_id=correlation_id,
            )
            await self._publisher.publish(request_event)

            # Wait for response
            try:
                return await asyncio.wait_for(response_future, timeout=timeout)
            except TimeoutError as exc:
                raise TimeoutError(
                    f"No reply from peer {target_peer_id!r} within {timeout}s"
                ) from exc
        finally:
            # Cleanup
            self._pending_rpc.pop(correlation_id, None)
            await reply_subscription.unsubscribe()

    async def start(self) -> None:
        """Start listening for incoming RPC requests."""
        # Start the transport if it has a start method (nng, rabbitmq, etc.)
        if hasattr(self._publisher, "start"):
            await self._publisher.start()
        # If subscriber is different from publisher, start it too
        if self._subscriber is not self._publisher and hasattr(self._subscriber, "start"):
            await self._subscriber.start()

        # Subscribe to RPC requests for this peer
        safe_own_id = _sanitize_for_event_type(self._own_peer_id)
        rpc_pattern = f"{_RPC_REQUEST_PREFIX}.{safe_own_id}"
        self._rpc_subscription = await self._subscriber.subscribe(
            [rpc_pattern], self._handle_rpc_request
        )
        logger.info("sleipnir_mesh: started peer=%s", self._own_peer_id)

    async def stop(self) -> None:
        """Graceful shutdown."""
        if self._rpc_subscription is not None:
            await self._rpc_subscription.unsubscribe()
            self._rpc_subscription = None

        for subscription in self._subscriptions.values():
            await subscription.unsubscribe()
        self._subscriptions.clear()

        # Cancel pending RPCs
        for future in self._pending_rpc.values():
            if not future.done():
                future.cancel()
        self._pending_rpc.clear()

        # Stop the transport if it has a stop method
        if hasattr(self._publisher, "stop"):
            await self._publisher.stop()
        if self._subscriber is not self._publisher and hasattr(self._subscriber, "stop"):
            await self._subscriber.stop()

        logger.info("sleipnir_mesh: stopped peer=%s", self._own_peer_id)

    @property
    def subscriber(self) -> SleipnirSubscriber:
        """Expose the underlying Sleipnir subscriber port."""
        return self._subscriber

    def set_rpc_handler(self, handler: Callable[[dict], Awaitable[dict]]) -> None:
        """Register handler for incoming RPC requests."""
        self._rpc_handler = handler

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_peer_trusted(self, peer_id: str) -> None:
        """Raise PeerNotFoundError if peer is not in discovery table."""
        if self._discovery is None:
            return  # No discovery = trust all
        try:
            peers = self._discovery.peers()  # type: ignore[attr-defined]
        except Exception:
            peers = {}
        if peer_id not in peers:
            raise PeerNotFoundError(peer_id)

    async def _handle_rpc_request(self, sleipnir_event: Any) -> None:
        """Handle incoming RPC request and send reply."""
        payload = sleipnir_event.payload
        request = payload.get("rpc_request", {})
        reply_topic = payload.get("reply_topic")
        correlation_id = sleipnir_event.correlation_id

        if not reply_topic:
            logger.warning("sleipnir_mesh: RPC request missing reply_topic")
            return

        # Process request
        if self._rpc_handler is not None:
            try:
                response = await self._rpc_handler(request)
            except Exception as exc:
                response = {"error": str(exc)}
        else:
            response = {"error": "no rpc handler registered"}

        # Send reply
        from sleipnir.domain.events import SleipnirEvent

        reply_event = SleipnirEvent(
            event_type=reply_topic,
            source=f"ravn:{self._own_peer_id}",
            payload={"rpc_response": response},
            summary="RPC reply",
            urgency=0.5,
            domain="code",
            timestamp=datetime.now(UTC),
            correlation_id=correlation_id,
        )
        try:
            await self._publisher.publish(reply_event)
        except Exception as exc:
            logger.debug("sleipnir_mesh: failed to send RPC reply: %s", exc)

"""Sleipnir → WebSocket bridge for Skuld.

Skuld subscribes to the Sleipnir event bus and forwards relevant events to
the browser via the :class:`~skuld.channels.ChannelRegistry`.  This decouples
the browser from direct service coupling — Volundr, Ting, and Ravn all publish
to Sleipnir; Skuld bridges the resulting stream to the WebSocket.

Filtering rules
---------------
- *session_id* filter: only events whose ``correlation_id`` or
  ``payload["session_id"]`` matches the configured session are forwarded.
  When ``session_id`` is ``None`` (no filter), all matching events are forwarded.
- *event_patterns*: shell-style wildcards (e.g. ``"volundr.*"``), forwarded
  verbatim to the Sleipnir subscriber port.
"""

from __future__ import annotations

import logging

from skuld.channels import ChannelRegistry
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)

#: Default patterns forwarded by the bridge.
DEFAULT_PATTERNS: list[str] = [
    "ravn.*",
    "volundr.*",
    "ting.*",
    "system.health.*",
]


class SleipnirBridge:
    """Subscribes to Sleipnir and forwards events to the channel registry.

    Lifecycle::

        bridge = SleipnirBridge(subscriber, registry, session_id="abc")
        await bridge.start()   # subscribes to Sleipnir
        # ... events flow to registry ...
        await bridge.stop()    # unsubscribes and cleans up

    Can also be used as an async context manager::

        async with SleipnirBridge(subscriber, registry) as bridge:
            ...
    """

    def __init__(
        self,
        subscriber: SleipnirSubscriber,
        registry: ChannelRegistry,
        session_id: str | None = None,
        event_patterns: list[str] | None = None,
    ) -> None:
        self._subscriber = subscriber
        self._registry = registry
        self._session_id = session_id
        self._patterns = event_patterns if event_patterns is not None else list(DEFAULT_PATTERNS)
        self._subscription: Subscription | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to Sleipnir and begin bridging events."""
        if self._subscription is not None:
            logger.warning("SleipnirBridge.start() called while already running — ignored")
            return
        self._subscription = await self._subscriber.subscribe(
            self._patterns,
            self._handle_event,
        )
        logger.info(
            "SleipnirBridge started: session_id=%s, patterns=%s",
            self._session_id,
            self._patterns,
        )

    async def stop(self) -> None:
        """Unsubscribe from Sleipnir and release resources."""
        if self._subscription is None:
            return
        await self._subscription.unsubscribe()
        self._subscription = None
        logger.info("SleipnirBridge stopped: session_id=%s", self._session_id)

    async def __aenter__(self) -> SleipnirBridge:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _handle_event(self, event: SleipnirEvent) -> None:
        """Filter by session/user context, then broadcast to all channels."""
        if not self._matches_context(event):
            return

        wire = self._to_wire(event)
        try:
            await self._registry.broadcast(wire)
        except Exception:
            logger.error(
                "SleipnirBridge: failed to broadcast event %s (%s)",
                event.event_id,
                event.event_type,
                exc_info=True,
            )

    def _matches_context(self, event: SleipnirEvent) -> bool:
        """Return True if the event is relevant to this bridge's context.

        When *session_id* is ``None``, all events pass through.
        Otherwise the event must have a matching ``correlation_id`` **or** a
        ``payload["session_id"]`` equal to our session.
        """
        if self._session_id is None:
            return True

        if event.correlation_id == self._session_id:
            return True

        payload_session = event.payload.get("session_id")
        if payload_session == self._session_id:
            return True

        return False

    @staticmethod
    def _to_wire(event: SleipnirEvent) -> dict:
        """Serialise a :class:`SleipnirEvent` to the channel wire format."""
        return {
            "type": "sleipnir",
            "event_id": event.event_id,
            "event_type": event.event_type,
            "source": event.source,
            "summary": event.summary,
            "payload": event.payload,
            "urgency": event.urgency,
            "domain": event.domain,
            "timestamp": event.timestamp.isoformat(),
            "correlation_id": event.correlation_id,
            "tenant_id": event.tenant_id,
        }

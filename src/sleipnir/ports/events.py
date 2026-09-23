"""Port interfaces for the Sleipnir event bus."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from sleipnir.domain.events import SleipnirEvent

#: Type alias for an async event handler.  Returning acknowledges the event;
#: raising asks for redelivery.  Handlers must be idempotent (at-least-once);
#: see :class:`SleipnirSubscriber`.
EventHandler = Callable[[SleipnirEvent], Awaitable[None]]


class Subscription(ABC):
    """Represents an active event subscription.

    Callers must call :meth:`unsubscribe` when they no longer wish to receive
    events, to release resources held by the transport.
    """

    @abstractmethod
    async def unsubscribe(self) -> None:
        """Cancel this subscription and release its resources."""


class SleipnirPublisher(ABC):
    """Port for publishing events onto the Sleipnir event bus."""

    @abstractmethod
    async def publish(self, event: SleipnirEvent) -> None:
        """Publish a single event.

        :param event: The event to publish.
        """

    @abstractmethod
    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        """Publish multiple events atomically (best-effort ordering).

        :param events: Events to publish; delivered in iteration order.
        """


class SleipnirSubscriber(ABC):
    """Port for subscribing to events on the Sleipnir event bus.

    Delivery guarantee
    ------------------
    A handler *returning* is the acknowledgement.  Transports backed by a
    durable broker log (NATS JetStream) acknowledge an event to the broker
    only after its handler has returned.  A handler that *raises* asks for
    redelivery with backoff; once the transport's max-deliver limit is spent
    the event is dead-lettered as a ``system.dlq.message`` event instead of
    being dropped.  A process that dies before the ack gets the event
    redelivered, to itself on restart or to another consumer-group member.

    Delivery is therefore **at-least-once**, never exactly-once: the same
    event (same ``event_id``) can reach a handler more than once — after a
    handler failure, a crash between handling and ack, a lost ack, or a
    consumer-group handoff.  **Handlers must be idempotent**, keyed on
    ``event_id``.  Events for one subscription are handled one at a time, and
    a full subscription queue withholds acks (backpressure) rather than
    discarding events.

    Transports without a broker-side log — in-process, nng, webhook, CLI
    command, and core (non-JetStream) NATS subjects — cannot redeliver.  They
    are at-most-once: an event whose handler raises is logged and not retried,
    and the broker-less transports drop their oldest queued event on overflow
    so that publishers never block.
    """

    @abstractmethod
    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        """Register *handler* for events matching any pattern in *event_types*.

        Pattern syntax follows shell-style wildcards (``fnmatch``):

        - ``"ravn.*"`` — all Ravn events
        - ``"ravn.tool.*"`` — all Ravn tool events
        - ``"*"`` — all events

        :param event_types: One or more patterns to subscribe to.
        :param handler: Async callable invoked for each matching event.  It
            must be idempotent: see the class docstring for the at-least-once
            contract.  Returning acknowledges the event; raising requests
            redelivery on transports that can redeliver.
        :returns: A :class:`Subscription` handle; call ``unsubscribe()`` to cancel.
        """

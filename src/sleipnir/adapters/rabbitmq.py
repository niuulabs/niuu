"""RabbitMQ transport adapter for Sleipnir.

Primary durable broker for production deployments.  Already present in the
ODIN infrastructure stack as Yggdrasil's backbone, so teams already running
ODIN need zero additional broker infrastructure.

Architecture
------------
- :class:`RabbitMQPublisher` — connects via AMQP, declares the topic exchange,
  publishes events with routing key = event_type.  Persistent delivery mode for
  urgency > threshold (default 0.4).
- :class:`RabbitMQSubscriber` — connects via AMQP, declares a per-subscriber
  queue, binds it to the topic exchange for each subscription pattern, and
  dispatches incoming messages to application-level handlers.
- :class:`RabbitMQTransport` — combined publisher + subscriber for single-process
  use.

Topology
--------
- Exchange: ``sleipnir.events`` (topic, durable)
- Routing key: event_type (e.g. ``ravn.tool.complete``)
- Dead-letter exchange: ``sleipnir.dead_letter`` (fanout, durable)
- Consumer queues: durable for named services, auto-delete for ephemeral

Routing key translation
-----------------------
fnmatch patterns are translated to AMQP topic routing keys:

- ``"*"``               → ``"#"``           (all events)
- ``"ravn.*"``          → ``"ravn.#"``      (all ravn events; fnmatch * spans dots)
- ``"ravn.tool.*"``     → ``"ravn.tool.#"`` (all ravn tool events)
- ``"ravn.tool.complete"`` → ``"ravn.tool.complete"`` (exact match)
- Any other wildcard pattern → ``"#"`` (receive all; app-level fnmatch filters)

Serialisation
-------------
JSON is used for all messages (not msgpack) to enable human-readable
inspection via the RabbitMQ management UI.

Configuration example
---------------------
::

    sleipnir:
      transport: rabbitmq
      url: amqp://guest:guest@rabbitmq:5672/
      exchange: sleipnir.events
      prefetch: 1
      durable_threshold_urgency: 0.4
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

try:
    import aio_pika
    import aio_pika.abc

    _AIO_PIKA_AVAILABLE = True
except ImportError:  # pragma: no cover
    _AIO_PIKA_AVAILABLE = False

from sleipnir.adapters._subscriber_support import (
    DEFAULT_RING_BUFFER_DEPTH,
    _BaseSubscription,
    consume_queue,
    dispatch_to_subscriptions,
)
from sleipnir.adapters.serialization import deserialize, serialize
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import EventHandler, SleipnirPublisher, SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults (all overridable via constructor kwargs or config)
# ---------------------------------------------------------------------------

DEFAULT_AMQP_URL = "amqp://guest:guest@localhost:5672/"
DEFAULT_EXCHANGE_NAME = "sleipnir.events"
DEFAULT_DEAD_LETTER_EXCHANGE = "sleipnir.dead_letter"

#: Prefetch count for fair dispatch — one outstanding message per consumer.
DEFAULT_PREFETCH_COUNT = 1

#: Events with urgency above this threshold are published as persistent
#: (delivery_mode=2); events at or below are transient (delivery_mode=1).
DEFAULT_DURABLE_THRESHOLD_URGENCY = 0.4

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fnmatch_to_amqp(pattern: str) -> str:
    """Translate an fnmatch pattern to an AMQP topic routing key.

    In AMQP topic exchanges:
    - ``*`` matches exactly one dot-delimited word
    - ``#`` matches zero or more dot-delimited words

    In fnmatch:
    - ``*`` matches any sequence of characters, including dots

    Therefore ``ravn.*`` in fnmatch means "anything under ravn", which maps to
    ``ravn.#`` in AMQP (not ``ravn.*``, which would only match a single word).

    Rules
    -----
    - ``"*"``   → ``"#"``              (subscribe to all)
    - ``"a.*"`` → ``"a.#"``            (trailing ``.*`` → ``.#``)
    - Exact     → unchanged             (no wildcard chars)
    - Other     → ``"#"``              (complex pattern; app-level filtering)
    """
    if pattern == "*":
        return "#"
    if pattern.endswith(".*"):
        # "ravn.*" → "ravn.#"  (strip the star, keep the dot, append hash)
        return pattern[:-1] + "#"
    if any(c in pattern for c in ("*", "?", "[")):
        # Non-trivial wildcard — subscribe to all, rely on fnmatch at app level
        return "#"
    return pattern


def _require_aio_pika() -> None:
    if not _AIO_PIKA_AVAILABLE:  # pragma: no cover
        raise ImportError(
            "aio-pika is required for the RabbitMQ transport adapter. "
            "Install it with: pip install aio-pika"
        )


def _encode_event(event: SleipnirEvent) -> bytes:
    """Serialise *event* to JSON bytes."""
    return serialize(event, fmt="json")


def _decode_event(data: bytes) -> SleipnirEvent | None:
    """Deserialise *data* from JSON bytes.  Returns ``None`` on failure."""
    try:
        return deserialize(data, fmt="json")
    except Exception:
        logger.exception("RabbitMQ: deserialization failed, message dropped")
        return None


# ---------------------------------------------------------------------------
# RabbitMQPublisher
# ---------------------------------------------------------------------------


class RabbitMQPublisher(SleipnirPublisher):
    """AMQP publisher for Sleipnir events.

    Connects to RabbitMQ with a robust (auto-reconnecting) connection, declares
    the topic exchange, and publishes :class:`SleipnirEvent` objects as JSON
    messages.

    Delivery mode per event:
    - ``urgency > durable_threshold_urgency`` → :attr:`~aio_pika.DeliveryMode.PERSISTENT`
    - ``urgency ≤ durable_threshold_urgency`` → :attr:`~aio_pika.DeliveryMode.NOT_PERSISTENT`

    Usage::

        pub = RabbitMQPublisher("amqp://guest:guest@rabbitmq:5672/")
        async with pub:
            await pub.publish(event)
    """

    def __init__(
        self,
        url: str = DEFAULT_AMQP_URL,
        exchange_name: str = DEFAULT_EXCHANGE_NAME,
        durable_threshold_urgency: float = DEFAULT_DURABLE_THRESHOLD_URGENCY,
    ) -> None:
        _require_aio_pika()
        self._url = url
        self._exchange_name = exchange_name
        self._durable_threshold_urgency = durable_threshold_urgency
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None  # type: ignore[name-defined]
        self._channel: aio_pika.abc.AbstractChannel | None = None  # type: ignore[name-defined]
        self._exchange: aio_pika.abc.AbstractExchange | None = None  # type: ignore[name-defined]

    async def start(self) -> None:
        """Connect to RabbitMQ and declare the topic exchange."""
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        self._exchange = await self._channel.declare_exchange(
            self._exchange_name,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        logger.debug("RabbitMQPublisher: connected, exchange=%s", self._exchange_name)

    async def stop(self) -> None:
        """Close the AMQP channel and connection."""
        if self._channel is not None:
            with suppress(Exception):
                await self._channel.close()
            self._channel = None
        if self._connection is not None:
            with suppress(Exception):
                await self._connection.close()
            self._connection = None
        self._exchange = None
        logger.debug("RabbitMQPublisher: closed")

    async def __aenter__(self) -> RabbitMQPublisher:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def publish(self, event: SleipnirEvent) -> None:
        if event.ttl is not None and event.ttl <= 0:
            logger.debug(
                "Dropping expired event %s (%s): ttl=%d",
                event.event_id,
                event.event_type,
                event.ttl,
            )
            return
        if self._exchange is None:
            raise RuntimeError("RabbitMQPublisher is not started. Call start() first.")
        delivery_mode = (
            aio_pika.DeliveryMode.PERSISTENT
            if event.urgency > self._durable_threshold_urgency
            else aio_pika.DeliveryMode.NOT_PERSISTENT
        )
        message = aio_pika.Message(
            body=_encode_event(event),
            delivery_mode=delivery_mode,
            content_type="application/json",
        )
        await self._exchange.publish(message, routing_key=event.event_type)

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        for event in events:
            await self.publish(event)


# ---------------------------------------------------------------------------
# RabbitMQSubscriber
# ---------------------------------------------------------------------------


class RabbitMQSubscriber(SleipnirSubscriber):
    """AMQP subscriber for Sleipnir events.

    Connects to RabbitMQ, declares a single queue for this subscriber, and
    binds it to the topic exchange for each subscription pattern.  Incoming
    messages are dispatched to application-level handlers via per-subscription
    asyncio queues (ring-buffer semantics on overflow).

    Queue durability:
    - When *service_id* is provided → durable, named queue (survives broker restart)
    - When *service_id* is ``None`` → auto-delete, anonymous queue (ephemeral)

    Dead-letter exchange:
    The subscriber queue is declared with ``x-dead-letter-exchange`` pointing to
    ``sleipnir.dead_letter``.  Nacked messages (e.g. on deserialization failure)
    are routed there for inspection by Sköll.

    Usage::

        sub = RabbitMQSubscriber(service_id="ravn:agent-abc")
        async with sub:
            handle = await sub.subscribe(["ravn.*"], my_handler)
            ...
            await handle.unsubscribe()
    """

    def __init__(
        self,
        url: str = DEFAULT_AMQP_URL,
        exchange_name: str = DEFAULT_EXCHANGE_NAME,
        dead_letter_exchange: str = DEFAULT_DEAD_LETTER_EXCHANGE,
        service_id: str | None = None,
        prefetch_count: int = DEFAULT_PREFETCH_COUNT,
        ring_buffer_depth: int = DEFAULT_RING_BUFFER_DEPTH,
    ) -> None:
        _require_aio_pika()
        if ring_buffer_depth < 1:
            raise ValueError(f"ring_buffer_depth must be >= 1, got {ring_buffer_depth}")
        self._url = url
        self._exchange_name = exchange_name
        self._dead_letter_exchange = dead_letter_exchange
        self._service_id = service_id
        self._prefetch_count = prefetch_count
        self._ring_buffer_depth = ring_buffer_depth

        self._connection: aio_pika.abc.AbstractRobustConnection | None = None  # type: ignore[name-defined]
        self._channel: aio_pika.abc.AbstractChannel | None = None  # type: ignore[name-defined]
        self._exchange: aio_pika.abc.AbstractExchange | None = None  # type: ignore[name-defined]
        self._queue: aio_pika.abc.AbstractQueue | None = None  # type: ignore[name-defined]
        self._consumer_tag: str | None = None

        self._subscriptions: list[_BaseSubscription] = []
        self._bound_amqp_keys: set[str] = set()
        self._running = False

    async def start(self) -> None:
        """Connect to RabbitMQ and begin consuming from the subscriber queue."""
        self._connection = await aio_pika.connect_robust(self._url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self._prefetch_count)

        # Declare dead-letter exchange first so queue arguments are valid.
        await self._channel.declare_exchange(
            self._dead_letter_exchange,
            aio_pika.ExchangeType.FANOUT,
            durable=True,
        )

        # Declare the main topic exchange.
        self._exchange = await self._channel.declare_exchange(
            self._exchange_name,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )

        is_named = self._service_id is not None
        self._queue = await self._channel.declare_queue(
            self._service_id or "",
            durable=is_named,
            exclusive=not is_named,
            auto_delete=not is_named,
            arguments={"x-dead-letter-exchange": self._dead_letter_exchange},
        )

        self._running = True
        # Defer AMQP consumption until the first subscribe() call so that
        # pre-existing messages are not consumed before any handler is registered.
        logger.debug("RabbitMQSubscriber: ready (queue=%s, consuming deferred)", self._queue.name)

    async def stop(self) -> None:
        """Cancel AMQP consumption and close the connection."""
        self._running = False
        if self._queue is not None and self._consumer_tag is not None:
            with suppress(Exception):
                await self._queue.cancel(self._consumer_tag)
            self._consumer_tag = None
        if self._channel is not None:
            with suppress(Exception):
                await self._channel.close()
            self._channel = None
        if self._connection is not None:
            with suppress(Exception):
                await self._connection.close()
            self._connection = None
        self._queue = None
        self._exchange = None
        logger.debug("RabbitMQSubscriber: closed")

    async def __aenter__(self) -> RabbitMQSubscriber:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        if self._queue is None:
            raise RuntimeError("RabbitMQSubscriber is not started. Call start() first.")
        # Bind the AMQP queue to the exchange for each new routing key.
        for pattern in event_types:
            amqp_key = _fnmatch_to_amqp(pattern)
            if amqp_key not in self._bound_amqp_keys:
                await self._queue.bind(self._exchange, routing_key=amqp_key)
                self._bound_amqp_keys.add(amqp_key)
                logger.debug(
                    "RabbitMQSubscriber: bound queue=%s key=%s",
                    self._queue.name,
                    amqp_key,
                )
        # Start consuming on first subscribe() so pre-existing messages
        # aren't consumed before any handler is registered.
        if self._consumer_tag is None:
            self._consumer_tag = await self._queue.consume(self._on_message)
            logger.debug("RabbitMQSubscriber: consuming from queue=%s", self._queue.name)
        queue: asyncio.Queue[SleipnirEvent] = asyncio.Queue(maxsize=self._ring_buffer_depth)
        task = asyncio.create_task(consume_queue(queue, handler))
        sub = _BaseSubscription(
            list(event_types), queue, task, lambda: self._remove_subscription(sub)
        )
        self._subscriptions.append(sub)
        return sub

    async def flush(self) -> None:
        """Wait until every queued (already-received) event has been processed."""
        for sub in list(self._subscriptions):
            await sub._queue.join()

    def _remove_subscription(self, sub: _BaseSubscription) -> None:
        with suppress(ValueError):
            self._subscriptions.remove(sub)

    async def _on_message(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,  # type: ignore[name-defined]
    ) -> None:
        """Receive an AMQP message, deserialise it, and dispatch to handlers.

        Acknowledgement policy:
        - Deserialization failure → ``nack(requeue=False)`` so the message is
          routed to ``sleipnir.dead_letter`` for Sköll inspection.
        - TTL-expired event → ``ack()``; the message is valid but stale.
        - Successful dispatch → ``ack()``.
        """
        event = _decode_event(message.body)
        if event is None:
            await message.nack(requeue=False)
            return
        await message.ack()
        await dispatch_to_subscriptions(event, self._subscriptions, self._ring_buffer_depth, logger)


# ---------------------------------------------------------------------------
# RabbitMQTransport — combined publisher + subscriber
# ---------------------------------------------------------------------------


class RabbitMQTransport(SleipnirPublisher, SleipnirSubscriber):
    """Combined RabbitMQ publisher + subscriber for single-process use.

    Opens both a :class:`RabbitMQPublisher` and a :class:`RabbitMQSubscriber`.
    Unlike nng (where events loop back via the socket), published events are
    received by the subscriber only if the subscriber's queue is bound to a
    routing key that matches the published event_type.

    Usage::

        bus = RabbitMQTransport(
            url="amqp://guest:guest@rabbitmq:5672/",
            service_id="ravn:agent-abc",
        )
        async with bus:
            handle = await bus.subscribe(["ravn.*"], my_handler)
            await bus.publish(event)
            await bus.flush()
            await handle.unsubscribe()
    """

    def __init__(
        self,
        url: str = DEFAULT_AMQP_URL,
        exchange_name: str = DEFAULT_EXCHANGE_NAME,
        dead_letter_exchange: str = DEFAULT_DEAD_LETTER_EXCHANGE,
        service_id: str | None = None,
        prefetch_count: int = DEFAULT_PREFETCH_COUNT,
        ring_buffer_depth: int = DEFAULT_RING_BUFFER_DEPTH,
        durable_threshold_urgency: float = DEFAULT_DURABLE_THRESHOLD_URGENCY,
    ) -> None:
        _require_aio_pika()
        self._publisher = RabbitMQPublisher(
            url=url,
            exchange_name=exchange_name,
            durable_threshold_urgency=durable_threshold_urgency,
        )
        self._subscriber = RabbitMQSubscriber(
            url=url,
            exchange_name=exchange_name,
            dead_letter_exchange=dead_letter_exchange,
            service_id=service_id,
            prefetch_count=prefetch_count,
            ring_buffer_depth=ring_buffer_depth,
        )

    async def start(self) -> None:
        """Start the publisher then the subscriber."""
        await self._publisher.start()
        await self._subscriber.start()

    async def stop(self) -> None:
        """Graceful shutdown: stop subscriber first, then publisher."""
        await self._subscriber.stop()
        await self._publisher.stop()

    async def __aenter__(self) -> RabbitMQTransport:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def publish(self, event: SleipnirEvent) -> None:
        await self._publisher.publish(event)

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        await self._publisher.publish_batch(events)

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        return await self._subscriber.subscribe(event_types, handler)

    async def flush(self) -> None:
        await self._subscriber.flush()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def rabbitmq_available() -> bool:
    """Return ``True`` if aio-pika is installed and the RabbitMQ adapter can be used."""
    return _AIO_PIKA_AVAILABLE

"""Shared helpers for Sleipnir subscriber adapters.

Extracted so that all transport adapters (in-process, nng, NATS, …) can
reuse the consumer loops, base subscription class, ring-buffer overflow helper,
and event dispatch logic without duplicating code.

Two consumer loops exist because transports differ in what they can promise:

- :func:`consume_deliveries` is for broker-backed transports that can
  redeliver.  Each queued :class:`Delivery` is settled with the broker only
  after the handler has run: :meth:`Delivery.ack` when it returned,
  :meth:`Delivery.nak` when it raised.  Callers enqueue with a blocking
  ``await queue.put(...)`` so a full queue withholds acks and pushes back on
  the broker instead of discarding events.
- :func:`consume_queue` is for broker-less transports (in-process, nng,
  webhook, CLI command).  Nothing can redeliver their events, so a failing
  handler is logged and the event is gone, and :func:`enqueue_with_overflow`
  drops the oldest event rather than block a publisher.  These transports are
  at-most-once by construction.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import suppress

from sleipnir.domain.events import SleipnirEvent, match_event_type
from sleipnir.ports.events import EventHandler, Subscription

#: Default depth of each subscriber's ring buffer (events).
#: All adapters use this value so the behaviour is consistent across transports.
DEFAULT_RING_BUFFER_DEPTH = 1000


class Delivery(ABC):
    """One event queued for a subscription handler, plus its broker settlement.

    The consumer loop calls exactly one of :meth:`ack` or :meth:`nak` once the
    handler has finished with :attr:`event`.  Implementations must not raise:
    a settlement that fails leaves the message unacknowledged, which the broker
    already treats as "redeliver later", so they record the failure and return.
    """

    __slots__ = ("event",)

    def __init__(self, event: SleipnirEvent) -> None:
        self.event = event

    @abstractmethod
    async def ack(self) -> None:
        """The handler returned: tell the broker never to redeliver this event."""

    @abstractmethod
    async def nak(self, error: Exception) -> None:
        """The handler raised *error*: request redelivery, or dead-letter the event."""


async def consume_deliveries(
    queue: asyncio.Queue[Delivery],
    handler: EventHandler,
) -> None:
    """Consumer loop for broker-backed transports: settle only after handling.

    The ack is sent after ``handler`` returns, never before, so an event whose
    handler raised, or whose process died mid-handler, is redelivered by the
    broker.  Cancellation (unsubscribe/stop) leaves the in-flight delivery
    unsettled on purpose; the owning subscription releases it.
    """
    while True:
        delivery = await queue.get()
        try:
            try:
                await handler(delivery.event)
            except Exception as exc:
                await delivery.nak(exc)
                continue
            await delivery.ack()
        finally:
            queue.task_done()


async def consume_queue(
    queue: asyncio.Queue[SleipnirEvent],
    handler: EventHandler,
) -> None:
    """Consumer loop for broker-less transports: read events and invoke *handler*.

    There is no broker to redeliver from, so a handler exception is logged with
    its traceback and the event is not retried (at-most-once).  Broker-backed
    transports use :func:`consume_deliveries` instead.
    """
    while True:
        event = await queue.get()
        try:
            await handler(event)
        except Exception:
            logging.getLogger(__name__).exception(
                "Handler raised an exception for event %s (%s)",
                event.event_id,
                event.event_type,
            )
        finally:
            queue.task_done()


async def enqueue_with_overflow(
    queue: asyncio.Queue[SleipnirEvent],
    event: SleipnirEvent,
    ring_buffer_depth: int,
    log: logging.Logger,
) -> bool:
    """Put *event* on *queue*, dropping the oldest entry on overflow.

    Only for broker-less transports, whose publishers must never block on a
    slow subscriber and which have no broker to push back on.  When the queue
    is full the oldest event is dequeued and discarded; a ``WARNING`` is logged
    with the dropped event's id and type.  Returns ``True`` when an event was
    dropped so callers can surface sustained backpressure through their stats.
    Broker-backed transports must use a blocking ``await queue.put(...)`` so
    that a full queue withholds acknowledgements instead of losing events.
    """
    overflowed = False
    if queue.full():
        with suppress(asyncio.QueueEmpty):
            dropped = queue.get_nowait()
            queue.task_done()
            overflowed = True
            log.warning(
                "Ring buffer overflow (depth=%d): dropped event %s (%s)",
                ring_buffer_depth,
                dropped.event_id,
                dropped.event_type,
            )
    await queue.put(event)
    return overflowed


class _BaseSubscription(Subscription):
    """Subscription backed by an asyncio.Queue and a consumer task.

    *remove_fn* is invoked once during :meth:`unsubscribe` to deregister this
    subscription from its owning bus or subscriber.  It receives no arguments
    and should remove *self* from the parent's subscription list.
    """

    def __init__(
        self,
        patterns: list[str],
        queue: asyncio.Queue[SleipnirEvent] | asyncio.Queue[Delivery],
        task: asyncio.Task[None],
        remove_fn: Callable[[], None],
    ) -> None:
        self._patterns = patterns
        self._queue = queue
        self._task = task
        self._remove_fn = remove_fn
        self._active = True

    @property
    def patterns(self) -> list[str]:
        return self._patterns

    @property
    def active(self) -> bool:
        return self._active

    async def unsubscribe(self) -> None:
        if not self._active:
            return
        self._active = False
        self._remove_fn()
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        # Drain remaining items so join() is never blocked.
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break


async def dispatch_to_subscriptions(
    event: SleipnirEvent,
    subscriptions: list[_BaseSubscription],
    ring_buffer_depth: int,
    log: logging.Logger,
) -> None:
    """Dispatch *event* to all active subscriptions whose patterns match.

    Events with ``ttl`` that is not ``None`` and ``<= 0`` are considered
    expired on arrival and silently dropped (logged at DEBUG level).

    This is the canonical dispatch loop shared by all transport adapters so
    that TTL handling, pattern matching, and ring-buffer overflow semantics
    are consistent everywhere.

    :param event: The event to dispatch.
    :param subscriptions: The list of active subscriptions to consider.
    :param ring_buffer_depth: Ring buffer depth used for overflow warnings.
    :param log: Logger to use for debug/warning messages.
    """
    if event.ttl is not None and event.ttl <= 0:
        log.debug(
            "Dropping expired event %s (%s): ttl=%d",
            event.event_id,
            event.event_type,
            event.ttl,
        )
        return
    for sub in list(subscriptions):
        if not sub.active:
            continue
        if not any(match_event_type(p, event.event_type) for p in sub.patterns):
            continue
        await enqueue_with_overflow(sub._queue, event, ring_buffer_depth, log)

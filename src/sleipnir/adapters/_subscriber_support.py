"""Shared helpers for Sleipnir subscriber adapters.

Extracted so that all transport adapters (in-process, nng, RabbitMQ, …) can
reuse the consumer loop, base subscription class, ring-buffer overflow helper,
and event dispatch logic without duplicating code.
"""

from __future__ import annotations
from contextlib import suppress

import asyncio
import logging
from collections.abc import Callable

from sleipnir.domain.events import SleipnirEvent, match_event_type
from sleipnir.ports.events import EventHandler, Subscription

#: Default depth of each subscriber's ring buffer (events).
#: All adapters use this value so the behaviour is consistent across transports.
DEFAULT_RING_BUFFER_DEPTH = 1000


async def consume_queue(
    queue: asyncio.Queue[SleipnirEvent],
    handler: EventHandler,
) -> None:
    """Consumer loop: read events from *queue* and invoke *handler*."""
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
) -> None:
    """Put *event* on *queue*, dropping the oldest entry on overflow.

    When the queue is full the oldest event is dequeued and discarded so that
    the producer is never blocked.  A ``WARNING`` is logged with the dropped
    event's id and type.
    """
    if queue.full():
        with suppress(asyncio.QueueEmpty):
            dropped = queue.get_nowait()
            queue.task_done()
            log.warning(
                "Ring buffer overflow (depth=%d): dropped event %s (%s)",
                ring_buffer_depth,
                dropped.event_id,
                dropped.event_type,
            )
    await queue.put(event)


class _BaseSubscription(Subscription):
    """Subscription backed by an asyncio.Queue and a consumer task.

    *remove_fn* is invoked once during :meth:`unsubscribe` to deregister this
    subscription from its owning bus or subscriber.  It receives no arguments
    and should remove *self* from the parent's subscription list.
    """

    def __init__(
        self,
        patterns: list[str],
        queue: asyncio.Queue[SleipnirEvent],
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

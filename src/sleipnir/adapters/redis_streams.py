"""Redis Streams transport adapter for Sleipnir.

Uses Redis Streams (XADD / XREADGROUP) for event pub/sub with native replay
capability.  One stream per namespace (e.g. ``sleipnir:ravn``).  Consumer
groups ensure each subscriber service reads independently without affecting
others.

Suitable for deployments where Redis is already in the stack and replay from
offset is required (new services can catch up on historical events at startup).

Example config::

    sleipnir:
      transport: redis_streams
      url: redis://localhost:6379
      stream_prefix: sleipnir
      maxlen: 10000
      replay_on_startup: true
      consumer_group: my-service
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from contextlib import suppress

from sleipnir.adapters._subscriber_support import (
    _BaseSubscription,
    consume_queue,
    enqueue_with_overflow,
)
from sleipnir.adapters.serialization import deserialize, serialize
from sleipnir.domain.events import EVENT_NAMESPACES, SleipnirEvent, match_event_type
from sleipnir.ports.events import EventHandler, SleipnirPublisher, SleipnirSubscriber, Subscription

try:
    import redis.asyncio as aioredis

    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level defaults — no magic numbers in business logic
# ---------------------------------------------------------------------------

DEFAULT_REDIS_URL = "redis://localhost:6379"
DEFAULT_STREAM_PREFIX = "sleipnir"
DEFAULT_MAXLEN = 10_000
DEFAULT_REPLAY_ON_STARTUP = False
DEFAULT_CONSUMER_GROUP = "sleipnir"
DEFAULT_RING_BUFFER_DEPTH = 1_000
DEFAULT_BLOCK_TIMEOUT_MS = 100
DEFAULT_REPLAY_BATCH_SIZE = 100

#: Redis stream field that stores the serialised event payload.
_PAYLOAD_FIELD = b"payload"

#: Stream ID sentinel: read all messages from the beginning (replay).
_ID_STREAM_START = "0"

#: Stream ID sentinel: only messages added after group creation (no replay).
_ID_STREAM_NEW = "$"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _stream_key(prefix: str, namespace: str) -> str:
    """Return the Redis stream key for *namespace*."""
    return f"{prefix}:{namespace}"


def _streams_for_patterns(prefix: str, patterns: list[str]) -> list[str]:
    """Return the Redis stream keys that must be watched for *patterns*.

    The namespace (first dot-segment of an event type) maps to one stream.
    A wildcard in the namespace position (e.g. ``"*"`` or ``"*.*"``) expands
    to all known-namespace streams.
    """
    namespaces: set[str] = set()
    for pattern in patterns:
        if pattern == "*":
            namespaces.update(EVENT_NAMESPACES)
            break
        first_seg = pattern.split(".")[0]
        if any(c in first_seg for c in ("*", "?", "[")):
            # Wildcard in namespace — must watch everything.
            namespaces.update(EVENT_NAMESPACES)
            break
        namespaces.add(first_seg)
    return [_stream_key(prefix, ns) for ns in sorted(namespaces)]


# ---------------------------------------------------------------------------
# Subscription implementation
# ---------------------------------------------------------------------------


class _RedisSubscription(_BaseSubscription):
    """Extends :class:`_BaseSubscription` with an extra reader task.

    The base class manages the handler task (queue → handler coroutine).
    This subclass adds a *reader task* (Redis → queue) that must be cancelled
    first so no new items land on the queue after the drain.
    """

    def __init__(
        self,
        patterns: list[str],
        queue: asyncio.Queue[SleipnirEvent],
        reader_task: asyncio.Task[None],
        handler_task: asyncio.Task[None],
        remove_fn: Callable[[], None],
    ) -> None:
        super().__init__(patterns, queue, handler_task, remove_fn)
        self._reader_task = reader_task

    async def unsubscribe(self) -> None:
        if not self._active:
            return
        self._reader_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._reader_task
        await super().unsubscribe()


# ---------------------------------------------------------------------------
# Transport adapter
# ---------------------------------------------------------------------------


class RedisStreamsTransport(SleipnirPublisher, SleipnirSubscriber):
    """Combined publisher + subscriber backed by Redis Streams.

    :param url: Redis connection URL (e.g. ``redis://localhost:6379``).
    :param stream_prefix: Prefix for stream keys (default ``"sleipnir"``).
    :param maxlen: Approximate MAXLEN for each stream (default 10 000).
    :param replay_on_startup: If ``True`` consumer groups start from the
        beginning of the stream so new services receive historical events.
    :param consumer_group: Redis consumer group name for this service.
    :param ring_buffer_depth: Local asyncio queue depth per subscription.
    :param block_timeout_ms: Milliseconds to block in ``XREADGROUP`` per poll.
    :param replay_batch_size: Number of entries fetched per XREADGROUP call.
    :param _redis: Optional pre-built Redis client (injection point for tests).
    """

    def __init__(
        self,
        url: str = DEFAULT_REDIS_URL,
        stream_prefix: str = DEFAULT_STREAM_PREFIX,
        maxlen: int = DEFAULT_MAXLEN,
        replay_on_startup: bool = DEFAULT_REPLAY_ON_STARTUP,
        consumer_group: str = DEFAULT_CONSUMER_GROUP,
        ring_buffer_depth: int = DEFAULT_RING_BUFFER_DEPTH,
        block_timeout_ms: int = DEFAULT_BLOCK_TIMEOUT_MS,
        replay_batch_size: int = DEFAULT_REPLAY_BATCH_SIZE,
        _redis=None,
    ) -> None:
        if ring_buffer_depth < 1:
            raise ValueError(f"ring_buffer_depth must be >= 1, got {ring_buffer_depth}")
        self._url = url
        self._stream_prefix = stream_prefix
        self._maxlen = maxlen
        self._replay_on_startup = replay_on_startup
        self._consumer_group = consumer_group
        self._ring_buffer_depth = ring_buffer_depth
        self._block_timeout_ms = block_timeout_ms
        self._replay_batch_size = replay_batch_size
        self._redis = _redis
        self._subscriptions: list[_RedisSubscription] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> RedisStreamsTransport:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def start(self) -> None:
        """Open the Redis connection (no-op if a client was injected)."""
        if self._redis is not None:
            return
        if not _REDIS_AVAILABLE:  # pragma: no cover
            raise ImportError(
                "redis is not installed. Install it with: pip install 'volundr[redis]'"
            )
        self._redis = aioredis.from_url(self._url, decode_responses=False)

    async def stop(self) -> None:
        """Cancel all active subscriptions and close the Redis connection."""
        for sub in list(self._subscriptions):
            await sub.unsubscribe()
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    # ------------------------------------------------------------------
    # SleipnirPublisher
    # ------------------------------------------------------------------

    async def publish(self, event: SleipnirEvent) -> None:
        """Publish *event* to the appropriate Redis stream.

        Events with ``ttl <= 0`` are expired on arrival and never written.
        """
        if event.ttl is not None and event.ttl <= 0:
            logger.debug(
                "Dropping expired event %s (%s): ttl=%d",
                event.event_id,
                event.event_type,
                event.ttl,
            )
            return
        namespace = event.event_type.split(".")[0]
        stream = _stream_key(self._stream_prefix, namespace)
        data = {_PAYLOAD_FIELD: serialize(event, "json")}
        await self._redis.xadd(stream, data, maxlen=self._maxlen, approximate=True)

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        """Publish all *events* in iteration order using a pipeline."""
        async with self._redis.pipeline(transaction=False) as pipe:
            for event in events:
                if event.ttl is not None and event.ttl <= 0:
                    logger.debug(
                        "Dropping expired event %s (%s): ttl=%d",
                        event.event_id,
                        event.event_type,
                        event.ttl,
                    )
                    continue
                namespace = event.event_type.split(".")[0]
                stream = _stream_key(self._stream_prefix, namespace)
                data = {_PAYLOAD_FIELD: serialize(event, "json")}
                pipe.xadd(stream, data, maxlen=self._maxlen, approximate=True)
            await pipe.execute()

    # ------------------------------------------------------------------
    # SleipnirSubscriber
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        """Subscribe *handler* to events matching any pattern in *event_types*.

        Each call creates a dedicated consumer group so that multiple
        subscriptions on the same transport receive every event independently
        (fan-out semantics, matching the in-process bus behaviour).
        """
        streams = _streams_for_patterns(self._stream_prefix, event_types)
        # Unique group per subscription → fan-out; base group name scopes it
        # to this service so operators can inspect groups by service prefix.
        sub_id = uuid.uuid4().hex[:8]
        group_name = f"{self._consumer_group}:{sub_id}"
        consumer_name = f"{group_name}:worker"

        for stream in streams:
            await self._ensure_group(stream, group_name)

        queue: asyncio.Queue[SleipnirEvent] = asyncio.Queue(maxsize=self._ring_buffer_depth)

        reader_task = asyncio.create_task(
            self._consume(streams, list(event_types), group_name, consumer_name, queue),
            name=f"sleipnir-redis-reader-{sub_id}",
        )
        handler_task = asyncio.create_task(
            consume_queue(queue, handler),
            name=f"sleipnir-redis-handler-{sub_id}",
        )

        sub = _RedisSubscription(
            list(event_types),
            queue,
            reader_task,
            handler_task,
            lambda: self._remove_subscription(sub),
        )
        self._subscriptions.append(sub)
        return sub

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _remove_subscription(self, sub: _RedisSubscription) -> None:
        with suppress(ValueError):
            self._subscriptions.remove(sub)

    async def _ensure_group(self, stream: str, group_name: str) -> None:
        """Create *group_name* on *stream* if it does not already exist.

        Uses ``MKSTREAM`` so the stream is also auto-created on first use.
        The start ID is ``"0"`` (full replay) or ``"$"`` (new messages only)
        depending on :attr:`replay_on_startup`.
        """
        start_id = _ID_STREAM_START if self._replay_on_startup else _ID_STREAM_NEW
        try:
            await self._redis.xgroup_create(
                stream,
                group_name,
                start_id,
                mkstream=True,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _consume(
        self,
        streams: list[str],
        patterns: list[str],
        group_name: str,
        consumer_name: str,
        queue: asyncio.Queue[SleipnirEvent],
    ) -> None:
        """Reader loop: poll Redis streams and enqueue matching events."""
        stream_dict = {s: ">" for s in streams}

        while True:
            try:
                results = await self._redis.xreadgroup(
                    groupname=group_name,
                    consumername=consumer_name,
                    streams=stream_dict,
                    count=self._replay_batch_size,
                    block=self._block_timeout_ms,
                )
                if not results:
                    continue

                for stream_key_raw, messages in results:
                    stream_key_str = (
                        stream_key_raw.decode()
                        if isinstance(stream_key_raw, bytes)
                        else stream_key_raw
                    )
                    for msg_id, data in messages:
                        await self._handle_message(
                            stream_key_str,
                            msg_id,
                            data,
                            group_name,
                            patterns,
                            queue,
                        )

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Consumer loop error (streams=%s)", streams)
                await asyncio.sleep(self._block_timeout_ms / 1000)

    async def _handle_message(
        self,
        stream: str,
        msg_id: bytes | str,
        data: dict,
        group_name: str,
        patterns: list[str],
        queue: asyncio.Queue[SleipnirEvent],
    ) -> None:
        """Deserialise, filter, and enqueue a single stream message."""
        payload_bytes = data.get(_PAYLOAD_FIELD)
        if payload_bytes is None:
            await self._redis.xack(stream, group_name, msg_id)
            return

        try:
            event = deserialize(payload_bytes, "json")
        except Exception:
            logger.exception("Failed to deserialise event from stream %s msg %s", stream, msg_id)
            await self._redis.xack(stream, group_name, msg_id)
            return

        await self._redis.xack(stream, group_name, msg_id)

        if event.ttl is not None and event.ttl <= 0:
            return

        if not any(match_event_type(p, event.event_type) for p in patterns):
            return

        await enqueue_with_overflow(queue, event, self._ring_buffer_depth, logger)

    async def flush(self) -> None:
        """Wait until every queued event has been processed by its handler.

        Consistent with :meth:`InProcessBus.flush` — useful in tests after
        publishing to ensure delivery before asserting.
        """
        for sub in list(self._subscriptions):
            await sub._queue.join()


def redis_available() -> bool:
    """Return ``True`` if ``redis`` is installed and the adapter can be used."""
    return _REDIS_AVAILABLE

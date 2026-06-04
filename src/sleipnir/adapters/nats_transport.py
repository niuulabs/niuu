"""NATS JetStream transport adapter for Sleipnir.

Multi-node, production event transport using NATS JetStream (nats-py client).

Architecture
------------
- :class:`NatsPublisher` — connects to NATS and publishes events to JetStream.
- :class:`NatsSubscriber` — connects to NATS and subscribes from JetStream,
  with consumer group support and replay from offset.
- :class:`NatsTransport` — combined publisher + subscriber (two connections).
- :class:`NatsBridgeAdapter` — bridges a local transport (nng/in-process) and
  a NATS cluster simultaneously, with deduplication by *event_id*.

Subject mapping
---------------
Sleipnir event types use dot-notation (e.g. ``ravn.tool.complete``).
They map directly to NATS subjects with a configurable prefix::

    event_type "ravn.tool.complete" → subject "sleipnir.ravn.tool.complete"

Subscription pattern wildcards translate to NATS subject wildcards::

    "*"                → "sleipnir.>"              (all events)
    "ravn.*"           → "sleipnir.ravn.>"          (namespace wildcard)
    "ravn.tool.*"      → "sleipnir.ravn.tool.>"     (sub-namespace wildcard)
    "ravn.tool.complete" → "sleipnir.ravn.tool.complete"  (exact match)

Complex patterns containing ``?`` or ``[`` fall back to ``"{prefix}.>"`` with
application-level :func:`~sleipnir.domain.events.match_event_type` filtering.

JetStream stream
----------------
All events are persisted in a single stream (``sleipnir`` by default) that
covers all subjects under the configured prefix.  Configurable retention
policies: ``limits`` (default), ``interest``, ``workqueue``.

Consumer groups
---------------
Pass ``consumer_group`` to :class:`NatsSubscriber` (or :class:`NatsTransport`)
to create a durable, queue-group consumer.  Multiple replicas sharing the same
group name receive each message exactly once, distributing load across the
group.

Replay
------
Pass ``replay_from_sequence`` or ``replay_from_time`` to
:class:`NatsSubscriber` for crash recovery.  On restart the subscriber resumes
from the specified position in the stream, reprocessing missed events.

Configuration example
---------------------
::

    sleipnir:
      transport: "nats"
      servers: ["nats://nats:4222"]
      stream:
        name: "sleipnir"
        retention: "limits"        # or "interest", "workqueue"
        max_age: "7d"
        max_bytes: "1GB"
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from collections import deque
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import nats
    import nats.js.api as js_api

    _NATS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _NATS_AVAILABLE = False

from sleipnir.adapters._subscriber_support import (
    _BaseSubscription,
    consume_queue,
    enqueue_with_overflow,
)
from sleipnir.adapters.serialization import deserialize, serialize
from sleipnir.domain.events import SleipnirEvent, match_event_type
from sleipnir.ports.events import EventHandler, SleipnirPublisher, SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults (no magic numbers — all overridable via constructor kwargs / config)
# ---------------------------------------------------------------------------

DEFAULT_SERVERS: list[str] = ["nats://localhost:4222"]
DEFAULT_STREAM_NAME = "sleipnir"
DEFAULT_SUBJECT_PREFIX = "sleipnir"
DEFAULT_RETENTION = "limits"

#: Default maximum stream age — 7 days in seconds.
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600

#: Default maximum stream size — 1 GiB in bytes.
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024

#: Per-subscriber in-process ring buffer depth (events).
DEFAULT_RING_BUFFER_DEPTH = 1000

#: NATS connection timeout in seconds.
DEFAULT_CONNECT_TIMEOUT_S = 10.0

#: Maximum reconnect attempts before giving up (-1 = unlimited).
DEFAULT_MAX_RECONNECT_ATTEMPTS = 60


def _build_tls_context(
    *,
    tls_ca_file: str = "",
    tls_cert_file: str = "",
    tls_key_file: str = "",
    tls_insecure_skip_verify: bool = False,
) -> ssl.SSLContext | None:
    """Build an SSL context for NATS TLS, or return None when TLS files are unset."""
    if (
        not tls_ca_file
        and not tls_cert_file
        and not tls_key_file
        and not tls_insecure_skip_verify
    ):
        return None
    context = ssl.create_default_context(cafile=tls_ca_file or None)
    if tls_insecure_skip_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if tls_cert_file or tls_key_file:
        context.load_cert_chain(certfile=tls_cert_file, keyfile=tls_key_file or None)
    return context


def _read_optional_file(path: str) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8").strip()


def _connect_options(
    *,
    tls_ca_file: str = "",
    tls_cert_file: str = "",
    tls_key_file: str = "",
    tls_hostname: str = "",
    tls_handshake_first: bool = False,
    tls_insecure_skip_verify: bool = False,
    user: str = "",
    password: str = "",
    token: str = "",
    nkeys_seed_file: str = "",
    nkeys_seed: str = "",
) -> dict[str, Any]:
    options: dict[str, Any] = {}
    tls_context = _build_tls_context(
        tls_ca_file=tls_ca_file,
        tls_cert_file=tls_cert_file,
        tls_key_file=tls_key_file,
        tls_insecure_skip_verify=tls_insecure_skip_verify,
    )
    if tls_context is not None:
        options["tls"] = tls_context
    if tls_hostname:
        options["tls_hostname"] = tls_hostname
    if tls_handshake_first:
        options["tls_handshake_first"] = True
    if user:
        options["user"] = user
    if password:
        options["password"] = password
    if token:
        options["token"] = token
    if nkeys_seed_file:
        options["nkeys_seed"] = nkeys_seed_file
    elif nkeys_seed:
        options["nkeys_seed_str"] = nkeys_seed
    return options

#: Maximum number of event IDs held in the deduplication cache.
DEFAULT_DEDUP_CACHE_SIZE = 10_000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def nats_available() -> bool:
    """Return ``True`` if nats-py is installed and the NATS adapter can be used."""
    return _NATS_AVAILABLE


def _require_nats() -> None:
    if not _NATS_AVAILABLE:  # pragma: no cover
        raise ImportError(
            "nats-py is required for the NATS transport adapter. "
            "Install it with: pip install nats-py"
        )


def _nats_subject_for_event(event_type: str, prefix: str) -> str:
    """Return the NATS publish subject for *event_type*."""
    return f"{prefix}.{event_type}"


def _nats_subjects_for_patterns(patterns: list[str], prefix: str) -> list[str]:
    """Translate Sleipnir fnmatch patterns to NATS subject filter strings.

    Rules
    -----
    - ``"*"`` → ``"{prefix}.>"``  (subscribe-all short-circuits immediately)
    - ``"ravn.*"`` → ``"{prefix}.ravn.>"``  (namespace wildcard)
    - ``"ravn.tool.*"`` → ``"{prefix}.ravn.tool.>"``  (sub-namespace wildcard)
    - ``"ravn.tool.complete"`` → ``"{prefix}.ravn.tool.complete"``  (exact)
    - Any other pattern containing ``*``, ``?`` or ``[`` → ``"{prefix}.>"``
      (receive all messages; application-level :func:`match_event_type` filters)
    """
    subjects: list[str] = []
    for pattern in patterns:
        if pattern == "*":
            return [f"{prefix}.>"]
        if pattern.endswith(".*"):
            # "ravn.*" → strip the star, append ">" → "ravn.>"
            subjects.append(f"{prefix}.{pattern[:-1]}>")
        elif any(c in pattern for c in ("*", "?", "[")):
            # Complex wildcard not expressible as a NATS prefix — subscribe all.
            return [f"{prefix}.>"]
        else:
            subjects.append(f"{prefix}.{pattern}")
    return subjects or [f"{prefix}.>"]


def _parse_retention(retention_str: str) -> Any:
    """Convert a string retention policy to the nats-py ``RetentionPolicy`` enum."""
    match retention_str:
        case "limits":
            return js_api.RetentionPolicy.LIMITS
        case "interest":
            return js_api.RetentionPolicy.INTEREST
        case "workqueue":
            return js_api.RetentionPolicy.WORK_QUEUE
        case _:
            raise ValueError(
                f"Unknown retention policy: {retention_str!r}. "
                "Use 'limits', 'interest', or 'workqueue'."
            )
    raise AssertionError("Unreachable _parse_retention fallthrough")


def _decode_nats_message(data: bytes) -> SleipnirEvent | None:
    """Decode a NATS message payload (msgpack-serialised :class:`SleipnirEvent`).

    Returns ``None`` and logs a warning on malformed or undeserializable input.
    """
    try:
        return deserialize(data)
    except Exception:
        logger.exception("NatsTransport: deserialization failed, message dropped")
        return None


async def _ensure_stream(
    js: Any,
    stream_name: str,
    subject_prefix: str,
    retention: str,
    max_age_seconds: int,
    max_bytes: int,
) -> None:
    """Ensure the JetStream stream exists, creating it if not present."""
    config = js_api.StreamConfig(
        name=stream_name,
        subjects=[f"{subject_prefix}.>"],
        retention=_parse_retention(retention),
        max_age=max_age_seconds,  # nats-py converts to nanoseconds internally
        max_bytes=max_bytes,
        storage=js_api.StorageType.FILE,
        num_replicas=1,
    )
    try:
        await js.stream_info(stream_name)
        logger.debug("NATS stream %r already exists", stream_name)
    except Exception:
        logger.debug("stream_info(%r) failed, attempting creation", stream_name, exc_info=True)
        try:
            await js.add_stream(config=config)
            logger.info("NATS stream %r created", stream_name)
        except Exception:
            logger.warning(
                "NATS stream %r could not be created; it may already exist",
                stream_name,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Deduplication cache
# ---------------------------------------------------------------------------


class _DeduplicationCache:
    """Bounded LRU-evicting set for tracking seen event IDs.

    Once *max_size* entries are held, the oldest entry is evicted before a
    new one is inserted, keeping memory usage constant.
    """

    def __init__(self, max_size: int) -> None:
        self._seen: set[str] = set()
        self._order: deque[str] = deque()
        self._max_size = max_size

    def is_seen(self, event_id: str) -> bool:
        return event_id in self._seen

    def mark_seen(self, event_id: str) -> None:
        if event_id in self._seen:
            return
        self._seen.add(event_id)
        self._order.append(event_id)
        if len(self._order) > self._max_size:
            evicted = self._order.popleft()
            self._seen.discard(evicted)


# ---------------------------------------------------------------------------
# NatsPublisher
# ---------------------------------------------------------------------------


class NatsPublisher(SleipnirPublisher):
    """NATS JetStream publisher.

    Connects to NATS, ensures the configured stream exists, and publishes
    :class:`~sleipnir.domain.events.SleipnirEvent` objects serialised with
    msgpack to ``{subject_prefix}.{event_type}``.

    Usage::

        pub = NatsPublisher(servers=["nats://nats:4222"])
        async with pub:
            await pub.publish(event)
    """

    def __init__(
        self,
        servers: list[str] | None = None,
        stream_name: str = DEFAULT_STREAM_NAME,
        subject_prefix: str = DEFAULT_SUBJECT_PREFIX,
        retention: str = DEFAULT_RETENTION,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS,
        ensure_stream: bool = True,
        tls_ca_file: str = "",
        tls_cert_file: str = "",
        tls_key_file: str = "",
        tls_hostname: str = "",
        tls_handshake_first: bool = False,
        tls_insecure_skip_verify: bool = False,
        user: str = "",
        password: str = "",
        token: str = "",
        nkeys_seed_file: str = "",
        nkeys_seed: str = "",
    ) -> None:
        _require_nats()
        self._servers = servers or DEFAULT_SERVERS
        self._stream_name = stream_name
        self._subject_prefix = subject_prefix
        self._retention = retention
        self._max_age_seconds = max_age_seconds
        self._max_bytes = max_bytes
        self._connect_timeout_s = connect_timeout_s
        self._max_reconnect_attempts = max_reconnect_attempts
        self._ensure_stream = ensure_stream
        self._connect_options = _connect_options(
            tls_ca_file=tls_ca_file,
            tls_cert_file=tls_cert_file,
            tls_key_file=tls_key_file,
            tls_hostname=tls_hostname,
            tls_handshake_first=tls_handshake_first,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
            user=user,
            password=password,
            token=token,
            nkeys_seed_file=nkeys_seed_file,
            nkeys_seed=nkeys_seed,
        )
        self._client: Any = None
        self._js: Any = None

    async def start(self) -> None:
        """Connect to NATS and ensure the JetStream stream exists."""
        self._client = await nats.connect(
            servers=self._servers,
            connect_timeout=self._connect_timeout_s,
            max_reconnect_attempts=self._max_reconnect_attempts,
            **self._connect_options,
        )
        self._js = self._client.jetstream()
        if self._ensure_stream:
            await _ensure_stream(
                self._js,
                self._stream_name,
                self._subject_prefix,
                self._retention,
                self._max_age_seconds,
                self._max_bytes,
            )
        logger.debug(
            "NatsPublisher: connected to %s, stream=%r",
            self._servers,
            self._stream_name,
        )

    async def stop(self) -> None:
        """Drain and close the NATS connection."""
        if self._client is not None:
            with suppress(Exception):
                await self._client.drain()
            with suppress(Exception):
                await self._client.close()
            self._client = None
            self._js = None
            logger.debug("NatsPublisher: closed")

    async def __aenter__(self) -> NatsPublisher:
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
        if self._js is None:
            raise RuntimeError("NatsPublisher is not started. Call start() first.")
        subject = _nats_subject_for_event(event.event_type, self._subject_prefix)
        payload = serialize(event)
        await self._js.publish(subject, payload)

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        for event in events:
            await self.publish(event)


# ---------------------------------------------------------------------------
# NatsSubscriber
# ---------------------------------------------------------------------------


class NatsSubscriber(SleipnirSubscriber):
    """NATS JetStream subscriber with consumer group and replay support.

    *consumer_group* enables parallel processing across replicas: all
    instances sharing the same group name receive each message exactly once
    (load distribution via JetStream queue groups).

    *replay_from_sequence* or *replay_from_time* enable crash recovery: on
    restart the subscriber resumes from the specified position in the stream,
    reprocessing any events that were missed.

    Usage::

        sub = NatsSubscriber(
            servers=["nats://nats:4222"],
            consumer_group="my-service",
        )
        async with sub:
            handle = await sub.subscribe(["ravn.*"], my_handler)
            ...
            await handle.unsubscribe()
    """

    def __init__(
        self,
        servers: list[str] | None = None,
        stream_name: str = DEFAULT_STREAM_NAME,
        subject_prefix: str = DEFAULT_SUBJECT_PREFIX,
        retention: str = DEFAULT_RETENTION,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        consumer_group: str | None = None,
        replay_from_sequence: int | None = None,
        replay_from_time: datetime | None = None,
        ring_buffer_depth: int = DEFAULT_RING_BUFFER_DEPTH,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS,
        ensure_stream: bool = True,
        tls_ca_file: str = "",
        tls_cert_file: str = "",
        tls_key_file: str = "",
        tls_hostname: str = "",
        tls_handshake_first: bool = False,
        tls_insecure_skip_verify: bool = False,
        user: str = "",
        password: str = "",
        token: str = "",
        nkeys_seed_file: str = "",
        nkeys_seed: str = "",
    ) -> None:
        _require_nats()
        if ring_buffer_depth < 1:
            raise ValueError(f"ring_buffer_depth must be >= 1, got {ring_buffer_depth}")
        self._servers = servers or DEFAULT_SERVERS
        self._stream_name = stream_name
        self._subject_prefix = subject_prefix
        self._retention = retention
        self._max_age_seconds = max_age_seconds
        self._max_bytes = max_bytes
        self._consumer_group = consumer_group
        self._replay_from_sequence = replay_from_sequence
        self._replay_from_time = replay_from_time
        self._ring_buffer_depth = ring_buffer_depth
        self._connect_timeout_s = connect_timeout_s
        self._max_reconnect_attempts = max_reconnect_attempts
        self._ensure_stream = ensure_stream
        self._connect_options = _connect_options(
            tls_ca_file=tls_ca_file,
            tls_cert_file=tls_cert_file,
            tls_key_file=tls_key_file,
            tls_hostname=tls_hostname,
            tls_handshake_first=tls_handshake_first,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
            user=user,
            password=password,
            token=token,
            nkeys_seed_file=nkeys_seed_file,
            nkeys_seed=nkeys_seed,
        )
        self._client: Any = None
        self._js: Any = None
        self._nats_subs: list[Any] = []
        self._subscriptions: list[_BaseSubscription] = []
        self._running = False

    async def start(self) -> None:
        """Connect to NATS and ensure the JetStream stream exists."""
        self._client = await nats.connect(
            servers=self._servers,
            connect_timeout=self._connect_timeout_s,
            max_reconnect_attempts=self._max_reconnect_attempts,
            **self._connect_options,
        )
        self._js = self._client.jetstream()
        if self._ensure_stream:
            await _ensure_stream(
                self._js,
                self._stream_name,
                self._subject_prefix,
                self._retention,
                self._max_age_seconds,
                self._max_bytes,
            )
        self._running = True
        logger.debug(
            "NatsSubscriber: connected to %s, stream=%r, group=%r",
            self._servers,
            self._stream_name,
            self._consumer_group,
        )

    async def stop(self) -> None:
        """Unsubscribe all NATS subscriptions and close the connection."""
        self._running = False
        for nats_sub in self._nats_subs:
            with suppress(Exception):
                await nats_sub.unsubscribe()
        self._nats_subs.clear()
        if self._client is not None:
            with suppress(Exception):
                await self._client.drain()
            with suppress(Exception):
                await self._client.close()
            self._client = None
            self._js = None
        logger.debug("NatsSubscriber: closed")

    async def __aenter__(self) -> NatsSubscriber:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        if self._js is None:
            raise RuntimeError("NatsSubscriber is not started. Call start() first.")

        queue: asyncio.Queue[SleipnirEvent] = asyncio.Queue(maxsize=self._ring_buffer_depth)
        consumer_task = asyncio.create_task(consume_queue(queue, handler))
        sub = _BaseSubscription(
            list(event_types),
            queue,
            consumer_task,
            lambda: self._remove_subscription(sub),
        )
        self._subscriptions.append(sub)

        config = self._build_consumer_config()
        subjects = _nats_subjects_for_patterns(event_types, self._subject_prefix)

        for subject in subjects:
            nats_sub = await self._create_nats_subscription(subject, event_types, sub, config)
            self._nats_subs.append(nats_sub)

        return sub

    async def _create_nats_subscription(
        self,
        subject: str,
        patterns: list[str],
        sub: _BaseSubscription,
        config: Any,
    ) -> Any:
        """Create a JetStream push subscription for *subject*."""

        async def _on_message(msg: Any) -> None:
            try:
                if not self._running:
                    return
                event = _decode_nats_message(msg.data)
                if event is None:
                    return
                if event.ttl is not None and event.ttl <= 0:
                    return
                if not any(match_event_type(p, event.event_type) for p in patterns):
                    return
                await enqueue_with_overflow(sub._queue, event, self._ring_buffer_depth, logger)
            finally:
                with suppress(Exception):
                    await msg.ack()

        kwargs: dict[str, Any] = {"stream": self._stream_name, "config": config}
        if self._consumer_group is not None:
            kwargs["durable"] = self._consumer_group
            kwargs["queue"] = self._consumer_group

        return await self._js.subscribe(subject, cb=_on_message, **kwargs)

    def _build_consumer_config(self) -> Any:
        """Build the :class:`~nats.js.api.ConsumerConfig` for this subscriber.

        - *replay_from_sequence* → ``DeliverPolicy.BY_START_SEQUENCE``
        - *replay_from_time* → ``DeliverPolicy.BY_START_TIME``
        - Otherwise → ``DeliverPolicy.NEW`` (only future messages)
        """
        if self._replay_from_sequence is not None:
            return js_api.ConsumerConfig(
                deliver_policy=js_api.DeliverPolicy.BY_START_SEQUENCE,
                opt_start_seq=self._replay_from_sequence,
                ack_policy=js_api.AckPolicy.EXPLICIT,
            )
        if self._replay_from_time is not None:
            return js_api.ConsumerConfig(
                deliver_policy=js_api.DeliverPolicy.BY_START_TIME,
                opt_start_time=self._replay_from_time,
                ack_policy=js_api.AckPolicy.EXPLICIT,
            )
        return js_api.ConsumerConfig(
            deliver_policy=js_api.DeliverPolicy.NEW,
            ack_policy=js_api.AckPolicy.EXPLICIT,
        )

    def _remove_subscription(self, sub: _BaseSubscription) -> None:
        with suppress(ValueError):
            self._subscriptions.remove(sub)


# ---------------------------------------------------------------------------
# NatsTransport — combined publisher + subscriber
# ---------------------------------------------------------------------------


class NatsTransport(SleipnirPublisher, SleipnirSubscriber):
    """Combined NATS JetStream publisher + subscriber.

    A convenience wrapper that creates a :class:`NatsPublisher` and a
    :class:`NatsSubscriber` backed by separate NATS connections.

    Usage::

        transport = NatsTransport(servers=["nats://nats:4222"])
        async with transport:
            handle = await transport.subscribe(["ravn.*"], my_handler)
            await transport.publish(event)
            await handle.unsubscribe()
    """

    def __init__(
        self,
        servers: list[str] | None = None,
        stream_name: str = DEFAULT_STREAM_NAME,
        subject_prefix: str = DEFAULT_SUBJECT_PREFIX,
        retention: str = DEFAULT_RETENTION,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        consumer_group: str | None = None,
        replay_from_sequence: int | None = None,
        replay_from_time: datetime | None = None,
        ring_buffer_depth: int = DEFAULT_RING_BUFFER_DEPTH,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS,
        ensure_stream: bool = True,
        tls_ca_file: str = "",
        tls_cert_file: str = "",
        tls_key_file: str = "",
        tls_hostname: str = "",
        tls_handshake_first: bool = False,
        tls_insecure_skip_verify: bool = False,
        user: str = "",
        password: str = "",
        token: str = "",
        nkeys_seed_file: str = "",
        nkeys_seed: str = "",
    ) -> None:
        _require_nats()
        self._publisher = NatsPublisher(
            servers=servers,
            stream_name=stream_name,
            subject_prefix=subject_prefix,
            retention=retention,
            max_age_seconds=max_age_seconds,
            max_bytes=max_bytes,
            connect_timeout_s=connect_timeout_s,
            max_reconnect_attempts=max_reconnect_attempts,
            ensure_stream=ensure_stream,
            tls_ca_file=tls_ca_file,
            tls_cert_file=tls_cert_file,
            tls_key_file=tls_key_file,
            tls_hostname=tls_hostname,
            tls_handshake_first=tls_handshake_first,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
            user=user,
            password=password,
            token=token,
            nkeys_seed_file=nkeys_seed_file,
            nkeys_seed=nkeys_seed,
        )
        self._subscriber = NatsSubscriber(
            servers=servers,
            stream_name=stream_name,
            subject_prefix=subject_prefix,
            retention=retention,
            max_age_seconds=max_age_seconds,
            max_bytes=max_bytes,
            consumer_group=consumer_group,
            replay_from_sequence=replay_from_sequence,
            replay_from_time=replay_from_time,
            ring_buffer_depth=ring_buffer_depth,
            connect_timeout_s=connect_timeout_s,
            max_reconnect_attempts=max_reconnect_attempts,
            ensure_stream=ensure_stream,
            tls_ca_file=tls_ca_file,
            tls_cert_file=tls_cert_file,
            tls_key_file=tls_key_file,
            tls_hostname=tls_hostname,
            tls_handshake_first=tls_handshake_first,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
            user=user,
            password=password,
            token=token,
            nkeys_seed_file=nkeys_seed_file,
            nkeys_seed=nkeys_seed,
        )

    async def start(self) -> None:
        """Connect publisher then subscriber."""
        await self._publisher.start()
        await self._subscriber.start()

    async def stop(self) -> None:
        """Graceful shutdown: stop subscriber first, then publisher."""
        await self._subscriber.stop()
        await self._publisher.stop()

    async def __aenter__(self) -> NatsTransport:
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


# ---------------------------------------------------------------------------
# NatsBridgeAdapter — bridge between local transport and NATS cluster
# ---------------------------------------------------------------------------


class _BridgeSubscription(Subscription):
    """Subscription that spans both a local and a NATS subscription."""

    def __init__(self, local_sub: Subscription, nats_sub: Subscription) -> None:
        self._local_sub = local_sub
        self._nats_sub = nats_sub

    async def unsubscribe(self) -> None:
        with suppress(Exception):
            await self._local_sub.unsubscribe()
        with suppress(Exception):
            await self._nats_sub.unsubscribe()


class NatsBridgeAdapter(SleipnirPublisher, SleipnirSubscriber):
    """Bridge between a local transport (nng/in-process) and NATS cluster.

    **Publishing**: events are forwarded to *both* the local transport and the
    NATS cluster so that both local and remote consumers receive them.

    **Subscribing**: the bridge subscribes to both transports and deduplicates
    by *event_id* so that each logical event is delivered to handlers exactly
    once, regardless of which transport delivered it first.

    The deduplication cache is bounded to *dedup_cache_size* entries.  When
    full, the oldest entry is evicted (LRU semantics).

    Usage::

        local = NngTransport("ipc:///tmp/sleipnir.sock")
        nats_t = NatsTransport(servers=["nats://nats:4222"])
        bridge = NatsBridgeAdapter(
            local_publisher=local,
            local_subscriber=local,
            nats_publisher=nats_t,
            nats_subscriber=nats_t,
        )
        async with local, nats_t:
            handle = await bridge.subscribe(["ravn.*"], my_handler)
            await bridge.publish(event)
            await handle.unsubscribe()
    """

    def __init__(
        self,
        local_publisher: SleipnirPublisher,
        local_subscriber: SleipnirSubscriber,
        nats_publisher: SleipnirPublisher,
        nats_subscriber: SleipnirSubscriber,
        dedup_cache_size: int = DEFAULT_DEDUP_CACHE_SIZE,
    ) -> None:
        self._local_pub = local_publisher
        self._local_sub = local_subscriber
        self._nats_pub = nats_publisher
        self._nats_sub = nats_subscriber
        self._dedup = _DeduplicationCache(dedup_cache_size)

    async def publish(self, event: SleipnirEvent) -> None:
        """Publish to both local and NATS transports."""
        await self._local_pub.publish(event)
        await self._nats_pub.publish(event)

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        for event in events:
            await self.publish(event)

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        """Subscribe to both transports, delivering each event exactly once."""

        async def _dedup_handler(event: SleipnirEvent) -> None:
            if self._dedup.is_seen(event.event_id):
                return
            self._dedup.mark_seen(event.event_id)
            await handler(event)

        local_sub = await self._local_sub.subscribe(event_types, _dedup_handler)
        nats_sub = await self._nats_sub.subscribe(event_types, _dedup_handler)
        return _BridgeSubscription(local_sub, nats_sub)

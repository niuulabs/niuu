"""NATS JetStream transport adapter for Sleipnir.

Multi-node, production event transport using NATS JetStream (nats-py client).

Architecture
------------
- :class:`NatsPublisher` — connects to NATS and publishes events to JetStream.
- :class:`NatsCorePublisher` — publishes live control events over core NATS.
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

Delivery guarantee
------------------
JetStream subscriptions are **at-least-once**; handlers must be idempotent.
A message is acknowledged only after the subscriber's handler has returned.
If the handler raises, the message is nak'd with a delay from ``nak_backoff_s``
and JetStream redelivers it.  After ``max_deliver`` failed deliveries it is
published to ``{prefix}.system.dlq.message`` and terminated.  It is never
acked unless it was handled or dead-lettered.  While a message waits in the
subscription's bounded queue or runs in its handler, the subscriber sends
in-progress pings every ``ack_progress_interval_s``.  Slow handling therefore
never triggers redelivery, but a crashed process stops pinging, and JetStream
redelivers after ``ack_wait_s``.  A full queue blocks the NATS callback and
withholds acks, and ``max_ack_pending`` stops JetStream pushing more.  Nothing
is dropped to make room.

Core NATS subscriptions (``core_subscriptions`` or ``stream_name: core``)
have no server-side log and no acks, so they are at-most-once.

Consumer and stream recovery
-----------------------------
If a JetStream store is reset (streams recreated empty), a subscriber's
existing consumers vanish with it and nats-py does not notice: the push
subscription just goes quiet.  Each JetStream subscription is polled every
``consumer_health_check_interval_s`` (plus jitter) via
``PushSubscription.consumer_info()``.  A ``404`` (consumer or stream not
found) is logged at ERROR and triggers recovery: the consumer is recreated
with the same durable name and config once its stream exists, retried with
``consumer_recovery_backoff_s`` while the stream is absent, replaying from a
bounded window (``consumer_recovery_max_replay_window_s``) so the gap is not
missed.  Any other health-check failure (timeout, connectivity) is logged at
WARNING (escalating to ERROR after ``consumer_check_failure_threshold``
consecutive failures) and left alone — only a confirmed 404 is treated as
loss, so a transient error never triggers a spurious consumer recreation.

A durable created (or recreated) by this code always gets a deterministic
push delivery subject — ``_INBOX.sleipnir.<durable>``, never a subject any
stream's ``{prefix}.>`` filter would capture, which the server otherwise
rejects as a "consumer deliver subject forms a cycle" — so every replica
that *creates* the durable lands on the same inbox.  An older durable
(created before this existed, or left with whatever random inbox nats-py
assigned) keeps that inbox until it is next recreated; this code never
rewrites a durable's config in place, because the server refuses to change a
push durable's deliver_subject while any client is still subscribed to its
current one (``400 err_code=10013 "consumer name already in use"``,
confirmed against server 2.14) — every replica already is, at startup, so a
write would simply fail.  Instead, a health check compares the durable's
live ``deliver_subject``/``deliver_group`` against what *this* subscription
is actually bound to, and on a mismatch resubscribes: nats-py binds to
whatever the server has right now, which fixes the deaf-replica case (one
replica recreated the durable and moved everyone else's inbox from under
them) with no server write.  Each watch is health-checked independently, in
its own task, so a slow recovery or rebind on one watch never delays the
others in the same subscription.

Consumer groups
---------------
Pass ``consumer_group`` to :class:`NatsSubscriber` (or :class:`NatsTransport`)
to create a durable, queue-group consumer.  Replicas sharing the same group
name split the messages between them: each message goes to one member at a
time, and to another member if the first fails or dies before acking.

A durable consumer that already exists keeps its server-side ``ack_wait``,
``max_deliver`` and ``max_ack_pending``.  nats-py binds to it without
reconfiguring it.  The subscriber enforces ``max_deliver`` and the DLQ itself,
so dead-lettering works on old durables too.  Apply the other settings with
``nats consumer edit`` or by recreating the durable.

Replay
------
Pass ``replay_from_sequence`` or ``replay_from_time`` to
:class:`NatsSubscriber` to choose where a new consumer starts in the stream.
Recovery from a crash does not depend on it: unacknowledged messages are
redelivered to a durable consumer group regardless.

Configuration example
---------------------
::

    sleipnir:
      transport: "nats"
      servers: ["nats://nats:4222"]
      stream:
        name: "sleipnir"
        jetstream_domain: ""       # optional; empty uses the server default
        retention: "limits"        # or "interest", "workqueue"
        max_age: "7d"
        max_bytes: "1GB"
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import ssl
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import ParseResult, urlparse
from urllib.request import getproxies

try:
    import nats
    import nats.js.api as js_api
    import nats.js.errors as js_errors
    from nats.aio.client import DEFAULT_BUFFER_SIZE
    from nats.aio.client import Client as NatsClient
    from nats.aio.transport import TcpTransport

    _NATS_AVAILABLE = True
except ImportError:  # pragma: no cover
    DEFAULT_BUFFER_SIZE = 0
    NatsClient = object  # type: ignore[assignment,misc]
    TcpTransport = object  # type: ignore[assignment,misc]
    js_errors = None  # type: ignore[assignment]
    _NATS_AVAILABLE = False

from sleipnir.adapters._subscriber_support import (
    Delivery,
    _BaseSubscription,
    consume_deliveries,
)
from sleipnir.adapters.serialization import deserialize, serialize
from sleipnir.domain import registry
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

#: Per-subscription queue depth (events).  A full queue blocks the NATS
#: callback and withholds acks; it never drops events.
DEFAULT_RING_BUFFER_DEPTH = 1000

#: JetStream deliveries of one message before it is dead-lettered.
DEFAULT_MAX_DELIVER = 5

#: Seconds JetStream waits for an ack or in-progress ping before redelivering.
DEFAULT_ACK_WAIT_S = 30.0

#: Seconds between in-progress pings for received but unsettled messages.
#: Must be shorter than the ack wait.
DEFAULT_ACK_PROGRESS_INTERVAL_S = 10.0

#: Redelivery delay (seconds) after the 1st, 2nd, … failed delivery; the last
#: entry repeats for any later attempt.
DEFAULT_NAK_BACKOFF_S: tuple[float, ...] = (1.0, 5.0, 30.0, 60.0)

#: Seconds between checks that each subscription's JetStream consumer (and
#: its stream) still exists.  nats-py's push-subscription heartbeats do not
#: surface a missed-heartbeat signal to the application for non-ordered
#: consumers, so this polls ``PushSubscription.consumer_info()`` instead —
#: the same JetStream API call nats-py itself uses to bind to a durable.
DEFAULT_CONSUMER_HEALTH_CHECK_INTERVAL_S = 15.0

#: Recovery retry delay (seconds) after the 1st, 2nd, … failed attempt to
#: recreate a consumer whose stream is still absent; the last entry repeats.
DEFAULT_CONSUMER_RECOVERY_BACKOFF_S: tuple[float, ...] = (1.0, 5.0, 15.0, 30.0)

#: Consecutive non-404 health-check failures before escalating the log from
#: WARNING to ERROR.  Every check failing (wrong credentials, network split)
#: must become loud even though no single failure is a confirmed loss.
DEFAULT_CONSUMER_CHECK_FAILURE_THRESHOLD = 3

#: Maximum random jitter (seconds) added on top of the health-check interval
#: so many subscriptions (or replicas) don't all poll JetStream in lockstep.
DEFAULT_CONSUMER_HEALTH_CHECK_JITTER_S = 3.0

#: Upper bound (seconds) on how far back a recreated consumer replays from,
#: regardless of how long its watch's last_healthy_at has been stale — caps
#: the replay burst after a subscription was unhealthy for a long time.
DEFAULT_CONSUMER_RECOVERY_MAX_REPLAY_WINDOW_S = 3600.0

#: Consecutive "consumer already exists" replies (a concurrent create by
#: another replica winning the race) tolerated with a short, immediate-ish
#: retry before falling back to the normal failure/backoff path.  Bounds
#: what would otherwise be an unbounded fast retry loop.
DEFAULT_CONSUMER_ALREADY_EXISTS_RETRY_LIMIT = 5

#: NATS connection timeout in seconds.
DEFAULT_CONNECT_TIMEOUT_S = 10.0

#: Maximum reconnect attempts before giving up (-1 = unlimited).
DEFAULT_MAX_RECONNECT_ATTEMPTS = 60

#: Hard deadline for one JetStream publish (seconds).
DEFAULT_PUBLISH_TIMEOUT_S = 10.0


def _sandbox_proxy_url(explicit: str = "") -> str:
    """Return the configured or operating-system CONNECT proxy, if any."""
    if explicit:
        return explicit
    proxies = getproxies()
    return str(proxies.get("all") or proxies.get("https") or proxies.get("http") or "").strip()


class _HttpConnectTcpTransport(TcpTransport):
    """nats-py TCP transport tunneled through an HTTP CONNECT proxy."""

    def __init__(self, proxy_url: str) -> None:
        super().__init__()
        proxy = urlparse(proxy_url)
        if proxy.scheme != "http" or not proxy.hostname:
            raise ValueError("NATS proxy URL must use http:// with a hostname")
        if proxy.username or proxy.password:
            raise ValueError("Authenticated NATS proxy URLs are not supported")
        self._proxy_host = proxy.hostname
        self._proxy_port = proxy.port or 80

    async def connect(self, uri: ParseResult, buffer_size: int, connect_timeout: int) -> None:
        host = uri.hostname
        port = uri.port
        if not host or not port:
            raise ValueError("NATS server URL must include a hostname and port")
        authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host=self._proxy_host,
                port=self._proxy_port,
                limit=buffer_size,
            ),
            connect_timeout,
        )
        writer.write(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode("ascii"))
        await writer.drain()
        try:
            response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), connect_timeout)
        except Exception:
            writer.close()
            await writer.wait_closed()
            raise
        status_line = response.split(b"\r\n", 1)[0]
        parts = status_line.split(b" ", 2)
        if len(parts) < 2 or parts[1] != b"200":
            writer.close()
            await writer.wait_closed()
            detail = status_line.decode("ascii", errors="replace")
            raise OSError(f"NATS proxy CONNECT failed: {detail}")
        self._bare_io_reader = self._io_reader = reader
        self._bare_io_writer = self._io_writer = writer


class _HttpConnectNatsClient(NatsClient):
    """nats-py client that preserves the CONNECT transport before it is connected."""

    def __init__(self, proxy_url: str) -> None:
        _require_nats()
        super().__init__()
        self._proxy_url = proxy_url

    async def _connect_to_server(self, server: Any) -> None:
        server.last_attempt = time.monotonic()
        if not self._transport:
            if server.uri.scheme in ("ws", "wss"):
                raise ValueError("NATS WebSocket URLs cannot use the TCP CONNECT transport")
            self._transport = _HttpConnectTcpTransport(self._proxy_url)
        await self._transport.connect(
            server.uri,
            buffer_size=DEFAULT_BUFFER_SIZE,
            connect_timeout=self.options["connect_timeout"],
        )


async def _connect_nats(
    *,
    servers: list[str],
    connect_timeout: float,
    max_reconnect_attempts: int,
    proxy_url: str,
    options: dict[str, Any],
) -> Any:
    _require_nats()
    resolved_proxy = _sandbox_proxy_url(proxy_url)
    if not resolved_proxy:
        return await nats.connect(
            servers=servers,
            connect_timeout=connect_timeout,
            max_reconnect_attempts=max_reconnect_attempts,
            **options,
        )
    client = _HttpConnectNatsClient(resolved_proxy)
    await client.connect(
        servers=servers,
        connect_timeout=connect_timeout,
        max_reconnect_attempts=max_reconnect_attempts,
        **options,
    )
    return client


def _build_tls_context(
    *,
    tls_ca_file: str = "",
    tls_ca_pem: str = "",
    tls_cert_file: str = "",
    tls_key_file: str = "",
    tls_legacy_ca: bool = False,
    tls_insecure_skip_verify: bool = False,
) -> ssl.SSLContext | None:
    """Build an SSL context for NATS TLS, or return None when TLS files are unset."""
    tls_ca_file = str(Path(tls_ca_file).expanduser()) if tls_ca_file else ""
    tls_cert_file = str(Path(tls_cert_file).expanduser()) if tls_cert_file else ""
    tls_key_file = str(Path(tls_key_file).expanduser()) if tls_key_file else ""
    if (
        not tls_ca_file
        and not tls_ca_pem
        and not tls_cert_file
        and not tls_key_file
        and not tls_legacy_ca
        and not tls_insecure_skip_verify
    ):
        return None
    context = ssl.create_default_context(cafile=tls_ca_file or None)
    if tls_ca_pem:
        context.load_verify_locations(cadata=tls_ca_pem)
    if tls_legacy_ca:
        context.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
    if tls_insecure_skip_verify:
        logger.warning(
            "NATS TLS certificate verification is DISABLED "
            "(tls_insecure_skip_verify=true) — connections are vulnerable to "
            "man-in-the-middle attacks; never use this outside development"
        )
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
    tls_ca_pem: str = "",
    tls_cert_file: str = "",
    tls_key_file: str = "",
    tls_hostname: str = "",
    tls_handshake_first: bool = False,
    tls_legacy_ca: bool = False,
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
        tls_ca_pem=tls_ca_pem,
        tls_cert_file=tls_cert_file,
        tls_key_file=tls_key_file,
        tls_legacy_ca=tls_legacy_ca,
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
        options["nkeys_seed"] = str(Path(nkeys_seed_file).expanduser())
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


def _additional_nats_subjects(event: SleipnirEvent) -> list[str]:
    """Return explicit extra NATS subjects requested by the event payload."""

    raw = event.payload.get("additional_nats_subjects")
    if isinstance(raw, str):
        candidates: list[Any] = [raw]
    elif isinstance(raw, list):
        candidates = raw
    else:
        return []
    subjects: list[str] = []
    for candidate in candidates:
        subject = str(candidate).strip()
        if subject:
            subjects.append(subject)
    return subjects


def _extra_subscription_matches(
    extra_subscription: dict[str, Any],
    event_types: list[str],
) -> bool:
    """Return whether an extra stream subscription belongs to this logical subscriber."""

    scoped_event_types = extra_subscription.get("event_types")
    if not scoped_event_types:
        return True
    return any(
        match_event_type(scoped_pattern, event_type)
        for scoped_pattern in scoped_event_types
        for event_type in event_types
    )


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


def _durable_name_for_subject(consumer_group: str, subject: str) -> str:
    """Return a stable JetStream durable name for a consumer group and filter subject.

    JetStream durables are bound to their filter subject.  A single logical
    service group may subscribe to RPC plus several event subjects, so each
    filter needs its own durable while replicas sharing the same filter still
    share work.
    """
    safe_group = "".join(ch if ch.isalnum() or ch in ("_", "-") else "-" for ch in consumer_group)
    safe_group = safe_group.strip("-_") or "group"
    digest = hashlib.sha1(subject.encode("utf-8")).hexdigest()[:12]
    return f"{safe_group[:48]}-{digest}"


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

    Returns ``None`` and logs the error on malformed or undeserializable input;
    JetStream callers dead-letter the message.
    """
    try:
        return deserialize(data)
    except Exception:
        logger.exception("NatsTransport: deserialization failed")
        return None


def _delivery_attempt(msg: Any) -> int:
    """Return how many times JetStream has delivered *msg* (1 on first delivery)."""
    return int(msg.metadata.num_delivered)


def _stream_sequence(msg: Any) -> int:
    """Return the sequence number of *msg* in its JetStream stream."""
    return int(msg.metadata.sequence.stream)


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
        except Exception as create_exc:
            # add_stream may legitimately lose a creation race; only continue
            # when the stream verifiably exists. Anything else fails loudly at
            # the cause instead of cryptically at first publish.
            try:
                await js.stream_info(stream_name)
                logger.debug("NATS stream %r created concurrently", stream_name)
            except Exception as info_exc:
                raise RuntimeError(
                    f"NATS stream {stream_name!r} does not exist and could not "
                    f"be created: {create_exc}"
                ) from info_exc


async def _safe_unsubscribe(nats_sub: Any, *, context: str) -> None:
    """Best-effort NATS unsubscribe; a failure is logged, never swallowed silently.

    The server drops the interest anyway when the connection closes, so a
    failure here is not fatal — but it must be visible, not a bare
    ``suppress(Exception)``, or a leaked subscription looks like success.
    """
    try:
        await nats_sub.unsubscribe()
    except Exception:
        logger.warning(
            "NatsSubscriber: NATS unsubscribe failed for %s; the server drops the "
            "interest when the connection closes",
            context,
            exc_info=True,
        )


#: JetStream API error code for "consumer name already in use" — the server's
#: reply when a create races another create (or a rebind) for the same
#: durable name that got there first.
_JS_ERR_CONSUMER_NAME_IN_USE = 10013

#: JetStream API error code for "start time can not be updated" — the same
#: race, reported this way instead of 10013 when the two concurrent creates'
#: configs disagree on ``opt_start_time`` (confirmed against server 2.14.2;
#: each replica computes its own recovery replay start time, so they usually
#: do).
_JS_ERR_CONSUMER_START_TIME_UPDATE = 10012

_JS_ERR_CODES_CONSUMER_ALREADY_EXISTS = (
    _JS_ERR_CONSUMER_NAME_IN_USE,
    _JS_ERR_CONSUMER_START_TIME_UPDATE,
)


def _is_consumer_already_exists_error(exc: Exception) -> bool:
    """True if *exc* means a concurrent create beat this one to the same durable.

    Two replicas racing to recreate the same missing durable can both attempt
    to create it — each with its own recovery config, since every replica
    tracks its own health-check history — so the loser's create is rejected
    even though the durable now exists exactly as intended.  nats-server
    reports this as "consumer name already in use" (err_code 10013) or, when
    the two configs' start times disagree, "start time can not be updated"
    (err_code 10012).  Either way the fix is the same: bind to what's there,
    not treat this as a failure needing the full backoff.
    """
    return getattr(exc, "err_code", None) in _JS_ERR_CODES_CONSUMER_ALREADY_EXISTS


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
        jetstream_domain: str = "",
        retention: str = DEFAULT_RETENTION,
        max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS,
        ensure_stream: bool = True,
        publish_timeout_s: float = DEFAULT_PUBLISH_TIMEOUT_S,
        tls_ca_file: str = "",
        tls_ca_pem: str = "",
        tls_cert_file: str = "",
        tls_key_file: str = "",
        tls_hostname: str = "",
        tls_handshake_first: bool = False,
        tls_legacy_ca: bool = False,
        tls_insecure_skip_verify: bool = False,
        user: str = "",
        password: str = "",
        token: str = "",
        nkeys_seed_file: str = "",
        nkeys_seed: str = "",
        proxy_url: str = "",
    ) -> None:
        _require_nats()
        self._servers = servers or DEFAULT_SERVERS
        self._stream_name = stream_name
        self._subject_prefix = subject_prefix
        self._jetstream_domain = jetstream_domain.strip()
        self._retention = retention
        self._max_age_seconds = max_age_seconds
        self._max_bytes = max_bytes
        self._connect_timeout_s = connect_timeout_s
        self._max_reconnect_attempts = max_reconnect_attempts
        self._ensure_stream = ensure_stream
        self._publish_timeout_s = publish_timeout_s
        self._proxy_url = proxy_url
        self._connect_options = _connect_options(
            tls_ca_file=tls_ca_file,
            tls_ca_pem=tls_ca_pem,
            tls_cert_file=tls_cert_file,
            tls_key_file=tls_key_file,
            tls_hostname=tls_hostname,
            tls_handshake_first=tls_handshake_first,
            tls_legacy_ca=tls_legacy_ca,
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
        self._client = await _connect_nats(
            servers=self._servers,
            connect_timeout=self._connect_timeout_s,
            max_reconnect_attempts=self._max_reconnect_attempts,
            proxy_url=self._proxy_url,
            options=self._connect_options,
        )
        js_options = {"domain": self._jetstream_domain} if self._jetstream_domain else {}
        self._js = self._client.jetstream(**js_options)
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
            "NatsPublisher: connected to %s, stream=%r, jetstream_domain=%r",
            self._servers,
            self._stream_name,
            self._jetstream_domain,
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
        await self._publish_with_timeout(subject, payload)
        for additional_subject in _additional_nats_subjects(event):
            if additional_subject != subject:
                await self._publish_with_timeout(additional_subject, payload)

    async def _publish_with_timeout(self, subject: str, payload: bytes) -> None:
        """Publish with a hard deadline so a slow broker cannot hang callers."""
        try:
            await asyncio.wait_for(
                self._js.publish(subject, payload),
                timeout=self._publish_timeout_s,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"NATS publish to {subject!r} timed out after {self._publish_timeout_s}s"
            ) from exc

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        for event in events:
            await self.publish(event)


# ---------------------------------------------------------------------------
# NatsCorePublisher
# ---------------------------------------------------------------------------


class NatsCorePublisher(SleipnirPublisher):
    """Core NATS publisher for live control subjects."""

    def __init__(
        self,
        servers: list[str] | None = None,
        subject_prefix: str = DEFAULT_SUBJECT_PREFIX,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS,
        tls_ca_file: str = "",
        tls_ca_pem: str = "",
        tls_cert_file: str = "",
        tls_key_file: str = "",
        tls_hostname: str = "",
        tls_handshake_first: bool = False,
        tls_legacy_ca: bool = False,
        tls_insecure_skip_verify: bool = False,
        user: str = "",
        password: str = "",
        token: str = "",
        nkeys_seed_file: str = "",
        nkeys_seed: str = "",
        proxy_url: str = "",
    ) -> None:
        _require_nats()
        self._servers = servers or DEFAULT_SERVERS
        self._subject_prefix = subject_prefix
        self._connect_timeout_s = connect_timeout_s
        self._max_reconnect_attempts = max_reconnect_attempts
        self._proxy_url = proxy_url
        self._connect_options = _connect_options(
            tls_ca_file=tls_ca_file,
            tls_ca_pem=tls_ca_pem,
            tls_cert_file=tls_cert_file,
            tls_key_file=tls_key_file,
            tls_hostname=tls_hostname,
            tls_handshake_first=tls_handshake_first,
            tls_legacy_ca=tls_legacy_ca,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
            user=user,
            password=password,
            token=token,
            nkeys_seed_file=nkeys_seed_file,
            nkeys_seed=nkeys_seed,
        )
        self._client: Any = None

    async def start(self) -> None:
        self._client = await _connect_nats(
            servers=self._servers,
            connect_timeout=self._connect_timeout_s,
            max_reconnect_attempts=self._max_reconnect_attempts,
            proxy_url=self._proxy_url,
            options=self._connect_options,
        )
        logger.debug(
            "NatsCorePublisher: connected to %s, subject_prefix=%r",
            self._servers,
            self._subject_prefix,
        )

    async def stop(self) -> None:
        if self._client is not None:
            with suppress(Exception):
                await self._client.drain()
            with suppress(Exception):
                await self._client.close()
            self._client = None
            logger.debug("NatsCorePublisher: closed")

    async def __aenter__(self) -> NatsCorePublisher:
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
        if self._client is None:
            raise RuntimeError("NatsCorePublisher is not started. Call start() first.")
        subject = _nats_subject_for_event(event.event_type, self._subject_prefix)
        await self._client.publish(subject, serialize(event))
        await self._client.flush()

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        for event in events:
            await self.publish(event)


# ---------------------------------------------------------------------------
# NatsSubscriber
# ---------------------------------------------------------------------------


class _JetStreamDelivery(Delivery):
    """A JetStream message, acknowledged only once its handler has returned."""

    __slots__ = ("msg", "_subscription")

    def __init__(self, event: SleipnirEvent, msg: Any, subscription: _NatsSubscription) -> None:
        super().__init__(event)
        self.msg = msg
        self._subscription = subscription
        subscription.unsettled.add(self)

    # Each settlement leaves ``unsettled`` only after it has been sent, so a
    # cancellation mid-settlement still has the message released on unsubscribe.

    async def ack(self) -> None:
        await self._subscription.owner._settle(self.msg, "ack")
        self._subscription.unsettled.discard(self)

    async def nak(self, error: Exception) -> None:
        await self._subscription.owner._reject(self.msg, error)
        self._subscription.unsettled.discard(self)

    async def release(self) -> None:
        """Hand an unhandled message straight back to JetStream (unsubscribe/stop)."""
        await self._subscription.owner._settle(self.msg, "nak")
        self._subscription.unsettled.discard(self)


class _CoreDelivery(Delivery):
    """A core NATS message: no server-side log, so nothing to ack or redeliver."""

    __slots__ = ("_owner",)

    def __init__(self, event: SleipnirEvent, owner: NatsSubscriber) -> None:
        super().__init__(event)
        self._owner = owner

    async def ack(self) -> None:
        return None

    async def nak(self, error: Exception) -> None:
        self._owner._stats["handler_failures"] += 1
        logger.error(
            "NatsSubscriber: handler raised for core NATS event %s (%s); core subjects "
            "are at-most-once, so the event is not redelivered",
            self.event.event_id,
            self.event.event_type,
            exc_info=error,
        )


class _ConsumerWatch:
    """Tracks one JetStream push subscription so it can be recovered or rebound.

    ``nats_sub`` is replaced in place when :meth:`NatsSubscriber._recover_consumer`
    or :meth:`NatsSubscriber._rebind_consumer` swaps in a new underlying push
    subscription, so the watch always names the subscription currently in
    service for *subject*.  ``durable`` is ``None`` for an ephemeral (no
    ``consumer_group``) subscription — it has no deliver-subject-sharing
    replicas to rebind against, only a consumer that can be lost.
    ``last_healthy_at`` bounds how far back a recreated consumer must replay
    from; see ``_build_recovery_consumer_config``.
    """

    __slots__ = (
        "subject",
        "stream_name",
        "patterns",
        "nats_sub",
        "durable",
        "last_healthy_at",
        "consecutive_check_failures",
    )

    def __init__(
        self,
        subject: str,
        stream_name: str,
        patterns: list[str],
        nats_sub: Any,
        durable: str | None,
    ) -> None:
        self.subject = subject
        self.stream_name = stream_name
        self.patterns = patterns
        self.nats_sub = nats_sub
        self.durable = durable
        self.last_healthy_at = datetime.now(UTC)
        self.consecutive_check_failures = 0


class _NatsSubscription(_BaseSubscription):
    """One logical subscription: its NATS subscriptions, queue and unsettled messages.

    Every JetStream message from the moment its callback accepts it until it
    is settled sits in :attr:`unsettled` and gets an in-progress ping every
    *progress_interval_s*, so waiting in the queue or a slow handler never
    exhausts ``ack_wait``.  Unsubscribing hands whatever is still unsettled
    back to JetStream with an immediate nak.

    :attr:`consumer_watches` holds one :class:`_ConsumerWatch` per JetStream
    push subscription (core NATS subscriptions are not watched — they have no
    consumer to lose), polled every *health_check_interval_s* (plus jitter) to
    detect and recover a deleted or rebound consumer.  The watchdog task is
    started lazily, on the first watch registered — a core-only subscription,
    or one torn down before it ever gets one, never pays for it.  Each watch
    is checked in its own task (:attr:`_watch_check_tasks`), so a slow
    recovery retry loop on one watch never delays the next poll of another.
    """

    def __init__(
        self,
        patterns: list[str],
        queue: asyncio.Queue[Delivery],
        task: asyncio.Task[None],
        remove_fn: Callable[[], None],
        owner: NatsSubscriber,
        progress_interval_s: float,
        health_check_interval_s: float,
        health_check_jitter_s: float,
    ) -> None:
        super().__init__(patterns, queue, task, remove_fn)
        self.owner = owner
        self.nats_subs: list[Any] = []
        self.consumer_watches: list[_ConsumerWatch] = []
        self.unsettled: set[_JetStreamDelivery] = set()
        self._health_check_interval_s = health_check_interval_s
        self._health_check_jitter_s = health_check_jitter_s
        self._watchdog_task: asyncio.Task[None] | None = None
        self._watch_check_tasks: dict[int, asyncio.Task[None]] = {}
        self._progress_task = asyncio.create_task(self._report_progress(progress_interval_s))

    async def put(self, delivery: Delivery) -> None:
        """Queue *delivery* for the handler, waiting while the queue is full."""
        await self._queue.put(delivery)

    async def _report_progress(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            for delivery in list(self.unsettled):
                await self.owner._settle(delivery.msg, "in_progress")

    def ensure_watchdog_started(self) -> None:
        """Start the consumer-health watchdog on the first watch registered.

        Called after a :class:`_ConsumerWatch` is appended.  A core-only
        subscription, or one that is unsubscribed before it ever registers a
        watch (for example a short-lived RPC reply subscription that gets its
        answer first), never spawns the task at all.
        """
        if self._watchdog_task is not None:
            return
        self._watchdog_task = asyncio.create_task(
            self._watch_consumers(self._health_check_interval_s, self._health_check_jitter_s)
        )

    async def _watch_consumers(self, interval_s: float, jitter_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s + random.uniform(0, jitter_s))
            for watch in list(self.consumer_watches):
                key = id(watch)
                existing = self._watch_check_tasks.get(key)
                if existing is not None and not existing.done():
                    # A check (or a recovery retry loop inside it) for this
                    # watch is still running from a previous tick — starting
                    # a second one could race the first's swap of
                    # watch.nats_sub.  Other watches are scheduled below
                    # regardless: isolation is per watch, not per tick.
                    continue
                self._watch_check_tasks[key] = asyncio.create_task(
                    self.owner._check_consumer_watch(self, watch)
                )

    async def unsubscribe(self) -> None:
        if not self.active:
            return
        await super().unsubscribe()
        # The watchdog scheduling loop, and every per-watch check/recovery
        # task it spawned, are cancelled and fully awaited before anything
        # below touches nats_subs/consumer_watches: otherwise a recovery
        # already in flight can resume after this method has cleared those
        # collections and append a live, never-acking subscription that
        # nothing then cleans up (it keeps eating queue-group messages and
        # max_deliver).
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._watchdog_task
        for check_task in self._watch_check_tasks.values():
            check_task.cancel()
        for check_task in list(self._watch_check_tasks.values()):
            with suppress(asyncio.CancelledError):
                await check_task
        self._watch_check_tasks.clear()
        for nats_sub in list(self.nats_subs):
            await _safe_unsubscribe(nats_sub, context="subscription teardown")
        self.nats_subs.clear()
        self.consumer_watches.clear()
        self._progress_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._progress_task
        for delivery in list(self.unsettled):
            await delivery.release()


class NatsSubscriber(SleipnirSubscriber):
    """NATS JetStream subscriber with at-least-once delivery.

    Each JetStream message is acked only after the handler returns.  A handler
    that raises gets the message redelivered with backoff, and after
    *max_deliver* deliveries it is dead-lettered.  Handlers must be idempotent.
    See the module docstring for the full guarantee.

    *consumer_group* enables parallel processing across replicas: instances
    sharing the group name split the messages between them (JetStream queue
    groups), and a message a member failed to ack goes to another member.

    *replay_from_sequence* or *replay_from_time* choose where a new consumer
    starts reading the stream.

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
        jetstream_domain: str = "",
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
        tls_ca_pem: str = "",
        tls_cert_file: str = "",
        tls_key_file: str = "",
        tls_hostname: str = "",
        tls_handshake_first: bool = False,
        tls_legacy_ca: bool = False,
        tls_insecure_skip_verify: bool = False,
        user: str = "",
        password: str = "",
        token: str = "",
        nkeys_seed_file: str = "",
        nkeys_seed: str = "",
        proxy_url: str = "",
        extra_subscriptions: list[dict[str, str]] | None = None,
        core_subscriptions: list[str | dict[str, str]] | None = None,
        max_deliver: int = DEFAULT_MAX_DELIVER,
        ack_wait_s: float = DEFAULT_ACK_WAIT_S,
        ack_progress_interval_s: float = DEFAULT_ACK_PROGRESS_INTERVAL_S,
        max_ack_pending: int | None = None,
        nak_backoff_s: list[float] | None = None,
        consumer_health_check_interval_s: float = DEFAULT_CONSUMER_HEALTH_CHECK_INTERVAL_S,
        consumer_recovery_backoff_s: list[float] | None = None,
        consumer_check_failure_threshold: int = DEFAULT_CONSUMER_CHECK_FAILURE_THRESHOLD,
        consumer_health_check_jitter_s: float = DEFAULT_CONSUMER_HEALTH_CHECK_JITTER_S,
        consumer_recovery_max_replay_window_s: float = (
            DEFAULT_CONSUMER_RECOVERY_MAX_REPLAY_WINDOW_S
        ),
        consumer_already_exists_retry_limit: int = DEFAULT_CONSUMER_ALREADY_EXISTS_RETRY_LIMIT,
    ) -> None:
        """Create the subscriber (connect with :meth:`start`).

        :param ring_buffer_depth: Events queued per subscription before the
            NATS callback blocks and acks are withheld.
        :param max_deliver: Deliveries of one message before it is
            dead-lettered to ``{subject_prefix}.system.dlq.message``.
        :param ack_wait_s: Seconds without an ack or in-progress ping before
            JetStream redelivers — how long a crashed process holds a message.
        :param ack_progress_interval_s: Seconds between in-progress pings for
            unsettled messages; must be shorter than *ack_wait_s*.
        :param max_ack_pending: Unacked messages JetStream may push to one
            consumer; defaults to *ring_buffer_depth*.
        :param nak_backoff_s: Redelivery delay after the 1st, 2nd, … failed
            delivery; the last entry repeats.
        :param consumer_health_check_interval_s: Seconds between checks that
            each JetStream consumer (and its stream) still exists.
        :param consumer_recovery_backoff_s: Retry delay after the 1st, 2nd, …
            failed attempt to recreate a lost consumer while its stream is
            still absent; the last entry repeats.
        :param consumer_check_failure_threshold: Consecutive non-404 health
            check failures before the log escalates from WARNING to ERROR.
        :param consumer_health_check_jitter_s: Maximum random jitter added to
            the health-check interval on each poll.
        :param consumer_recovery_max_replay_window_s: Upper bound on how far
            back a recreated consumer replays from, regardless of how long
            its last health check has been stale.
        :param consumer_already_exists_retry_limit: Consecutive "consumer
            already exists" replies (a concurrent create winning) tolerated
            with a short retry before falling back to the normal
            failure/backoff path.
        """
        _require_nats()
        if ring_buffer_depth < 1:
            raise ValueError(f"ring_buffer_depth must be >= 1, got {ring_buffer_depth}")
        if max_deliver < 1:
            raise ValueError(f"max_deliver must be >= 1, got {max_deliver}")
        if ack_wait_s <= 0:
            raise ValueError(f"ack_wait_s must be > 0, got {ack_wait_s}")
        if not 0 < ack_progress_interval_s < ack_wait_s:
            raise ValueError(
                "ack_progress_interval_s must be > 0 and shorter than ack_wait_s "
                f"({ack_wait_s}), got {ack_progress_interval_s}"
            )
        resolved_max_ack_pending = ring_buffer_depth if max_ack_pending is None else max_ack_pending
        if resolved_max_ack_pending < 1:
            raise ValueError(f"max_ack_pending must be >= 1, got {resolved_max_ack_pending}")
        backoff = list(DEFAULT_NAK_BACKOFF_S if nak_backoff_s is None else nak_backoff_s)
        if not backoff or any(delay < 0 for delay in backoff):
            raise ValueError(
                f"nak_backoff_s must be a non-empty list of delays >= 0, got {nak_backoff_s}"
            )
        if consumer_health_check_interval_s <= 0:
            raise ValueError(
                "consumer_health_check_interval_s must be > 0, got "
                f"{consumer_health_check_interval_s}"
            )
        recovery_backoff = list(
            DEFAULT_CONSUMER_RECOVERY_BACKOFF_S
            if consumer_recovery_backoff_s is None
            else consumer_recovery_backoff_s
        )
        if not recovery_backoff or any(delay < 0 for delay in recovery_backoff):
            raise ValueError(
                "consumer_recovery_backoff_s must be a non-empty list of delays >= 0, got "
                f"{consumer_recovery_backoff_s}"
            )
        if consumer_check_failure_threshold < 1:
            raise ValueError(
                "consumer_check_failure_threshold must be >= 1, got "
                f"{consumer_check_failure_threshold}"
            )
        if consumer_health_check_jitter_s < 0:
            raise ValueError(
                f"consumer_health_check_jitter_s must be >= 0, got {consumer_health_check_jitter_s}"
            )
        if consumer_recovery_max_replay_window_s <= 0:
            raise ValueError(
                "consumer_recovery_max_replay_window_s must be > 0, got "
                f"{consumer_recovery_max_replay_window_s}"
            )
        if consumer_already_exists_retry_limit < 1:
            raise ValueError(
                "consumer_already_exists_retry_limit must be >= 1, got "
                f"{consumer_already_exists_retry_limit}"
            )
        self._servers = servers or DEFAULT_SERVERS
        self._stream_name = stream_name
        self._subject_prefix = subject_prefix
        self._jetstream_domain = jetstream_domain.strip()
        self._retention = retention
        self._max_age_seconds = max_age_seconds
        self._max_bytes = max_bytes
        self._consumer_group = consumer_group
        self._replay_from_sequence = replay_from_sequence
        self._replay_from_time = replay_from_time
        self._ring_buffer_depth = ring_buffer_depth
        self._max_deliver = max_deliver
        self._ack_wait_s = ack_wait_s
        self._ack_progress_interval_s = ack_progress_interval_s
        self._max_ack_pending = resolved_max_ack_pending
        self._nak_backoff_s = backoff
        self._consumer_health_check_interval_s = consumer_health_check_interval_s
        self._consumer_recovery_backoff_s = recovery_backoff
        self._consumer_check_failure_threshold = consumer_check_failure_threshold
        self._consumer_health_check_jitter_s = consumer_health_check_jitter_s
        self._consumer_recovery_max_replay_window_s = consumer_recovery_max_replay_window_s
        self._consumer_already_exists_retry_limit = consumer_already_exists_retry_limit
        self._dlq_subject = _nats_subject_for_event(registry.SYSTEM_DLQ_MESSAGE, subject_prefix)
        self._connect_timeout_s = connect_timeout_s
        self._max_reconnect_attempts = max_reconnect_attempts
        self._ensure_stream = ensure_stream
        self._proxy_url = proxy_url
        self._connect_options = _connect_options(
            tls_ca_file=tls_ca_file,
            tls_ca_pem=tls_ca_pem,
            tls_cert_file=tls_cert_file,
            tls_key_file=tls_key_file,
            tls_hostname=tls_hostname,
            tls_handshake_first=tls_handshake_first,
            tls_legacy_ca=tls_legacy_ca,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
            user=user,
            password=password,
            token=token,
            nkeys_seed_file=nkeys_seed_file,
            nkeys_seed=nkeys_seed,
        )
        self._extra_subscriptions = [
            {
                "subject": str(entry.get("subject") or "").strip(),
                "stream_name": str(
                    entry.get("stream_name") or entry.get("streamName") or ""
                ).strip(),
                "event_types": [
                    str(event_type).strip()
                    for event_type in (
                        entry.get("event_types")
                        or entry.get("eventTypes")
                        or entry.get("patterns")
                        or []
                    )
                    if str(event_type).strip()
                ],
            }
            for entry in extra_subscriptions or []
            if str(entry.get("subject") or "").strip()
        ]
        self._core_subscriptions = [
            str(entry.get("subject") if isinstance(entry, dict) else entry).strip()
            for entry in core_subscriptions or []
            if str(entry.get("subject") if isinstance(entry, dict) else entry).strip()
        ]
        self._client: Any = None
        self._js: Any = None
        self._subscriptions: list[_NatsSubscription] = []
        self._running = False
        self._stats: dict[str, int] = {
            "ack_failures": 0,
            "nak_failures": 0,
            "term_failures": 0,
            "in_progress_failures": 0,
            "handler_failures": 0,
            "decode_failures": 0,
            "dlq_published": 0,
            "dlq_publish_failures": 0,
            "consumer_lost": 0,
            "consumer_recovered": 0,
            "consumer_recovery_failures": 0,
            "consumer_check_failures": 0,
            "consumer_rebound": 0,
            "consumer_rebind_failures": 0,
        }

    def stats(self) -> dict[str, int]:
        """Counters for the failure paths this subscriber has survived."""
        return dict(self._stats)

    async def start(self) -> None:
        """Connect to NATS and ensure the JetStream stream exists."""
        self._client = await _connect_nats(
            servers=self._servers,
            connect_timeout=self._connect_timeout_s,
            max_reconnect_attempts=self._max_reconnect_attempts,
            proxy_url=self._proxy_url,
            options=self._connect_options,
        )
        js_options = {"domain": self._jetstream_domain} if self._jetstream_domain else {}
        self._js = self._client.jetstream(**js_options)
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
            "NatsSubscriber: connected to %s, stream=%r, group=%r, jetstream_domain=%r",
            self._servers,
            self._stream_name,
            self._consumer_group,
            self._jetstream_domain,
        )

    async def stop(self) -> None:
        """Unsubscribe everything, hand unsettled messages back, and close the connection."""
        self._running = False
        for sub in list(self._subscriptions):
            await sub.unsubscribe()
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

        queue: asyncio.Queue[Delivery] = asyncio.Queue(maxsize=self._ring_buffer_depth)
        consumer_task = asyncio.create_task(consume_deliveries(queue, handler))
        sub = _NatsSubscription(
            list(event_types),
            queue,
            consumer_task,
            lambda: self._remove_subscription(sub),
            owner=self,
            progress_interval_s=self._ack_progress_interval_s,
            health_check_interval_s=self._consumer_health_check_interval_s,
            health_check_jitter_s=self._consumer_health_check_jitter_s,
        )
        self._subscriptions.append(sub)
        try:
            await self._attach_nats_subscriptions(sub, event_types)
        except BaseException:
            await sub.unsubscribe()
            raise
        return sub

    async def _attach_nats_subscriptions(
        self,
        sub: _NatsSubscription,
        event_types: list[str],
    ) -> None:
        core_only = self._stream_name.lower() == "core"
        subscriptions = []
        if not core_only:
            subscriptions = [
                {"subject": subject, "stream_name": self._stream_name}
                for subject in _nats_subjects_for_patterns(event_types, self._subject_prefix)
            ]
            subscriptions.extend(
                extra_subscription
                for extra_subscription in self._extra_subscriptions
                if _extra_subscription_matches(extra_subscription, event_types)
            )

        seen: set[tuple[str, str]] = set()
        for subscription in subscriptions:
            subject = subscription["subject"]
            stream_name = subscription.get("stream_name") or self._stream_name
            key = (stream_name, subject)
            if key in seen:
                continue
            seen.add(key)
            watch = _ConsumerWatch(
                subject,
                stream_name,
                list(event_types),
                nats_sub=None,
                durable=self._durable_for_subject(subject),
            )
            nats_sub = await self._create_nats_subscription(
                subject,
                event_types,
                sub,
                # A fresh ConsumerConfig per subscription: nats-py push
                # subscribe stamps deliver_subject onto the config object, so
                # sharing one config makes every consumer push to the same
                # inbox and each message fan out to every callback.
                self._build_consumer_config(),
                stream_name=stream_name,
            )
            watch.nats_sub = nats_sub
            sub.nats_subs.append(nats_sub)
            sub.consumer_watches.append(watch)
            sub.ensure_watchdog_started()

        core_subjects = list(self._core_subscriptions)
        if core_only:
            core_subjects.extend(_nats_subjects_for_patterns(event_types, self._subject_prefix))
        for subject in dict.fromkeys(core_subjects):
            nats_sub = await self._create_core_subscription(subject, event_types, sub)
            sub.nats_subs.append(nats_sub)

    async def _create_core_subscription(
        self,
        subject: str,
        patterns: list[str],
        sub: _NatsSubscription,
    ) -> Any:
        """Create a core NATS subscription for live control subjects (at-most-once)."""

        async def _on_message(msg: Any) -> None:
            if not self._running or not sub.active:
                return
            event = _decode_nats_message(msg.data)
            if event is None:
                self._stats["decode_failures"] += 1
                return
            if event.ttl is not None and event.ttl <= 0:
                return
            if not any(match_event_type(p, event.event_type) for p in patterns):
                return
            await sub.put(_CoreDelivery(event, self))

        return await self._client.subscribe(subject, cb=_on_message)

    def _durable_for_subject(self, subject: str) -> str | None:
        """Return this subscriber's durable name for *subject*, or ``None`` if ephemeral."""
        if self._consumer_group is None:
            return None
        return _durable_name_for_subject(self._consumer_group, subject)

    def _deliver_subject_for_durable(self, durable: str) -> str:
        """Deterministic push-delivery inbox for *durable*.

        Must NOT be a subject any stream's ``{prefix}.>`` filter would
        capture: JetStream rejects a consumer whose deliver_subject falls
        inside its own stream's subjects as "a cycle" (delivering into it
        would republish every message back into the stream it came from) —
        confirmed against nats-server 2.14 (``400 err_code=10081``).
        ``_INBOX.`` is never captured by any Sleipnir stream and is the
        conventional NATS prefix for delivery inboxes, so
        ``_INBOX.sleipnir.<durable>`` is safe regardless of which stream
        (this subscriber's own, or an ``extra_subscriptions`` one) the
        durable belongs to.

        All replicas of a queue-group subscription must also land on the
        same inbox.  nats-py only assigns a random inbox when *creating* a
        brand new consumer — if one replica recreates a deleted durable
        while another is still bound to its old, randomly-assigned inbox,
        the second replica goes deaf even though the durable "exists"
        again.  Deriving the inbox from the durable name (already sanitized
        to safe subject-token characters by ``_durable_name_for_subject``)
        makes every replica's create-or-bind agree on the same inbox, and
        lets a health check detect a replica bound to the wrong one (see
        ``_check_consumer_watch``).
        """
        return f"_INBOX.sleipnir.{durable}"

    async def _create_nats_subscription(
        self,
        subject: str,
        patterns: list[str],
        sub: _NatsSubscription,
        config: Any,
        stream_name: str | None = None,
    ) -> Any:
        """Create (or bind to) a JetStream push subscription for *subject*.

        The callback only queues the message; the subscription's consumer
        loop acks it after the handler returns.  Messages the subscriber
        deliberately ignores (expired TTL, a pattern this subscription does
        not match) are acked here.  A message that arrives while stopping is
        left unacked, and JetStream redelivers it.

        A durable's ``deliver_subject``/``deliver_group`` are only taken from
        *config* when nats-py actually creates a new consumer here; binding
        to an existing one uses the server's current config regardless (see
        the module docstring's "Consumer and stream recovery" section) — an
        existing durable bound to a stale inbox is fixed by calling this
        again (:meth:`_rebind_consumer`), which simply picks up whatever
        inbox the server has now.
        """

        async def _on_message(msg: Any) -> None:
            if not self._running or not sub.active:
                return
            event = _decode_nats_message(msg.data)
            if event is None:
                self._stats["decode_failures"] += 1
                await self._dead_letter(
                    msg, reason="deserialization failed", attempt=_delivery_attempt(msg)
                )
                return
            if event.ttl is not None and event.ttl <= 0:
                await self._settle(msg, "ack")
                return
            if not any(match_event_type(p, event.event_type) for p in patterns):
                await self._settle(msg, "ack")
                return
            await sub.put(_JetStreamDelivery(event, msg, sub))

        durable = self._durable_for_subject(subject)
        kwargs: dict[str, Any] = {
            "stream": stream_name or self._stream_name,
            "config": config,
            # nats-py otherwise acks every message as soon as this callback
            # returns — after queueing, before the handler has run.
            "manual_ack": True,
        }
        if durable is not None:
            config.deliver_subject = self._deliver_subject_for_durable(durable)
            config.deliver_group = durable
            kwargs["durable"] = durable
            kwargs["queue"] = durable

        return await self._js.subscribe(subject, cb=_on_message, **kwargs)

    async def _settle(self, msg: Any, op: str, **kwargs: float) -> None:
        """Send one JetStream acknowledgement: ``ack``, ``nak``, ``term`` or ``in_progress``.

        An acknowledgement that fails to send leaves the message unacked.
        JetStream answers that with a redelivery after ``ack_wait``, which is
        a duplicate, not a loss.  So the failure is counted and logged here
        rather than raised into the consumer loop.
        """
        try:
            await getattr(msg, op)(**kwargs)
        except Exception:
            counter = f"{op}_failures"
            self._stats[counter] += 1
            logger.warning(
                "NatsSubscriber: %s failed for subject=%s (JetStream redelivers the "
                "message after ack_wait); %s=%d",
                op,
                getattr(msg, "subject", "?"),
                counter,
                self._stats[counter],
                exc_info=True,
            )

    def _nak_delay(self, attempt: int) -> float:
        """Redelivery delay after failed delivery number *attempt* (1-based)."""
        return self._nak_backoff_s[min(attempt, len(self._nak_backoff_s)) - 1]

    async def _reject(self, msg: Any, error: Exception) -> None:
        """The handler raised: nak for a delayed redelivery, or dead-letter when exhausted."""
        self._stats["handler_failures"] += 1
        attempt = _delivery_attempt(msg)
        if attempt < self._max_deliver:
            delay = self._nak_delay(attempt)
            logger.warning(
                "NatsSubscriber: handler raised for subject=%s (delivery %d of %d); "
                "redelivering in %.1fs",
                getattr(msg, "subject", "?"),
                attempt,
                self._max_deliver,
                delay,
                exc_info=error,
            )
            await self._settle(msg, "nak", delay=delay)
            return
        logger.error(
            "NatsSubscriber: handler raised for subject=%s on its final delivery "
            "(%d of %d); dead-lettering",
            getattr(msg, "subject", "?"),
            attempt,
            self._max_deliver,
            exc_info=error,
        )
        await self._dead_letter(
            msg,
            reason=f"handler failed after {attempt} deliveries: {error!r}",
            attempt=attempt,
        )

    async def _dead_letter(self, msg: Any, *, reason: str, attempt: int) -> None:
        """Move an unprocessable message to the DLQ, then terminate it.

        A message is terminated only once its DLQ record is safely published.
        If the DLQ publish fails, the message is nak'd with backoff, so the
        dead-lettering is retried on redelivery.  A DLQ record that cannot be
        processed itself is terminated without another DLQ record, which would
        loop.  It stays in the stream at the logged sequence.
        """
        subject = str(getattr(msg, "subject", "") or "")
        if subject == self._dlq_subject:
            logger.error(
                "NatsSubscriber: DLQ record on %s (stream sequence %s) cannot be "
                "processed (%s); terminating it — it remains in the stream",
                subject,
                _stream_sequence(msg),
                reason,
            )
            await self._settle(msg, "term")
            return
        if await self._publish_dlq(msg, reason=reason, attempt=attempt):
            await self._settle(msg, "term")
            return
        delay = self._nak_delay(attempt)
        logger.error(
            "NatsSubscriber: could not dead-letter the message on %s (stream sequence %s, "
            "delivery %d of %d); nak so it is retried in %.1fs. If JetStream has "
            "already reached max_deliver for this consumer it stops redelivering and "
            "the message stays in the stream at that sequence",
            subject,
            _stream_sequence(msg),
            attempt,
            self._max_deliver,
            delay,
        )
        await self._settle(msg, "nak", delay=delay)

    async def _publish_dlq(self, msg: Any, *, reason: str, attempt: int) -> bool:
        """Publish a DLQ record for *msg*; return whether it was published.

        The record is itself a valid Sleipnir event (``system.dlq.message``)
        carrying the raw bytes base64-encoded, published under the configured
        prefix so the main stream retains it.
        """
        subject = str(getattr(msg, "subject", "") or "")
        import base64  # noqa: PLC0415

        record = SleipnirEvent(
            event_type=registry.SYSTEM_DLQ_MESSAGE,
            source="sleipnir:nats-subscriber",
            payload={
                "reason": reason,
                "original_subject": subject,
                "stream": self._stream_name,
                "stream_sequence": _stream_sequence(msg),
                "deliveries": attempt,
                "raw_base64": base64.b64encode(bytes(msg.data)).decode("ascii"),
                "raw_bytes": len(msg.data),
            },
            summary=f"Dead-lettered message from {subject or 'unknown subject'}: {reason}",
            urgency=0.7,
            domain="infrastructure",
            timestamp=datetime.now(UTC),
        )
        try:
            await self._js.publish(self._dlq_subject, serialize(record))
        except Exception:
            self._stats["dlq_publish_failures"] += 1
            logger.exception(
                "NatsSubscriber: failed to publish the DLQ record for %s; the message "
                "stays unacked",
                subject,
            )
            return False
        self._stats["dlq_published"] += 1
        return True

    async def _check_consumer_watch(self, sub: _NatsSubscription, watch: _ConsumerWatch) -> None:
        """Poll JetStream for *watch*'s consumer; recover or rebind it as needed.

        A confirmed 404 (consumer or stream not found) means the consumer
        must be recreated.  A 200 whose ``deliver_subject``/``deliver_group``
        no longer match what *this* subscription is actually bound to means
        another replica (or an operator) recreated the durable while this
        process was still listening on its old inbox — the durable "exists"
        but this replica would otherwise never receive anything again.  The
        fix is to resubscribe: nats-py binds to whatever the server currently
        has, with no server-side write (the server refuses to change a push
        durable's deliver subject while anything is still subscribed to its
        current inbox — every replica is, at startup — so writing a new one
        here is not an option; see ``_rebind_consumer``).  Any other failure
        (timeout, connectivity) is logged and left alone — recreating on a
        transient error risks a duplicate consumer racing the one that is
        still there.
        """
        if not sub.active or not self._running:
            return
        try:
            info = await watch.nats_sub.consumer_info()
        except js_errors.NotFoundError:
            await self._recover_consumer(
                sub, watch, reason="its consumer is missing (deleted, or its stream was reset)"
            )
            return
        except Exception:
            watch.consecutive_check_failures += 1
            self._stats["consumer_check_failures"] += 1
            level = (
                logging.ERROR
                if watch.consecutive_check_failures >= self._consumer_check_failure_threshold
                else logging.WARNING
            )
            logger.log(
                level,
                "NatsSubscriber: consumer health check failed for subject=%s stream=%s "
                "(%d consecutive failure(s)); not recreating (treating as transient)",
                watch.subject,
                watch.stream_name,
                watch.consecutive_check_failures,
                exc_info=True,
            )
            return
        watch.consecutive_check_failures = 0
        watch.last_healthy_at = datetime.now(UTC)
        if watch.durable is None:
            return
        bound_deliver_subject = watch.nats_sub.subject
        if (
            info.config.deliver_subject == bound_deliver_subject
            and info.config.deliver_group == watch.durable
        ):
            return
        await self._rebind_consumer(
            sub,
            watch,
            reason=(
                f"it now delivers to {info.config.deliver_subject!r} via group "
                f"{info.config.deliver_group!r}, not the {bound_deliver_subject!r} this "
                f"subscription is bound to (recreated elsewhere)"
            ),
        )

    async def _rebind_consumer(
        self, sub: _NatsSubscription, watch: _ConsumerWatch, *, reason: str
    ) -> None:
        """Resubscribe to *watch*'s durable so nats-py binds to the server's current inbox.

        No server write: the server refuses to change a push durable's
        deliver_subject while any client is subscribed to its current one
        (``400 err_code=10013 "consumer name already in use"``, confirmed
        against server 2.14) — and at startup every replica already is.
        Binding to an existing durable makes nats-py fetch and use whatever
        config the server has right now (see ``_create_nats_subscription``'s
        docstring), which is exactly what a replica stuck on a stale inbox
        needs; the durable's config itself is left untouched.  A single
        attempt: a failure here is retried on the next health-check poll,
        not in a loop that would block this subscription's other watches.
        """
        logger.error(
            "NatsSubscriber: JetStream durable for subject=%s stream=%s is bound to the "
            "wrong inbox: %s — resubscribing",
            watch.subject,
            watch.stream_name,
            reason,
        )
        try:
            new_nats_sub = await self._create_nats_subscription(
                watch.subject,
                watch.patterns,
                sub,
                self._build_consumer_config(),
                stream_name=watch.stream_name,
            )
        except Exception:
            self._stats["consumer_rebind_failures"] += 1
            logger.error(
                "NatsSubscriber: rebind failed for subject=%s stream=%s; will retry on the "
                "next health check",
                watch.subject,
                watch.stream_name,
                exc_info=True,
            )
            return
        if not (sub.active and self._running):
            await _safe_unsubscribe(
                new_nats_sub,
                context=f"rebound but abandoned subscription (subject={watch.subject})",
            )
            return
        old_nats_sub = watch.nats_sub
        with suppress(ValueError):
            sub.nats_subs.remove(old_nats_sub)
        sub.nats_subs.append(new_nats_sub)
        watch.nats_sub = new_nats_sub
        watch.last_healthy_at = datetime.now(UTC)
        self._stats["consumer_rebound"] += 1
        logger.info(
            "NatsSubscriber: JetStream durable for subject=%s stream=%s rebound to its "
            "current server-side inbox; delivery resumed",
            watch.subject,
            watch.stream_name,
        )
        await _safe_unsubscribe(
            old_nats_sub, context=f"replaced push subscription (subject={watch.subject})"
        )

    async def _recover_consumer(
        self, sub: _NatsSubscription, watch: _ConsumerWatch, *, reason: str
    ) -> None:
        """Recreate *watch*'s missing consumer, retrying with backoff while its stream is
        absent."""
        self._stats["consumer_lost"] += 1
        logger.error(
            "NatsSubscriber: JetStream consumer for subject=%s stream=%s needs recovery: "
            "%s — this subscription stopped receiving messages on it; recovering",
            watch.subject,
            watch.stream_name,
            reason,
        )
        attempt = 0
        already_exists_retries = 0
        while sub.active and self._running:
            attempt += 1
            if self._ensure_stream and watch.stream_name == self._stream_name:
                # Only this subscriber's own configured stream: an extra
                # (foreign) stream's subjects/retention are not known here,
                # and guessing them would risk creating it with the wrong
                # config instead of leaving it to whatever owns it.
                try:
                    await _ensure_stream(
                        self._js,
                        self._stream_name,
                        self._subject_prefix,
                        self._retention,
                        self._max_age_seconds,
                        self._max_bytes,
                    )
                except Exception:
                    logger.warning(
                        "NatsSubscriber: could not re-ensure stream=%s before recovery "
                        "attempt %d for subject=%s; attempting consumer recreation anyway",
                        watch.stream_name,
                        attempt,
                        watch.subject,
                        exc_info=True,
                    )
            start_time = self._recovery_replay_start_time(watch)
            try:
                new_nats_sub = await self._create_nats_subscription(
                    watch.subject,
                    watch.patterns,
                    sub,
                    self._build_recovery_consumer_config(start_time),
                    stream_name=watch.stream_name,
                )
            except Exception as exc:
                already_exists = _is_consumer_already_exists_error(exc)
                retry_limit = self._consumer_already_exists_retry_limit
                if already_exists and already_exists_retries < retry_limit:
                    # Another replica racing to recreate the same missing
                    # durable created it first — with its own recovery
                    # config (a different replay start time is expected,
                    # since each replica tracks its own health-check
                    # history), so the server rejects a second create with
                    # "consumer name already in use" (err_code 10013) or,
                    # when the two start times disagree, "start time can not
                    # be updated" (err_code 10012, confirmed on server
                    # 2.14.2).  The durable exists exactly as intended;
                    # retry with a short delay to bind to it, bounded so a
                    # persistent disagreement still falls back to the normal
                    # failure path below instead of retrying forever.
                    already_exists_retries += 1
                    delay = self._consumer_recovery_delay(attempt)
                    logger.info(
                        "NatsSubscriber: durable for subject=%s stream=%s was created "
                        "concurrently by another replica on attempt %d; retrying in %.1fs "
                        "to bind to it",
                        watch.subject,
                        watch.stream_name,
                        attempt,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                self._stats["consumer_recovery_failures"] += 1
                delay = self._consumer_recovery_delay(attempt)
                logger.error(
                    "NatsSubscriber: consumer recovery for subject=%s stream=%s failed on "
                    "attempt %d (%s); retrying in %.1fs",
                    watch.subject,
                    watch.stream_name,
                    attempt,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            if not (sub.active and self._running):
                # Unsubscribed (or stopped) while the create was in flight:
                # the subscription this consumer belongs to no longer exists,
                # so leaving it bound would leak a live, never-acking push
                # subscription that keeps eating queue-group messages and
                # max_deliver.
                await _safe_unsubscribe(
                    new_nats_sub,
                    context=f"recovered but abandoned subscription (subject={watch.subject})",
                )
                return
            old_nats_sub = watch.nats_sub
            with suppress(ValueError):
                sub.nats_subs.remove(old_nats_sub)
            sub.nats_subs.append(new_nats_sub)
            watch.nats_sub = new_nats_sub
            watch.last_healthy_at = datetime.now(UTC)
            self._stats["consumer_recovered"] += 1
            logger.info(
                "NatsSubscriber: JetStream consumer for subject=%s stream=%s recovered after "
                "%d attempt(s); delivery resumed",
                watch.subject,
                watch.stream_name,
                attempt,
            )
            await _safe_unsubscribe(
                old_nats_sub, context=f"replaced push subscription (subject={watch.subject})"
            )
            return

    def _recovery_replay_start_time(self, watch: _ConsumerWatch) -> datetime:
        """Replay start time for a recreated consumer: *last_healthy_at*, margined and bounded.

        ``last_healthy_at`` is refreshed by every successful health check, so
        it is at most one ``consumer_health_check_interval_s`` (plus jitter,
        plus any time spent retrying while unhealthy) in the past — a good
        proxy for how far back this subscription could actually have missed
        messages.  It is stamped client-side, after the health check's
        server round trip returns, so it can run ahead of the server's own
        clock; a message published right at the moment the consumer was
        actually lost could sit at a stream timestamp earlier than what this
        pod believes "last healthy" was, and get skipped.  Subtracting one
        ``consumer_health_check_interval_s`` as a margin covers that gap
        (handlers are idempotent, so replaying a message already delivered
        before the loss is harmless).  The result is still clamped to
        *consumer_recovery_max_replay_window_s* so a subscription that was
        unhealthy for a long time (or whose watch was only just created, at
        `now`) never triggers a days-old replay burst.
        """
        margined = watch.last_healthy_at - timedelta(seconds=self._consumer_health_check_interval_s)
        earliest_allowed = datetime.now(UTC) - timedelta(
            seconds=self._consumer_recovery_max_replay_window_s
        )
        return max(margined, earliest_allowed)

    def _consumer_recovery_delay(self, attempt: int) -> float:
        """Recovery retry delay after failed attempt number *attempt* (1-based)."""
        backoff = self._consumer_recovery_backoff_s
        return backoff[min(attempt, len(backoff)) - 1]

    def _build_consumer_config(self) -> Any:
        """Build the :class:`~nats.js.api.ConsumerConfig` for this subscriber.

        Every consumer uses explicit acks, ``ack_wait``, ``max_deliver`` and
        ``max_ack_pending`` from this subscriber.  The deliver policy:

        - *replay_from_sequence* → ``DeliverPolicy.BY_START_SEQUENCE``
        - *replay_from_time* → ``DeliverPolicy.BY_START_TIME``
        - Otherwise → ``DeliverPolicy.NEW`` (only future messages)
        """
        settlement = {
            "ack_policy": js_api.AckPolicy.EXPLICIT,
            "ack_wait": self._ack_wait_s,
            "max_deliver": self._max_deliver,
            "max_ack_pending": self._max_ack_pending,
        }
        if self._replay_from_sequence is not None:
            return js_api.ConsumerConfig(
                deliver_policy=js_api.DeliverPolicy.BY_START_SEQUENCE,
                opt_start_seq=self._replay_from_sequence,
                **settlement,
            )
        if self._replay_from_time is not None:
            return js_api.ConsumerConfig(
                deliver_policy=js_api.DeliverPolicy.BY_START_TIME,
                opt_start_time=self._replay_from_time,
                **settlement,
            )
        return js_api.ConsumerConfig(deliver_policy=js_api.DeliverPolicy.NEW, **settlement)

    def _build_recovery_consumer_config(self, start_time: datetime) -> Any:
        """Consumer config for a recreated consumer.

        Recovery must not reuse the startup deliver policy: ``NEW`` would
        skip every message published during the gap, and this subscriber's
        own *replay_from_time*/*replay_from_sequence* are startup-only — they
        point at a moment or a stream sequence that may no longer mean
        anything once the stream itself was reset.  Replaying from
        *start_time* (see ``_recovery_replay_start_time``) instead covers
        the gap this subscription may have missed, without replaying the
        whole stream.

        Only used to *create* a brand new consumer (``_recover_consumer``);
        rebinding to an existing durable whose inbox no longer matches
        (``_rebind_consumer``) never sends this — nats-py fetches and uses
        the server's current config as-is when the durable already exists.
        """
        return js_api.ConsumerConfig(
            deliver_policy=js_api.DeliverPolicy.BY_START_TIME,
            opt_start_time=start_time,
            ack_policy=js_api.AckPolicy.EXPLICIT,
            ack_wait=self._ack_wait_s,
            max_deliver=self._max_deliver,
            max_ack_pending=self._max_ack_pending,
        )

    def _remove_subscription(self, sub: _NatsSubscription) -> None:
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
        jetstream_domain: str = "",
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
        publish_timeout_s: float = DEFAULT_PUBLISH_TIMEOUT_S,
        tls_ca_file: str = "",
        tls_ca_pem: str = "",
        tls_cert_file: str = "",
        tls_key_file: str = "",
        tls_hostname: str = "",
        tls_handshake_first: bool = False,
        tls_legacy_ca: bool = False,
        tls_insecure_skip_verify: bool = False,
        user: str = "",
        password: str = "",
        token: str = "",
        nkeys_seed_file: str = "",
        nkeys_seed: str = "",
        proxy_url: str = "",
        extra_subscriptions: list[dict[str, str]] | None = None,
        core_subscriptions: list[str | dict[str, str]] | None = None,
        max_deliver: int = DEFAULT_MAX_DELIVER,
        ack_wait_s: float = DEFAULT_ACK_WAIT_S,
        ack_progress_interval_s: float = DEFAULT_ACK_PROGRESS_INTERVAL_S,
        max_ack_pending: int | None = None,
        nak_backoff_s: list[float] | None = None,
        consumer_health_check_interval_s: float = DEFAULT_CONSUMER_HEALTH_CHECK_INTERVAL_S,
        consumer_recovery_backoff_s: list[float] | None = None,
        consumer_check_failure_threshold: int = DEFAULT_CONSUMER_CHECK_FAILURE_THRESHOLD,
        consumer_health_check_jitter_s: float = DEFAULT_CONSUMER_HEALTH_CHECK_JITTER_S,
        consumer_recovery_max_replay_window_s: float = (
            DEFAULT_CONSUMER_RECOVERY_MAX_REPLAY_WINDOW_S
        ),
        consumer_already_exists_retry_limit: int = DEFAULT_CONSUMER_ALREADY_EXISTS_RETRY_LIMIT,
    ) -> None:
        _require_nats()
        self._publisher = NatsPublisher(
            servers=servers,
            stream_name=stream_name,
            subject_prefix=subject_prefix,
            jetstream_domain=jetstream_domain,
            retention=retention,
            max_age_seconds=max_age_seconds,
            max_bytes=max_bytes,
            connect_timeout_s=connect_timeout_s,
            max_reconnect_attempts=max_reconnect_attempts,
            ensure_stream=ensure_stream,
            publish_timeout_s=publish_timeout_s,
            tls_ca_file=tls_ca_file,
            tls_ca_pem=tls_ca_pem,
            tls_cert_file=tls_cert_file,
            tls_key_file=tls_key_file,
            tls_hostname=tls_hostname,
            tls_handshake_first=tls_handshake_first,
            tls_legacy_ca=tls_legacy_ca,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
            user=user,
            password=password,
            token=token,
            nkeys_seed_file=nkeys_seed_file,
            nkeys_seed=nkeys_seed,
            proxy_url=proxy_url,
        )
        self._subscriber = NatsSubscriber(
            servers=servers,
            stream_name=stream_name,
            subject_prefix=subject_prefix,
            jetstream_domain=jetstream_domain,
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
            tls_ca_pem=tls_ca_pem,
            tls_cert_file=tls_cert_file,
            tls_key_file=tls_key_file,
            tls_hostname=tls_hostname,
            tls_handshake_first=tls_handshake_first,
            tls_legacy_ca=tls_legacy_ca,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
            user=user,
            password=password,
            token=token,
            nkeys_seed_file=nkeys_seed_file,
            nkeys_seed=nkeys_seed,
            proxy_url=proxy_url,
            extra_subscriptions=extra_subscriptions,
            core_subscriptions=core_subscriptions,
            max_deliver=max_deliver,
            ack_wait_s=ack_wait_s,
            ack_progress_interval_s=ack_progress_interval_s,
            max_ack_pending=max_ack_pending,
            nak_backoff_s=nak_backoff_s,
            consumer_health_check_interval_s=consumer_health_check_interval_s,
            consumer_recovery_backoff_s=consumer_recovery_backoff_s,
            consumer_check_failure_threshold=consumer_check_failure_threshold,
            consumer_health_check_jitter_s=consumer_health_check_jitter_s,
            consumer_recovery_max_replay_window_s=consumer_recovery_max_replay_window_s,
            consumer_already_exists_retry_limit=consumer_already_exists_retry_limit,
        )

    async def start(self) -> None:
        """Connect publisher then subscriber."""
        await self._publisher.start()
        await self._subscriber.start()

    async def stop(self) -> None:
        """Graceful shutdown: stop subscriber first, then publisher."""
        await self._subscriber.stop()
        await self._publisher.stop()

    def stats(self) -> dict[str, int]:
        """Failure-path counters from the subscriber side of the transport."""
        return self._subscriber.stats()

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

    **Subscribing**: the bridge subscribes to both transports and skips an
    event whose *event_id* a handler has already processed successfully, so
    the copy arriving over the second transport is normally suppressed.  An
    event is only recorded as seen once its handler has returned: a failed
    attempt must not suppress the redelivery that retries it.  Two copies
    that arrive while the first is still being handled both reach the
    handler, so this is still at-least-once and handlers must be idempotent.

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
        """Subscribe to both transports, skipping events already handled successfully."""

        async def _dedup_handler(event: SleipnirEvent) -> None:
            if self._dedup.is_seen(event.event_id):
                return
            await handler(event)
            self._dedup.mark_seen(event.event_id)

        local_sub = await self._local_sub.subscribe(event_types, _dedup_handler)
        nats_sub = await self._nats_sub.subscribe(event_types, _dedup_handler)
        return _BridgeSubscription(local_sub, nats_sub)


# ---------------------------------------------------------------------------
# Public connection helpers
# ---------------------------------------------------------------------------

#: Public aliases so other packages (e.g. Ravn signal transports) reuse the
#: same proxy-aware connect path and TLS/auth option handling instead of
#: duplicating them per adapter.
connect_nats = _connect_nats
build_connect_options = _connect_options

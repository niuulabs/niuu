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
        jetstream_domain: ""       # optional; empty uses the server default
        retention: "limits"        # or "interest", "workqueue"
        max_age: "7d"
        max_bytes: "1GB"
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import ssl
import time
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import ParseResult, urlparse
from urllib.request import getproxies

try:
    import nats
    import nats.js.api as js_api
    from nats.aio.client import DEFAULT_BUFFER_SIZE
    from nats.aio.client import Client as NatsClient
    from nats.aio.transport import TcpTransport

    _NATS_AVAILABLE = True
except ImportError:  # pragma: no cover
    DEFAULT_BUFFER_SIZE = 0
    NatsClient = object  # type: ignore[assignment,misc]
    TcpTransport = object  # type: ignore[assignment,misc]
    _NATS_AVAILABLE = False

from sleipnir.adapters._subscriber_support import (
    _BaseSubscription,
    consume_queue,
    enqueue_with_overflow,
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

#: Per-subscriber in-process ring buffer depth (events).
DEFAULT_RING_BUFFER_DEPTH = 1000

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
    ) -> None:
        _require_nats()
        if ring_buffer_depth < 1:
            raise ValueError(f"ring_buffer_depth must be >= 1, got {ring_buffer_depth}")
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
        self._nats_subs: list[Any] = []
        self._subscriptions: list[_BaseSubscription] = []
        self._running = False
        self._stats: dict[str, int] = {
            "ack_failures": 0,
            "decode_failures": 0,
            "dlq_published": 0,
            "dlq_publish_failures": 0,
            "ring_buffer_overflows": 0,
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
            self._nats_subs.append(nats_sub)

        core_subjects = list(self._core_subscriptions)
        if core_only:
            core_subjects.extend(_nats_subjects_for_patterns(event_types, self._subject_prefix))
        for subject in dict.fromkeys(core_subjects):
            nats_sub = await self._create_core_subscription(subject, event_types, sub)
            self._nats_subs.append(nats_sub)

        return sub

    async def _create_core_subscription(
        self,
        subject: str,
        patterns: list[str],
        sub: _BaseSubscription,
    ) -> Any:
        """Create a core NATS subscription for live control subjects."""

        async def _on_message(msg: Any) -> None:
            if not self._running:
                return
            event = _decode_nats_message(msg.data)
            if event is None:
                self._stats["decode_failures"] += 1
                return
            if event.ttl is not None and event.ttl <= 0:
                return
            if not any(match_event_type(p, event.event_type) for p in patterns):
                return
            overflowed = await enqueue_with_overflow(
                sub._queue, event, self._ring_buffer_depth, logger
            )
            if overflowed:
                self._stats["ring_buffer_overflows"] += 1

        return await self._client.subscribe(subject, cb=_on_message)

    async def _create_nats_subscription(
        self,
        subject: str,
        patterns: list[str],
        sub: _BaseSubscription,
        config: Any,
        stream_name: str | None = None,
    ) -> Any:
        """Create a JetStream push subscription for *subject*."""

        async def _on_message(msg: Any) -> None:
            try:
                if not self._running:
                    return
                event = _decode_nats_message(msg.data)
                if event is None:
                    self._stats["decode_failures"] += 1
                    await self._publish_dlq(msg, reason="deserialization failed")
                    return
                if event.ttl is not None and event.ttl <= 0:
                    return
                if not any(match_event_type(p, event.event_type) for p in patterns):
                    return
                overflowed = await enqueue_with_overflow(
                    sub._queue, event, self._ring_buffer_depth, logger
                )
                if overflowed:
                    self._stats["ring_buffer_overflows"] += 1
            finally:
                try:
                    await msg.ack()
                except Exception:
                    self._stats["ack_failures"] += 1
                    logger.warning(
                        "NatsSubscriber: ack failed for subject=%s "
                        "(message will be redelivered); ack_failures=%d",
                        getattr(msg, "subject", "?"),
                        self._stats["ack_failures"],
                        exc_info=True,
                    )

        kwargs: dict[str, Any] = {"stream": stream_name or self._stream_name, "config": config}
        if self._consumer_group is not None:
            durable = _durable_name_for_subject(self._consumer_group, subject)
            kwargs["durable"] = durable
            kwargs["queue"] = durable

        return await self._js.subscribe(subject, cb=_on_message, **kwargs)

    async def _publish_dlq(self, msg: Any, *, reason: str) -> None:
        """Dead-letter an unprocessable message instead of dropping it.

        The record is itself a valid Sleipnir event (``system.dlq.message``)
        carrying the raw bytes base64-encoded, published under the configured
        prefix so the main stream retains it. DLQ records are never
        dead-lettered again — a poison DLQ entry is dropped with an error log.
        """
        subject = str(getattr(msg, "subject", "") or "")
        dlq_subject = _nats_subject_for_event(registry.SYSTEM_DLQ_MESSAGE, self._subject_prefix)
        if subject == dlq_subject:
            logger.error(
                "NatsSubscriber: DLQ record on %s is itself unprocessable; dropping",
                subject,
            )
            return
        import base64  # noqa: PLC0415

        record = SleipnirEvent(
            event_type=registry.SYSTEM_DLQ_MESSAGE,
            source="sleipnir:nats-subscriber",
            payload={
                "reason": reason,
                "original_subject": subject,
                "stream": self._stream_name,
                "raw_base64": base64.b64encode(bytes(msg.data)).decode("ascii"),
                "raw_bytes": len(msg.data),
            },
            summary=f"Dead-lettered message from {subject or 'unknown subject'}: {reason}",
            urgency=0.7,
            domain="infrastructure",
            timestamp=datetime.now(UTC),
        )
        try:
            await self._js.publish(dlq_subject, serialize(record))
            self._stats["dlq_published"] += 1
        except Exception:
            self._stats["dlq_publish_failures"] += 1
            logger.exception(
                "NatsSubscriber: failed to dead-letter message from %s; "
                "raw payload is lost after ack",
                subject,
            )

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


# ---------------------------------------------------------------------------
# Public connection helpers
# ---------------------------------------------------------------------------

#: Public aliases so other packages (e.g. Ravn signal transports) reuse the
#: same proxy-aware connect path and TLS/auth option handling instead of
#: duplicating them per adapter.
connect_nats = _connect_nats
build_connect_options = _connect_options

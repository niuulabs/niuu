"""nng PUB/SUB transport adapter for Sleipnir.

Multi-process, single-node event transport using pynng (nng Python bindings).

Architecture
------------
- :class:`NngPublisher` — binds a Pub0 socket; call :meth:`~NngPublisher.start`
  before publishing.
- :class:`NngSubscriber` — dials a Sub0 socket; call :meth:`~NngSubscriber.start`
  before subscribing.
- :class:`NngTransport` — combined publisher + subscriber backed by both sockets,
  suitable for single-process use where the same service publishes and subscribes.

Wire format
-----------
Every nng message is::

    <event_type_bytes> \\x00 <msgpack_payload>

The event type prefix enables nng's native prefix-based topic filtering so that
subscribers only receive messages matching their subscription patterns.

Configuration example
---------------------
::

    sleipnir:
      transport: "nng"
      address: "ipc:///tmp/sleipnir.sock"
      discovery:
        enabled: true
        registry_path: /run/odin/sleipnir.json

Transports
----------
- **IPC** (``ipc:///tmp/sleipnir.sock``) — Unix-domain socket, ~10-50 µs latency.
- **TCP** (``tcp://localhost:9500``) — for multi-machine or container deployments.

Service discovery
-----------------
Pass a :class:`~sleipnir.adapters.discovery.ServiceRegistry` to enable
multi-process discovery.  :class:`NngPublisher` registers its socket address on
:meth:`~NngPublisher.start` and deregisters on :meth:`~NngPublisher.stop`.
:class:`NngSubscriber` reads the registry on :meth:`~NngSubscriber.start` and
dials every live publisher socket it finds.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

try:
    import pynng
    import pynng.exceptions

    _PYNNG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYNNG_AVAILABLE = False

from sleipnir.adapters._subscriber_support import (
    DEFAULT_RING_BUFFER_DEPTH,
    _BaseSubscription,
    consume_queue,
    dispatch_to_subscriptions,
)
from sleipnir.adapters.discovery import ServiceRegistry
from sleipnir.adapters.serialization import deserialize, serialize
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import EventHandler, SleipnirPublisher, SleipnirSubscriber, Subscription

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults (avoids magic numbers in business logic; values can be overridden
# via constructor kwargs or config).
# ---------------------------------------------------------------------------

DEFAULT_IPC_ADDRESS = "ipc:///tmp/sleipnir.sock"
DEFAULT_TCP_ADDRESS = "tcp://localhost:9500"

#: How long to wait for an incoming message before looping (milliseconds).
#: Controls shutdown responsiveness — smaller = faster stop, more CPU.
DEFAULT_RECV_TIMEOUT_MS = 100

#: Seconds to wait between bind retries when address is already in use.
DEFAULT_BIND_RETRY_DELAY_S = 0.1

#: Maximum number of bind attempts before raising.
DEFAULT_BIND_MAX_RETRIES = 50

#: Milliseconds to sleep after opening sockets to let nng establish the
#: underlying IPC/TCP connection before the first publish.
DEFAULT_CONNECT_SETTLE_MS = 20

#: Minimum reconnect interval in milliseconds.  nng's default is 1000ms which
#: is too slow for tests and fast-restart scenarios.  50ms gives sub-second
#: reconnection without hammering the OS.
DEFAULT_RECONNECT_MIN_MS = 50

#: Maximum reconnect interval (0 = same as min, i.e. fixed interval).
DEFAULT_RECONNECT_MAX_MS = 0

#: How often to re-discover publishers from the registry (seconds).
#: Enables subscribers to find publishers that registered after startup.
DEFAULT_REDISCOVER_INTERVAL_S = 5.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _nng_topics_for_patterns(patterns: list[str]) -> list[bytes]:
    """Translate fnmatch patterns to nng topic-prefix byte strings.

    nng delivers a message to a SUB socket if the message *starts with* the
    subscribed topic bytes.  An empty-bytes subscription ``b""`` matches all
    messages.

    Rules
    -----
    - ``"*"`` → ``b""`` (subscribe to everything)
    - ``"ravn.*"`` → ``b"ravn."`` (namespace prefix)
    - ``"ravn.tool.*"`` → ``b"ravn.tool."`` (sub-namespace prefix)
    - ``"ravn.tool.complete"`` → ``b"ravn.tool.complete\\x00"`` (exact match)
    - Any other pattern with wildcard characters (``*``, ``?``, ``[``) →
      ``b""`` (receive all; application-level fnmatch filtering handles it)
    """
    topics: list[bytes] = []
    for pattern in patterns:
        if pattern == "*":
            return [b""]  # wildcard: receive all; short-circuit
        if pattern.endswith(".*"):
            # "ravn.*" → "ravn."  (trim the star, keep the dot)
            prefix = pattern[:-1]
            topics.append(prefix.encode())
        elif any(c in pattern for c in ("*", "?", "[")):
            # Pattern contains wildcard characters not handled by prefix rules
            # (e.g. "ravn.tool.?").  Subscribe to all messages and rely on
            # application-level fnmatch filtering for correctness.
            return [b""]
        else:
            # Exact event type: include the null separator so no other type
            # with the same prefix can slip through.
            topics.append(pattern.encode() + b"\x00")
    return topics or [b""]


# ---------------------------------------------------------------------------
# NngPublisher
# ---------------------------------------------------------------------------


class NngPublisher(SleipnirPublisher):
    """nng Pub0 publisher.

    Binds a PUB socket at *address* and serialises :class:`SleipnirEvent`
    objects with msgpack before sending.

    When *service_id* and *registry* are provided, the publisher registers
    itself in the service registry on :meth:`start` and deregisters on
    :meth:`stop`, enabling other processes to discover this socket
    automatically.

    Usage::

        pub = NngPublisher("ipc:///tmp/sleipnir.sock")
        async with pub:
            await pub.publish(event)
    """

    def __init__(
        self,
        address: str = DEFAULT_IPC_ADDRESS,
        bind_retry_delay_s: float = DEFAULT_BIND_RETRY_DELAY_S,
        bind_max_retries: int = DEFAULT_BIND_MAX_RETRIES,
        service_id: str | None = None,
        registry: ServiceRegistry | None = None,
    ) -> None:
        _require_pynng()
        self._address = address
        self._bind_retry_delay_s = bind_retry_delay_s
        self._bind_max_retries = bind_max_retries
        self._service_id = service_id
        self._registry = registry
        self._socket: pynng.Pub0 | None = None  # type: ignore[name-defined]

    async def start(self) -> None:
        """Bind the PUB socket, retrying on transient address-in-use errors.

        If discovery is configured, registers this publisher in the service
        registry after the socket is bound.
        """
        socket = pynng.Pub0()
        try:
            for attempt in range(self._bind_max_retries):
                try:
                    socket.listen(self._address)
                    logger.debug("NngPublisher: bound to %s", self._address)
                    self._socket = socket
                    break
                except pynng.AddressInUse:
                    if attempt == self._bind_max_retries - 1:
                        raise
                    logger.warning(
                        "NngPublisher: %s in use, retry %d/%d",
                        self._address,
                        attempt + 1,
                        self._bind_max_retries,
                    )
                    await asyncio.sleep(self._bind_retry_delay_s)
        except Exception:
            socket.close()
            raise
        if self._registry is not None and self._service_id is not None:
            await asyncio.get_running_loop().run_in_executor(
                None, self._registry.register, self._service_id, self._address
            )

    async def stop(self) -> None:
        """Deregister from discovery (if configured), then close the PUB socket."""
        if self._registry is not None and self._service_id is not None:
            with suppress(Exception):
                await asyncio.get_running_loop().run_in_executor(
                    None, self._registry.deregister, self._service_id
                )
        if self._socket is not None:
            with suppress(Exception):
                self._socket.close()
            self._socket = None
            logger.debug("NngPublisher: closed")

    async def __aenter__(self) -> NngPublisher:
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
        if self._socket is None:
            raise RuntimeError("NngPublisher is not started. Call start() first.")
        msg = _encode_message(event)
        await self._socket.asend(msg)

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        for event in events:
            await self.publish(event)


# ---------------------------------------------------------------------------
# NngSubscriber
# ---------------------------------------------------------------------------


class NngSubscriber(SleipnirSubscriber):
    """nng Sub0 subscriber.

    Dials a SUB socket to *address* and dispatches received events to
    registered handlers via per-subscription asyncio queues.

    When a *registry* is provided, :meth:`start` queries it for all live
    publisher sockets and dials each one, enabling multi-publisher discovery.
    If the registry is empty, *address* is used as the fallback.

    Usage::

        sub = NngSubscriber("ipc:///tmp/sleipnir.sock")
        async with sub:
            handle = await sub.subscribe(["ravn.*"], my_handler)
            ...
            await handle.unsubscribe()
    """

    def __init__(
        self,
        address: str = DEFAULT_IPC_ADDRESS,
        recv_timeout_ms: int = DEFAULT_RECV_TIMEOUT_MS,
        ring_buffer_depth: int = DEFAULT_RING_BUFFER_DEPTH,
        connect_settle_ms: int = DEFAULT_CONNECT_SETTLE_MS,
        reconnect_min_ms: int = DEFAULT_RECONNECT_MIN_MS,
        reconnect_max_ms: int = DEFAULT_RECONNECT_MAX_MS,
        registry: ServiceRegistry | None = None,
        rediscover_interval_s: float = DEFAULT_REDISCOVER_INTERVAL_S,
        peer_addresses: list[str] | None = None,
    ) -> None:
        _require_pynng()
        if ring_buffer_depth < 1:
            raise ValueError(f"ring_buffer_depth must be >= 1, got {ring_buffer_depth}")
        self._address = address
        self._recv_timeout_ms = recv_timeout_ms
        self._ring_buffer_depth = ring_buffer_depth
        self._connect_settle_ms = connect_settle_ms
        self._reconnect_min_ms = reconnect_min_ms
        self._reconnect_max_ms = reconnect_max_ms
        self._registry = registry
        self._rediscover_interval_s = rediscover_interval_s
        self._peer_addresses = peer_addresses or []

        self._socket: pynng.Sub0 | None = None  # type: ignore[name-defined]
        self._recv_task: asyncio.Task[None] | None = None
        self._rediscover_task: asyncio.Task[None] | None = None
        self._subscriptions: list[_BaseSubscription] = []
        self._nng_subscribed_topics: set[bytes] = set()
        self._dialed_addresses: set[str] = set()
        self._running = False

    async def start(self) -> None:
        """Dial the SUB socket and start the receive loop.

        When discovery is configured, all live publisher sockets from the
        registry are dialled.  Falls back to *address* when the registry is
        empty or discovery is disabled.

        Also starts a background task that periodically re-discovers publishers
        so that late-registering nodes are found.
        """
        self._socket = pynng.Sub0()
        self._socket.recv_timeout = self._recv_timeout_ms
        self._socket.reconnect_time_min = self._reconnect_min_ms
        self._socket.reconnect_time_max = self._reconnect_max_ms

        addresses = await self._discover_addresses()
        for addr in addresses:
            self._socket.dial(addr, block=False)
            self._dialed_addresses.add(addr)
            logger.debug("NngSubscriber: dialing %s", addr)

        if self._connect_settle_ms > 0:
            await asyncio.sleep(self._connect_settle_ms / 1000.0)
        self._running = True
        self._recv_task = asyncio.create_task(self._recv_loop(), name="sleipnir-nng-sub-recv")

        # Start periodic re-discovery if registry or peer addresses are configured
        if (self._registry is not None or self._peer_addresses) and self._rediscover_interval_s > 0:
            self._rediscover_task = asyncio.create_task(
                self._rediscover_loop(), name="sleipnir-nng-sub-rediscover"
            )

    async def _discover_addresses(self) -> list[str]:
        """Return the list of publisher addresses to dial.

        Resolution order:
        1. Service registry (JSON file, if provided)
        2. Static peer addresses (from discovery, if provided)
        3. Fallback to *self._address*
        """
        if self._registry is not None:
            loop = asyncio.get_running_loop()
            entries = await loop.run_in_executor(None, self._registry.list_services)
            if entries:
                addresses = [e.socket for e in entries]
                logger.debug("NngSubscriber: registry: %d publisher(s)", len(addresses))
                return addresses

        if self._peer_addresses:
            logger.debug("NngSubscriber: peer_addresses: %d pub(s)", len(self._peer_addresses))
            return list(self._peer_addresses)

        return [self._address]

    async def _rediscover_loop(self) -> None:
        """Periodically re-discover publishers and dial any new ones.

        This allows subscribers to find publishers that registered after
        the subscriber started, enabling late-joining nodes in a mesh.
        """
        while self._running:
            await asyncio.sleep(self._rediscover_interval_s)
            if not self._running or self._socket is None:
                return
            try:
                addresses = await self._discover_addresses()
                for addr in addresses:
                    if addr not in self._dialed_addresses:
                        self._socket.dial(addr, block=False)
                        self._dialed_addresses.add(addr)
                        logger.info("NngSubscriber: discovered new publisher, dialing %s", addr)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("NngSubscriber: rediscovery error; will retry")

    async def stop(self) -> None:
        """Cancel the receive and rediscovery loops, then close the SUB socket."""
        self._running = False
        if self._rediscover_task and not self._rediscover_task.done():
            self._rediscover_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._rediscover_task
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._recv_task
        if self._socket is not None:
            with suppress(Exception):
                self._socket.close()
            self._socket = None
        self._dialed_addresses.clear()
        logger.debug("NngSubscriber: closed")

    async def __aenter__(self) -> NngSubscriber:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        if self._socket is None:
            raise RuntimeError("NngSubscriber is not started. Call start() first.")
        # Register nng-level topic subscriptions for efficient pre-filtering.
        for topic in _nng_topics_for_patterns(event_types):
            if topic not in self._nng_subscribed_topics:
                self._socket.subscribe(topic)
                self._nng_subscribed_topics.add(topic)
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

    async def _recv_loop(self) -> None:
        """Read messages from the SUB socket and dispatch to handlers."""
        while self._running:
            try:
                data = await self._socket.arecv()
            except pynng.Timeout:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self._running:
                    return
                logger.exception("NngSubscriber: receive error; will retry")
                await asyncio.sleep(self._recv_timeout_ms / 1000.0)
                continue
            await self._dispatch(data)

    async def _dispatch(self, data: bytes) -> None:
        """Decode *data* and deliver to all matching subscriptions."""
        event = _decode_message(data)
        if event is None:
            return
        await dispatch_to_subscriptions(event, self._subscriptions, self._ring_buffer_depth, logger)


# ---------------------------------------------------------------------------
# NngTransport — combined publisher + subscriber
# ---------------------------------------------------------------------------


class NngTransport(SleipnirPublisher, SleipnirSubscriber):
    """Combined nng PUB/SUB transport for single-process use.

    Opens both a :class:`NngPublisher` (which binds) and a
    :class:`NngSubscriber` (which dials) at *address*.  Events published
    by this process loop back through nng and are delivered to local
    subscribers as well as remote ones.

    When *service_id* and *registry* are provided, the publisher registers
    itself in the service registry on start, and the subscriber discovers all
    live publishers (including itself) to dial.

    Usage::

        bus = NngTransport("ipc:///tmp/sleipnir.sock")
        async with bus:
            handle = await bus.subscribe(["ravn.*"], my_handler)
            await bus.publish(event)
            await bus.flush()
            await handle.unsubscribe()
    """

    def __init__(
        self,
        address: str = DEFAULT_IPC_ADDRESS,
        recv_timeout_ms: int = DEFAULT_RECV_TIMEOUT_MS,
        bind_retry_delay_s: float = DEFAULT_BIND_RETRY_DELAY_S,
        bind_max_retries: int = DEFAULT_BIND_MAX_RETRIES,
        ring_buffer_depth: int = DEFAULT_RING_BUFFER_DEPTH,
        connect_settle_ms: int = DEFAULT_CONNECT_SETTLE_MS,
        reconnect_min_ms: int = DEFAULT_RECONNECT_MIN_MS,
        reconnect_max_ms: int = DEFAULT_RECONNECT_MAX_MS,
        service_id: str | None = None,
        registry: ServiceRegistry | None = None,
        rediscover_interval_s: float = DEFAULT_REDISCOVER_INTERVAL_S,
        peer_addresses: list[str] | None = None,
    ) -> None:
        _require_pynng()
        self._publisher = NngPublisher(
            address=address,
            bind_retry_delay_s=bind_retry_delay_s,
            bind_max_retries=bind_max_retries,
            service_id=service_id,
            registry=registry,
        )
        self._subscriber = NngSubscriber(
            address=address,
            recv_timeout_ms=recv_timeout_ms,
            ring_buffer_depth=ring_buffer_depth,
            connect_settle_ms=connect_settle_ms,
            reconnect_min_ms=reconnect_min_ms,
            reconnect_max_ms=reconnect_max_ms,
            registry=registry,
            rediscover_interval_s=rediscover_interval_s,
            peer_addresses=peer_addresses,
        )

    async def start(self) -> None:
        """Bind the PUB socket, then dial the SUB socket."""
        await self._publisher.start()
        await self._subscriber.start()

    async def stop(self) -> None:
        """Graceful shutdown: stop subscriber first, then publisher."""
        await self._subscriber.stop()
        await self._publisher.stop()

    async def __aenter__(self) -> NngTransport:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # SleipnirPublisher
    # ------------------------------------------------------------------

    async def publish(self, event: SleipnirEvent) -> None:
        await self._publisher.publish(event)

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        await self._publisher.publish_batch(events)

    # ------------------------------------------------------------------
    # SleipnirSubscriber
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        return await self._subscriber.subscribe(event_types, handler)

    async def flush(self) -> None:
        """Wait until every queued (already-received) event has been processed."""
        await self._subscriber.flush()

    def _remove_subscription(self, sub: _BaseSubscription) -> None:
        self._subscriber._remove_subscription(sub)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _require_pynng() -> None:
    if not _PYNNG_AVAILABLE:  # pragma: no cover
        raise ImportError(
            "pynng is required for the nng transport adapter. Install it with: pip install pynng"
        )


def _encode_message(event: SleipnirEvent) -> bytes:
    """Encode *event* as ``<event_type>\\x00<msgpack_payload>``."""
    return event.event_type.encode() + b"\x00" + serialize(event)


def _decode_message(data: bytes) -> SleipnirEvent | None:
    """Decode a message produced by :func:`_encode_message`.

    Returns ``None`` and logs a warning on malformed input.
    """
    try:
        sep_idx = data.index(b"\x00")
    except ValueError:
        logger.warning("NngTransport: malformed message (no \\x00 separator), dropped")
        return None
    body = data[sep_idx + 1 :]
    try:
        return deserialize(body)
    except Exception:
        logger.exception("NngTransport: deserialization failed, dropped")
        return None


def nng_available() -> bool:
    """Return ``True`` if pynng is installed and the nng transport can be used."""
    return _PYNNG_AVAILABLE

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
from collections.abc import Callable
from contextlib import suppress
from urllib.parse import urlsplit

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
        advertise_host: str | None = None,
    ) -> None:
        _require_pynng()
        self._address = address
        self._bind_retry_delay_s = bind_retry_delay_s
        self._bind_max_retries = bind_max_retries
        self._service_id = service_id
        self._registry = registry
        self._advertise_host = advertise_host
        self._socket: pynng.Pub0 | None = None  # type: ignore[name-defined]
        self._bound_address: str | None = None

    @property
    def bound_address(self) -> str:
        """The address the PUB socket really listens on (port 0 resolved)."""
        if self._bound_address is None:
            raise RuntimeError("NngPublisher is not bound. Call start() or bind() first.")
        return self._bound_address

    @property
    def advertised_address(self) -> str:
        """The address peers on other machines dial: the bound port on ``advertise_host``.

        A loopback or IPC address is advertised as it is bound. A wildcard bind
        (``0.0.0.0``) has no address of its own, so it needs ``advertise_host``; without it
        this raises rather than announce an address nobody can reach.
        """
        return _advertised(self.bound_address, self._advertise_host)

    async def bind(self) -> None:
        """Bind the PUB socket, retrying on transient address-in-use errors (idempotent)."""
        if self._socket is not None:
            return
        socket = pynng.Pub0()
        try:
            for attempt in range(self._bind_max_retries):
                try:
                    socket.listen(self._address)
                    logger.debug("NngPublisher: bound to %s", self._address)
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
        self._socket = socket
        self._bound_address = _resolved_address(self._address, socket)

    async def start(self) -> None:
        """Bind the PUB socket, then register it in the service registry if one is set."""
        await self.bind()
        if self._registry is not None and self._service_id is not None:
            await asyncio.get_running_loop().run_in_executor(
                None, self._registry.register, self._service_id, self.advertised_address
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
            self._bound_address = None
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
        peer_addresses: list[str] | Callable[[], list[str]] | None = None,
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
        # Address -> the dialer that connects to it, so a peer that left can be hung up on.
        self._dialers: dict[str, pynng.Dialer] = {}  # type: ignore[name-defined]
        self._dialed_addresses: set[str] = set()
        # This process's own PUB address (set by the transport once it is bound).
        self._local_address: str | None = None
        self._running = False

    def set_local_address(self, address: str) -> None:
        """Dial this process's own PUB too, so what it publishes reaches its own handlers."""
        self._local_address = address

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

        self._sync_dialers(await self._discover_addresses())

        if self._connect_settle_ms > 0:
            await asyncio.sleep(self._connect_settle_ms / 1000.0)
        self._running = True
        self._recv_task = asyncio.create_task(self._recv_loop(), name="sleipnir-nng-sub-recv")

        # Start periodic re-discovery if registry or peer addresses are configured
        has_peer_source = self._registry is not None or bool(self._peer_addresses)
        if has_peer_source and self._rediscover_interval_s > 0:
            self._rediscover_task = asyncio.create_task(
                self._rediscover_loop(), name="sleipnir-nng-sub-rediscover"
            )

    async def _discover_addresses(self) -> list[str]:
        """Return the publisher addresses to dial right now.

        The union of the service registry (JSON file, if provided), the peer addresses
        (a list, or a provider called on every pass) and this process's own PUB address.
        With none of them known, *self._address* is dialed, which is how a lone
        subscriber of a fixed address works.
        """
        addresses: list[str] = []
        if self._registry is not None:
            loop = asyncio.get_running_loop()
            entries = await loop.run_in_executor(None, self._registry.list_services)
            addresses.extend(e.socket for e in entries)
        addresses.extend(self._resolve_peer_addresses())
        if self._local_address is not None:
            addresses.append(self._local_address)
        if not addresses:
            addresses.append(self._address)
        return list(dict.fromkeys(addresses))

    def _sync_dialers(self, wanted: list[str]) -> None:
        """Dial the addresses not dialed yet and hang up on the ones no longer wanted."""
        wanted_set = set(wanted)
        for addr in wanted:
            if addr in self._dialers:
                continue
            self._dialers[addr] = self._socket.dial(addr, block=False)
            self._dialed_addresses.add(addr)
            logger.info("NngSubscriber: dialing %s", addr)
        for addr in [a for a in self._dialers if a not in wanted_set]:
            dialer = self._dialers.pop(addr)
            self._dialed_addresses.discard(addr)
            with suppress(Exception):
                dialer.close()
            logger.info("NngSubscriber: peer left, hung up on %s", addr)

    async def refresh(self) -> None:
        """Re-read the peers now (a discovery join or leave), instead of at the next pass."""
        if not self._running or self._socket is None:
            return
        self._sync_dialers(await self._discover_addresses())

    def _resolve_peer_addresses(self) -> list[str]:
        """Return the configured peer publishers.

        A callable is re-invoked on every re-discovery pass, so a peer that
        joins after this subscriber started is still dialed.  A plain list is
        fixed at construction, which is why late joiners were previously
        unreachable.
        """
        if callable(self._peer_addresses):
            try:
                return list(self._peer_addresses())
            except Exception:
                # Not "nobody is there": the dialed peers stay as they were.
                logger.exception(
                    "NngSubscriber: the peer address provider failed; keeping the peers "
                    "already dialed"
                )
                return list(self._dialers)
        return list(self._peer_addresses)

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
                self._sync_dialers(await self._discover_addresses())
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
        self._dialers.clear()
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
        peer_addresses: list[str] | Callable[[], list[str]] | None = None,
        advertise_host: str | None = None,
    ) -> None:
        _require_pynng()
        self._publisher = NngPublisher(
            address=address,
            bind_retry_delay_s=bind_retry_delay_s,
            bind_max_retries=bind_max_retries,
            service_id=service_id,
            registry=registry,
            advertise_host=advertise_host,
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

    async def bind(self) -> None:
        """Bind the PUB socket now, so its address can be announced before the mesh starts."""
        await self._publisher.bind()

    @property
    def pub_address(self) -> str:
        """The address other peers dial to hear this process (see ``advertised_address``)."""
        return self._publisher.advertised_address

    async def refresh_peers(self) -> None:
        """Dial peers that joined and hang up on peers that left, right now."""
        await self._subscriber.refresh()

    async def start(self) -> None:
        """Bind the PUB socket, then dial the SUB socket."""
        await self._publisher.start()
        self._subscriber.set_local_address(_dialable(self._publisher.bound_address))
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


_WILDCARD_HOSTS = frozenset({"", "0.0.0.0", "::", "*"})


def _hostport(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _resolved_address(address: str, socket: object) -> str:
    """The address *socket* listens on: *address* with a port 0 replaced by the real port."""
    parts = urlsplit(address)
    if parts.scheme != "tcp":
        return address
    # The address object's ``port`` attribute is in network byte order; its text is not.
    port = str(socket.listeners[0].local_address).rsplit(":", 1)[1]  # type: ignore[attr-defined]
    return f"tcp://{_hostport(parts.hostname or '', int(port))}"


def _advertised(bound: str, advertise_host: str | None) -> str:
    """The address to announce for a socket bound at *bound*."""
    parts = urlsplit(bound)
    if parts.scheme != "tcp":
        return bound
    host = parts.hostname or ""
    if advertise_host:
        return f"tcp://{_hostport(advertise_host, parts.port or 0)}"
    if host in _WILDCARD_HOSTS:
        raise ValueError(
            f"the nng PUB socket listens on every interface ({bound}) and has no address of "
            "its own to announce: set mesh.nng.advertise_host to the host or IP other "
            "machines reach this one on, or bind a specific address in mesh.nng.pub_sub_address"
        )
    return bound


def _dialable(bound: str) -> str:
    """The address this process dials to reach its own PUB (a wildcard is dialed on loopback)."""
    parts = urlsplit(bound)
    if parts.scheme == "tcp" and (parts.hostname or "") in _WILDCARD_HOSTS:
        return f"tcp://127.0.0.1:{parts.port}"
    return bound


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

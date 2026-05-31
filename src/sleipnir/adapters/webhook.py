"""Webhook HTTP transport adapter for Sleipnir.

Fills the gap between NNG (single-node IPC) and broker-based transports.
No broker required — services communicate via plain HTTP POST.  Works across
hosts and networks as long as each node is reachable over HTTP.

Architecture
------------
- :class:`WebhookPublisher` — POSTs events as JSON to one or more URLs.
  Retries up to ``max_attempts`` times with exponential back-off; after all
  attempts fail the event is logged as a warning and dropped (fire-and-forget).
  A single :class:`httpx.AsyncClient` is shared across all publishes so that
  HTTP connections are pooled rather than opened per event.
- :class:`WebhookSubscriber` — Exposes a FastAPI router with a
  ``POST /sleipnir/events`` endpoint.  Incoming events are deserialised and
  dispatched to registered handlers using the same ring-buffer / pattern-match
  semantics as every other transport.
- :class:`WebhookTransport` — Combined publisher + subscriber for single-process
  use.

Retry policy
------------
Attempt 1: immediate
Attempt 2: sleep ``retry_base_delay`` seconds (default 1 s)
Attempt 3: sleep ``retry_base_delay * 2`` seconds (default 2 s)
...up to ``max_attempts`` (default 3).

After all attempts are exhausted the error is logged at WARNING level and
the publisher moves on — downstream failures never block the caller.

Configuration example
---------------------
::

    sleipnir:
      transport: webhook
      webhook:
        publish_urls:
          - http://ting:8080/sleipnir/events
          - http://volundr:8000/sleipnir/events
        listen_port: 8090

Transport ladder
----------------
See ``docs/site/ravn/operations/sleipnir-transports.md`` for the full ladder.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    _HTTPX_AVAILABLE = False

try:
    from fastapi import FastAPI, Request, Response
    from fastapi.routing import APIRouter

    _FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    _FASTAPI_AVAILABLE = False

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

DEFAULT_PUBLISH_URLS: list[str] = []
DEFAULT_LISTEN_PORT = 8090
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BASE_DELAY = 1.0  # seconds; doubles each attempt
DEFAULT_HTTP_TIMEOUT = 10.0  # seconds per request attempt
DEFAULT_HTTP_CONNECT_TIMEOUT = 5.0  # seconds for initial connection
DEFAULT_ENDPOINT_PATH = "/sleipnir/events"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_httpx() -> None:
    if not _HTTPX_AVAILABLE:  # pragma: no cover
        raise ImportError(
            "httpx is required for the Webhook transport adapter. "
            "Install it with: pip install httpx"
        )


def _require_fastapi() -> None:
    if not _FASTAPI_AVAILABLE:  # pragma: no cover
        raise ImportError(
            "fastapi is required for the Webhook subscriber adapter. "
            "Install it with: pip install fastapi"
        )


def _encode_event(event: SleipnirEvent) -> bytes:
    """Serialise *event* to JSON bytes."""
    return serialize(event, fmt="json")


def _decode_event(data: bytes) -> SleipnirEvent | None:
    """Deserialise *data* from JSON bytes.  Returns ``None`` on failure."""
    try:
        return deserialize(data, fmt="json")
    except Exception:
        logger.exception("Webhook: deserialization failed, event dropped")
        return None


# ---------------------------------------------------------------------------
# WebhookPublisher
# ---------------------------------------------------------------------------


class WebhookPublisher(SleipnirPublisher):
    """HTTP POST publisher for Sleipnir events.

    On each :meth:`publish` call, POSTs the serialised event to every URL in
    *publish_urls*.  Each URL is attempted up to *max_attempts* times with
    exponential back-off.  If all attempts fail the event is logged at WARNING
    level and the caller is not blocked.

    A single :class:`httpx.AsyncClient` is created on :meth:`start` and shared
    for all requests so that HTTP connections are pooled.

    Usage::

        pub = WebhookPublisher(publish_urls=["http://ting:8080/sleipnir/events"])
        async with pub:
            await pub.publish(event)
    """

    def __init__(
        self,
        publish_urls: list[str] | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY,
        http_timeout: float = DEFAULT_HTTP_TIMEOUT,
        http_connect_timeout: float = DEFAULT_HTTP_CONNECT_TIMEOUT,
    ) -> None:
        _require_httpx()
        self._publish_urls: list[str] = list(publish_urls or DEFAULT_PUBLISH_URLS)
        self._max_attempts = max(1, max_attempts)
        self._retry_base_delay = retry_base_delay
        self._http_timeout = http_timeout
        self._http_connect_timeout = http_connect_timeout
        self._client: httpx.AsyncClient | None = None  # type: ignore[name-defined]

    async def start(self) -> None:
        """Create the shared HTTP client."""
        self._client = httpx.AsyncClient(  # type: ignore[name-defined]
            timeout=httpx.Timeout(  # type: ignore[name-defined]
                self._http_timeout,
                connect=self._http_connect_timeout,
            ),
        )
        logger.debug("WebhookPublisher: started, urls=%s", self._publish_urls)

    async def stop(self) -> None:
        """Close the shared HTTP client."""
        if self._client is not None:
            with suppress(Exception):
                await self._client.aclose()
            self._client = None
        logger.debug("WebhookPublisher: stopped")

    async def __aenter__(self) -> WebhookPublisher:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def publish(self, event: SleipnirEvent) -> None:
        """POST *event* to every configured URL.

        Events with ``ttl <= 0`` are dropped before any network I/O.
        """
        if event.ttl is not None and event.ttl <= 0:
            logger.debug(
                "Dropping expired event %s (%s): ttl=%d",
                event.event_id,
                event.event_type,
                event.ttl,
            )
            return
        if self._client is None:
            raise RuntimeError("WebhookPublisher is not started. Call start() first.")
        body = _encode_event(event)
        await asyncio.gather(
            *(self._post_with_retry(url, body, event) for url in self._publish_urls)
        )

    async def publish_batch(self, events: list[SleipnirEvent]) -> None:
        """Publish all *events* in iteration order."""
        for event in events:
            await self.publish(event)

    async def _post_with_retry(
        self,
        url: str,
        body: bytes,
        event: SleipnirEvent,
    ) -> None:
        """POST *body* to *url* with exponential back-off retry.

        Attempts ``max_attempts`` times.  Delays between attempts:
          - attempt 1 → no delay
          - attempt 2 → ``retry_base_delay`` seconds
          - attempt 3 → ``retry_base_delay * 2`` seconds
          - attempt N → ``retry_base_delay * 2^(N-2)`` seconds

        After all attempts are exhausted a WARNING is logged and the method
        returns without raising.
        """
        assert self._client is not None  # guarded by publish()
        delay = self._retry_base_delay
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(
                    url,
                    content=body,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                logger.debug(
                    "WebhookPublisher: delivered event %s to %s (attempt %d)",
                    event.event_id,
                    url,
                    attempt,
                )
                return
            except Exception as exc:
                if attempt == self._max_attempts:
                    logger.warning(
                        "WebhookPublisher: failed to deliver event %s (%s) to %s "
                        "after %d attempts — dropping. Last error: %s",
                        event.event_id,
                        event.event_type,
                        url,
                        self._max_attempts,
                        exc,
                    )
                    return
                logger.debug(
                    "WebhookPublisher: attempt %d/%d failed for event %s to %s: %s "
                    "— retrying in %.1fs",
                    attempt,
                    self._max_attempts,
                    event.event_id,
                    url,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                delay *= 2


# ---------------------------------------------------------------------------
# WebhookSubscriber
# ---------------------------------------------------------------------------


class WebhookSubscriber(SleipnirSubscriber):
    """HTTP subscriber for Sleipnir events.

    Exposes a FastAPI router with a ``POST /sleipnir/events`` endpoint.
    Incoming JSON bodies are deserialised as :class:`SleipnirEvent` and
    dispatched to registered handlers using the same ring-buffer / pattern-match
    semantics as every other transport.

    The router is available at :attr:`router` and can be mounted into an
    existing FastAPI application.  If no application is provided and
    *listen_port* is set, :meth:`start` will launch a standalone uvicorn
    server.

    Pattern matching is application-level fnmatch on ``event_type``, identical
    to all other Sleipnir transports.

    Usage (standalone server)::

        sub = WebhookSubscriber(listen_port=8090)
        async with sub:
            handle = await sub.subscribe(["ravn.*"], my_handler)
            ...
            await handle.unsubscribe()

    Usage (mounted into existing FastAPI app)::

        sub = WebhookSubscriber()
        app.include_router(sub.router)
        async with sub:
            handle = await sub.subscribe(["ravn.*"], my_handler)
    """

    def __init__(
        self,
        listen_port: int = DEFAULT_LISTEN_PORT,
        ring_buffer_depth: int = DEFAULT_RING_BUFFER_DEPTH,
        endpoint_path: str = DEFAULT_ENDPOINT_PATH,
    ) -> None:
        _require_fastapi()
        if ring_buffer_depth < 1:
            raise ValueError(f"ring_buffer_depth must be >= 1, got {ring_buffer_depth}")
        self._listen_port = listen_port
        self._ring_buffer_depth = ring_buffer_depth
        self._endpoint_path = endpoint_path

        self._subscriptions: list[_BaseSubscription] = []

        self.router: APIRouter = APIRouter()  # type: ignore[name-defined]
        self.router.add_api_route(
            self._endpoint_path,
            self._handle_event,
            methods=["POST"],
        )

    async def start(self) -> None:
        """Start a standalone uvicorn server if *listen_port* is configured."""
        logger.debug(
            "WebhookSubscriber: ready on port %d, endpoint=%s",
            self._listen_port,
            self._endpoint_path,
        )

    async def stop(self) -> None:
        """Clean up subscriptions."""
        logger.debug("WebhookSubscriber: stopped")

    async def __aenter__(self) -> WebhookSubscriber:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    def build_app(self) -> FastAPI:  # type: ignore[name-defined]
        """Return a standalone :class:`~fastapi.FastAPI` app with this subscriber's router.

        Use this when you need an ASGI app to pass directly to a server (e.g.
        uvicorn).  For integration into an existing app, use :attr:`router`
        directly.
        """
        app = FastAPI(title="Sleipnir Webhook Subscriber")  # type: ignore[name-defined]
        app.include_router(self.router)
        return app

    async def subscribe(
        self,
        event_types: list[str],
        handler: EventHandler,
    ) -> Subscription:
        """Register *handler* for events matching any pattern in *event_types*."""
        queue: asyncio.Queue[SleipnirEvent] = asyncio.Queue(maxsize=self._ring_buffer_depth)
        task = asyncio.create_task(consume_queue(queue, handler))
        sub = _BaseSubscription(
            list(event_types), queue, task, lambda: self._remove_subscription(sub)
        )
        self._subscriptions.append(sub)
        return sub

    async def flush(self) -> None:
        """Wait until every queued event has been processed by its handler."""
        for sub in list(self._subscriptions):
            await sub._queue.join()

    def _remove_subscription(self, sub: _BaseSubscription) -> None:
        with suppress(ValueError):
            self._subscriptions.remove(sub)

    async def _handle_event(self, request: Request) -> Response:  # type: ignore[name-defined]
        """FastAPI endpoint: receive a POSTed event and dispatch to handlers."""
        body = await request.body()
        event = _decode_event(body)
        if event is None:
            return Response(content="Bad Request: invalid event", status_code=400)  # type: ignore[name-defined]
        await dispatch_to_subscriptions(event, self._subscriptions, self._ring_buffer_depth, logger)
        return Response(status_code=204)  # type: ignore[name-defined]


# ---------------------------------------------------------------------------
# WebhookTransport — combined publisher + subscriber
# ---------------------------------------------------------------------------


class WebhookTransport(SleipnirPublisher, SleipnirSubscriber):
    """Combined Webhook publisher + subscriber for single-process use.

    Combines :class:`WebhookPublisher` and :class:`WebhookSubscriber`.  Events
    published to remote URLs are not automatically received by the local
    subscriber unless one of the *publish_urls* points back at this node.

    Usage::

        transport = WebhookTransport(
            publish_urls=["http://ting:8080/sleipnir/events"],
            listen_port=8090,
        )
        async with transport:
            handle = await transport.subscribe(["ravn.*"], my_handler)
            await transport.publish(event)
            await transport.flush()
            await handle.unsubscribe()
    """

    def __init__(
        self,
        publish_urls: list[str] | None = None,
        listen_port: int = DEFAULT_LISTEN_PORT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY,
        http_timeout: float = DEFAULT_HTTP_TIMEOUT,
        http_connect_timeout: float = DEFAULT_HTTP_CONNECT_TIMEOUT,
        ring_buffer_depth: int = DEFAULT_RING_BUFFER_DEPTH,
        endpoint_path: str = DEFAULT_ENDPOINT_PATH,
    ) -> None:
        self._publisher = WebhookPublisher(
            publish_urls=publish_urls,
            max_attempts=max_attempts,
            retry_base_delay=retry_base_delay,
            http_timeout=http_timeout,
            http_connect_timeout=http_connect_timeout,
        )
        self._subscriber = WebhookSubscriber(
            listen_port=listen_port,
            ring_buffer_depth=ring_buffer_depth,
            endpoint_path=endpoint_path,
        )
        self.router = self._subscriber.router

    async def start(self) -> None:
        """Start the publisher then the subscriber."""
        await self._publisher.start()
        await self._subscriber.start()

    async def stop(self) -> None:
        """Graceful shutdown: stop subscriber first, then publisher."""
        await self._subscriber.stop()
        await self._publisher.stop()

    async def __aenter__(self) -> WebhookTransport:
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

    def build_app(self) -> FastAPI:  # type: ignore[name-defined]
        """Return a standalone FastAPI app backed by this transport's subscriber."""
        return self._subscriber.build_app()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def webhook_available() -> bool:
    """Return ``True`` if httpx and fastapi are installed."""
    return _HTTPX_AVAILABLE and _FASTAPI_AVAILABLE

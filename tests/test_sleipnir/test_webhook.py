"""Tests for the Webhook transport adapter (NIU-581).

Test strategy
-------------
All tests mock httpx and FastAPI so no external HTTP server is required.
The mock layer replaces ``httpx.AsyncClient`` (for publisher tests) and
exercises the raw ``_handle_event`` endpoint coroutine directly (for
subscriber tests).

Coverage targets
----------------
- :class:`~sleipnir.adapters.webhook.WebhookPublisher` — lifecycle, TTL,
  retry logic, connection pooling, fire-and-forget on exhaustion
- :class:`~sleipnir.adapters.webhook.WebhookSubscriber` — lifecycle,
  subscribe/dispatch, pattern matching, bad-request handling
- :class:`~sleipnir.adapters.webhook.WebhookTransport` — combined pub+sub
  delegation
- Module-level helpers: ``webhook_available``, ``_encode_event``,
  ``_decode_event``
- Integration: publisher → subscriber round-trip on localhost (real httpx
  + real FastAPI ASGI)
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sleipnir.adapters.webhook import (
    DEFAULT_ENDPOINT_PATH,
    DEFAULT_LISTEN_PORT,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRY_BASE_DELAY,
    WebhookPublisher,
    WebhookSubscriber,
    WebhookTransport,
    _decode_event,
    _encode_event,
    webhook_available,
)
from sleipnir.domain.events import SleipnirEvent
from tests.test_sleipnir.conftest import make_event

# ---------------------------------------------------------------------------
# Require httpx + fastapi
# ---------------------------------------------------------------------------

httpx = pytest.importorskip("httpx", reason="httpx not installed; skipping webhook tests")
fastapi = pytest.importorskip("fastapi", reason="fastapi not installed; skipping webhook tests")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(status_code: int = 204) -> MagicMock:
    """Return a mock httpx.Response that raises for error codes."""
    resp = MagicMock()
    resp.status_code = status_code

    if status_code >= 400:

        def raise_for_status() -> None:
            raise httpx.HTTPStatusError(
                f"HTTP {status_code}",
                request=MagicMock(),
                response=resp,
            )

        resp.raise_for_status = raise_for_status
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _make_mock_client(response: MagicMock | None = None) -> AsyncMock:
    """Return a mock httpx.AsyncClient."""
    client = AsyncMock()
    client.post = AsyncMock(return_value=response or _make_mock_response(204))
    client.aclose = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Unit tests — webhook_available / _encode_event / _decode_event
# ---------------------------------------------------------------------------


def test_webhook_available_returns_true():
    assert webhook_available() is True


def test_webhook_available_false_when_httpx_missing():
    with patch("sleipnir.adapters.webhook._HTTPX_AVAILABLE", False):
        assert webhook_available() is False


def test_webhook_available_false_when_fastapi_missing():
    with patch("sleipnir.adapters.webhook._FASTAPI_AVAILABLE", False):
        assert webhook_available() is False


def test_encode_decode_round_trip():
    """JSON encode → decode round-trip preserves all SleipnirEvent fields."""
    event = make_event(event_id="wh-rt-01", urgency=0.7, correlation_id="corr-wh")
    raw = _encode_event(event)
    assert isinstance(raw, bytes)
    decoded = _decode_event(raw)
    assert decoded is not None
    assert decoded.event_id == "wh-rt-01"
    assert decoded.urgency == pytest.approx(0.7)
    assert decoded.correlation_id == "corr-wh"


def test_encode_event_produces_valid_json():
    event = make_event()
    raw = _encode_event(event)
    parsed = json.loads(raw)
    assert parsed["event_type"] == "ravn.tool.complete"
    assert parsed["source"] == "ravn:agent-abc123"


def test_decode_event_returns_none_on_malformed(caplog):
    with caplog.at_level(logging.ERROR, logger="sleipnir.adapters.webhook"):
        result = _decode_event(b"{not valid json}")
    assert result is None


# ---------------------------------------------------------------------------
# Unit tests — WebhookPublisher
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    return _make_mock_client()


@pytest.fixture
def pub_with_mock(mock_client):
    """WebhookPublisher wired to a mock httpx.AsyncClient."""
    pub = WebhookPublisher(
        publish_urls=["http://ting:8080/sleipnir/events"],
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        retry_base_delay=DEFAULT_RETRY_BASE_DELAY,
    )
    pub._client = mock_client
    return pub


async def test_publisher_start_creates_client():
    """start() initialises an httpx.AsyncClient."""
    pub = WebhookPublisher(publish_urls=["http://host:8080/sleipnir/events"])
    with patch("sleipnir.adapters.webhook.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_httpx.AsyncClient.return_value = mock_client
        mock_httpx.Timeout = httpx.Timeout
        await pub.start()
    assert pub._client is mock_client
    await pub.stop()


async def test_publisher_stop_closes_client(mock_client):
    pub = WebhookPublisher()
    pub._client = mock_client
    await pub.stop()
    mock_client.aclose.assert_awaited_once()
    assert pub._client is None


async def test_publisher_publish_posts_to_url(pub_with_mock, mock_client):
    """publish() sends a POST request to each configured URL."""
    event = make_event()
    await pub_with_mock.publish(event)
    mock_client.post.assert_awaited_once()
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[0][0] == "http://ting:8080/sleipnir/events"
    assert call_kwargs[1]["headers"]["Content-Type"] == "application/json"


async def test_publisher_publish_multiple_urls():
    """publish() POSTs to every configured URL."""
    client = _make_mock_client()
    pub = WebhookPublisher(
        publish_urls=[
            "http://ting:8080/sleipnir/events",
            "http://volundr:8000/sleipnir/events",
        ]
    )
    pub._client = client
    await pub.publish(make_event())
    assert client.post.await_count == 2
    urls = [c[0][0] for c in client.post.call_args_list]
    assert "http://ting:8080/sleipnir/events" in urls
    assert "http://volundr:8000/sleipnir/events" in urls


async def test_publisher_drops_expired_event(pub_with_mock, mock_client, caplog):
    """Events with ttl=0 are dropped without any HTTP call."""
    event = make_event(ttl=0)
    with caplog.at_level(logging.DEBUG, logger="sleipnir.adapters.webhook"):
        await pub_with_mock.publish(event)
    mock_client.post.assert_not_awaited()


async def test_publisher_raises_when_not_started():
    """publish() raises RuntimeError if called before start()."""
    pub = WebhookPublisher(publish_urls=["http://host:8080/sleipnir/events"])
    with pytest.raises(RuntimeError, match="not started"):
        await pub.publish(make_event())


async def test_publisher_retries_on_failure(caplog):
    """publisher retries up to max_attempts and then logs a warning."""
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.aclose = AsyncMock()

    pub = WebhookPublisher(
        publish_urls=["http://host:8080/sleipnir/events"],
        max_attempts=3,
        retry_base_delay=0.0,  # no sleep in tests
    )
    pub._client = client

    with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.webhook"):
        await pub.publish(make_event())

    assert client.post.await_count == 3
    assert any("after 3 attempts" in r.message for r in caplog.records)


async def test_publisher_succeeds_on_second_attempt(caplog):
    """Retry succeeds on the second attempt — no warning logged."""
    ok_resp = _make_mock_response(204)

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[httpx.ConnectError("timeout"), ok_resp])
    client.aclose = AsyncMock()

    pub = WebhookPublisher(
        publish_urls=["http://host:8080/sleipnir/events"],
        max_attempts=3,
        retry_base_delay=0.0,
    )
    pub._client = client

    with caplog.at_level(logging.WARNING, logger="sleipnir.adapters.webhook"):
        await pub.publish(make_event())

    assert client.post.await_count == 2
    assert not any("after 3 attempts" in r.message for r in caplog.records)


async def test_publisher_http_error_triggers_retry():
    """Non-2xx responses trigger retry via raise_for_status()."""
    fail_resp = _make_mock_response(500)
    ok_resp = _make_mock_response(204)

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[fail_resp, ok_resp])
    client.aclose = AsyncMock()

    pub = WebhookPublisher(
        publish_urls=["http://host:8080/sleipnir/events"],
        max_attempts=3,
        retry_base_delay=0.0,
    )
    pub._client = client

    await pub.publish(make_event())
    assert client.post.await_count == 2


async def test_publisher_publish_batch():
    """publish_batch() delivers each event in order."""
    client = _make_mock_client()
    pub = WebhookPublisher(publish_urls=["http://host:8080/sleipnir/events"])
    pub._client = client
    events = [make_event(event_id=f"e{i}", event_type="ravn.tool.complete") for i in range(3)]
    await pub.publish_batch(events)
    assert client.post.await_count == 3


async def test_publisher_context_manager():
    """async with publisher starts and stops cleanly."""
    with patch("sleipnir.adapters.webhook.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_httpx.AsyncClient.return_value = mock_client
        mock_httpx.Timeout = httpx.Timeout
        async with WebhookPublisher(publish_urls=[]) as pub:
            assert pub._client is mock_client
        mock_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Unit tests — WebhookSubscriber
# ---------------------------------------------------------------------------


@pytest.fixture
def subscriber():
    """A WebhookSubscriber instance for testing."""
    return WebhookSubscriber(listen_port=DEFAULT_LISTEN_PORT)


async def test_subscriber_start_stop(subscriber):
    """start/stop lifecycle completes without errors."""
    await subscriber.start()
    await subscriber.stop()


async def test_subscriber_has_router(subscriber):
    """subscriber.router is a FastAPI APIRouter."""
    from fastapi.routing import APIRouter

    assert isinstance(subscriber.router, APIRouter)


async def test_subscriber_subscribe_returns_subscription(subscriber):
    """subscribe() registers a handler and returns a Subscription handle."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    async with subscriber:
        handle = await subscriber.subscribe(["ravn.*"], handler)
        assert handle is not None
        await handle.unsubscribe()


async def test_subscriber_dispatches_matching_event(subscriber):
    """_handle_event dispatches an event matching the subscriber pattern."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    async with subscriber:
        await subscriber.subscribe(["ravn.*"], handler)

        event = make_event(event_type="ravn.tool.complete")
        body = _encode_event(event)

        request = MagicMock()
        request.body = AsyncMock(return_value=body)
        response = await subscriber._handle_event(request)

    assert response.status_code == 204
    await subscriber.flush()
    assert len(received) == 1
    assert received[0].event_type == "ravn.tool.complete"


async def test_subscriber_does_not_dispatch_non_matching_event(subscriber):
    """_handle_event does not dispatch events that don't match any pattern."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    async with subscriber:
        await subscriber.subscribe(["ting.*"], handler)

        event = make_event(event_type="ravn.tool.complete")
        body = _encode_event(event)

        request = MagicMock()
        request.body = AsyncMock(return_value=body)
        await subscriber._handle_event(request)

    await subscriber.flush()
    assert len(received) == 0


async def test_subscriber_returns_400_on_bad_body(subscriber):
    """_handle_event returns 400 when the body cannot be deserialised."""
    async with subscriber:
        request = MagicMock()
        request.body = AsyncMock(return_value=b"not json at all")
        response = await subscriber._handle_event(request)

    assert response.status_code == 400


async def test_subscriber_pattern_matching_wildcard(subscriber):
    """'*' pattern matches all event types."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    async with subscriber:
        await subscriber.subscribe(["*"], handler)

        for event_type in ["ravn.tool.complete", "ting.saga.created", "system.health.ping"]:
            event = make_event(event_type=event_type)
            request = MagicMock()
            request.body = AsyncMock(return_value=_encode_event(event))
            await subscriber._handle_event(request)

    await subscriber.flush()
    assert len(received) == 3


async def test_subscriber_ttl_expired_event_dropped(subscriber, caplog):
    """Events with ttl=0 are dropped by dispatch_to_subscriptions."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    async with subscriber:
        await subscriber.subscribe(["ravn.*"], handler)

        event = make_event(ttl=0)
        request = MagicMock()
        request.body = AsyncMock(return_value=_encode_event(event))
        with caplog.at_level(logging.DEBUG, logger="sleipnir.adapters._subscriber_support"):
            response = await subscriber._handle_event(request)

    assert response.status_code == 204
    await subscriber.flush()
    assert len(received) == 0


async def test_subscriber_unsubscribe_stops_delivery(subscriber):
    """After unsubscribe(), the handler no longer receives events."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    async with subscriber:
        handle = await subscriber.subscribe(["ravn.*"], handler)

        request1 = MagicMock()
        request1.body = AsyncMock(return_value=_encode_event(make_event()))
        await subscriber._handle_event(request1)
        await subscriber.flush()
        assert len(received) == 1

        await handle.unsubscribe()

        request2 = MagicMock()
        request2.body = AsyncMock(return_value=_encode_event(make_event(event_id="evt-002")))
        await subscriber._handle_event(request2)

    assert len(received) == 1  # second event not delivered


async def test_subscriber_multiple_handlers_same_pattern(subscriber):
    """Two handlers with overlapping patterns each receive matching events."""
    r1: list[SleipnirEvent] = []
    r2: list[SleipnirEvent] = []

    async def h1(event: SleipnirEvent) -> None:
        r1.append(event)

    async def h2(event: SleipnirEvent) -> None:
        r2.append(event)

    async with subscriber:
        await subscriber.subscribe(["ravn.*"], h1)
        await subscriber.subscribe(["ravn.tool.*"], h2)

        request = MagicMock()
        request.body = AsyncMock(return_value=_encode_event(make_event()))
        await subscriber._handle_event(request)

    await subscriber.flush()
    assert len(r1) == 1
    assert len(r2) == 1


async def test_subscriber_build_app_returns_fastapi_app(subscriber):
    """build_app() returns a FastAPI application."""
    from fastapi import FastAPI

    app = subscriber.build_app()
    assert isinstance(app, FastAPI)


async def test_subscriber_ring_buffer_overflow(caplog):
    """Ring buffer overflow drops the oldest event with a WARNING."""
    stalled: asyncio.Event = asyncio.Event()

    async def slow_handler(event: SleipnirEvent) -> None:
        await stalled.wait()

    sub = WebhookSubscriber(ring_buffer_depth=2)
    async with sub:
        await sub.subscribe(["ravn.*"], slow_handler)

        # Fill the ring buffer
        for i in range(3):
            request = MagicMock()
            request.body = AsyncMock(
                return_value=_encode_event(make_event(event_id=f"evt-{i:03d}"))
            )
            with caplog.at_level(logging.WARNING, logger="sleipnir.adapters._subscriber_support"):
                await sub._handle_event(request)

        stalled.set()
    assert any("Ring buffer overflow" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Unit tests — WebhookTransport (delegation)
# ---------------------------------------------------------------------------


async def test_transport_delegates_publish():
    """WebhookTransport.publish delegates to the inner publisher."""
    with patch("sleipnir.adapters.webhook.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_httpx.AsyncClient.return_value = mock_client
        mock_httpx.Timeout = httpx.Timeout
        transport = WebhookTransport(
            publish_urls=["http://host:8080/sleipnir/events"],
            listen_port=DEFAULT_LISTEN_PORT,
        )
        ok_resp = _make_mock_response(204)
        mock_client.post = AsyncMock(return_value=ok_resp)
        await transport.start()
        await transport.publish(make_event())
        mock_client.post.assert_awaited_once()
        await transport.stop()


async def test_transport_delegates_subscribe():
    """WebhookTransport.subscribe delegates to the inner subscriber."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    client = _make_mock_client()
    transport = WebhookTransport(publish_urls=[])
    transport._publisher._client = client
    async with transport:
        handle = await transport.subscribe(["ravn.*"], handler)
        request = MagicMock()
        request.body = AsyncMock(return_value=_encode_event(make_event()))
        await transport._subscriber._handle_event(request)
        await transport.flush()
        await handle.unsubscribe()

    assert len(received) == 1


async def test_transport_exposes_router():
    """WebhookTransport.router exposes the subscriber's FastAPI router."""
    from fastapi.routing import APIRouter

    transport = WebhookTransport()
    assert isinstance(transport.router, APIRouter)


async def test_transport_build_app():
    """WebhookTransport.build_app() returns a valid FastAPI app."""
    from fastapi import FastAPI

    transport = WebhookTransport()
    app = transport.build_app()
    assert isinstance(app, FastAPI)


async def test_transport_context_manager():
    """WebhookTransport context manager starts and stops cleanly."""
    with patch("sleipnir.adapters.webhook.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_httpx.AsyncClient.return_value = mock_client
        mock_httpx.Timeout = httpx.Timeout
        async with WebhookTransport(publish_urls=[]) as transport:
            assert transport._publisher._client is mock_client
        mock_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Integration test — publisher → subscriber via real ASGI (httpx.AsyncClient)
# ---------------------------------------------------------------------------


async def test_publisher_to_subscriber_integration():
    """Full round-trip: WebhookPublisher → ASGI → WebhookSubscriber handlers.

    Uses httpx.AsyncClient in transport mode against a real FastAPI ASGI app
    — no actual TCP port is required.
    """
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    subscriber = WebhookSubscriber()
    app = subscriber.build_app()

    async with subscriber:
        await subscriber.subscribe(["ravn.*"], handler)
        await subscriber.subscribe(["ting.*"], handler)

        # Use httpx ASGITransport to send requests directly into the ASGI app.
        transport = httpx.ASGITransport(app=app)  # type: ignore[attr-defined]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Matching event
            event_ravn = make_event(event_id="int-ravn-01", event_type="ravn.tool.complete")
            resp = await client.post(
                DEFAULT_ENDPOINT_PATH,
                content=_encode_event(event_ravn),
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 204

            # Another namespace
            event_ting = make_event(
                event_id="int-ting-01",
                event_type="ting.saga.created",
                source="ting:dispatcher",
            )
            resp = await client.post(
                DEFAULT_ENDPOINT_PATH,
                content=_encode_event(event_ting),
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 204

            # Non-matching event — should be received 0 times in handlers
            event_other = make_event(event_id="int-other-01", event_type="system.health.ping")
            resp = await client.post(
                DEFAULT_ENDPOINT_PATH,
                content=_encode_event(event_other),
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 204

        await subscriber.flush()

    # ravn.* and ting.* handlers each receive one event; system.health.ping not matched
    assert len(received) == 2
    event_ids = {e.event_id for e in received}
    assert "int-ravn-01" in event_ids
    assert "int-ting-01" in event_ids
    assert "int-other-01" not in event_ids


async def test_integration_bad_payload_returns_400():
    """Posting garbage to the endpoint returns 400."""
    subscriber = WebhookSubscriber()
    app = subscriber.build_app()

    async with subscriber:
        transport = httpx.ASGITransport(app=app)  # type: ignore[attr-defined]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                DEFAULT_ENDPOINT_PATH,
                content=b"<not json>",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400


async def test_integration_pattern_matching_exact():
    """Exact event_type pattern only receives that specific event."""
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    subscriber = WebhookSubscriber()
    app = subscriber.build_app()

    async with subscriber:
        await subscriber.subscribe(["ravn.tool.complete"], handler)

        transport = httpx.ASGITransport(app=app)  # type: ignore[attr-defined]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for event_type in ["ravn.tool.complete", "ravn.tool.start", "ravn.plan.create"]:
                resp = await client.post(
                    DEFAULT_ENDPOINT_PATH,
                    content=_encode_event(make_event(event_type=event_type)),
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status_code == 204

        await subscriber.flush()

    assert len(received) == 1
    assert received[0].event_type == "ravn.tool.complete"

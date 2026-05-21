"""Tests for WebhookNotificationAdapter."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from ting.adapters.webhook_notification import WebhookNotificationAdapter
from ting.ports.notification_channel import Notification, NotificationUrgency


def _notification(urgency: NotificationUrgency = NotificationUrgency.HIGH) -> Notification:
    return Notification(
        title="Run ready for review",
        body="Run NIU-100 is ready for review.",
        urgency=urgency,
        owner_id="dev-user",
        event_type="run.state_changed",
        metadata={"tracker_id": "NIU-100", "pr_url": "https://example.com/pr/1"},
    )


@pytest.mark.asyncio
async def test_send_posts_json_to_url() -> None:
    received: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received["url"] = str(request.url)
        received["body"] = json.loads(request.content.decode())
        received["headers"] = dict(request.headers)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    adapter = WebhookNotificationAdapter(url="https://example.com/hook")
    adapter._client = httpx.AsyncClient(transport=transport)

    await adapter.send(_notification())
    await adapter.close()

    assert received["url"] == "https://example.com/hook"
    body = received["body"]
    assert isinstance(body, dict)
    assert body["title"] == "Run ready for review"
    assert body["event_type"] == "run.state_changed"
    assert body["urgency"] == "high"
    assert body["metadata"]["tracker_id"] == "NIU-100"
    assert "X-Niuu-Signature" not in received["headers"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_send_signs_body_when_secret_set() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["raw_body"] = request.content
        captured["headers"] = dict(request.headers)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    adapter = WebhookNotificationAdapter(
        url="https://example.com/hook",
        secret="topsecret",
    )
    adapter._client = httpx.AsyncClient(transport=transport)

    await adapter.send(_notification())
    await adapter.close()

    raw_body = captured["raw_body"]
    assert isinstance(raw_body, bytes)
    expected_sig = hmac.new(b"topsecret", raw_body, hashlib.sha256).hexdigest()
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-niuu-signature"] == f"sha256={expected_sig}"


@pytest.mark.asyncio
async def test_should_notify_respects_min_urgency() -> None:
    adapter = WebhookNotificationAdapter(url="https://example.com/hook", min_urgency="medium")

    assert adapter.should_notify(_notification(NotificationUrgency.HIGH))
    assert adapter.should_notify(_notification(NotificationUrgency.MEDIUM))
    assert not adapter.should_notify(_notification(NotificationUrgency.LOW))

    await adapter.close()


@pytest.mark.asyncio
async def test_send_skips_when_url_empty(caplog: pytest.LogCaptureFixture) -> None:
    adapter = WebhookNotificationAdapter(url="")
    with caplog.at_level("WARNING"):
        await adapter.send(_notification())
    await adapter.close()
    assert "url not configured" in caplog.text


@pytest.mark.asyncio
async def test_send_swallows_http_errors(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    transport = httpx.MockTransport(handler)
    adapter = WebhookNotificationAdapter(url="https://example.com/hook")
    adapter._client = httpx.AsyncClient(transport=transport)

    with caplog.at_level("WARNING"):
        await adapter.send(_notification())
    await adapter.close()
    assert "returned 503" in caplog.text


@pytest.mark.asyncio
async def test_send_swallows_network_errors(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure")

    transport = httpx.MockTransport(handler)
    adapter = WebhookNotificationAdapter(url="https://example.com/hook")
    adapter._client = httpx.AsyncClient(transport=transport)

    with caplog.at_level("WARNING"):
        await adapter.send(_notification())
    await adapter.close()
    assert "Failed to deliver webhook" in caplog.text


@pytest.mark.asyncio
async def test_send_includes_extra_headers() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    adapter = WebhookNotificationAdapter(
        url="https://example.com/hook",
        headers={"X-Custom-Token": "abc123"},
    )
    adapter._client = httpx.AsyncClient(transport=transport)

    await adapter.send(_notification())
    await adapter.close()

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-custom-token"] == "abc123"

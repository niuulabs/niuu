"""Tests for WebhookNotificationAdapter."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from niuu.ports.notifications import NotificationDeliveryError
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
async def test_send_without_url_raises() -> None:
    adapter = WebhookNotificationAdapter(url="")
    with pytest.raises(NotificationDeliveryError, match="no url configured") as raised:
        await adapter.send(_notification())
    await adapter.close()
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_send_raises_on_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    transport = httpx.MockTransport(handler)
    adapter = WebhookNotificationAdapter(url="https://example.com/hook/secret-path")
    adapter._client = httpx.AsyncClient(transport=transport)

    with pytest.raises(NotificationDeliveryError, match="HTTP 503") as raised:
        await adapter.send(_notification())
    await adapter.close()
    assert raised.value.retryable is True
    assert "secret-path" not in str(raised.value)


@pytest.mark.asyncio
async def test_send_raises_on_network_errors_without_the_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure for https://example.com/hook/secret-path")

    transport = httpx.MockTransport(handler)
    adapter = WebhookNotificationAdapter(url="https://example.com/hook/secret-path")
    adapter._client = httpx.AsyncClient(transport=transport)

    with pytest.raises(NotificationDeliveryError, match="ConnectError") as raised:
        await adapter.send(_notification())
    await adapter.close()
    assert "secret-path" not in str(raised.value)
    assert raised.value.__cause__ is None


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

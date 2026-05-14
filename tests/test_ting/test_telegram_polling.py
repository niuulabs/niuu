"""Tests for TelegramPollingService — the polling shim."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ting.adapters.telegram_polling import TelegramPollingService


def _make_service(
    *, secret: str = "", self_url: str = "http://127.0.0.1:8080"
) -> TelegramPollingService:
    return TelegramPollingService(
        bot_token="dummy:token",
        webhook_secret=secret,
        self_url=self_url,
    )


@pytest.mark.asyncio
async def test_forward_update_posts_to_webhook_endpoint() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    svc = _make_service()
    svc._client = httpx.AsyncClient(transport=transport)

    update = MagicMock()
    update.to_dict.return_value = {"update_id": 42, "message": {"text": "/status"}}

    await svc._forward_update(update, context=None)
    await svc._client.aclose()

    assert captured["url"] == "http://127.0.0.1:8080/api/v1/ting/telegram/webhook"
    assert captured["body"] == {"update_id": 42, "message": {"text": "/status"}}
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "x-telegram-bot-api-secret-token" not in headers


@pytest.mark.asyncio
async def test_forward_update_includes_secret_header() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    svc = _make_service(secret="topsecret")
    svc._client = httpx.AsyncClient(transport=transport)

    update = MagicMock()
    update.to_dict.return_value = {"update_id": 1}

    await svc._forward_update(update, context=None)
    await svc._client.aclose()

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-telegram-bot-api-secret-token"] == "topsecret"


@pytest.mark.asyncio
async def test_forward_update_swallows_router_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    transport = httpx.MockTransport(handler)
    svc = _make_service()
    svc._client = httpx.AsyncClient(transport=transport)

    update = MagicMock()
    update.to_dict.return_value = {"update_id": 1}

    with caplog.at_level("WARNING"):
        await svc._forward_update(update, context=None)
    await svc._client.aclose()

    assert "Webhook router returned 503" in caplog.text


@pytest.mark.asyncio
async def test_forward_update_swallows_network_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns fail")

    transport = httpx.MockTransport(handler)
    svc = _make_service()
    svc._client = httpx.AsyncClient(transport=transport)

    update = MagicMock()
    update.to_dict.return_value = {"update_id": 1}

    with caplog.at_level("WARNING"):
        await svc._forward_update(update, context=None)
    await svc._client.aclose()

    assert "Failed to forward Telegram update" in caplog.text


@pytest.mark.asyncio
async def test_forward_update_drops_unserialisable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: dict[str, object] = {"called": False}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["called"] = True
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    svc = _make_service()
    svc._client = httpx.AsyncClient(transport=transport)

    update = MagicMock()
    update.to_dict.side_effect = RuntimeError("nope")

    with caplog.at_level("WARNING"):
        await svc._forward_update(update, context=None)
    await svc._client.aclose()

    assert captured["called"] is False
    assert "Failed to serialise Telegram update" in caplog.text


@pytest.mark.asyncio
async def test_start_skips_when_no_token(caplog: pytest.LogCaptureFixture) -> None:
    svc = TelegramPollingService(
        bot_token="",
        webhook_secret="",
        self_url="http://127.0.0.1:8080",
    )
    with caplog.at_level("INFO"):
        await svc.start()
    assert svc._started is False
    assert "bot_token not configured" in caplog.text
    await svc.stop()


@pytest.mark.asyncio
async def test_stop_when_never_started_is_safe() -> None:
    svc = _make_service()
    # Has not started; stop() should still close the http client cleanly.
    await svc.stop()
    assert svc._started is False


@pytest.mark.asyncio
async def test_stop_tears_down_application_cleanly() -> None:
    svc = _make_service()
    mock_app = MagicMock()
    mock_app.updater = MagicMock()
    mock_app.updater.stop = AsyncMock()
    mock_app.stop = AsyncMock()
    mock_app.shutdown = AsyncMock()
    svc._application = mock_app
    svc._started = True

    await svc.stop()

    mock_app.updater.stop.assert_awaited_once()
    mock_app.stop.assert_awaited_once()
    mock_app.shutdown.assert_awaited_once()
    assert svc._application is None
    assert svc._started is False

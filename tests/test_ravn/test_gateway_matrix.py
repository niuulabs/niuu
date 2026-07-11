"""Tests for the Matrix gateway adapter."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.adapters.channels.gateway_matrix import MatrixGateway, _quote
from ravn.config import MatrixChannelConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    user_id_env: str = "MX_USER",
    access_token_env: str = "MX_TOKEN",
    e2e: bool = False,
) -> MatrixChannelConfig:
    return MatrixChannelConfig(
        enabled=True,
        homeserver="https://matrix.example.com",
        user_id_env=user_id_env,
        access_token_env=access_token_env,
        e2e=e2e,
        sync_timeout_ms=100,
        retry_delay=0.01,
        message_max_chars=32000,
    )


def _make_gateway_mock(response: str = "agent reply") -> MagicMock:
    gw = MagicMock()
    gw.handle_message = AsyncMock(return_value=response)
    return gw


def _make_http_client(
    get_json: dict | None = None,
    put_json: dict | None = None,
    post_json: dict | None = None,
) -> AsyncMock:
    get_resp = MagicMock()
    get_resp.raise_for_status = MagicMock()
    get_resp.json = MagicMock(return_value=get_json or {"next_batch": "t1", "rooms": {}})

    put_resp = MagicMock()
    put_resp.raise_for_status = MagicMock()
    put_resp.json = MagicMock(return_value=put_json or {"event_id": "$evt1"})

    post_resp = MagicMock()
    post_resp.raise_for_status = MagicMock()
    post_resp.json = MagicMock(return_value=post_json or {"content_uri": "mxc://example/abc"})

    client = AsyncMock()
    client.get = AsyncMock(return_value=get_resp)
    client.put = AsyncMock(return_value=put_resp)
    client.post = AsyncMock(return_value=post_resp)
    return client


# ---------------------------------------------------------------------------
# on_message
# ---------------------------------------------------------------------------


def test_on_message_registers_handler():
    adapter = MatrixGateway(_make_config(), _make_gateway_mock())
    handler = AsyncMock()
    adapter.on_message(handler)
    assert adapter._handler is handler


# ---------------------------------------------------------------------------
# start() — missing token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_early_when_no_token(caplog):
    import logging

    cfg = _make_config(access_token_env="MX_MISSING_TOKEN_XYZ")
    adapter = MatrixGateway(cfg, _make_gateway_mock())

    with caplog.at_level(logging.ERROR):
        await adapter.start()

    assert "MX_MISSING_TOKEN_XYZ" in caplog.text
    assert adapter._task is None


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_e2e_is_rejected_instead_of_sending_plaintext():
    with pytest.raises(ValueError, match="e2e"):
        _make_config(e2e=True)


# ---------------------------------------------------------------------------
# stop() — not started
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_when_not_started():
    adapter = MatrixGateway(_make_config(), _make_gateway_mock())
    await adapter.stop()  # should not raise


# ---------------------------------------------------------------------------
# send_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_text_puts_message_event(monkeypatch):
    monkeypatch.setenv("MX_TOKEN", "tok")
    client = _make_http_client()
    adapter = MatrixGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._access_token = "tok"

    await adapter.send_text("!room:example.com", "hello matrix")

    assert client.put.called
    url = client.put.call_args[0][0]
    assert "rooms" in url
    assert "send" in url
    body = client.put.call_args[1]["json"]
    assert body["body"] == "hello matrix"
    assert body["msgtype"] == "m.text"


@pytest.mark.asyncio
async def test_send_text_truncates_long_message(monkeypatch):
    monkeypatch.setenv("MX_TOKEN", "tok")
    client = _make_http_client()
    cfg = MatrixChannelConfig(
        enabled=True,
        homeserver="https://matrix.example.com",
        user_id_env="MX_USER",
        access_token_env="MX_TOKEN",
        message_max_chars=10,
        retry_delay=0.0,
    )
    adapter = MatrixGateway(cfg, _make_gateway_mock(), http_client=client)
    adapter._access_token = "tok"

    await adapter.send_text("!r:h", "x" * 20)

    body = client.put.call_args[1]["json"]["body"]
    assert len(body) <= 10
    assert body.endswith("...")


# ---------------------------------------------------------------------------
# send_image
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_image_uploads_then_sends_event(monkeypatch):
    monkeypatch.setenv("MX_TOKEN", "tok")
    client = _make_http_client(post_json={"content_uri": "mxc://host/abc"})
    adapter = MatrixGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._access_token = "tok"

    await adapter.send_image("!r:h", b"\x89PNG", caption="test")

    # First post = media upload, then put = message event
    assert client.post.called
    assert client.put.called
    body = client.put.call_args[1]["json"]
    assert body["msgtype"] == "m.image"
    assert body["url"] == "mxc://host/abc"


# ---------------------------------------------------------------------------
# send_audio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_audio_uploads_then_sends_event(monkeypatch):
    monkeypatch.setenv("MX_TOKEN", "tok")
    client = _make_http_client(post_json={"content_uri": "mxc://host/ogg"})
    adapter = MatrixGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._access_token = "tok"

    await adapter.send_audio("!r:h", b"OGG")

    assert client.post.called
    assert client.put.called
    body = client.put.call_args[1]["json"]
    assert body["msgtype"] == "m.audio"


# ---------------------------------------------------------------------------
# _handle_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_event_dispatches_text_message(monkeypatch):
    monkeypatch.setenv("MX_TOKEN", "tok")
    client = _make_http_client()
    gw = _make_gateway_mock("reply")
    cfg = _make_config()
    adapter = MatrixGateway(cfg, gw, http_client=client)
    adapter._access_token = "tok"
    adapter._user_id = "@bot:example.com"

    handler = AsyncMock()
    adapter.on_message(handler)

    event: dict[str, Any] = {
        "type": "m.room.message",
        "sender": "@alice:example.com",
        "content": {"msgtype": "m.text", "body": "hello matrix"},
    }
    await adapter._handle_event("!room:example.com", event)

    gw.handle_message.assert_awaited_once_with("matrix:!room:example.com", "hello matrix")
    handler.assert_awaited_once()
    assert client.put.called


@pytest.mark.asyncio
async def test_handle_event_ignores_own_messages():
    gw = _make_gateway_mock()
    adapter = MatrixGateway(_make_config(), gw)
    adapter._user_id = "@bot:example.com"

    event: dict[str, Any] = {
        "type": "m.room.message",
        "sender": "@bot:example.com",
        "content": {"msgtype": "m.text", "body": "own message"},
    }
    await adapter._handle_event("!room:h", event)
    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_event_ignores_non_message_events():
    gw = _make_gateway_mock()
    adapter = MatrixGateway(_make_config(), gw)
    adapter._user_id = "@bot:h"

    event: dict[str, Any] = {
        "type": "m.room.member",
        "sender": "@alice:h",
        "content": {"membership": "join"},
    }
    await adapter._handle_event("!room:h", event)
    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_event_ignores_non_text_msgtype():
    gw = _make_gateway_mock()
    adapter = MatrixGateway(_make_config(), gw)
    adapter._user_id = "@bot:h"

    event: dict[str, Any] = {
        "type": "m.room.message",
        "sender": "@alice:h",
        "content": {"msgtype": "m.image", "url": "mxc://h/abc"},
    }
    await adapter._handle_event("!room:h", event)
    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_event_agent_exception(monkeypatch):
    monkeypatch.setenv("MX_TOKEN", "tok")
    client = _make_http_client()
    gw = MagicMock()
    gw.handle_message = AsyncMock(side_effect=RuntimeError("crash"))
    adapter = MatrixGateway(_make_config(), gw, http_client=client)
    adapter._access_token = "tok"
    adapter._user_id = "@bot:h"

    event: dict[str, Any] = {
        "type": "m.room.message",
        "sender": "@alice:h",
        "content": {"msgtype": "m.text", "body": "hi"},
    }
    # Should not raise
    await adapter._handle_event("!room:h", event)
    # Error fallback sent
    assert client.put.called


# ---------------------------------------------------------------------------
# _sync_once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_once_processes_timeline_events(monkeypatch):
    monkeypatch.setenv("MX_TOKEN", "tok")
    gw = _make_gateway_mock("ok")
    client = _make_http_client(
        get_json={
            "next_batch": "t2",
            "rooms": {
                "join": {
                    "!room:h": {
                        "timeline": {
                            "events": [
                                {
                                    "type": "m.room.message",
                                    "sender": "@alice:h",
                                    "content": {"msgtype": "m.text", "body": "hello"},
                                }
                            ]
                        }
                    }
                }
            },
        }
    )
    adapter = MatrixGateway(_make_config(), gw, http_client=client)
    adapter._access_token = "tok"
    adapter._user_id = "@bot:h"

    await adapter._sync_once()

    assert adapter._next_batch == "t2"
    gw.handle_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# _quote helper
# ---------------------------------------------------------------------------


def test_quote_encodes_room_id():
    result = _quote("!abc:matrix.example.com")
    assert "!" not in result or "%21" in result
    assert ":" not in result


# ---------------------------------------------------------------------------
# run() — no token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_cleanly_when_no_token():
    cfg = _make_config(access_token_env="MX_MISSING_XYZ")
    adapter = MatrixGateway(cfg, _make_gateway_mock())
    await asyncio.wait_for(adapter.run(), timeout=1.0)


# ---------------------------------------------------------------------------
# Additional coverage: start/stop with token, _run retry loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_task_and_stop_cancels(monkeypatch):
    """start() creates task; stop() cancels and clears it."""
    monkeypatch.setenv("MX_TOKEN", "tok")
    cancel_called = False

    async def fake_run() -> None:
        nonlocal cancel_called
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancel_called = True
            raise

    adapter = MatrixGateway(_make_config(), _make_gateway_mock())
    adapter._access_token = "tok"
    adapter._run = fake_run

    await adapter.start()
    assert adapter._task is not None
    await asyncio.sleep(0)  # allow task to start
    await adapter.stop()
    assert adapter._task is None
    assert cancel_called


@pytest.mark.asyncio
async def test_run_retries_on_sync_error(monkeypatch):
    """_run() retries on sync error, stops on CancelledError."""
    monkeypatch.setenv("MX_TOKEN", "tok")
    call_count = 0

    async def fake_sync() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("sync failure")
        raise asyncio.CancelledError

    adapter = MatrixGateway(_make_config(), _make_gateway_mock())
    adapter._access_token = "tok"
    adapter._sync_once = fake_sync

    with pytest.raises(asyncio.CancelledError):
        await adapter._run()

    assert call_count == 2


@pytest.mark.asyncio
async def test_run_convenience_wrapper(monkeypatch):
    monkeypatch.setenv("MX_TOKEN", "tok")

    async def quick_run() -> None:
        raise asyncio.CancelledError

    adapter = MatrixGateway(_make_config(), _make_gateway_mock())
    adapter._access_token = "tok"
    adapter._run = quick_run

    await asyncio.wait_for(adapter.run(), timeout=1.0)


@pytest.mark.asyncio
async def test_sync_once_with_next_batch_token(monkeypatch):
    """_sync_once sends 'since' param when next_batch is set."""
    monkeypatch.setenv("MX_TOKEN", "tok")
    client = _make_http_client(get_json={"next_batch": "t99", "rooms": {}})
    adapter = MatrixGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._access_token = "tok"
    adapter._next_batch = "t55"

    await adapter._sync_once()

    call_params = client.get.call_args[1]["params"]
    assert call_params["since"] == "t55"
    assert adapter._next_batch == "t99"

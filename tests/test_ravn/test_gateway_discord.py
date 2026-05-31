"""Tests for the Discord gateway adapter."""

from __future__ import annotations
from contextlib import suppress

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravn.adapters.channels.gateway_discord import (
    _OP_HEARTBEAT_ACK,
    _OP_HELLO,
    _OP_IDENTIFY,
    _SLASH_PROMPTS,
    DiscordGateway,
)
from ravn.config import DiscordChannelConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    token_env: str = "DC_TOKEN",
    guild_id: str = "111",
    message_max_chars: int = 2000,
) -> DiscordChannelConfig:
    return DiscordChannelConfig(
        enabled=True,
        token_env=token_env,
        guild_id=guild_id,
        message_max_chars=message_max_chars,
        retry_delay=0.01,
        gateway_url="wss://fake.discord.gg",
        api_base="https://discord.example.com/api/v10",
    )


def _make_gateway_mock(response: str = "agent reply") -> MagicMock:
    gw = MagicMock()
    gw.handle_message = AsyncMock(return_value=response)
    return gw


def _make_http_client(response_json: dict | None = None) -> AsyncMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=response_json or {"id": "msg123"})
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    return client


# ---------------------------------------------------------------------------
# GatewayChannelPort — on_message
# ---------------------------------------------------------------------------


def test_on_message_registers_handler():
    cfg = _make_config()
    gw = _make_gateway_mock()
    adapter = DiscordGateway(cfg, gw)
    handler = AsyncMock()
    adapter.on_message(handler)
    assert adapter._handler is handler


# ---------------------------------------------------------------------------
# send_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_text_posts_to_rest_api(monkeypatch):
    monkeypatch.setenv("DC_TOKEN", "test-token")
    client = _make_http_client()
    cfg = _make_config()
    adapter = DiscordGateway(cfg, _make_gateway_mock(), http_client=client)
    adapter._token = "test-token"

    await adapter.send_text("111/999", "hello world")

    assert client.post.called
    call_url = client.post.call_args[0][0]
    assert "999/messages" in call_url
    assert client.post.call_args[1]["json"]["content"] == "hello world"


@pytest.mark.asyncio
async def test_send_text_truncates_long_message(monkeypatch):
    monkeypatch.setenv("DC_TOKEN", "tok")
    client = _make_http_client()
    cfg = _make_config(message_max_chars=10)
    adapter = DiscordGateway(cfg, _make_gateway_mock(), http_client=client)
    adapter._token = "tok"

    await adapter.send_text("0/1", "x" * 20)

    sent_content = client.post.call_args[1]["json"]["content"]
    assert len(sent_content) <= 10
    assert sent_content.endswith("...")


@pytest.mark.asyncio
async def test_send_text_bare_channel_id(monkeypatch):
    monkeypatch.setenv("DC_TOKEN", "tok")
    client = _make_http_client()
    cfg = _make_config()
    adapter = DiscordGateway(cfg, _make_gateway_mock(), http_client=client)
    adapter._token = "tok"

    await adapter.send_text("555", "hi")

    call_url = client.post.call_args[0][0]
    assert "/channels/555/messages" in call_url


# ---------------------------------------------------------------------------
# send_image / send_audio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_image_posts_multipart(monkeypatch):
    monkeypatch.setenv("DC_TOKEN", "tok")
    client = _make_http_client()
    cfg = _make_config()
    adapter = DiscordGateway(cfg, _make_gateway_mock(), http_client=client)
    adapter._token = "tok"

    await adapter.send_image("111/999", b"\x89PNG", caption="test cap")

    assert client.post.called
    # Multipart upload: files= kwarg contains the image, data= has payload_json
    call_kwargs = client.post.call_args[1]
    assert "files" in call_kwargs
    assert "files[0]" in call_kwargs["files"]
    filename, data, mime = call_kwargs["files"]["files[0]"]
    assert filename == "image.png"
    assert data == b"\x89PNG"
    assert mime == "image/png"
    import json as _json

    payload_json = _json.loads(call_kwargs["data"]["payload_json"])
    assert payload_json["content"] == "test cap"


@pytest.mark.asyncio
async def test_send_audio_posts_multipart(monkeypatch):
    monkeypatch.setenv("DC_TOKEN", "tok")
    client = _make_http_client()
    cfg = _make_config()
    adapter = DiscordGateway(cfg, _make_gateway_mock(), http_client=client)
    adapter._token = "tok"

    await adapter.send_audio("111/999", b"OGG_DATA")

    assert client.post.called
    call_kwargs = client.post.call_args[1]
    assert "files" in call_kwargs
    filename, data, mime = call_kwargs["files"]["files[0]"]
    assert filename == "audio.ogg"
    assert data == b"OGG_DATA"
    assert mime == "audio/ogg"


# ---------------------------------------------------------------------------
# start() — token missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_early_when_no_token(caplog):
    import logging

    cfg = _make_config(token_env="DC_MISSING_TOKEN_XYZ")
    adapter = DiscordGateway(cfg, _make_gateway_mock())

    with caplog.at_level(logging.ERROR):
        await adapter.start()

    assert "DC_MISSING_TOKEN_XYZ" in caplog.text
    assert adapter._task is None


# ---------------------------------------------------------------------------
# stop() — no task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_when_not_started():
    cfg = _make_config()
    adapter = DiscordGateway(cfg, _make_gateway_mock())
    # Should not raise
    await adapter.stop()


# ---------------------------------------------------------------------------
# _translate_slash
# ---------------------------------------------------------------------------


def test_translate_slash_plain_text_unchanged():
    adapter = DiscordGateway(_make_config(), _make_gateway_mock())
    assert adapter._translate_slash("hello world") == "hello world"


@pytest.mark.parametrize("cmd", ["/compact", "/budget", "/status", "/stop", "/todo"])
def test_translate_slash_known_commands(cmd: str):
    adapter = DiscordGateway(_make_config(), _make_gateway_mock())
    result = adapter._translate_slash(cmd)
    assert result == _SLASH_PROMPTS[cmd]


def test_translate_slash_unknown_passthrough():
    adapter = DiscordGateway(_make_config(), _make_gateway_mock())
    assert adapter._translate_slash("/unknown") == "/unknown"


# ---------------------------------------------------------------------------
# _channel_id
# ---------------------------------------------------------------------------


def test_channel_id_extracts_from_composite():
    adapter = DiscordGateway(_make_config(), _make_gateway_mock())
    assert adapter._channel_id("123/456") == "456"


def test_channel_id_bare():
    adapter = DiscordGateway(_make_config(), _make_gateway_mock())
    assert adapter._channel_id("456") == "456"


# ---------------------------------------------------------------------------
# _on_message_create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_message_create_dispatches_to_gateway(monkeypatch):
    monkeypatch.setenv("DC_TOKEN", "tok")
    client = _make_http_client()
    gw = _make_gateway_mock("hello reply")
    cfg = _make_config()
    adapter = DiscordGateway(cfg, gw, http_client=client)
    adapter._token = "tok"

    handler = AsyncMock()
    adapter.on_message(handler)

    event: dict[str, Any] = {
        "channel_id": "999",
        "guild_id": "111",
        "id": "msg1",
        "author": {"bot": False, "id": "user1"},
        "content": "hello bot",
    }
    await adapter._on_message_create(event)

    gw.handle_message.assert_awaited_once_with("discord:111/999", "hello bot")
    handler.assert_awaited_once_with("111/999", "hello bot")
    # send_text should have been called
    assert client.post.called


@pytest.mark.asyncio
async def test_on_message_create_ignores_bot_messages():
    gw = _make_gateway_mock()
    adapter = DiscordGateway(_make_config(), gw)

    event: dict[str, Any] = {
        "channel_id": "1",
        "guild_id": "1",
        "id": "m1",
        "author": {"bot": True},
        "content": "bot message",
    }
    await adapter._on_message_create(event)
    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_create_ignores_empty_text():
    gw = _make_gateway_mock()
    adapter = DiscordGateway(_make_config(), gw)

    event: dict[str, Any] = {
        "channel_id": "1",
        "guild_id": "1",
        "id": "m1",
        "author": {"bot": False},
        "content": "",
    }
    await adapter._on_message_create(event)
    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_message_create_translates_slash(monkeypatch):
    monkeypatch.setenv("DC_TOKEN", "tok")
    client = _make_http_client()
    gw = _make_gateway_mock("ok")
    adapter = DiscordGateway(_make_config(), gw, http_client=client)
    adapter._token = "tok"

    event: dict[str, Any] = {
        "channel_id": "1",
        "guild_id": "111",
        "id": "m1",
        "author": {"bot": False},
        "content": "/status",
    }
    await adapter._on_message_create(event)

    called_prompt = gw.handle_message.call_args[0][1]
    assert "status" in called_prompt.lower()


@pytest.mark.asyncio
async def test_on_message_create_handles_agent_exception(monkeypatch):
    monkeypatch.setenv("DC_TOKEN", "tok")
    client = _make_http_client()
    gw = MagicMock()
    gw.handle_message = AsyncMock(side_effect=RuntimeError("crash"))
    adapter = DiscordGateway(_make_config(), gw, http_client=client)
    adapter._token = "tok"

    event: dict[str, Any] = {
        "channel_id": "1",
        "guild_id": "111",
        "id": "m1",
        "author": {"bot": False},
        "content": "hello",
    }
    # Should not raise; sends error message instead
    await adapter._on_message_create(event)
    assert client.post.called  # error fallback message sent


# ---------------------------------------------------------------------------
# _on_reaction_add — approval workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_reaction_add_approve(monkeypatch):
    monkeypatch.setenv("DC_TOKEN", "tok")
    client = _make_http_client()
    gw = _make_gateway_mock("approved!")
    adapter = DiscordGateway(_make_config(), gw, http_client=client)
    adapter._token = "tok"

    # Seed pending approval
    adapter._pending_approvals["msg999"] = ("111/1", "discord:111/1")

    reaction = {
        "message_id": "msg999",
        "emoji": {"name": "👍"},
    }
    await adapter._on_reaction_add(reaction)

    gw.handle_message.assert_awaited_once_with("discord:111/1", "approved")
    assert "msg999" not in adapter._pending_approvals


@pytest.mark.asyncio
async def test_on_reaction_add_reject(monkeypatch):
    monkeypatch.setenv("DC_TOKEN", "tok")
    client = _make_http_client()
    gw = _make_gateway_mock("rejected!")
    adapter = DiscordGateway(_make_config(), gw, http_client=client)
    adapter._token = "tok"

    adapter._pending_approvals["msg42"] = ("111/2", "discord:111/2")

    reaction = {
        "message_id": "msg42",
        "emoji": {"name": "👎"},
    }
    await adapter._on_reaction_add(reaction)

    gw.handle_message.assert_awaited_once_with("discord:111/2", "rejected")


@pytest.mark.asyncio
async def test_on_reaction_add_unknown_message_ignored():
    gw = _make_gateway_mock()
    adapter = DiscordGateway(_make_config(), gw)

    reaction = {"message_id": "unknown", "emoji": {"name": "👍"}}
    await adapter._on_reaction_add(reaction)
    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_reaction_add_other_emoji_ignored():
    gw = _make_gateway_mock()
    adapter = DiscordGateway(_make_config(), gw)

    adapter._pending_approvals["m1"] = ("ch", "discord:ch")
    reaction = {"message_id": "m1", "emoji": {"name": "🎉"}}
    await adapter._on_reaction_add(reaction)
    gw.handle_message.assert_not_awaited()
    # Still in pending (not removed)
    assert "m1" in adapter._pending_approvals


# ---------------------------------------------------------------------------
# _handle_dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_dispatch_ready_logs(caplog):
    import logging

    adapter = DiscordGateway(_make_config(), _make_gateway_mock())
    data = {"user": {"username": "TestBot"}}
    with caplog.at_level(logging.INFO):
        await adapter._handle_dispatch("READY", data)
    assert "TestBot" in caplog.text


# ---------------------------------------------------------------------------
# WebSocket loop — _connect_and_listen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_and_listen_identifies_on_hello(monkeypatch):
    """The adapter should send IDENTIFY after receiving HELLO."""

    sent_messages: list[str] = []

    class FakeWS:
        def __init__(self) -> None:
            self._msgs = iter(
                [
                    json.dumps({"op": _OP_HELLO, "d": {"heartbeat_interval": 10000}}),
                    json.dumps({"op": _OP_HEARTBEAT_ACK, "d": None}),
                ]
            )

        async def send(self, msg: str) -> None:
            sent_messages.append(msg)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._msgs)
            except StopIteration:
                raise asyncio.CancelledError

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setenv("DC_TOKEN", "fake-token")
    cfg = _make_config()
    adapter = DiscordGateway(cfg, _make_gateway_mock())
    adapter._token = "fake-token"

    fake_ws = FakeWS()

    with patch("ravn.adapters.channels.gateway_discord.websockets") as mock_ws:
        mock_ws.connect.return_value = fake_ws

        with suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(adapter._connect_and_listen(), timeout=0.5)

    # At least one IDENTIFY message should have been sent
    identify_msgs = [m for m in sent_messages if json.loads(m).get("op") == _OP_IDENTIFY]
    assert len(identify_msgs) >= 1


# ---------------------------------------------------------------------------
# run() — returns when not started (no token)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_cleanly_when_no_token():
    cfg = _make_config(token_env="DC_MISSING_XYZ")
    adapter = DiscordGateway(cfg, _make_gateway_mock())
    # run() should return promptly because start() finds no token
    await asyncio.wait_for(adapter.run(), timeout=1.0)


# ---------------------------------------------------------------------------
# Additional coverage: start/stop with token, _run retry, _heartbeat_loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_task_and_stop_cancels(monkeypatch):
    """start() creates _task; stop() cancels and clears it."""
    monkeypatch.setenv("DC_TOKEN", "tok")
    cancel_called = False

    async def fake_run():
        nonlocal cancel_called
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancel_called = True
            raise

    adapter = DiscordGateway(_make_config(), _make_gateway_mock())
    adapter._token = "tok"
    adapter._run = fake_run

    await adapter.start()
    assert adapter._task is not None
    await asyncio.sleep(0)  # allow task to start
    await adapter.stop()
    assert adapter._task is None
    assert cancel_called


@pytest.mark.asyncio
async def test_run_retries_on_exception(monkeypatch):
    """_run() reconnects after a non-cancel exception."""
    monkeypatch.setenv("DC_TOKEN", "tok")
    call_count = 0

    async def fake_connect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("first failure")
        raise asyncio.CancelledError

    adapter = DiscordGateway(_make_config(), _make_gateway_mock())
    adapter._token = "tok"
    adapter._connect_and_listen = fake_connect

    with pytest.raises(asyncio.CancelledError):
        await adapter._run()

    assert call_count == 2


@pytest.mark.asyncio
async def test_heartbeat_loop_sends_heartbeat():
    """_heartbeat_loop sends OP 1 HEARTBEAT messages."""
    sent: list[str] = []

    class FakeWS:
        async def send(self, msg: str) -> None:
            sent.append(msg)

    adapter = DiscordGateway(_make_config(), _make_gateway_mock())
    ws = FakeWS()

    # Run heartbeat for just long enough to send one beat
    task = asyncio.create_task(adapter._heartbeat_loop(ws, 0.05))
    await asyncio.sleep(0.12)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    import json as _json

    beats = [_json.loads(m) for m in sent if _json.loads(m).get("op") == 1]
    assert len(beats) >= 1


@pytest.mark.asyncio
async def test_heartbeat_loop_exits_on_ws_close():
    """_heartbeat_loop exits cleanly if the WS send fails."""

    class BrokenWS:
        async def send(self, msg: str) -> None:
            raise ConnectionError("closed")

    adapter = DiscordGateway(_make_config(), _make_gateway_mock())
    ws = BrokenWS()
    # Should return without exception
    await asyncio.wait_for(adapter._heartbeat_loop(ws, 0.01), timeout=1.0)


@pytest.mark.asyncio
async def test_run_convenience_wrapper(monkeypatch):
    """run() wraps start() + awaiting the internal task."""
    monkeypatch.setenv("DC_TOKEN", "tok")

    async def quick_run():
        raise asyncio.CancelledError

    adapter = DiscordGateway(_make_config(), _make_gateway_mock())
    adapter._token = "tok"
    adapter._run = quick_run

    # Should complete without raising
    await asyncio.wait_for(adapter.run(), timeout=1.0)


# ---------------------------------------------------------------------------
# _pending_approvals cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_approvals_cap_evicts_oldest(monkeypatch):
    """When max_pending_approvals is reached, the oldest entry is evicted."""
    monkeypatch.setenv("DC_TOKEN", "tok")
    client = _make_http_client()
    cfg = DiscordChannelConfig(
        enabled=True,
        token_env="DC_TOKEN",
        guild_id="",
        message_max_chars=2000,
        retry_delay=0.01,
        gateway_url="wss://fake",
        api_base="https://discord.example.com/api/v10",
        max_pending_approvals=2,
    )
    gw = _make_gateway_mock("ok")
    adapter = DiscordGateway(cfg, gw, http_client=client)
    adapter._token = "tok"

    # Seed the dict to capacity
    adapter._pending_approvals["old1"] = ("ch", "discord:ch")
    adapter._pending_approvals["old2"] = ("ch", "discord:ch")

    # A new approval message should evict the oldest ("old1")
    msg: dict[str, Any] = {
        "channel_id": "C1",
        "content": "task done [APPROVAL_REQUESTED]",
        "id": "new_msg",
        "author": {},
    }
    await adapter._on_message_create(msg)

    assert "old1" not in adapter._pending_approvals
    assert "old2" in adapter._pending_approvals
    assert "new_msg" in adapter._pending_approvals

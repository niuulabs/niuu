"""Tests for the Telegram gateway adapter."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ravn.adapters.channels.gateway_telegram import (
    _BOT_COMMANDS,
    _SLASH_PROMPTS,
    TelegramGateway,
)
from ravn.config import TelegramChannelConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    allowed_chat_ids: list[int] | None = None,
    token_env: str = "TG_TOKEN",
) -> TelegramChannelConfig:
    return TelegramChannelConfig(
        enabled=True,
        token_env=token_env,
        allowed_chat_ids=allowed_chat_ids or [],
        poll_timeout=1,
        retry_delay=0.01,
        message_max_chars=4096,
    )


def _make_gateway_mock(response: str = "agent reply") -> MagicMock:
    gw = MagicMock()
    gw.handle_message = AsyncMock(return_value=response)
    return gw


def _make_update(
    update_id: int,
    chat_id: int,
    text: str,
) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "text": text,
        },
    }


# ---------------------------------------------------------------------------
# _is_allowed
# ---------------------------------------------------------------------------


def test_is_allowed_empty_list_permits_all():
    cfg = _make_config(allowed_chat_ids=[])
    tg = TelegramGateway(cfg, _make_gateway_mock())
    assert tg._is_allowed(999999) is True


def test_is_allowed_with_list_permits_listed():
    cfg = _make_config(allowed_chat_ids=[111, 222])
    tg = TelegramGateway(cfg, _make_gateway_mock())
    assert tg._is_allowed(111) is True
    assert tg._is_allowed(333) is False


# ---------------------------------------------------------------------------
# _translate_command
# ---------------------------------------------------------------------------


def test_translate_command_plain_text_unchanged():
    tg = TelegramGateway(_make_config(), _make_gateway_mock())
    assert tg._translate_command("hello world") == "hello world"


@pytest.mark.parametrize("cmd", ["/stop", "/status", "/todo", "/budget"])
def test_translate_command_slash_commands(cmd: str):
    tg = TelegramGateway(_make_config(), _make_gateway_mock())
    prompt = tg._translate_command(cmd)
    assert prompt == _SLASH_PROMPTS[cmd]


def test_translate_command_unknown_slash_passthrough():
    tg = TelegramGateway(_make_config(), _make_gateway_mock())
    assert tg._translate_command("/unknown") == "/unknown"


def test_translate_command_slash_with_args():
    tg = TelegramGateway(_make_config(), _make_gateway_mock())
    # /status with extra args — command is matched, prompt returned
    result = tg._translate_command("/status extra args")
    assert result == _SLASH_PROMPTS["/status"]


# ---------------------------------------------------------------------------
# run() — token not set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_early_when_no_token(caplog):
    cfg = _make_config(token_env="RAVN_TEST_MISSING_TOKEN_XYZ")
    tg = TelegramGateway(cfg, _make_gateway_mock())
    import logging

    with caplog.at_level(logging.ERROR):
        await tg.run()

    assert "RAVN_TEST_MISSING_TOKEN_XYZ" in caplog.text


# ---------------------------------------------------------------------------
# _handle_update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_update_dispatches_to_gateway(monkeypatch):
    monkeypatch.setenv("TG_TOKEN", "test-token")
    gw = _make_gateway_mock("hello")
    cfg = _make_config(allowed_chat_ids=[42])
    tg = TelegramGateway(cfg, gw)
    tg._token = "test-token"

    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))

    update = _make_update(1, 42, "hello bot")
    await tg._handle_update(client, update)

    gw.handle_message.assert_awaited_once_with("telegram:42", "hello bot")
    # sendMessage should have been called
    send_calls = [call for call in client.post.call_args_list if "sendMessage" in str(call)]
    assert len(send_calls) == 1


@pytest.mark.asyncio
async def test_handle_update_ignores_disallowed_chat(monkeypatch):
    gw = _make_gateway_mock()
    cfg = _make_config(allowed_chat_ids=[111])
    tg = TelegramGateway(cfg, gw)
    tg._token = "test-token"

    client = AsyncMock()
    update = _make_update(1, 999, "hello")
    await tg._handle_update(client, update)

    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_update_skips_non_text_message():
    gw = _make_gateway_mock()
    tg = TelegramGateway(_make_config(), gw)
    tg._token = "test-token"

    client = AsyncMock()
    update = {
        "update_id": 1,
        "message": {"chat": {"id": 1}, "text": ""},
    }
    await tg._handle_update(client, update)

    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_update_skips_missing_message():
    gw = _make_gateway_mock()
    tg = TelegramGateway(_make_config(), gw)
    tg._token = "test-token"

    client = AsyncMock()
    await tg._handle_update(client, {"update_id": 1})

    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_update_translates_slash_command():
    gw = _make_gateway_mock("ok")
    tg = TelegramGateway(_make_config(), gw)
    tg._token = "test-token"

    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))

    update = _make_update(1, 1, "/status")
    await tg._handle_update(client, update)

    called_text = gw.handle_message.call_args[0][1]
    assert "status" in called_text.lower()


@pytest.mark.asyncio
async def test_handle_update_sends_error_message_on_agent_exception():
    gw = MagicMock()
    gw.handle_message = AsyncMock(side_effect=RuntimeError("agent crash"))
    tg = TelegramGateway(_make_config(), gw)
    tg._token = "test-token"

    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))

    update = _make_update(1, 1, "hi")
    await tg._handle_update(client, update)

    # Still sends a message (the error fallback)
    assert client.post.called


# ---------------------------------------------------------------------------
# _send_message truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_splits_long_text():
    tg = TelegramGateway(_make_config(token_env="TK"), _make_gateway_mock())
    tg._token = "test-token"

    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock())

    long_text = "x" * 5000
    await tg._send_message(client, 1, long_text)

    sent = [call[1]["json"]["text"] for call in client.post.call_args_list]
    assert [len(chunk) for chunk in sent] == [4096, 904]
    assert "".join(sent) == long_text


@pytest.mark.asyncio
async def test_send_message_short_text_unchanged():
    tg = TelegramGateway(_make_config(token_env="TK"), _make_gateway_mock())
    tg._token = "test-token"

    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock())

    await tg._send_message(client, 1, "hi")
    sent_text = client.post.call_args[1]["json"]["text"]
    assert sent_text == "hi"


# ---------------------------------------------------------------------------
# _poll_once — offset tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_once_advances_offset():
    gw = _make_gateway_mock("ok")
    tg = TelegramGateway(_make_config(), gw)
    tg._token = "test-token"

    client = AsyncMock()
    client.get = AsyncMock(
        return_value=MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(
                return_value={
                    "ok": True,
                    "result": [_make_update(10, 1, "hi"), _make_update(11, 1, "bye")],
                }
            ),
        )
    )
    client.post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))

    await tg._poll_once(client)

    assert tg._offset == 12  # last update_id + 1


@pytest.mark.asyncio
async def test_poll_once_handles_not_ok_response():
    gw = _make_gateway_mock()
    tg = TelegramGateway(_make_config(), gw)
    tg._token = "test-token"

    client = AsyncMock()
    client.get = AsyncMock(
        return_value=MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={"ok": False, "description": "Bad token"}),
        )
    )

    await tg._poll_once(client)

    gw.handle_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# _register_commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_commands_posts_commands():
    tg = TelegramGateway(_make_config(token_env="TK"), _make_gateway_mock())
    tg._token = "test-token"

    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock())

    await tg._register_commands(client)

    assert client.post.called
    posted_body = client.post.call_args[1]["json"]
    assert "commands" in posted_body
    assert len(posted_body["commands"]) == len(_BOT_COMMANDS)


@pytest.mark.asyncio
async def test_register_commands_swallows_exception(caplog):
    import logging

    tg = TelegramGateway(_make_config(token_env="TK"), _make_gateway_mock())
    tg._token = "test-token"

    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("fail"))

    with caplog.at_level(logging.WARNING):
        await tg._register_commands(client)
    # Should not raise; warning logged instead.


# ---------------------------------------------------------------------------
# _send_typing — exception silencing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_typing_swallows_exception():
    tg = TelegramGateway(_make_config(), _make_gateway_mock())
    tg._token = "test-token"

    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("fail"))

    # Should not raise
    await tg._send_typing(client, 42)


# ---------------------------------------------------------------------------
# _send_message — exception logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_logs_on_exception(caplog):
    import logging

    tg = TelegramGateway(_make_config(token_env="TK"), _make_gateway_mock())
    tg._token = "test-token"

    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("network fail"))

    with caplog.at_level(logging.ERROR):
        await tg._send_message(client, 99, "hello")

    assert "99" in caplog.text


# ---------------------------------------------------------------------------
# run() — main polling loop (with token set)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cancels_cleanly(monkeypatch):
    """run() exits cleanly when the task is cancelled after one poll."""
    monkeypatch.setenv("TG_TOKEN", "test-token")

    gw = _make_gateway_mock("ok")
    cfg = _make_config(token_env="TG_TOKEN")
    tg = TelegramGateway(cfg, gw)
    tg._token = "test-token"

    poll_count = 0

    async def fake_poll_once(client):
        nonlocal poll_count
        poll_count += 1
        raise asyncio.CancelledError()

    tg._poll_once = fake_poll_once

    # Patch _register_commands so it doesn't need a real HTTP client.
    async def fake_register(client):
        pass

    tg._register_commands = fake_register

    with pytest.raises(asyncio.CancelledError):
        await tg.run()

    assert poll_count == 1


@pytest.mark.asyncio
async def test_run_retries_on_exception(monkeypatch):
    """run() retries after a non-cancel exception."""
    monkeypatch.setenv("TG_TOKEN", "test-token")

    gw = _make_gateway_mock("ok")
    cfg = TelegramChannelConfig(
        enabled=True,
        token_env="TG_TOKEN",
        allowed_chat_ids=[],
        poll_timeout=1,
        retry_delay=0.0,
        message_max_chars=4096,
    )
    tg = TelegramGateway(cfg, gw)
    tg._token = "test-token"

    call_count = 0

    async def fake_poll_once(client):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient error")
        raise asyncio.CancelledError()

    tg._poll_once = fake_poll_once

    async def fake_register(client):
        pass

    tg._register_commands = fake_register

    with pytest.raises(asyncio.CancelledError):
        await tg.run()

    assert call_count == 2


@pytest.mark.asyncio
async def test_a_rejected_chunk_is_logged_and_the_rest_is_not_sent(caplog):
    import logging

    tg = TelegramGateway(_make_config(token_env="TK"), _make_gateway_mock())
    tg._token = "test-token"
    rejected = MagicMock()
    rejected.raise_for_status.side_effect = httpx.HTTPStatusError(
        "400", request=MagicMock(), response=MagicMock()
    )
    client = AsyncMock()
    client.post = AsyncMock(return_value=rejected)

    with caplog.at_level(logging.ERROR):
        await tg._send_message(client, 7, "x" * 5000)

    # A 4xx used to pass unnoticed: post() does not raise on it.
    assert client.post.await_count == 1
    assert "7" in caplog.text

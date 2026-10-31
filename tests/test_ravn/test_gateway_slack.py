"""Tests for the Slack gateway adapter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.adapters.channels.gateway_slack import (
    _SLASH_PROMPTS,
    SlackGateway,
)
from ravn.config import SlackChannelConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    bot_token_env: str = "SLACK_TOKEN",
    poll_interval: float = 0.0,
) -> SlackChannelConfig:
    return SlackChannelConfig(
        enabled=True,
        bot_token_env=bot_token_env,
        poll_interval=poll_interval,
        message_max_chars=3000,
        retry_delay=0.01,
        api_base="https://slack.example.com/api",
    )


def _make_gateway_mock(response: str = "agent reply") -> MagicMock:
    gw = MagicMock()
    gw.handle_message = AsyncMock(return_value=response)
    return gw


def _make_http_client(
    post_json: dict | None = None,
    get_json: dict | None = None,
) -> AsyncMock:
    post_resp = MagicMock()
    post_resp.raise_for_status = MagicMock()
    post_resp.json = MagicMock(return_value=post_json or {"ok": True})

    get_resp = MagicMock()
    get_resp.raise_for_status = MagicMock()
    get_resp.json = MagicMock(return_value=get_json or {"ok": True, "channels": [], "messages": []})

    client = AsyncMock()
    client.post = AsyncMock(return_value=post_resp)
    client.get = AsyncMock(return_value=get_resp)
    return client


# ---------------------------------------------------------------------------
# on_message
# ---------------------------------------------------------------------------


def test_on_message_registers_handler():
    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    handler = AsyncMock()
    adapter.on_message(handler)
    assert adapter._handler is handler


# ---------------------------------------------------------------------------
# send_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_text_posts_to_api(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "xoxb-test")
    client = _make_http_client()
    adapter = SlackGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._bot_token = "xoxb-test"

    await adapter.send_text("C0123", "hello slack")

    assert client.post.called
    call_json = client.post.call_args[1]["json"]
    assert call_json["channel"] == "C0123"
    assert call_json["text"] == "hello slack"


@pytest.mark.asyncio
async def test_send_text_splits_long_message(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    client = _make_http_client()
    cfg = SlackChannelConfig(
        enabled=True,
        bot_token_env="SLACK_TOKEN",
        message_max_chars=10,
        retry_delay=0.0,
        api_base="https://slack.example.com/api",
    )
    adapter = SlackGateway(cfg, _make_gateway_mock(), http_client=client)
    adapter._bot_token = "tok"

    await adapter.send_text("C1", "x" * 20)

    sent = [call[1]["json"]["text"] for call in client.post.call_args_list]
    assert sent == ["x" * 10, "x" * 10]


# ---------------------------------------------------------------------------
# send_image / send_audio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_image_calls_files_upload(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    client = _make_http_client()
    adapter = SlackGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._bot_token = "tok"

    await adapter.send_image("C1", b"\x89PNG", caption="cap")

    assert client.post.called
    call_url = client.post.call_args[0][0]
    assert "files.upload" in call_url


@pytest.mark.asyncio
async def test_send_audio_calls_files_upload(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    client = _make_http_client()
    adapter = SlackGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._bot_token = "tok"

    await adapter.send_audio("C1", b"OGG_DATA")

    assert client.post.called
    call_url = client.post.call_args[0][0]
    assert "files.upload" in call_url


# ---------------------------------------------------------------------------
# start() — missing token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_early_when_no_token(caplog):
    import logging

    cfg = _make_config(bot_token_env="SLACK_MISSING_TOKEN_XYZ")
    adapter = SlackGateway(cfg, _make_gateway_mock())

    with caplog.at_level(logging.ERROR):
        await adapter.start()

    assert "SLACK_MISSING_TOKEN_XYZ" in caplog.text
    assert adapter._task is None


# ---------------------------------------------------------------------------
# stop() — not started
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_when_not_started():
    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    await adapter.stop()  # should not raise


# ---------------------------------------------------------------------------
# _should_handle
# ---------------------------------------------------------------------------


def test_should_handle_dm_channel():
    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    adapter._bot_user_id = "UBOT"
    assert adapter._should_handle("D12345", "anything") is True


def test_should_handle_mention():
    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    adapter._bot_user_id = "UBOT"
    assert adapter._should_handle("C12345", "<@UBOT> hello") is True


def test_should_handle_not_mentioned():
    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    adapter._bot_user_id = "UBOT"
    assert adapter._should_handle("C12345", "hello everyone") is False


def test_should_handle_slash_command():
    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    adapter._bot_user_id = "UBOT"
    assert adapter._should_handle("C12345", "/ravn-status") is True


# ---------------------------------------------------------------------------
# _strip_mention
# ---------------------------------------------------------------------------


def test_strip_mention_removes_bot_mention():
    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    adapter._bot_user_id = "UBOT"
    assert adapter._strip_mention("<@UBOT> hello world") == "hello world"


def test_strip_mention_no_mention_unchanged():
    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    adapter._bot_user_id = "UBOT"
    assert adapter._strip_mention("hello world") == "hello world"


# ---------------------------------------------------------------------------
# _translate_slash
# ---------------------------------------------------------------------------


def test_translate_slash_plain_unchanged():
    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    assert adapter._translate_slash("hello") == "hello"


@pytest.mark.parametrize("cmd", list(_SLASH_PROMPTS.keys()))
def test_translate_slash_known_commands(cmd: str):
    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    assert adapter._translate_slash(cmd) == _SLASH_PROMPTS[cmd]


# ---------------------------------------------------------------------------
# _poll_channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_channel_dispatches_mention(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    import time

    old_ts = str(time.time() - 100)
    new_ts = str(time.time())

    gw = _make_gateway_mock("reply")
    client = _make_http_client(
        get_json={
            "ok": True,
            "messages": [
                {
                    "ts": new_ts,
                    "user": "UUSER",
                    "text": "<@UBOT> hello",
                }
            ],
        }
    )
    adapter = SlackGateway(_make_config(), gw, http_client=client)
    adapter._bot_token = "tok"
    adapter._bot_user_id = "UBOT"
    adapter._channel_cursors["C1"] = old_ts

    handler = AsyncMock()
    adapter.on_message(handler)

    await adapter._poll_channel("C1")

    gw.handle_message.assert_awaited_once()
    session_id = gw.handle_message.call_args[0][0]
    assert session_id == "slack:C1"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_channel_skips_bot_messages(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    import time

    gw = _make_gateway_mock()
    client = _make_http_client(
        get_json={
            "ok": True,
            "messages": [
                {
                    "ts": str(time.time()),
                    "bot_id": "BBOT",
                    "text": "bot post",
                }
            ],
        }
    )
    adapter = SlackGateway(_make_config(), gw, http_client=client)
    adapter._bot_token = "tok"
    adapter._bot_user_id = "UBOT"
    adapter._channel_cursors["C1"] = "0"

    await adapter._poll_channel("C1")
    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_channel_skips_own_messages(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    import time

    gw = _make_gateway_mock()
    client = _make_http_client(
        get_json={
            "ok": True,
            "messages": [
                {
                    "ts": str(time.time()),
                    "user": "UBOT",  # own user ID
                    "text": "my own message",
                }
            ],
        }
    )
    adapter = SlackGateway(_make_config(), gw, http_client=client)
    adapter._bot_token = "tok"
    adapter._bot_user_id = "UBOT"
    adapter._channel_cursors["C1"] = "0"

    await adapter._poll_channel("C1")
    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_channel_handles_agent_exception(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    import time

    gw = MagicMock()
    gw.handle_message = AsyncMock(side_effect=RuntimeError("crash"))
    client = _make_http_client(
        get_json={
            "ok": True,
            "messages": [
                {
                    "ts": str(time.time()),
                    "user": "UUSER",
                    "text": "<@UBOT> help",
                }
            ],
        }
    )
    adapter = SlackGateway(_make_config(), gw, http_client=client)
    adapter._bot_token = "tok"
    adapter._bot_user_id = "UBOT"
    adapter._channel_cursors["C1"] = "0"

    # Should not raise
    await adapter._poll_channel("C1")
    # Error fallback message sent
    assert client.post.called


# ---------------------------------------------------------------------------
# _resolve_bot_identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_bot_identity(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    client = _make_http_client(post_json={"ok": True, "user_id": "UBOT123"})
    adapter = SlackGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._bot_token = "tok"

    await adapter._resolve_bot_identity()
    assert adapter._bot_user_id == "UBOT123"


# ---------------------------------------------------------------------------
# run() — no token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_cleanly_when_no_token():
    cfg = _make_config(bot_token_env="SLACK_MISSING_XYZ")
    adapter = SlackGateway(cfg, _make_gateway_mock())
    await asyncio.wait_for(adapter.run(), timeout=1.0)


# ---------------------------------------------------------------------------
# Additional coverage: _discover_channels, _poll_all_channels, _run loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_channels_populates_watched(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    client = _make_http_client(
        get_json={
            "ok": True,
            "channels": [
                {"id": "C001", "is_member": True},
                {"id": "C002", "is_member": False},
                {"id": "C003", "is_member": True},
            ],
        }
    )
    adapter = SlackGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._bot_token = "tok"

    await adapter._discover_channels()

    assert "C001" in adapter._watched_channels
    assert "C002" not in adapter._watched_channels
    assert "C003" in adapter._watched_channels
    # Cursors seeded for watched channels
    assert "C001" in adapter._channel_cursors
    assert "C003" in adapter._channel_cursors


@pytest.mark.asyncio
async def test_poll_all_channels_calls_each(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    client = _make_http_client(get_json={"ok": True, "messages": []})
    adapter = SlackGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._bot_token = "tok"
    adapter._watched_channels = ["C001", "C002"]
    adapter._channel_cursors = {"C001": "0", "C002": "0"}

    await adapter._poll_all_channels()

    # GET called once per channel
    assert client.get.call_count == 2


@pytest.mark.asyncio
async def test_run_loop_stops_on_cancel(monkeypatch):
    """_run() resolves identity, discovers channels, then stops on CancelledError."""
    monkeypatch.setenv("SLACK_TOKEN", "tok")

    call_count = 0

    async def fake_resolve():
        pass

    async def fake_discover():
        pass

    async def fake_poll():
        nonlocal call_count
        call_count += 1
        raise asyncio.CancelledError

    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    adapter._bot_token = "tok"
    adapter._resolve_bot_identity = fake_resolve
    adapter._discover_channels = fake_discover
    adapter._poll_all_channels = fake_poll

    with pytest.raises(asyncio.CancelledError):
        await adapter._run()

    assert call_count == 1


@pytest.mark.asyncio
async def test_run_loop_retries_on_exception(monkeypatch):
    """_run() retries polling after a transient exception."""
    monkeypatch.setenv("SLACK_TOKEN", "tok")
    call_count = 0

    async def fake_resolve():
        pass

    async def fake_discover():
        pass

    async def fake_poll():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient")
        raise asyncio.CancelledError

    adapter = SlackGateway(_make_config(poll_interval=0.0), _make_gateway_mock())
    adapter._bot_token = "tok"
    adapter._resolve_bot_identity = fake_resolve
    adapter._discover_channels = fake_discover
    adapter._poll_all_channels = fake_poll

    with pytest.raises(asyncio.CancelledError):
        await adapter._run()

    assert call_count == 2


@pytest.mark.asyncio
async def test_run_startup_exception_returns(caplog):
    """_run() returns early if identity resolution fails."""
    import logging

    async def boom():
        raise RuntimeError("startup fail")

    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    adapter._bot_token = "tok"
    adapter._resolve_bot_identity = boom

    with caplog.at_level(logging.ERROR):
        await adapter._run()

    # No exception propagated; error logged
    assert "startup" in caplog.text.lower() or "failed" in caplog.text.lower()


@pytest.mark.asyncio
async def test_start_creates_task_and_stop_cancels(monkeypatch):
    monkeypatch.setenv("SLACK_TOKEN", "tok")

    cancel_called = False

    async def fake_run():
        nonlocal cancel_called
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancel_called = True
            raise

    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    adapter._bot_token = "tok"
    adapter._run = fake_run

    await adapter.start()
    assert adapter._task is not None
    await asyncio.sleep(0)  # allow task to start
    await adapter.stop()
    assert adapter._task is None
    assert cancel_called


@pytest.mark.asyncio
async def test_run_convenience_wrapper(monkeypatch):
    """run() starts and awaits the internal task, handles CancelledError."""
    monkeypatch.setenv("SLACK_TOKEN", "tok")

    async def immediate_cancel():
        raise asyncio.CancelledError

    adapter = SlackGateway(_make_config(), _make_gateway_mock())
    adapter._bot_token = "tok"
    adapter._run = immediate_cancel

    # run() should return cleanly (no propagation) even with no token scenario
    await asyncio.wait_for(adapter.run(), timeout=1.0)

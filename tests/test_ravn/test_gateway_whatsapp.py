"""Tests for the WhatsApp gateway adapter."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.adapters.channels.gateway_whatsapp import WhatsAppGateway
from ravn.config import WhatsAppChannelConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(
    api_key_env: str = "WA_KEY",
    phone_number_id_env: str = "WA_PHONE",
    webhook_verify_token_env: str = "WA_VERIFY",
    mode: str = "business_api",
    message_max_chars: int = 4096,
) -> WhatsAppChannelConfig:
    return WhatsAppChannelConfig(
        enabled=True,
        mode=mode,
        api_key_env=api_key_env,
        phone_number_id_env=phone_number_id_env,
        webhook_verify_token_env=webhook_verify_token_env,
        webhook_host="127.0.0.1",
        webhook_port=17478,
        retry_delay=0.01,
        message_max_chars=message_max_chars,
        api_base="https://graph.example.com/v18.0",
    )


def _make_gateway_mock(response: str = "agent reply") -> MagicMock:
    gw = MagicMock()
    gw.handle_message = AsyncMock(return_value=response)
    return gw


def _make_http_client(post_json: dict | None = None) -> AsyncMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=post_json or {"messages": [{"id": "wamid.123"}]})

    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    return client


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


def test_local_bridge_mode_is_rejected_by_typed_config():
    with pytest.raises(ValueError, match="mode"):
        _make_config(mode="local_bridge")


# ---------------------------------------------------------------------------
# on_message
# ---------------------------------------------------------------------------


def test_on_message_registers_handler():
    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock())
    handler = AsyncMock()
    adapter.on_message(handler)
    assert adapter._handler is handler


# ---------------------------------------------------------------------------
# start() — missing API key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_returns_early_when_no_api_key(caplog):
    import logging

    cfg = _make_config(api_key_env="WA_MISSING_KEY_XYZ")
    adapter = WhatsAppGateway(cfg, _make_gateway_mock())

    with caplog.at_level(logging.ERROR):
        await adapter.start()

    assert "WhatsApp API key is not set" in caplog.text
    assert adapter._server_task is None


# ---------------------------------------------------------------------------
# stop() — not started
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_when_not_started():
    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock())
    await adapter.stop()  # should not raise


# ---------------------------------------------------------------------------
# send_text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_text_posts_to_graph_api(monkeypatch):
    monkeypatch.setenv("WA_KEY", "Bearer tok")
    monkeypatch.setenv("WA_PHONE", "12345678")
    client = _make_http_client()
    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._api_key = "Bearer tok"
    adapter._phone_number_id = "12345678"

    await adapter.send_text("+49123456", "hello whatsapp")

    assert client.post.called
    call_url = client.post.call_args[0][0]
    assert "12345678/messages" in call_url
    payload = client.post.call_args[1]["json"]
    assert payload["to"] == "+49123456"
    assert payload["text"]["body"] == "hello whatsapp"
    assert payload["type"] == "text"


@pytest.mark.asyncio
async def test_send_text_truncates_long_message(monkeypatch):
    monkeypatch.setenv("WA_KEY", "tok")
    monkeypatch.setenv("WA_PHONE", "99999")
    client = _make_http_client()
    cfg = _make_config(message_max_chars=10)
    adapter = WhatsAppGateway(cfg, _make_gateway_mock(), http_client=client)
    adapter._api_key = "tok"
    adapter._phone_number_id = "99999"

    await adapter.send_text("+1", "x" * 20)

    body = client.post.call_args[1]["json"]["text"]["body"]
    assert len(body) <= 10
    assert body.endswith("...")


# ---------------------------------------------------------------------------
# send_image / send_audio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_image_uploads_and_sends(monkeypatch):
    monkeypatch.setenv("WA_KEY", "tok")
    monkeypatch.setenv("WA_PHONE", "99999")
    media_resp = MagicMock()
    media_resp.raise_for_status = MagicMock()
    media_resp.json = MagicMock(return_value={"id": "media_id_123"})

    msg_resp = MagicMock()
    msg_resp.raise_for_status = MagicMock()
    msg_resp.json = MagicMock(return_value={"messages": [{"id": "wamid1"}]})

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[media_resp, msg_resp])

    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._api_key = "tok"
    adapter._phone_number_id = "99999"

    await adapter.send_image("+1", b"\x89PNG", caption="img cap")

    assert client.post.call_count == 2
    msg_payload = client.post.call_args[1]["json"]
    assert msg_payload["type"] == "image"
    assert msg_payload["image"]["id"] == "media_id_123"
    assert msg_payload["image"]["caption"] == "img cap"


@pytest.mark.asyncio
async def test_send_audio_uploads_and_sends(monkeypatch):
    monkeypatch.setenv("WA_KEY", "tok")
    monkeypatch.setenv("WA_PHONE", "99999")
    media_resp = MagicMock()
    media_resp.raise_for_status = MagicMock()
    media_resp.json = MagicMock(return_value={"id": "audio_media_id"})

    msg_resp = MagicMock()
    msg_resp.raise_for_status = MagicMock()
    msg_resp.json = MagicMock(return_value={"messages": [{"id": "wamid2"}]})

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[media_resp, msg_resp])

    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock(), http_client=client)
    adapter._api_key = "tok"
    adapter._phone_number_id = "99999"

    await adapter.send_audio("+1", b"OGG_DATA")

    assert client.post.call_count == 2
    msg_payload = client.post.call_args[1]["json"]
    assert msg_payload["type"] == "audio"
    assert msg_payload["audio"]["id"] == "audio_media_id"


# ---------------------------------------------------------------------------
# _handle_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_message_text(monkeypatch):
    monkeypatch.setenv("WA_KEY", "tok")
    monkeypatch.setenv("WA_PHONE", "99999")
    client = _make_http_client()
    gw = _make_gateway_mock("reply")
    adapter = WhatsAppGateway(_make_config(), gw, http_client=client)
    adapter._api_key = "tok"
    adapter._phone_number_id = "99999"

    handler = AsyncMock()
    adapter.on_message(handler)

    msg: dict[str, Any] = {
        "type": "text",
        "from": "+4912345",
        "text": {"body": "hello"},
    }
    await adapter._handle_message(msg)

    gw.handle_message.assert_awaited_once_with("whatsapp:+4912345", "hello")
    handler.assert_awaited_once_with("+4912345", "hello")
    assert client.post.called


@pytest.mark.asyncio
async def test_handle_message_audio_sends_stub():
    """Voice messages are flagged with a stub pending NIU-533."""
    gw = _make_gateway_mock("ok")
    client = _make_http_client()
    adapter = WhatsAppGateway(_make_config(), gw, http_client=client)
    adapter._api_key = "tok"
    adapter._phone_number_id = "99999"

    msg: dict[str, Any] = {
        "type": "audio",
        "from": "+4912345",
        "audio": {"id": "aud1"},
    }
    await adapter._handle_message(msg)

    prompt = gw.handle_message.call_args[0][1]
    assert "STT" in prompt or "Voice" in prompt


@pytest.mark.asyncio
async def test_handle_message_image_uses_caption():
    gw = _make_gateway_mock("ok")
    client = _make_http_client()
    adapter = WhatsAppGateway(_make_config(), gw, http_client=client)
    adapter._api_key = "tok"
    adapter._phone_number_id = "99999"

    msg: dict[str, Any] = {
        "type": "image",
        "from": "+49",
        "image": {"id": "img1", "caption": "my photo"},
    }
    await adapter._handle_message(msg)

    prompt = gw.handle_message.call_args[0][1]
    assert "my photo" in prompt


@pytest.mark.asyncio
async def test_handle_message_unknown_type_ignored():
    gw = _make_gateway_mock()
    adapter = WhatsAppGateway(_make_config(), gw)

    msg: dict[str, Any] = {"type": "sticker", "from": "+49"}
    await adapter._handle_message(msg)
    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_agent_exception(monkeypatch):
    monkeypatch.setenv("WA_KEY", "tok")
    monkeypatch.setenv("WA_PHONE", "99")
    client = _make_http_client()
    gw = MagicMock()
    gw.handle_message = AsyncMock(side_effect=RuntimeError("crash"))
    adapter = WhatsAppGateway(_make_config(), gw, http_client=client)
    adapter._api_key = "tok"
    adapter._phone_number_id = "99"

    msg: dict[str, Any] = {
        "type": "text",
        "from": "+49",
        "text": {"body": "hi"},
    }
    # Should not raise
    await adapter._handle_message(msg)
    # Error fallback sent
    assert client.post.called


# ---------------------------------------------------------------------------
# _handle_webhook_body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_webhook_body_dispatches_messages():
    gw = _make_gateway_mock("ok")
    client = _make_http_client()
    adapter = WhatsAppGateway(_make_config(), gw, http_client=client)
    adapter._api_key = "tok"
    adapter._phone_number_id = "99999"

    body: dict[str, Any] = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "type": "text",
                                    "from": "+49",
                                    "text": {"body": "test"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    await adapter._handle_webhook_body(body)

    gw.handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_webhook_body_empty_does_not_raise():
    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock())
    await adapter._handle_webhook_body({})
    await adapter._handle_webhook_body({"entry": []})


# ---------------------------------------------------------------------------
# run() — no API key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_returns_cleanly_when_no_api_key():
    cfg = _make_config(api_key_env="WA_MISSING_KEY_XYZ")
    adapter = WhatsAppGateway(cfg, _make_gateway_mock())
    await asyncio.wait_for(adapter.run(), timeout=1.0)


# ---------------------------------------------------------------------------
# GatewayChannelPort — port conformance
# ---------------------------------------------------------------------------


def test_port_conformance():
    """WhatsAppGateway satisfies GatewayChannelPort."""
    from ravn.ports.gateway_channel import GatewayChannelPort

    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock())
    assert isinstance(adapter, GatewayChannelPort)


# ---------------------------------------------------------------------------
# Additional coverage: _handle_webhook_body edge cases, start/stop lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_task_and_stop_cancels(monkeypatch):
    """start() creates server_task; stop() cancels it."""
    monkeypatch.setenv("WA_KEY", "tok")
    monkeypatch.setenv("WA_PHONE", "99999")
    cancel_called = False

    async def fake_serve():
        nonlocal cancel_called
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancel_called = True
            raise

    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock())
    adapter._api_key = "tok"
    adapter._run_webhook_server = fake_serve

    await adapter.start()
    assert adapter._server_task is not None
    await asyncio.sleep(0)  # allow task to start
    await adapter.stop()
    assert adapter._server_task is None
    assert cancel_called


@pytest.mark.asyncio
async def test_run_convenience_wrapper_no_api_key():
    """run() returns cleanly when API key is missing."""
    cfg = _make_config(api_key_env="WA_MISSING_KEY_XYZ")
    adapter = WhatsAppGateway(cfg, _make_gateway_mock())
    await asyncio.wait_for(adapter.run(), timeout=1.0)


@pytest.mark.asyncio
async def test_handle_webhook_body_multiple_entries():
    """_handle_webhook_body dispatches from multiple entries."""
    gw = _make_gateway_mock("ok")
    client = _make_http_client()
    adapter = WhatsAppGateway(_make_config(), gw, http_client=client)
    adapter._api_key = "tok"
    adapter._phone_number_id = "99999"

    body: dict = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [{"type": "text", "from": "+49", "text": {"body": "a"}}]
                        }
                    }
                ]
            },
            {
                "changes": [
                    {
                        "value": {
                            "messages": [{"type": "text", "from": "+44", "text": {"body": "b"}}]
                        }
                    }
                ]
            },
        ]
    }
    await adapter._handle_webhook_body(body)
    assert gw.handle_message.await_count == 2


@pytest.mark.asyncio
async def test_handle_message_image_no_caption():
    """Image messages with no caption use fallback text."""
    gw = _make_gateway_mock("ok")
    client = _make_http_client()
    adapter = WhatsAppGateway(_make_config(), gw, http_client=client)
    adapter._api_key = "tok"
    adapter._phone_number_id = "99999"

    msg: dict = {"type": "image", "from": "+49", "image": {"id": "img1"}}
    await adapter._handle_message(msg)

    prompt = gw.handle_message.call_args[0][1]
    assert "[Image received]" in prompt


@pytest.mark.asyncio
async def test_handle_message_text_empty_body_ignored():
    """Text messages with an empty body are silently dropped."""
    gw = _make_gateway_mock("ok")
    adapter = WhatsAppGateway(_make_config(), gw)

    msg: dict = {"type": "text", "from": "+49", "text": {"body": ""}}
    await adapter._handle_message(msg)
    gw.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_with_task_handles_cancel(monkeypatch):
    """run() awaits the server task and swallows CancelledError."""
    monkeypatch.setenv("WA_KEY", "tok")
    monkeypatch.setenv("WA_PHONE", "99999")

    async def immediate_cancel() -> None:
        raise asyncio.CancelledError

    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock())
    adapter._api_key = "tok"
    adapter._run_webhook_server = immediate_cancel

    await asyncio.wait_for(adapter.run(), timeout=1.0)


# ---------------------------------------------------------------------------
# _verify_signature
# ---------------------------------------------------------------------------


def test_verify_signature_no_secret_always_passes():
    """Without a webhook secret, all requests are allowed."""
    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock())
    assert adapter._verify_signature(b"body", "") is True
    assert adapter._verify_signature(b"body", "sha256=wrong") is True


def test_verify_signature_correct_hmac():
    """Correct HMAC digest passes verification."""
    import hashlib
    import hmac as _hmac

    secret = "mysecret"
    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock())
    adapter._webhook_secret = secret

    body = b'{"entry": []}'
    digest = "sha256=" + _hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert adapter._verify_signature(body, digest) is True


def test_verify_signature_wrong_hmac():
    """Wrong HMAC digest fails verification."""
    adapter = WhatsAppGateway(_make_config(), _make_gateway_mock())
    adapter._webhook_secret = "mysecret"
    assert adapter._verify_signature(b"body", "sha256=deadbeef") is False

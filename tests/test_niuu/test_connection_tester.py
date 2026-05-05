"""Tests for niuu.domain.services.connection_tester."""

from __future__ import annotations

import httpx
import pytest
import respx

import niuu.domain.services.connection_tester as _ct

# ---------------------------------------------------------------------------
# test_code_forge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_code_forge_success():
    respx.get("http://my-server/api/v1/volundr/me").mock(
        return_value=httpx.Response(200, json={"email": "dev@example.com"})
    )
    result = await _ct.test_code_forge(url="http://my-server", token="tok")
    assert result.success is True
    assert "dev@example.com" in result.message
    assert result.provider == "volundr"
    assert result.user == "dev@example.com"


@pytest.mark.asyncio
@respx.mock
async def test_code_forge_uses_user_id_fallback():
    respx.get("http://my-server/api/v1/volundr/me").mock(
        return_value=httpx.Response(200, json={"user_id": "uid-123"})
    )
    result = await _ct.test_code_forge(url="http://my-server", token="tok")
    assert result.success is True
    assert result.user == "uid-123"


@pytest.mark.asyncio
@respx.mock
async def test_code_forge_uses_authenticated_fallback():
    respx.get("http://my-server/api/v1/volundr/me").mock(
        return_value=httpx.Response(200, json={})
    )
    result = await _ct.test_code_forge(url="http://my-server", token="tok")
    assert result.success is True
    assert result.user == "authenticated"


@pytest.mark.asyncio
@respx.mock
async def test_code_forge_auth_failure():
    respx.get("http://my-server/api/v1/volundr/me").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    result = await _ct.test_code_forge(url="http://my-server", token="bad")
    assert result.success is False
    assert "401" in result.message


@pytest.mark.asyncio
async def test_code_forge_empty_url():
    result = await _ct.test_code_forge(url="", token="tok")
    assert result.success is False
    assert "No URL" in result.message


@pytest.mark.asyncio
@respx.mock
async def test_code_forge_connection_error():
    respx.get("http://unreachable/api/v1/volundr/me").mock(
        side_effect=httpx.ConnectError("refused")
    )
    result = await _ct.test_code_forge(url="http://unreachable", token="tok")
    assert result.success is False
    assert "unreachable" in result.message


# ---------------------------------------------------------------------------
# test_telegram_bot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_telegram_bot_success():
    respx.get("https://api.telegram.org/bot123456:ABCdefGHIjklMNOpqrsTUVwxyz/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"username": "mybot"}})
    )
    result = await _ct.test_telegram_bot(bot_token="123456:ABCdefGHIjklMNOpqrsTUVwxyz")
    assert result.success is True
    assert "@mybot" in result.message
    assert result.provider == "telegram"


@pytest.mark.asyncio
@respx.mock
async def test_telegram_bot_invalid_token():
    respx.get("https://api.telegram.org/botBAD/getMe").mock(
        return_value=httpx.Response(200, json={"ok": False})
    )
    result = await _ct.test_telegram_bot(bot_token="BAD")
    assert result.success is False


@pytest.mark.asyncio
async def test_telegram_bot_empty_token():
    result = await _ct.test_telegram_bot(bot_token="")
    assert result.success is False
    assert "No bot token" in result.message


@pytest.mark.asyncio
@respx.mock
async def test_telegram_bot_network_error():
    respx.get("https://api.telegram.org/bottok/getMe").mock(side_effect=Exception("network error"))
    result = await _ct.test_telegram_bot(bot_token="tok")
    assert result.success is False


# ---------------------------------------------------------------------------
# test_connection dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_connection_code_forge():
    respx.get("http://forge/api/v1/volundr/me").mock(
        return_value=httpx.Response(200, json={"email": "x@y.com"})
    )
    result = await _ct.test_connection("code_forge", {"url": "http://forge"}, {"token": "tok"})
    assert result.success is True


@pytest.mark.asyncio
@respx.mock
async def test_connection_messaging():
    respx.get("https://api.telegram.org/bot123456:ABCdefGHIjklMNOpqrsTUVwxyz/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"username": "bot"}})
    )
    token = "123456:ABCdefGHIjklMNOpqrsTUVwxyz"
    result = await _ct.test_connection("messaging", {}, {"bot_token": token})
    assert result.success is True


@pytest.mark.asyncio
async def test_connection_other_with_credentials():
    result = await _ct.test_connection("source_control", {}, {"token": "abc"})
    assert result.success is True
    assert result.provider == "source_control"


@pytest.mark.asyncio
async def test_connection_other_no_credentials():
    result = await _ct.test_connection("source_control", {}, {})
    assert result.success is False

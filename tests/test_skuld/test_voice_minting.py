"""Asking Forge for a call's short-lived provider credential (``skuld.voice.minting``)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from skuld.voice.minting import ForgeVoiceMinter, MintedCredential, VoiceMintError

GOOD_BODY = {
    "provider": "xai",
    "token": "ephemeral-secret",
    "expires_at_ms": 1_800_000_000_000,
    "model": "grok-voice-3.0",
    "session_bound": False,
}


def _minter(handler: Callable[[httpx.Request], httpx.Response]) -> ForgeVoiceMinter:
    client = httpx.AsyncClient(base_url="http://forge.test", transport=httpx.MockTransport(handler))

    async def get_client() -> httpx.AsyncClient:
        return client

    return ForgeVoiceMinter(get_client, session_id="sess/1", timeout_seconds=7.5)


async def _mint(minter: ForgeVoiceMinter) -> MintedCredential:
    return await minter.mint(
        provider="xai",
        model="grok-voice-3.0",
        voice="ara",
        instructions="be brief",
        reasoning="high",
    )


async def test_success_posts_to_forges_route_and_returns_the_credential():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.raw_path.decode()
        seen["body"] = json.loads(request.content)
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, json=GOOD_BODY)

    credential = await _mint(_minter(handler))

    assert credential == MintedCredential(
        provider="xai",
        token="ephemeral-secret",
        expires_at_ms=1_800_000_000_000,
        model="grok-voice-3.0",
        session_bound=False,
    )
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/v1/forge/sessions/sess%2F1/voice/client-secrets"
    assert seen["body"] == {
        "provider": "xai",
        "model": "grok-voice-3.0",
        "voice": "ara",
        "instructions": "be brief",
        "reasoning": "high",
    }
    assert seen["timeout"]["read"] == 7.5


async def test_a_session_bound_credential_says_so():
    minter = _minter(lambda request: httpx.Response(200, json={**GOOD_BODY, "session_bound": True}))

    assert (await _mint(minter)).session_bound is True


async def test_a_refusal_carries_forges_own_reason():
    minter = _minter(lambda request: httpx.Response(403, json={"detail": "no voice grant"}))

    with pytest.raises(VoiceMintError) as refused:
        await _mint(minter)

    assert "HTTP 403" in str(refused.value)
    assert "no voice grant" in str(refused.value)
    assert "xai" in str(refused.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(502, text="<html>bad gateway</html>"),
        httpx.Response(500, json={"error": "no detail key"}),
        httpx.Response(500, json=["not", "a", "dict"]),
        httpx.Response(500, json={"detail": ""}),
        httpx.Response(500, json={"detail": {"nested": True}}),
    ],
)
async def test_a_refusal_without_a_reason_falls_back_to_the_status_line(response):
    minter = _minter(lambda request: response)

    with pytest.raises(VoiceMintError) as refused:
        await _mint(minter)

    assert f"HTTP {response.status_code}" in str(refused.value)
    assert response.reason_phrase in str(refused.value)


async def test_a_transport_error_is_a_mint_error_not_a_hang_or_a_swallow():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(VoiceMintError, match="could not be reached") as failed:
        await _mint(_minter(handler))

    assert "connection refused" in str(failed.value)
    assert isinstance(failed.value.__cause__, httpx.ConnectError)


async def test_a_timeout_is_a_mint_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    with pytest.raises(VoiceMintError, match="could not be reached"):
        await _mint(_minter(handler))


async def test_a_success_that_is_not_json_is_a_mint_error():
    minter = _minter(lambda request: httpx.Response(200, text="OK"))

    with pytest.raises(VoiceMintError, match="not JSON"):
        await _mint(minter)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({k: v for k, v in GOOD_BODY.items() if k != "token"}, "token"),
        ({**GOOD_BODY, "token": ""}, "token is empty"),
        ({**GOOD_BODY, "token": 5}, "token is empty"),
        ({k: v for k, v in GOOD_BODY.items() if k != "model"}, "model"),
        ({**GOOD_BODY, "model": ""}, "model is empty"),
        ({k: v for k, v in GOOD_BODY.items() if k != "expires_at_ms"}, "expires_at_ms"),
        ({**GOOD_BODY, "expires_at_ms": "soon"}, "soon"),
        ({k: v for k, v in GOOD_BODY.items() if k != "session_bound"}, "session_bound"),
        ({k: v for k, v in GOOD_BODY.items() if k != "provider"}, "provider"),
        (["a", "list"], "indices"),
    ],
)
async def test_a_body_without_a_usable_credential_is_a_mint_error(body, reason):
    minter = _minter(lambda request: httpx.Response(200, json=body))

    with pytest.raises(VoiceMintError, match="without a usable credential") as failed:
        await _mint(minter)

    assert reason in str(failed.value)

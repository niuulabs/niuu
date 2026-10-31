"""Realtime credential minters over ``/v1/realtime/client_secrets``."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ravn.adapters.voice.client_secrets import OpenAIRealtimeMinter, XaiRealtimeMinter
from ravn.ports.realtime_voice import RealtimeCredentialError


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "key"
    path.write_text("sk-test-key\n", encoding="utf-8")
    return path


def _transport(status: int, body: object, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


async def test_xai_mints_a_ttl_only_secret(key_file: Path) -> None:
    seen: list[httpx.Request] = []
    minter = XaiRealtimeMinter(
        key_file=str(key_file),
        ttl_seconds=300,
        transport=_transport(200, {"value": "xai-secret", "expires_at": 1_800_000_000}, seen),
    )

    credential = await minter.mint(model="grok-voice-3.0", voice="ara", instructions="Be Sidekick.")

    assert credential.token == "xai-secret"
    assert credential.expires_at_ms == 1_800_000_000_000
    assert credential.model == "grok-voice-3.0"
    assert not credential.session_bound
    request = seen[0]
    assert str(request.url) == "https://api.x.ai/v1/realtime/client_secrets"
    assert request.headers["Authorization"] == "Bearer sk-test-key"
    assert json.loads(request.content) == {"expires_after": {"seconds": 300}}


async def test_openai_bakes_model_instructions_voice_and_reasoning(key_file: Path) -> None:
    seen: list[httpx.Request] = []
    minter = OpenAIRealtimeMinter(
        key_file=str(key_file),
        transport=_transport(
            200,
            {
                "client_secret": {"value": "ek_123", "expires_at": 1_800_000_000},
                "session": {"model": "gpt-realtime-2.1"},
            },
            seen,
        ),
    )

    credential = await minter.mint(
        model="gpt-realtime-2.1", voice="marin", instructions="Be Sidekick.", reasoning="low"
    )

    assert credential.token == "ek_123"
    assert credential.session_bound
    body = json.loads(seen[0].content)
    assert body["expires_after"] == {"anchor": "created_at", "seconds": 600}
    assert body["session"] == {
        "type": "realtime",
        "model": "gpt-realtime-2.1",
        "instructions": "Be Sidekick.",
        "audio": {"output": {"voice": "marin"}},
        "reasoning": {"effort": "low"},
    }


async def test_openai_drops_an_unknown_reasoning_effort(key_file: Path) -> None:
    seen: list[httpx.Request] = []
    minter = OpenAIRealtimeMinter(
        key_file=str(key_file), transport=_transport(200, {"value": "ek_1"}, seen)
    )

    credential = await minter.mint(model="m", voice="", instructions="", reasoning="extreme")

    assert "reasoning" not in json.loads(seen[0].content)["session"]
    assert credential.expires_at_ms > 0


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"error": "bad key"}, "HTTP 401"),
        (200, "not json", "not JSON"),
        (200, [1, 2], "no object"),
        (200, {"expires_at": 1}, "no secret"),
    ],
)
async def test_failed_mints_raise(key_file: Path, status: int, body: object, message: str) -> None:
    minter = XaiRealtimeMinter(key_file=str(key_file), transport=_transport(status, body, []))

    with pytest.raises(RealtimeCredentialError, match=message):
        await minter.mint(model="m", voice="v", instructions="")


async def test_unreachable_provider_raises(key_file: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    minter = XaiRealtimeMinter(key_file=str(key_file), transport=httpx.MockTransport(handler))

    with pytest.raises(RealtimeCredentialError, match="mint failed"):
        await minter.mint(model="m", voice="v", instructions="")


def test_missing_empty_or_unset_key_file_is_fatal(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.write_text("  \n", encoding="utf-8")

    with pytest.raises(ValueError, match="needs key_file"):
        XaiRealtimeMinter(key_file="")
    with pytest.raises(FileNotFoundError, match="does not exist"):
        XaiRealtimeMinter(key_file=str(tmp_path / "absent"))
    with pytest.raises(ValueError, match="is empty"):
        OpenAIRealtimeMinter(key_file=str(empty))


def test_non_positive_ttl_is_fatal(key_file: Path) -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        XaiRealtimeMinter(key_file=str(key_file), ttl_seconds=0)

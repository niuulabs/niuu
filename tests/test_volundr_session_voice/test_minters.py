"""OpenAI and xAI realtime minters over httpx.MockTransport."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from niuu import cap_telemetry
from volundr.adapters.outbound.realtime_voice import OpenAIRealtimeMinter, XaiRealtimeMinter
from volundr.ports.realtime_voice import (
    RealtimeCredential,
    RealtimeCredentialError,
    RealtimeCredentialMinterPort,
)

KEY = "sk-owner-key"


class Recorder:
    """A MockTransport handler that records the request and answers as told."""

    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


def _openai(recorder: Recorder, **kwargs: Any) -> OpenAIRealtimeMinter:
    return OpenAIRealtimeMinter(transport=recorder.transport, **kwargs)


def _xai(recorder: Recorder, **kwargs: Any) -> XaiRealtimeMinter:
    return XaiRealtimeMinter(transport=recorder.transport, **kwargs)


async def _mint(minter: RealtimeCredentialMinterPort, **kwargs: Any) -> RealtimeCredential:
    args: dict[str, Any] = {
        "api_key": KEY,
        "model": "gpt-realtime",
        "voice": "marin",
        "instructions": "Be brief.",
    }
    args.update(kwargs)
    return await minter.mint(**args)


class TestOpenAI:
    async def test_scopes_the_secret_to_the_session(self) -> None:
        recorder = Recorder(
            httpx.Response(200, json={"value": "ek_1", "expires_at": 1_700_000_000})
        )
        credential = await _mint(_openai(recorder, ttl_seconds=300), reasoning="low")
        request = recorder.requests[0]
        assert str(request.url) == "https://api.openai.com/v1/realtime/client_secrets"
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert recorder.body == {
            "expires_after": {"anchor": "created_at", "seconds": 300},
            "session": {
                "type": "realtime",
                "model": "gpt-realtime",
                "instructions": "Be brief.",
                "audio": {"output": {"voice": "marin"}},
                "reasoning": {"effort": "low"},
            },
        }
        assert credential == RealtimeCredential(
            token="ek_1",
            expires_at_ms=1_700_000_000_000,
            model="gpt-realtime",
            session_bound=True,
        )

    async def test_leaves_out_what_the_call_did_not_ask_for(self) -> None:
        recorder = Recorder(httpx.Response(200, json={"value": "ek", "expires_at": 1}))
        await _mint(_openai(recorder), voice="", instructions="")
        assert recorder.body["session"] == {"type": "realtime", "model": "gpt-realtime"}

    async def test_refuses_an_unknown_reasoning_effort(self) -> None:
        recorder = Recorder(httpx.Response(200, json={}))
        with pytest.raises(RealtimeCredentialError, match="reasoning 'extreme'"):
            await _mint(_openai(recorder), reasoning="extreme")
        assert recorder.requests == []

    async def test_reads_the_nested_client_secret_and_session_model(self) -> None:
        recorder = Recorder(
            httpx.Response(
                200,
                json={
                    "client_secret": {"value": "ek_nested", "expires_at": 1_700_000_001.5},
                    "session": {"model": "gpt-realtime-2"},
                },
            )
        )
        credential = await _mint(_openai(recorder, base_url="https://proxy.example/"))
        assert str(recorder.requests[0].url) == "https://proxy.example/v1/realtime/client_secrets"
        assert credential.token == "ek_nested"
        assert credential.expires_at_ms == 1_700_000_001_500
        assert credential.model == "gpt-realtime-2"


class TestXai:
    async def test_asks_only_for_the_expiry(self) -> None:
        recorder = Recorder(httpx.Response(200, json={"value": "xk", "expires_at": 1_700_000_000}))
        credential = await _mint(_xai(recorder, ttl_seconds=120), model="grok-voice")
        request = recorder.requests[0]
        assert str(request.url) == "https://api.x.ai/v1/realtime/client_secrets"
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        assert recorder.body == {"expires_after": {"seconds": 120}}
        assert credential == RealtimeCredential(
            token="xk", expires_at_ms=1_700_000_000_000, model="grok-voice", session_bound=False
        )


class TestErrors:
    async def test_quotes_the_providers_error_message(self) -> None:
        recorder = Recorder(
            httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}})
        )
        with pytest.raises(RealtimeCredentialError) as raised:
            await _mint(_openai(recorder))
        assert str(raised.value) == (
            "openai realtime mint returned HTTP 401: Incorrect API key provided"
        )
        assert KEY not in str(raised.value)

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(500, text="<html>upstream secret detail</html>"),
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(429, json={"error": {"code": "rate_limit"}}),
            httpx.Response(503, json=["not", "an", "object"]),
        ],
    )
    async def test_without_a_json_message_reports_only_the_status(
        self, response: httpx.Response
    ) -> None:
        recorder = Recorder(response)
        with pytest.raises(RealtimeCredentialError) as raised:
            await _mint(_xai(recorder))
        assert str(raised.value) == f"xai realtime mint returned HTTP {response.status_code}"

    async def test_a_body_that_is_not_json_is_an_error(self) -> None:
        recorder = Recorder(httpx.Response(200, text="not json"))
        with pytest.raises(RealtimeCredentialError, match="not JSON"):
            await _mint(_openai(recorder))

    async def test_a_body_that_is_not_an_object_is_an_error(self) -> None:
        recorder = Recorder(httpx.Response(200, json=["x"]))
        with pytest.raises(RealtimeCredentialError, match="returned no object"):
            await _mint(_openai(recorder))

    @pytest.mark.parametrize("data", [{"expires_at": 1}, {"value": "", "expires_at": 1}])
    async def test_a_response_without_a_secret_is_an_error(self, data: dict[str, Any]) -> None:
        recorder = Recorder(httpx.Response(200, json=data))
        with pytest.raises(RealtimeCredentialError, match="returned no secret"):
            await _mint(_openai(recorder))

    @pytest.mark.parametrize("expiry", [None, "soon", True])
    async def test_a_secret_without_an_expiry_is_refused_not_guessed(self, expiry: Any) -> None:
        recorder = Recorder(httpx.Response(200, json={"value": "ek", "expires_at": expiry}))
        with pytest.raises(RealtimeCredentialError, match="no expiry"):
            await _mint(_openai(recorder))

    async def test_a_transport_error_is_an_error_naming_the_cause(self) -> None:
        recorder = Recorder(httpx.ConnectError("connection refused"))
        with pytest.raises(RealtimeCredentialError, match="ConnectError: connection refused"):
            await _mint(_xai(recorder))

    async def test_a_timeout_is_an_error_and_is_counted(self) -> None:
        cap_telemetry.reset_cap_hits()
        recorder = Recorder(httpx.ReadTimeout("slow"))
        with pytest.raises(RealtimeCredentialError, match="timeout_seconds"):
            await _mint(_openai(recorder, timeout_seconds=3))
        counts = cap_telemetry.health_counts()
        assert counts["cap_hits.voice.providers.<name>.kwargs.timeout_seconds"] == 1


class TestConstruction:
    @pytest.mark.parametrize("cls", [OpenAIRealtimeMinter, XaiRealtimeMinter])
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"ttl_seconds": 0}, "ttl_seconds must be positive"),
            ({"ttl_seconds": -5}, "ttl_seconds must be positive"),
            ({"timeout_seconds": 0}, "timeout_seconds must be positive"),
            ({"timeout_seconds": -1.5}, "timeout_seconds must be positive"),
            ({"base_url": ""}, "needs a base_url"),
        ],
    )
    def test_refuses_what_cannot_work(self, cls: Any, kwargs: dict[str, Any], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            cls(**kwargs)

    def test_provider_names(self) -> None:
        assert OpenAIRealtimeMinter().provider == "openai"
        assert XaiRealtimeMinter().provider == "xai"

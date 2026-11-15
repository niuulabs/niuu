"""create_session_voice_service: disabled is None, enabled and impossible is fatal."""

from __future__ import annotations

from typing import Any

import pytest

from volundr.adapters.outbound.realtime_voice import OpenAIRealtimeMinter, XaiRealtimeMinter
from volundr.composition_builders import SessionVoiceUnavailableError, create_session_voice_service
from volundr.config import Settings

OPENAI = "volundr.adapters.outbound.realtime_voice.OpenAIRealtimeMinter"
XAI = "volundr.adapters.outbound.realtime_voice.XaiRealtimeMinter"


def _settings(*, tokens: bool = True, providers: dict[str, Any] | None = None, **voice: Any):
    return Settings(
        forge_mcp={"session_tokens": {"enabled": tokens}},
        voice={
            "enabled": True,
            "default_persona": "voice-persona",
            "providers": providers
            if providers is not None
            else {"openai": {"adapter": OPENAI, "credential_name": "openai-realtime"}},
            **voice,
        },
    )


def test_disabled_voice_builds_nothing() -> None:
    assert create_session_voice_service(Settings(), object()) is None  # type: ignore[arg-type]


def test_enabled_builds_every_configured_provider() -> None:
    settings = _settings(
        providers={
            "openai": {
                "adapter": OPENAI,
                "kwargs": {"ttl_seconds": 120},
                "credential_name": "openai-realtime",
            },
            "xai": {
                "adapter": XAI,
                "credential_name": "xai-realtime",
                "credential_key": "key",
            },
        }
    )
    service = create_session_voice_service(settings, object())  # type: ignore[arg-type]
    assert service is not None
    assert service.provider_names == ("openai", "xai")
    openai = service._providers["openai"]
    assert isinstance(openai.minter, OpenAIRealtimeMinter)
    assert openai.minter._ttl_seconds == 120
    assert (openai.credential_name, openai.credential_key) == ("openai-realtime", "api_key")
    xai = service._providers["xai"]
    assert isinstance(xai.minter, XaiRealtimeMinter)
    assert (xai.credential_name, xai.credential_key) == ("xai-realtime", "key")


def test_enabled_without_session_credentials_is_fatal() -> None:
    with pytest.raises(SessionVoiceUnavailableError, match="forge_mcp.session_tokens"):
        create_session_voice_service(_settings(tokens=False), object())  # type: ignore[arg-type]


def test_an_adapter_that_is_not_a_minter_is_a_type_error() -> None:
    settings = _settings(
        providers={"openai": {"adapter": "collections.OrderedDict", "credential_name": "c"}}
    )
    with pytest.raises(TypeError, match="voice.providers.openai.adapter"):
        create_session_voice_service(settings, object())  # type: ignore[arg-type]


def test_a_minter_that_cannot_be_built_is_fatal() -> None:
    settings = _settings(
        providers={
            "openai": {
                "adapter": OPENAI,
                "kwargs": {"ttl_seconds": 0},
                "credential_name": "c",
            }
        }
    )
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        create_session_voice_service(settings, object())  # type: ignore[arg-type]

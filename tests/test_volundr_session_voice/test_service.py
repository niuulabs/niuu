"""SessionVoiceCredentialService: the owner's own key, the configured provider, loud errors."""

from __future__ import annotations

from typing import Any

import pytest

from volundr.domain.services.session_voice import (
    SessionVoiceCredentialService,
    UnknownVoiceProviderError,
    VoiceProviderBinding,
    VoiceProviderKeyMissingError,
)
from volundr.ports.realtime_voice import (
    RealtimeCredential,
    RealtimeCredentialError,
    RealtimeCredentialMinterPort,
)


class FakeStore:
    """Only ``get_value`` is read by the service."""

    def __init__(self, values: dict[tuple[str, str, str], dict[str, str]] | None = None) -> None:
        self.values = values or {}
        self.reads: list[tuple[str, str, str]] = []

    async def get_value(self, owner_type: str, owner_id: str, name: str) -> dict[str, str] | None:
        self.reads.append((owner_type, owner_id, name))
        return self.values.get((owner_type, owner_id, name))


class FakeMinter(RealtimeCredentialMinterPort):
    provider = "fake"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def mint(self, **kwargs: Any) -> RealtimeCredential:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return RealtimeCredential(token="tok", expires_at_ms=5, model="m", session_bound=True)


def _service(
    store: FakeStore, openai: FakeMinter, xai: FakeMinter | None = None
) -> SessionVoiceCredentialService:
    providers = {"openai": VoiceProviderBinding(openai, "openai-realtime", "api_key")}
    if xai is not None:
        providers["xai"] = VoiceProviderBinding(xai, "xai-realtime", "key")
    return SessionVoiceCredentialService(store, providers)  # type: ignore[arg-type]


async def _mint(service: SessionVoiceCredentialService, provider: str = "openai") -> Any:
    return await service.mint(
        owner_id="owner-1",
        provider=provider,
        model="gpt-realtime",
        voice="marin",
        instructions="Be brief.",
        reasoning="low",
    )


async def test_mints_with_the_owners_own_key() -> None:
    store = FakeStore({("user", "owner-1", "openai-realtime"): {"api_key": "  sk-owner \n"}})
    minter = FakeMinter()
    service = _service(store, minter)
    credential = await _mint(service)
    assert credential.token == "tok"
    assert service.provider_names == ("openai",)
    assert store.reads == [("user", "owner-1", "openai-realtime")]
    assert minter.calls == [
        {
            "api_key": "sk-owner",
            "model": "gpt-realtime",
            "voice": "marin",
            "instructions": "Be brief.",
            "reasoning": "low",
        }
    ]


async def test_uses_the_providers_own_credential_and_field() -> None:
    store = FakeStore(
        {
            ("user", "owner-1", "openai-realtime"): {"api_key": "sk-openai"},
            ("user", "owner-1", "xai-realtime"): {"key": "xai-key"},
        }
    )
    openai, xai = FakeMinter(), FakeMinter()
    await _mint(_service(store, openai, xai), "xai")
    assert openai.calls == []
    assert xai.calls[0]["api_key"] == "xai-key"


async def test_an_unconfigured_provider_is_refused_and_never_substituted() -> None:
    store = FakeStore({("user", "owner-1", "openai-realtime"): {"api_key": "sk"}})
    minter = FakeMinter()
    service = _service(store, minter)
    with pytest.raises(UnknownVoiceProviderError) as raised:
        await _mint(service, "gemini")
    assert "'gemini'" in str(raised.value)
    assert "configured providers: openai" in str(raised.value)
    assert minter.calls == []
    assert store.reads == []


async def test_no_configured_provider_says_none() -> None:
    service = SessionVoiceCredentialService(FakeStore(), {})  # type: ignore[arg-type]
    with pytest.raises(UnknownVoiceProviderError, match="configured providers: none"):
        await _mint(service)


async def test_a_missing_credential_names_the_remedy_and_does_not_mint() -> None:
    minter = FakeMinter()
    with pytest.raises(VoiceProviderKeyMissingError) as raised:
        await _mint(_service(FakeStore(), minter))
    assert "store a user credential named 'openai-realtime' with field 'api_key'" in str(
        raised.value
    )
    assert minter.calls == []


@pytest.mark.parametrize("data", [{}, {"other": "x"}, {"api_key": ""}, {"api_key": "   "}])
async def test_a_credential_without_the_field_or_with_an_empty_value_is_refused(
    data: dict[str, str],
) -> None:
    store = FakeStore({("user", "owner-1", "openai-realtime"): data})
    minter = FakeMinter()
    with pytest.raises(VoiceProviderKeyMissingError, match="no 'api_key' field"):
        await _mint(_service(store, minter))
    assert minter.calls == []


async def test_a_provider_failure_propagates() -> None:
    store = FakeStore({("user", "owner-1", "openai-realtime"): {"api_key": "sk"}})
    minter = FakeMinter(RealtimeCredentialError("openai realtime mint returned HTTP 401"))
    with pytest.raises(RealtimeCredentialError, match="HTTP 401"):
        await _mint(_service(store, minter))

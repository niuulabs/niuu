"""SessionVoiceContributor and voice.enabled: every session gets Skuld's voice channel."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.contributors.session_voice import (
    VOICE_DEFAULT_PERSONA_ENV,
    VOICE_ENABLED_ENV,
    SessionVoiceContributor,
)
from volundr.config import Settings
from volundr.domain.models import Principal, Session, SessionSpec
from volundr.domain.ports import SessionContext, SessionPersona, SessionPersonaProvider
from volundr.domain.session_launch_facts import VOICE_CHANNEL_FACT
from volundr.main import _create_contributors

VOICE_SETTINGS = {
    "enabled": True,
    "default_persona": "lexi",
    "providers": {
        "openai": {"adapter": "x.Minter", "credential_name": "openai"},
    },
}


def _document(voice: object = None, name: str = "lexi") -> dict:
    definition: dict = {"name": name, "system_prompt_template": "Talk"}
    if voice is not None:
        definition["voice"] = voice
    return {
        "schema_version": 1,
        "id": name,
        "revision": "content-0123456789abcdef",
        "definition": definition,
    }


def _provider(persona: SessionPersona | None) -> AsyncMock:
    provider = AsyncMock(spec=SessionPersonaProvider)
    provider.get.return_value = persona
    return provider


def _persona(document: dict) -> SessionPersona:
    return SessionPersona(name="lexi", system_prompt="Talk", document=document)


VOICE = {"voices": {"default": {"provider": "openai", "voice": "marin"}}}


def _principal(user_id: str = "user-1") -> Principal:
    return Principal(user_id=user_id, email="u@example.com", tenant_id="t", roles=[])


async def test_every_session_gets_the_voice_settings_and_the_default_persona() -> None:
    document = _document(VOICE)
    provider = _provider(_persona(document))
    contributor = SessionVoiceContributor(default_persona=" lexi ", persona_provider=provider)

    contribution = await contributor.contribute(
        Session(name="s", owner_id="owner"), SessionContext(principal=_principal())
    )

    provider.get.assert_awaited_once_with("user-1", "lexi")
    env = {item["name"]: item["value"] for item in contribution.pod_spec.env}
    assert env[VOICE_ENABLED_ENV] == "true"
    assert json.loads(env[VOICE_DEFAULT_PERSONA_ENV]) == document
    assert len(contribution.pod_spec.env) == 2
    assert contribution.launch_facts == {VOICE_CHANNEL_FACT: True}
    assert contribution.values == {}


async def test_without_a_principal_the_persona_is_the_session_owners() -> None:
    provider = _provider(_persona(_document(VOICE)))
    contributor = SessionVoiceContributor(default_persona="lexi", persona_provider=provider)

    await contributor.contribute(Session(name="s", owner_id="owner-7"), SessionContext())

    provider.get.assert_awaited_once_with("owner-7", "lexi")


def test_the_skuld_settings_are_named_as_skuld_reads_them() -> None:
    assert VOICE_ENABLED_ENV == "SKULD__VOICE_CHANNEL__ENABLED"
    assert VOICE_DEFAULT_PERSONA_ENV == "SKULD__VOICE_CHANNEL__DEFAULT_PERSONA"


def test_the_contributor_is_named_for_the_channel() -> None:
    contributor = SessionVoiceContributor(default_persona="lexi", persona_provider=_provider(None))
    assert contributor.name == "session_voice"


@pytest.mark.parametrize("name", ["", "   "])
def test_a_contributor_without_a_persona_name_names_the_fix(name: str) -> None:
    with pytest.raises(ValueError, match="voice.default_persona"):
        SessionVoiceContributor(default_persona=name, persona_provider=_provider(None))


def test_a_contributor_without_a_persona_catalog_names_the_fix() -> None:
    with pytest.raises(ValueError, match="no persona catalog"):
        SessionVoiceContributor(default_persona="lexi")


async def test_a_persona_the_owner_does_not_have_fails_the_launch() -> None:
    contributor = SessionVoiceContributor(default_persona="lexi", persona_provider=_provider(None))

    with pytest.raises(ValueError, match=r"voice.default_persona 'lexi' is not in the persona"):
        await contributor.contribute(Session(name="s"), SessionContext(principal=_principal()))


async def test_a_persona_without_a_pinned_document_fails_the_launch() -> None:
    contributor = SessionVoiceContributor(
        default_persona="lexi", persona_provider=_provider(_persona({}))
    )

    with pytest.raises(ValueError, match="carries no pinned document"):
        await contributor.contribute(Session(name="s"), SessionContext(principal=_principal()))


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 1, "id": "lexi"},
        {"definition": "not a mapping"},
        _document(),
        _document("not a mapping"),
        _document({}),
        _document({"voices": {}}),
        _document({"voices": []}),
    ],
    ids=[
        "no-definition",
        "definition-not-a-mapping",
        "no-voice-section",
        "voice-not-a-mapping",
        "voice-without-voices",
        "voices-empty-mapping",
        "voices-empty-list",
    ],
)
async def test_a_persona_without_a_voice_fails_the_launch_naming_it(document: dict) -> None:
    contributor = SessionVoiceContributor(
        default_persona="lexi", persona_provider=_provider(_persona(document))
    )

    with pytest.raises(ValueError, match=r"'lexi' has no voice: section"):
        await contributor.contribute(Session(name="s"), SessionContext(principal=_principal()))


class TestWiring:
    def test_voice_is_off_by_default_and_wires_nothing(self) -> None:
        assert "session_voice" not in [c.name for c in _create_contributors(Settings())]

    def test_enabled_voice_wires_the_contributor_once(self) -> None:
        contributors = _create_contributors(
            Settings(voice=VOICE_SETTINGS), persona_provider=_provider(None)
        )

        assert [c.name for c in contributors].count("session_voice") == 1

    def test_a_configured_contributor_is_not_wired_twice(self) -> None:
        from volundr.config import SessionContributorConfig

        settings = Settings(
            voice=VOICE_SETTINGS,
            session_contributors=[
                SessionContributorConfig(
                    adapter=(
                        "volundr.adapters.outbound.contributors.session_voice."
                        "SessionVoiceContributor"
                    ),
                    kwargs={"default_persona": "other"},
                )
            ],
        )

        contributors = _create_contributors(settings, persona_provider=_provider(None))

        assert [c.name for c in contributors].count("session_voice") == 1

    def test_enabled_voice_without_a_persona_catalog_is_fatal(self) -> None:
        with pytest.raises(ValueError, match="no persona catalog"):
            _create_contributors(Settings(voice=VOICE_SETTINGS))


class TestDelivery:
    async def test_the_voice_env_reaches_the_merged_spec(self) -> None:
        contributor = SessionVoiceContributor(
            default_persona="lexi", persona_provider=_provider(_persona(_document(VOICE)))
        )
        contribution = await contributor.contribute(
            Session(name="s"), SessionContext(principal=_principal())
        )

        spec = SessionSpec.merge([contribution])

        assert {item["name"] for item in spec.pod_spec.env} == {
            VOICE_ENABLED_ENV,
            VOICE_DEFAULT_PERSONA_ENV,
        }

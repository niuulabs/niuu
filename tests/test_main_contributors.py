from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from volundr.config import SessionContributorConfig, Settings
from volundr.domain.ports import SessionPersonaProvider
from volundr.main import _create_contributors


def test_create_contributors_auto_wires_workload_config_once() -> None:
    settings = Settings(
        session_contributors=[
            SessionContributorConfig(
                adapter=(
                    "volundr.adapters.outbound.contributors.workload_config."
                    "WorkloadConfigContributor"
                )
            )
        ]
    )

    contributors = _create_contributors(settings)
    workload_contributors = [
        contributor for contributor in contributors if contributor.name == "workload_config"
    ]

    assert len(workload_contributors) == 1


def test_create_contributors_auto_wires_persona_provider_once() -> None:
    provider = AsyncMock(spec=SessionPersonaProvider)

    contributors = _create_contributors(Settings(), persona_provider=provider)

    assert [contributor.name for contributor in contributors].count("persona") == 1
    names = [contributor.name for contributor in contributors]
    assert names.index("persona") < max(
        index for index, name in enumerate(names) if name == "prompt"
    )


def test_create_contributors_auto_wires_integrations_with_a_registry() -> None:
    """Docker and host installs get the integrations contributor (Claude auth
    mode, MCP servers, the model gateway URL) without listing it in config."""
    from volundr.domain.services.integration_registry import IntegrationRegistry

    contributors = _create_contributors(Settings(), integration_registry=IntegrationRegistry([]))
    assert [c.name for c in contributors if c.name == "integrations"] == ["integrations"]
    assert not [c for c in _create_contributors(Settings()) if c.name == "integrations"]


def test_create_contributors_keeps_a_configured_integrations_contributor_single() -> None:
    from volundr.domain.services.integration_registry import IntegrationRegistry

    settings = Settings(
        session_contributors=[
            SessionContributorConfig(
                adapter="volundr.adapters.outbound.contributors.integrations.IntegrationContributor"
            )
        ]
    )
    contributors = _create_contributors(settings, integration_registry=IntegrationRegistry([]))
    assert len([c for c in contributors if c.name == "integrations"]) == 1


def test_create_contributors_auto_wires_the_team_room_once_before_the_prompt() -> None:
    """A team's root and members get their mesh realm and room with no contributor listed."""
    names = [contributor.name for contributor in _create_contributors(Settings())]

    assert names.count("team_room") == 1
    assert names.index("team_room") < names.index("prompt")


def test_create_contributors_keeps_a_configured_team_room_single() -> None:
    settings = Settings(
        session_contributors=[
            SessionContributorConfig(
                adapter="volundr.adapters.outbound.contributors.team_room.TeamRoomContributor"
            )
        ]
    )

    names = [contributor.name for contributor in _create_contributors(settings)]

    assert names.count("team_room") == 1


def _team_room(settings: Settings, **ports: object):
    return next(c for c in _create_contributors(settings, **ports) if c.name == "team_room")


def test_the_team_room_hands_a_team_root_its_voices_only_when_voice_is_on() -> None:
    voice = {
        "enabled": True,
        "default_persona": "narrator",
        "providers": {
            "openai": {
                "adapter": "volundr.adapters.outbound.realtime_voice.OpenAIRealtimeMinter",
                "credential_name": "openai-realtime",
            }
        },
    }
    on = Settings(voice=voice)

    assert _team_room(Settings())._voice_enabled is False
    assert _team_room(on, persona_provider=AsyncMock(spec=SessionPersonaProvider))._voice_enabled


_VOICE_ON = {
    "enabled": True,
    "default_persona": "narrator",
    "providers": {
        "openai": {
            "adapter": "volundr.adapters.outbound.realtime_voice.OpenAIRealtimeMinter",
            "credential_name": "openai-realtime",
        }
    },
}
_TEAM_ROOM = "volundr.adapters.outbound.contributors.team_room.TeamRoomContributor"


def test_a_configured_team_room_without_voice_is_refused_while_voice_is_on() -> None:
    settings = Settings(
        voice=_VOICE_ON, session_contributors=[SessionContributorConfig(adapter=_TEAM_ROOM)]
    )

    with pytest.raises(RuntimeError, match="voice_enabled: true"):
        _create_contributors(settings, persona_provider=AsyncMock(spec=SessionPersonaProvider))


def test_a_configured_team_room_with_voice_is_kept() -> None:
    settings = Settings(
        voice=_VOICE_ON,
        session_contributors=[
            SessionContributorConfig(adapter=_TEAM_ROOM, kwargs={"voice_enabled": True})
        ],
    )

    room = _team_room(settings, persona_provider=AsyncMock(spec=SessionPersonaProvider))

    assert room.voice_enabled is True

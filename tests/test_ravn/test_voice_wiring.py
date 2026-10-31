"""Composing the voice channel from settings and persona: configured or fatal."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ravn.adapters.channels.voice.gateway import VoiceGateway
from ravn.adapters.channels.voice.room import VoiceRoomCopier
from ravn.adapters.personas.loader import PersonaConfig, PersonaVoiceConfig
from ravn.cli import voice_wiring
from ravn.cli.commands import _make_channel_tasks
from ravn.config import GatewayChannelsConfig, Settings, VoiceChannelConfig, VoiceMinterConfig
from tests.test_ravn.test_voice_prompt_tools_room import FakeTool

_MINTER = "ravn.adapters.voice.client_secrets.XaiRealtimeMinter"


def _settings(tmp_path: Path, **voice: object) -> Settings:
    key = tmp_path / "xai-key"
    key.write_text("sk-test", encoding="utf-8")
    fields: dict[str, object] = {
        "enabled": True,
        "owner_id": "owner-1",
        "store_path": str(tmp_path / "calls.db"),
        "minters": {"xai": VoiceMinterConfig(adapter=_MINTER, kwargs={"key_file": str(key)})},
    }
    fields.update(voice)
    settings = Settings()
    settings.gateway.channels.voice = VoiceChannelConfig(**fields)
    return settings


def _persona(**voice: object) -> PersonaConfig:
    fields: dict[str, object] = {"voices": {"grok-voice": "ara"}, "tools": ["web_search"]}
    fields.update(voice)
    return PersonaConfig(
        name="sidekick",
        system_prompt_template="You are Sidekick.",
        allowed_tools=["web"],
        voice=PersonaVoiceConfig(**fields),  # type: ignore[arg-type]
    )


@pytest.fixture(autouse=True)
def fake_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ravn.cli.commands._build_tool_mcp_tools",
        lambda settings, persona_config: [FakeTool("web_search"), FakeTool("web_fetch")],
    )


def test_builds_the_gateway_with_only_the_voice_tools(tmp_path: Path) -> None:
    gateway = voice_wiring.build_voice_gateway(_settings(tmp_path), _persona())

    assert isinstance(gateway, VoiceGateway)
    names = [d["name"] for d in gateway._tools.definitions()]  # noqa: SLF001
    asyncio.run(gateway.aclose())
    assert names[0] == "web_search" and "web_fetch" not in names


def test_room_copy_is_built_when_the_broker_is_configured(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, room_broker_url="http://127.0.0.1:8081", room_participant_id="flock-sidekick"
    )

    room = voice_wiring.build_room_copier(settings.gateway.channels.voice)

    assert isinstance(room, VoiceRoomCopier)
    assert voice_wiring.build_room_copier(VoiceChannelConfig()) is None
    asyncio.run(room.aclose())


@pytest.mark.parametrize("persona", [None, PersonaConfig(name="quiet")])
def test_a_persona_without_voice_is_fatal(tmp_path: Path, persona: PersonaConfig | None) -> None:
    with pytest.raises(ValueError, match="has no voice section"):
        voice_wiring.build_voice_gateway(_settings(tmp_path), persona)


def test_a_voice_tool_the_persona_cannot_build_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\['tracker_issue'\]"):
        voice_wiring.build_voice_gateway(
            _settings(tmp_path), _persona(tools=["web_search", "tracker_issue"])
        )


def test_no_minters_is_fatal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no minters"):
        voice_wiring.build_voice_gateway(_settings(tmp_path, minters={}), _persona())


def test_a_minter_that_is_not_a_minter_is_fatal() -> None:
    config = VoiceChannelConfig(
        minters={
            "xai": VoiceMinterConfig(
                adapter="collections.OrderedDict",
                kwargs={},
            )
        }
    )

    with pytest.raises(TypeError, match="RealtimeCredentialMinterPort"):
        voice_wiring.build_minters(config)


async def test_channel_tasks_start_the_voice_channel(tmp_path: Path) -> None:
    started: list[str] = []

    class Gateway:
        async def run(self) -> None:
            started.append("voice")

    channels = GatewayChannelsConfig(voice=VoiceChannelConfig(enabled=True))
    pairs = _make_channel_tasks(channels, gw=None, voice_factory=Gateway)
    await asyncio.gather(*(task for task, _ in pairs))

    assert [name for _, name in pairs] == ["voice"] and started == ["voice"]


def test_channel_tasks_without_a_voice_factory_are_fatal() -> None:
    channels = GatewayChannelsConfig(voice=VoiceChannelConfig(enabled=True))

    with pytest.raises(RuntimeError, match="builds no voice channel"):
        _make_channel_tasks(channels, gw=None)

"""The voice channel's configuration and its fatal prerequisites (``skuld.config``)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from skuld.config import SkuldSettings, VoiceConfig
from tests.test_skuld.voice_support import OWNER, persona_document


def _ready(**overrides):
    values = {
        "session": {"owner_id": OWNER},
        "volundr_api_url": "http://forge.test",
        "forge_mcp": {"token": "forge-session-token"},
        "voice_channel": {"enabled": True},
    }
    values.update(overrides)
    return values


class TestVoiceConfig:
    def test_defaults_are_off_and_consistent(self):
        config = VoiceConfig()

        assert config.enabled is False
        assert config.default_persona is None
        assert config.send_confirm_seconds < config.tool_timeout_seconds
        assert set(config.models) == {"grok", "gpt-realtime"}

    @pytest.mark.parametrize("confirm", [60.0, 61.0])
    def test_a_send_wait_that_reaches_the_tool_timeout_is_refused(self, confirm):
        with pytest.raises(ValidationError, match="send_confirm_seconds must be below"):
            VoiceConfig(send_confirm_seconds=confirm, tool_timeout_seconds=60.0)

    def test_a_wait_just_under_the_timeout_is_accepted(self):
        config = VoiceConfig(send_confirm_seconds=59.0, tool_timeout_seconds=60.0)

        assert config.send_confirm_seconds == 59.0

    @pytest.mark.parametrize(
        "field", ["mint_timeout_seconds", "send_confirm_seconds", "tool_timeout_seconds"]
    )
    def test_a_non_positive_time_is_refused(self, field):
        with pytest.raises(ValidationError):
            VoiceConfig(**{field: 0})

    def test_the_full_read_budget_has_a_floor_above_the_header_room(self):
        with pytest.raises(ValidationError):
            VoiceConfig(full_read_max_chars=1023)

    @pytest.mark.parametrize("field", ["card_recent_turns", "card_turn_max_chars"])
    def test_card_bounds_must_be_positive(self, field):
        with pytest.raises(ValidationError):
            VoiceConfig(**{field: 0})


class TestSettingsPrerequisites:
    def test_voice_off_needs_nothing(self):
        assert SkuldSettings().voice_channel.enabled is False

    def test_enabled_with_everything_present_loads(self):
        settings = SkuldSettings(**_ready())

        assert settings.voice_channel.enabled is True

    def test_enabled_with_nothing_names_every_missing_prerequisite(self):
        with pytest.raises(ValidationError) as refused:
            SkuldSettings(voice_channel={"enabled": True})

        message = str(refused.value)
        assert "voice_channel.enabled is true but the voice channel cannot serve without" in message
        assert "a Forge session credential" in message
        assert "volundr_api_url" in message
        assert "session.owner_id" in message
        assert "SKULD__VOICE_CHANNEL__ENABLED=false" in message

    @pytest.mark.parametrize(
        ("override", "named"),
        [
            ({"forge_mcp": {}}, "a Forge session credential"),
            ({"volundr_api_url": ""}, "volundr_api_url"),
            ({"session": {"owner_id": "  "}}, "session.owner_id"),
            ({"event_log_enabled": False}, "event_log_enabled: true"),
            ({"history_hydration_enabled": False}, "history_hydration_enabled: true"),
        ],
    )
    def test_each_missing_prerequisite_alone_is_fatal_and_named(self, override, named):
        with pytest.raises(ValidationError, match="cannot serve without") as refused:
            SkuldSettings(**_ready(**override))

        assert named in str(refused.value)

    def test_only_the_missing_ones_are_named(self):
        with pytest.raises(ValidationError) as refused:
            SkuldSettings(**_ready(volundr_api_url=""))

        message = str(refused.value)
        assert "volundr_api_url" in message
        assert "session.owner_id" not in message
        assert "a Forge session credential" not in message

    def test_a_mounted_token_file_satisfies_the_credential(self, tmp_path):
        token_file = tmp_path / "token"
        token_file.write_text("file-session-token")

        settings = SkuldSettings(**_ready(forge_mcp={"token_file": str(token_file)}))

        assert settings.forge_mcp.token == "file-session-token"
        assert settings.voice_channel.enabled is True

    def test_an_unreadable_token_file_is_fatal_before_voice_can_start(self):
        with pytest.raises(ValidationError, match="cannot be read"):
            SkuldSettings(**_ready(forge_mcp={"token_file": "/nonexistent/forge/token"}))


class TestDefaultPersonaFromEnvironment:
    def test_a_json_string_parses_into_the_persona_document(self, monkeypatch):
        document = persona_document()
        monkeypatch.setenv("SKULD__VOICE_CHANNEL__DEFAULT_PERSONA", json.dumps(document))

        settings = SkuldSettings()

        assert settings.voice_channel.default_persona == document

    def test_the_rest_of_the_voice_config_comes_from_the_environment_too(self, monkeypatch):
        monkeypatch.setenv("SKULD__VOICE_CHANNEL__ENABLED", "true")
        monkeypatch.setenv("SKULD__VOICE_CHANNEL__DEFAULT_PERSONA", json.dumps(persona_document()))
        monkeypatch.setenv("SKULD__VOLUNDR_API_URL", "http://forge.test")
        monkeypatch.setenv("SKULD__SESSION__OWNER_ID", OWNER)
        monkeypatch.setenv("SKULD__FORGE_MCP__TOKEN", "forge-session-token")

        settings = SkuldSettings()

        assert settings.voice_channel.enabled is True
        assert settings.voice_channel.default_persona["id"] == "sidekick"

    def test_malformed_json_is_refused_not_ignored(self, monkeypatch):
        monkeypatch.setenv("SKULD__VOICE_CHANNEL__DEFAULT_PERSONA", "{not json")

        with pytest.raises(ValidationError, match="voice_channel.default_persona"):
            SkuldSettings()


class TestSharedMiniConfig:
    """Mini mode: every service reads one ~/.niuu/config.yaml (NIUU_CONFIG)."""

    def test_forges_voice_section_in_the_shared_file_does_not_stop_skuld(
        self, tmp_path, monkeypatch
    ):
        shared = tmp_path / "config.yaml"
        shared.write_text(
            "voice:\n"
            "  enabled: true\n"
            "  default_persona: lexi\n"
            "  providers:\n"
            "    xai:\n"
            "      adapter: volundr.adapters.outbound.realtime_voice.XaiRealtimeMinter\n"
            "      credential_name: xai\n",
            encoding="utf-8",
        )
        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [shared])

        settings = SkuldSettings()

        assert settings.voice_channel.enabled is False
        assert settings.voice_channel.default_persona is None

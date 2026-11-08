"""Tests for RavnProfile integration points: RavnGateway.get_status and _apply_profile."""

from __future__ import annotations

from unittest.mock import MagicMock

from ravn.domain.profile import RavnProfile

# ---------------------------------------------------------------------------
# RavnGateway.get_status
# ---------------------------------------------------------------------------


class TestRavnGatewayGetStatus:
    def _make_gateway(self, profile: RavnProfile | None = None) -> object:
        from ravn.adapters.channels.gateway import RavnGateway
        from ravn.config import GatewayConfig

        return RavnGateway(GatewayConfig(), MagicMock(), profile=profile)

    def test_status_without_profile(self) -> None:
        gw = self._make_gateway(profile=None)
        status = gw.get_status()
        assert "session_count" in status
        assert "active_sessions" in status
        assert "profile" not in status

    def test_status_with_profile(self) -> None:
        profile = RavnProfile(
            name="tanngrisnir",
            location="gimle",
            deployment="k8s",
            specialisations=["coding"],
        )
        gw = self._make_gateway(profile=profile)
        status = gw.get_status()
        assert "profile" in status
        p = status["profile"]
        assert p["name"] == "tanngrisnir"
        assert p["location"] == "gimle"
        assert p["deployment"] == "k8s"
        assert p["specialisations"] == ["coding"]

    def test_status_session_count_accurate(self) -> None:
        gw = self._make_gateway()
        assert gw.get_status()["session_count"] == 0


# ---------------------------------------------------------------------------
# _apply_profile
# ---------------------------------------------------------------------------


class TestApplyProfile:
    def _make_settings(self) -> object:
        from ravn.config import Settings

        return Settings()

    def test_system_prompt_extra_appended(self) -> None:
        from ravn.cli.commands import _apply_profile
        from ravn.config import Settings

        settings = Settings()
        settings.agent.system_prompt = "Base prompt."
        profile = RavnProfile(name="x", system_prompt_extra="Extra context.")
        system_prompt, _, _ = _apply_profile(profile, settings, persona_config=None)
        assert "Base prompt." in system_prompt
        assert "Extra context." in system_prompt

    def test_empty_system_prompt_extra_not_appended(self) -> None:
        from ravn.cli.commands import _apply_profile
        from ravn.config import Settings

        settings = Settings()
        settings.agent.system_prompt = "Base prompt."
        profile = RavnProfile(name="x", system_prompt_extra="")
        system_prompt, _, _ = _apply_profile(profile, settings, persona_config=None)
        assert system_prompt == "Base prompt."

    def test_checkpoint_enabled_by_profile(self) -> None:
        from ravn.cli.commands import _apply_profile
        from ravn.config import Settings

        settings = Settings()
        settings.checkpoint.enabled = False
        profile = RavnProfile(name="x", checkpoint_enabled=True)
        _apply_profile(profile, settings, persona_config=None)
        assert settings.checkpoint.enabled is True

    def test_mcp_servers_filtered_by_profile(self) -> None:
        from ravn.cli.commands import _apply_profile
        from ravn.config import MCPServerConfig, Settings

        settings = Settings()
        settings.mcp_servers = [
            MCPServerConfig(name="linear", command="npx", args=["-y", "linear-mcp"], enabled=True),
            MCPServerConfig(name="gmail", command="npx", args=["-y", "gmail-mcp"], enabled=True),
            MCPServerConfig(name="slack", command="npx", args=["-y", "slack-mcp"], enabled=True),
        ]
        profile = RavnProfile(name="x", mcp_servers=["linear", "gmail"])
        _apply_profile(profile, settings, persona_config=None)
        names = {s.name for s in settings.mcp_servers}
        assert names == {"linear", "gmail"}
        assert "slack" not in names

    def test_mcp_servers_not_filtered_when_profile_list_empty(self) -> None:
        from ravn.cli.commands import _apply_profile
        from ravn.config import MCPServerConfig, Settings

        settings = Settings()
        settings.mcp_servers = [
            MCPServerConfig(name="linear", command="npx", args=["-y", "linear-mcp"], enabled=True),
        ]
        profile = RavnProfile(name="x", mcp_servers=[])
        _apply_profile(profile, settings, persona_config=None)
        assert len(settings.mcp_servers) == 1

    def test_persona_config_overrides_applied_before_extra(self) -> None:
        from personas.domain.persona import PersonaConfig, PersonaLLMConfig
        from ravn.cli.commands import _apply_profile
        from ravn.config import Settings

        settings = Settings()
        persona = PersonaConfig(
            name="coding-agent",
            system_prompt_template="Persona prompt.",
            llm=PersonaLLMConfig(),
        )
        profile = RavnProfile(name="x", system_prompt_extra="Profile extra.")
        system_prompt, _, _ = _apply_profile(profile, settings, persona_config=persona)
        assert "Persona prompt." in system_prompt
        assert "Profile extra." in system_prompt
        # Extra must come after persona template
        assert system_prompt.index("Persona prompt.") < system_prompt.index("Profile extra.")

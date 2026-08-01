"""Tests for SessionDefinitionContributor."""

import pytest

from niuu.config_models import SessionDefinitionConfig, default_session_definitions
from volundr.adapters.outbound.contributors.session_def import (
    SessionDefinitionContributor,
)
from volundr.domain.ports import SessionContext


def _mock_session():
    """Create a minimal mock session for testing."""
    from unittest.mock import MagicMock

    session = MagicMock()
    session.id = "test-session-id"
    session.name = "test-session"
    session.model = "claude-sonnet-4-6"
    return session


DEFINITIONS = {
    "skuldClaude": SessionDefinitionConfig(
        enabled=True,
        display_name="Claude Code",
        description="Anthropic Claude",
        labels=["session"],
        default_model="claude-sonnet-4-6",
        defaults={
            "broker": {
                "cliType": "claude",
                "transport": "sdk",
                "transportAdapter": "skuld.transports.sdk.SDKTransport",
            },
        },
    ),
    "skuldCodex": SessionDefinitionConfig(
        enabled=True,
        display_name="OpenAI Codex",
        description="OpenAI Codex via WebSocket",
        labels=["session"],
        default_model="",
        defaults={
            "broker": {
                "cliType": "codex-ws",
                "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
            },
        },
    ),
    "skuldDisabled": SessionDefinitionConfig(
        enabled=False,
        display_name="Disabled",
        description="Should not be used",
        defaults={"broker": {"cliType": "nope"}},
    ),
}


class TestSessionDefinitionContributor:
    @pytest.mark.asyncio
    async def test_merges_definition_defaults(self):
        contributor = SessionDefinitionContributor(definitions=DEFINITIONS)
        context = SessionContext(definition="skuldCodex")

        result = await contributor.contribute(_mock_session(), context)

        assert result.values["broker"]["cliType"] == "codex-ws"
        assert "transportAdapter" in result.values["broker"]

    @pytest.mark.asyncio
    async def test_drops_empty_definition_api_url(self):
        definitions = {
            "skuldCodex": SessionDefinitionConfig(
                enabled=True,
                defaults={
                    "broker": {"cliType": "codex-ws"},
                    "volundr": {"apiUrl": ""},
                },
            )
        }
        contributor = SessionDefinitionContributor(definitions=definitions)
        context = SessionContext(definition="skuldCodex")

        result = await contributor.contribute(_mock_session(), context)

        assert result.values["broker"]["cliType"] == "codex-ws"
        assert "volundr" not in result.values

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_definition(self):
        contributor = SessionDefinitionContributor(definitions=DEFINITIONS)
        context = SessionContext()

        result = await contributor.contribute(_mock_session(), context)

        assert result.values == {}

    @pytest.mark.asyncio
    async def test_uses_default_definition(self):
        contributor = SessionDefinitionContributor(
            definitions=DEFINITIONS, default_definition="skuldClaude"
        )
        context = SessionContext()

        result = await contributor.contribute(_mock_session(), context)

        assert result.values["broker"]["cliType"] == "claude"
        assert result.values["model"] == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_explicit_definition_overrides_default(self):
        contributor = SessionDefinitionContributor(
            definitions=DEFINITIONS, default_definition="skuldClaude"
        )
        context = SessionContext(definition="skuldCodex")

        result = await contributor.contribute(_mock_session(), context)

        assert result.values["broker"]["cliType"] == "codex-ws"
        assert "model" not in result.values

    @pytest.mark.asyncio
    async def test_disabled_definition_returns_empty(self):
        contributor = SessionDefinitionContributor(definitions=DEFINITIONS)
        context = SessionContext(definition="skuldDisabled")

        result = await contributor.contribute(_mock_session(), context)

        assert result.values == {}

    @pytest.mark.asyncio
    async def test_unknown_definition_returns_empty(self):
        contributor = SessionDefinitionContributor(definitions=DEFINITIONS)
        context = SessionContext(definition="nonexistent")

        result = await contributor.contribute(_mock_session(), context)

        assert result.values == {}

    def test_contributor_name(self):
        contributor = SessionDefinitionContributor()
        assert contributor.name == "session_definition"


class TestSessionDefinitionConfig:
    def test_defaults(self):
        config = SessionDefinitionConfig()
        assert config.enabled is True
        assert config.display_name == ""
        assert config.description == ""
        assert config.labels == []
        assert config.default_model == ""
        assert config.defaults == {}

    def test_from_dict(self):
        config = SessionDefinitionConfig(
            enabled=True,
            display_name="Claude Code",
            description="Test",
            labels=["session"],
            default_model="claude-sonnet-4-6",
            defaults={"broker": {"cliType": "claude"}},
        )
        assert config.display_name == "Claude Code"
        assert config.defaults["broker"]["cliType"] == "claude"


class TestDefaultSessionDefinitions:
    def test_returns_three_definitions(self):
        defs = default_session_definitions()
        assert "skuldClaude" in defs
        assert "skuldClaudeInteractive" in defs
        assert "skuldCodex" in defs
        assert "skuldOpenCode" in defs

    def test_all_enabled(self):
        for defn in default_session_definitions().values():
            assert defn.enabled is True

    def test_claude_defaults(self):
        claude = default_session_definitions()["skuldClaude"]
        assert claude.display_name == "Claude Code"
        assert claude.defaults["broker"]["cliType"] == "claude"
        assert claude.defaults["broker"]["transport"] == "sdk"
        assert claude.defaults["broker"]["transportAdapter"] == "skuld.transports.sdk.SDKTransport"

    def test_claude_interactive_defaults(self):
        interactive = default_session_definitions()["skuldClaudeInteractive"]
        assert interactive.display_name == "Claude Code Interactive"
        assert interactive.default_model == "claude-sonnet-4-6"
        assert interactive.defaults["broker"]["cliType"] == "claude"
        assert interactive.defaults["broker"]["transport"] == "tmux-interactive"
        assert (
            interactive.defaults["broker"]["transportAdapter"]
            == "skuld.transports.tmux_interactive.TmuxInteractiveTransport"
        )
        assert interactive.defaults["broker"]["skipPermissions"] is True

    def test_codex_defaults(self):
        codex = default_session_definitions()["skuldCodex"]
        assert codex.display_name == "OpenAI Codex"
        assert codex.defaults["broker"]["cliType"] == "codex-ws"

        batch = default_session_definitions()["skuldCodexExec"]
        assert batch.defaults["broker"]["cliType"] == "codex-ws"
        assert (
            batch.defaults["broker"]["transportAdapter"]
            == "skuld.transports.codex_ws.CodexWebSocketTransport"
        )

    def test_opencode_defaults(self):
        opencode = default_session_definitions()["skuldOpenCode"]
        assert opencode.display_name == "OpenCode"
        assert opencode.defaults["broker"]["cliType"] == "opencode"

    @pytest.mark.asyncio
    async def test_default_definitions_work_with_contributor(self):
        """Default definitions integrate correctly with the contributor."""
        defs = default_session_definitions()
        contributor = SessionDefinitionContributor(
            definitions=defs, default_definition="skuldClaude"
        )
        # No explicit definition — should fall back to skuldClaude
        context = SessionContext()
        result = await contributor.contribute(_mock_session(), context)
        assert result.values["broker"]["cliType"] == "claude"
        assert result.values["model"] == "claude-opus-4-8"

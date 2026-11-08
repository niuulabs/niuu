"""Persona resolution and RAVN.md project overrides in the Ravn CLI."""

from __future__ import annotations

from unittest.mock import patch

from niuu.domain.outcome import OutcomeField
from personas.domain.permission_mode import PermissionMode
from personas.domain.persona import (
    PersonaConfig,
    PersonaConsumes,
    PersonaFanIn,
    PersonaLLMConfig,
    PersonaProduces,
    PersonaVoiceConfig,
)
from ravn.cli.commands import _merge_project_overrides
from ravn.config import ProjectConfig

# ---------------------------------------------------------------------------
# _merge_project_overrides
# ---------------------------------------------------------------------------


class TestMergeProjectOverrides:
    def _make_persona(self, **overrides) -> PersonaConfig:
        defaults = dict(
            name="base",
            system_prompt_template="base prompt",
            allowed_tools=["file"],
            forbidden_tools=["cascade"],
            permission_mode="workspace-write",
            llm=PersonaLLMConfig(primary_alias="balanced", thinking_enabled=True),
            iteration_budget=40,
        )
        defaults.update(overrides)
        return PersonaConfig(**defaults)

    def _make_project(self, **overrides) -> ProjectConfig:
        defaults = dict(
            project_name="proj",
            persona="",
            allowed_tools=[],
            forbidden_tools=[],
            permission_mode="",
            iteration_budget=0,
            notes="",
        )
        defaults.update(overrides)
        return ProjectConfig(**defaults)

    def test_empty_project_config_returns_persona_unchanged(self) -> None:
        persona = self._make_persona()
        project = self._make_project()
        merged = _merge_project_overrides(persona, project)
        assert merged.allowed_tools == ["file"]
        assert merged.forbidden_tools == ["cascade"]
        assert merged.permission_mode == "workspace-write"
        assert merged.iteration_budget == 40

    def test_project_allowed_tools_override(self) -> None:
        persona = self._make_persona(allowed_tools=["file"])
        project = self._make_project(allowed_tools=["web", "git"])
        merged = _merge_project_overrides(persona, project)
        assert merged.allowed_tools == ["web", "git"]

    def test_project_forbidden_tools_override(self) -> None:
        persona = self._make_persona(forbidden_tools=["cascade"])
        project = self._make_project(forbidden_tools=["volundr", "cascade"])
        merged = _merge_project_overrides(persona, project)
        assert merged.forbidden_tools == ["volundr", "cascade"]

    def test_project_permission_mode_override(self) -> None:
        persona = self._make_persona(permission_mode="workspace-write")
        project = self._make_project(permission_mode="read-only")
        merged = _merge_project_overrides(persona, project)
        assert merged.permission_mode == "read-only"
        assert merged.parsed_permission_mode is PermissionMode.READ_ONLY

    def test_project_iteration_budget_override(self) -> None:
        persona = self._make_persona(iteration_budget=40)
        project = self._make_project(iteration_budget=10)
        merged = _merge_project_overrides(persona, project)
        assert merged.iteration_budget == 10

    def test_llm_config_preserved_from_persona(self) -> None:
        persona = self._make_persona()
        project = self._make_project(permission_mode="read-only")
        merged = _merge_project_overrides(persona, project)
        assert merged.llm.primary_alias == "balanced"
        assert merged.llm.thinking_enabled is True

    def test_system_prompt_preserved_from_persona(self) -> None:
        persona = self._make_persona(system_prompt_template="special prompt")
        project = self._make_project()
        merged = _merge_project_overrides(persona, project)
        assert merged.system_prompt_template == "special prompt"

    def test_name_preserved_from_persona(self) -> None:
        persona = self._make_persona(name="original")
        project = self._make_project()
        merged = _merge_project_overrides(persona, project)
        assert merged.name == "original"

    def test_project_zero_budget_keeps_persona_budget(self) -> None:
        persona = self._make_persona(iteration_budget=40)
        project = self._make_project(iteration_budget=0)
        merged = _merge_project_overrides(persona, project)
        assert merged.iteration_budget == 40

    def test_project_empty_tools_keep_persona_tools(self) -> None:
        persona = self._make_persona(allowed_tools=["file", "git"])
        project = self._make_project(allowed_tools=[])
        merged = _merge_project_overrides(persona, project)
        assert merged.allowed_tools == ["file", "git"]

    def test_returns_new_instance_not_mutating_original(self) -> None:
        persona = self._make_persona()
        project = self._make_project(permission_mode="read-only")
        merged = _merge_project_overrides(persona, project)
        assert merged is not persona
        assert persona.permission_mode == "workspace-write"


# ---------------------------------------------------------------------------
# CLI integration — _resolve_persona
# ---------------------------------------------------------------------------


class TestResolvePersona:
    def test_resolve_builtin_by_name(self) -> None:
        from ravn.cli.commands import _resolve_persona

        cfg = _resolve_persona("coder", None)
        assert cfg is not None
        assert cfg.name == "coder"

    def test_resolve_falls_back_to_project_config_persona(self) -> None:
        from ravn.cli.commands import _resolve_persona

        project = ProjectConfig(project_name="p", persona="security-auditor")
        cfg = _resolve_persona("", project)
        assert cfg is not None
        assert cfg.name == "security-auditor"

    def test_cli_flag_takes_precedence_over_project_config(self) -> None:
        from ravn.cli.commands import _resolve_persona

        project = ProjectConfig(project_name="p", persona="security-auditor")
        cfg = _resolve_persona("reviewer", project)
        assert cfg is not None
        assert cfg.name == "reviewer"

    def test_no_persona_returns_none(self) -> None:
        from ravn.cli.commands import _resolve_persona

        assert _resolve_persona("", None) is None

    def test_unknown_persona_name_returns_none_with_warning(self, capsys) -> None:
        from ravn.cli.commands import _resolve_persona

        result = _resolve_persona("does-not-exist", None)
        assert result is None

    def test_project_overrides_applied_in_resolved_persona(self) -> None:
        from ravn.cli.commands import _resolve_persona

        project = ProjectConfig(
            project_name="p",
            persona="",
            allowed_tools=["web"],
            forbidden_tools=[],
            permission_mode="read-only",
            iteration_budget=5,
        )
        cfg = _resolve_persona("coder", project)
        assert cfg is not None
        assert cfg.allowed_tools == ["web"]
        assert cfg.permission_mode == "read-only"
        assert cfg.iteration_budget == 5


# ---------------------------------------------------------------------------
# CLI _build_agent persona integration
# ---------------------------------------------------------------------------


class TestBuildAgentWithPersona:
    def test_persona_system_prompt_applied(self) -> None:
        from unittest.mock import MagicMock

        from ravn.cli.commands import _build_agent
        from ravn.config import Settings

        persona = PersonaConfig(
            name="test",
            system_prompt_template="custom system prompt",
            iteration_budget=15,
        )

        settings = Settings()
        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_cls.return_value = MagicMock()
            agent, _ = _build_agent(settings, persona_config=persona)

        assert agent._system_prompt == "custom system prompt"
        assert agent.max_iterations == 15

    def test_no_persona_uses_settings_defaults(self) -> None:
        from unittest.mock import MagicMock

        from ravn.cli.commands import _build_agent
        from ravn.config import Settings

        settings = Settings()
        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_cls.return_value = MagicMock()
            agent, _ = _build_agent(settings, persona_config=None)

        assert agent._system_prompt == settings.agent.system_prompt
        assert agent.max_iterations == settings.agent.max_iterations

    def test_persona_empty_system_prompt_keeps_settings_prompt_and_zero_budget(self) -> None:
        from unittest.mock import MagicMock

        from ravn.cli.commands import _build_agent
        from ravn.config import Settings

        persona = PersonaConfig(name="empty", system_prompt_template="", iteration_budget=0)
        settings = Settings()

        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_cls.return_value = MagicMock()
            agent, _ = _build_agent(settings, persona_config=persona)

        assert agent._system_prompt == settings.agent.system_prompt
        assert agent.max_iterations == 0

    def test_read_only_persona_uses_deny_all_permission(self) -> None:
        from unittest.mock import MagicMock, patch

        from ravn.adapters.permission.enforcer import PermissionEnforcer
        from ravn.cli.commands import _build_agent
        from ravn.config import Settings

        persona = PersonaConfig(name="research", permission_mode="read-only")
        settings = Settings()

        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_cls.return_value = MagicMock()
            agent, _ = _build_agent(settings, persona_config=persona)

        assert isinstance(agent._permission, PermissionEnforcer)

    def test_non_read_only_persona_uses_permission_enforcer(self) -> None:
        from unittest.mock import MagicMock, patch

        from ravn.adapters.permission.enforcer import PermissionEnforcer
        from ravn.cli.commands import _build_agent
        from ravn.config import Settings

        persona = PersonaConfig(name="coder", permission_mode="workspace-write")
        settings = Settings()

        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_cls.return_value = MagicMock()
            agent, _ = _build_agent(settings, persona_config=persona)

        assert isinstance(agent._permission, PermissionEnforcer)

    def test_no_tools_flag_overrides_persona_permission(self) -> None:
        from unittest.mock import MagicMock, patch

        from ravn.adapters.permission.allow_deny import DenyAllPermission
        from ravn.cli.commands import _build_agent
        from ravn.config import Settings

        persona = PersonaConfig(name="full", permission_mode="full-access")
        settings = Settings()

        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_cls.return_value = MagicMock()
            agent, _ = _build_agent(settings, no_tools=True, persona_config=persona)

        assert isinstance(agent._permission, DenyAllPermission)


# ---------------------------------------------------------------------------
# CLI --persona flag end-to-end (via CliRunner)
# ---------------------------------------------------------------------------


class TestCliPersonaFlag:
    def test_persona_flag_shown_in_help(self) -> None:
        import re

        from typer.testing import CliRunner

        from ravn.cli.commands import app

        runner = CliRunner()
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        # Strip ANSI escape codes before checking — typer may colorize output.
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        assert "--persona" in plain

    def test_persona_flag_accepted(self) -> None:
        from unittest.mock import AsyncMock

        from typer.testing import CliRunner

        from ravn.cli.commands import app

        runner = CliRunner()
        mock_run = AsyncMock(return_value=None)
        with patch("ravn.cli.commands._run_with_signals", mock_run):
            result = runner.invoke(app, ["run", "--persona", "coder", "hello"])

        assert result.exit_code == 0
        assert mock_run.await_count == 1
        persona = mock_run.await_args.kwargs["persona_config"]
        assert persona is not None
        assert persona.name == "coder"


# ---------------------------------------------------------------------------
# _merge_project_overrides — contract fields preserved
# ---------------------------------------------------------------------------


class TestMergeProjectOverridesWithContracts:
    def _make_project(self):  # type: ignore[return]
        from ravn.config import ProjectConfig

        return ProjectConfig(
            project_name="proj",
            persona="",
            allowed_tools=[],
            forbidden_tools=[],
            permission_mode="",
            iteration_budget=0,
            notes="",
        )

    def test_merge_preserves_produces(self) -> None:
        produces = PersonaProduces(
            event_type="x.done",
            schema={"v": OutcomeField(type="string", description="v")},
        )
        persona = PersonaConfig(name="x", produces=produces)
        merged = _merge_project_overrides(persona, self._make_project())
        assert merged.produces.event_type == "x.done"
        assert "v" in merged.produces.schema

    def test_merge_preserves_consumes(self) -> None:
        consumes = PersonaConsumes(event_types=["a.b"], injects=["x"])
        persona = PersonaConfig(name="x", consumes=consumes)
        merged = _merge_project_overrides(persona, self._make_project())
        assert merged.consumes.event_types == ["a.b"]
        assert merged.consumes.injects == ["x"]

    def test_merge_preserves_fan_in(self) -> None:
        fan_in = PersonaFanIn(strategy="all_must_pass", contributes_to="verdict")
        persona = PersonaConfig(name="x", fan_in=fan_in)
        merged = _merge_project_overrides(persona, self._make_project())
        assert merged.fan_in.strategy == "all_must_pass"
        assert merged.fan_in.contributes_to == "verdict"


def test_merge_keeps_the_persona_voice() -> None:
    persona = PersonaConfig(
        name="speaker",
        voice=PersonaVoiceConfig(voices={"grok-voice": "ara"}, tools=["web_search"]),
    )

    merged = _merge_project_overrides(persona, ProjectConfig())

    assert merged.voice == persona.voice

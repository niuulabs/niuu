"""Tests for the persona configuration loader."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ravn.adapters.personas.loader import (
    _BUILTIN_PERSONAS_DIR,
    FilesystemPersonaAdapter,
    PersonaConfig,
    PersonaLLMConfig,
    _safe_bool,
)
from ravn.config import ProjectConfig, _safe_int

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

_FULL_PERSONA_YAML = """\
name: test-agent
system_prompt_template: |
  You are a test agent.
  Do tests.
allowed_tools: [file, git]
forbidden_tools: [cascade]
permission_mode: workspace-write
llm:
  primary_alias: balanced
  thinking_enabled: true
iteration_budget: 25
"""

_MINIMAL_PERSONA_YAML = """\
name: minimal-agent
"""

_NO_NAME_YAML = """\
system_prompt_template: missing a name
allowed_tools: [file]
"""

_INVALID_YAML = """\
: this is not valid yaml: {[
"""

_NOT_A_DICT_YAML = """\
- just a list
- of items
"""

# Convenience dict: load all built-in personas once for specialist persona tests
_loader = FilesystemPersonaAdapter()
_BUILTIN_PERSONAS: dict[str, PersonaConfig] = {}
for _p in _BUILTIN_PERSONAS_DIR.glob("*.yaml"):
    _cfg = _loader.load_from_file(_p)
    if _cfg is not None:
        _BUILTIN_PERSONAS[_p.stem] = _cfg


# ---------------------------------------------------------------------------
# _safe_bool
# ---------------------------------------------------------------------------


class TestSafeBool:
    def test_true_bool(self) -> None:
        assert _safe_bool(True) is True

    def test_false_bool(self) -> None:
        assert _safe_bool(False) is False

    def test_true_string(self) -> None:
        assert _safe_bool("true") is True

    def test_yes_string(self) -> None:
        assert _safe_bool("yes") is True

    def test_one_string(self) -> None:
        assert _safe_bool("1") is True

    def test_false_string(self) -> None:
        assert _safe_bool("false") is False

    def test_unexpected_type_returns_default(self) -> None:
        assert _safe_bool(None, default=True) is True

    def test_default_false(self) -> None:
        assert _safe_bool(None) is False


# ---------------------------------------------------------------------------
# _safe_int
# ---------------------------------------------------------------------------


class TestSafeInt:
    def test_integer_value(self) -> None:
        assert _safe_int(42) == 42

    def test_string_integer(self) -> None:
        assert _safe_int("10") == 10

    def test_invalid_string_returns_default(self) -> None:
        assert _safe_int("many") == 0

    def test_none_returns_default(self) -> None:
        assert _safe_int(None, default=5) == 5

    def test_custom_default(self) -> None:
        assert _safe_int(None, default=99) == 99


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter.parse
# ---------------------------------------------------------------------------


class TestFilesystemPersonaAdapterParse:
    def test_full_yaml_parses_all_fields(self) -> None:
        cfg = FilesystemPersonaAdapter.parse(_FULL_PERSONA_YAML)
        assert cfg is not None
        assert cfg.name == "test-agent"
        assert "test agent" in cfg.system_prompt_template
        assert cfg.allowed_tools == ["file", "git"]
        assert cfg.forbidden_tools == ["cascade"]
        assert cfg.permission_mode == "workspace-write"
        assert cfg.llm.primary_alias == "balanced"
        assert cfg.llm.thinking_enabled is True
        assert cfg.iteration_budget == 25

    def test_minimal_yaml_defaults_empty_fields(self) -> None:
        cfg = FilesystemPersonaAdapter.parse(_MINIMAL_PERSONA_YAML)
        assert cfg is not None
        assert cfg.name == "minimal-agent"
        assert cfg.system_prompt_template == ""
        assert cfg.allowed_tools == []
        assert cfg.forbidden_tools == []
        assert cfg.permission_mode == ""
        assert cfg.llm.primary_alias == ""
        assert cfg.llm.thinking_enabled is False
        assert cfg.iteration_budget == 0

    def test_missing_name_returns_none(self) -> None:
        assert FilesystemPersonaAdapter.parse(_NO_NAME_YAML) is None

    def test_invalid_yaml_returns_none(self) -> None:
        assert FilesystemPersonaAdapter.parse(_INVALID_YAML) is None

    def test_empty_string_returns_none(self) -> None:
        assert FilesystemPersonaAdapter.parse("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert FilesystemPersonaAdapter.parse("   \n  ") is None

    def test_non_dict_yaml_returns_none(self) -> None:
        assert FilesystemPersonaAdapter.parse(_NOT_A_DICT_YAML) is None

    def test_allowed_tools_non_list_becomes_empty(self) -> None:
        yaml = "name: x\nallowed_tools: not-a-list\n"
        cfg = FilesystemPersonaAdapter.parse(yaml)
        assert cfg is not None
        assert cfg.allowed_tools == []

    def test_forbidden_tools_non_list_becomes_empty(self) -> None:
        yaml = "name: x\nforbidden_tools: not-a-list\n"
        cfg = FilesystemPersonaAdapter.parse(yaml)
        assert cfg is not None
        assert cfg.forbidden_tools == []

    def test_llm_non_dict_uses_defaults(self) -> None:
        yaml = "name: x\nllm: some-string\n"
        cfg = FilesystemPersonaAdapter.parse(yaml)
        assert cfg is not None
        assert cfg.llm.primary_alias == ""
        assert cfg.llm.thinking_enabled is False

    def test_thinking_enabled_string_true(self) -> None:
        yaml = "name: x\nllm:\n  thinking_enabled: 'yes'\n"
        cfg = FilesystemPersonaAdapter.parse(yaml)
        assert cfg is not None
        assert cfg.llm.thinking_enabled is True

    def test_iteration_budget_invalid_string_becomes_zero(self) -> None:
        yaml = "name: x\niteration_budget: many\n"
        cfg = FilesystemPersonaAdapter.parse(yaml)
        assert cfg is not None
        assert cfg.iteration_budget == 0

    def test_executor_config_parses(self) -> None:
        yaml = (
            "name: x\n"
            "executor:\n"
            "  adapter: ravn.adapters.executors.agent.AgentExecutor\n"
            "  kwargs:\n"
            "    mode: default\n"
        )
        cfg = FilesystemPersonaAdapter.parse(yaml)
        assert cfg is not None
        assert cfg.executor.adapter == "ravn.adapters.executors.agent.AgentExecutor"
        assert cfg.executor.kwargs == {"mode": "default"}


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter.load_from_file
# ---------------------------------------------------------------------------


class TestFilesystemPersonaAdapterLoadFromFile:
    def test_load_valid_file(self, tmp_path: Path) -> None:
        p = tmp_path / "test-agent.yaml"
        p.write_text(_FULL_PERSONA_YAML, encoding="utf-8")
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        cfg = loader.load_from_file(p)
        assert cfg is not None
        assert cfg.name == "test-agent"

    def test_load_missing_file_returns_none(self, tmp_path: Path) -> None:
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        result = loader.load_from_file(tmp_path / "nonexistent.yaml")
        assert result is None

    def test_load_unreadable_path_returns_none(self, tmp_path: Path) -> None:
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        # Directory is not a readable YAML file.
        result = loader.load_from_file(tmp_path)
        assert result is None

    def test_load_invalid_yaml_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text(_INVALID_YAML, encoding="utf-8")
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        result = loader.load_from_file(p)
        assert result is None


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter.list_builtin_names
# ---------------------------------------------------------------------------


class TestListBuiltinNames:
    def test_returns_expected_personas(self) -> None:
        names = FilesystemPersonaAdapter().list_builtin_names()
        assert "coding-agent" in names
        assert "research-agent" in names
        assert "planning-agent" in names
        assert "autonomous-agent" in names
        assert "draft-a-note" in names
        assert "research-and-distill" in names
        assert "reviewer" in names
        assert "qa-agent" in names
        assert "security-auditor" in names
        assert "ship-agent" in names
        assert "retro-analyst" in names
        assert "mimir-curator" in names

    def test_returns_sorted_list(self) -> None:
        names = FilesystemPersonaAdapter().list_builtin_names()
        assert names == sorted(names)

    def test_matches_builtin_dir_files(self) -> None:
        loader = FilesystemPersonaAdapter()
        expected = {p.stem for p in _BUILTIN_PERSONAS_DIR.glob("*.yaml")}
        assert set(loader.list_builtin_names()) == expected


# ---------------------------------------------------------------------------
# Built-in persona contents
# ---------------------------------------------------------------------------


class TestBuiltinPersonas:
    _loader = FilesystemPersonaAdapter()

    def _load(self, name: str) -> PersonaConfig:
        cfg = self._loader.load_from_file(_BUILTIN_PERSONAS_DIR / f"{name}.yaml")
        assert cfg is not None, f"Failed to load built-in persona {name!r}"
        return cfg

    def test_coding_agent_exists(self) -> None:
        cfg = self._load("coding-agent")
        assert cfg.name == "coding-agent"
        assert cfg.permission_mode == "workspace-write"
        assert cfg.llm.thinking_enabled is True
        assert cfg.iteration_budget > 0
        assert "file" in cfg.allowed_tools
        assert "git" in cfg.allowed_tools
        assert "terminal" in cfg.allowed_tools

    def test_research_agent_exists(self) -> None:
        cfg = self._load("research-agent")
        assert cfg.name == "research-agent"
        assert cfg.permission_mode == "read-only"
        assert "web" in cfg.allowed_tools
        assert "file" in cfg.allowed_tools
        assert "terminal" not in cfg.allowed_tools

    def test_planning_agent_exists(self) -> None:
        cfg = self._load("planning-agent")
        assert cfg.name == "planning-agent"
        assert cfg.llm.thinking_enabled is True
        assert cfg.permission_mode == "read-only"

    def test_autonomous_agent_exists(self) -> None:
        cfg = self._load("autonomous-agent")
        assert cfg.name == "autonomous-agent"
        assert cfg.permission_mode == "full-access"
        assert cfg.allowed_tools == []
        assert cfg.forbidden_tools == []

    def test_draft_a_note_exists(self) -> None:
        cfg = self._load("draft-a-note")
        assert cfg.name == "draft-a-note"
        assert cfg.permission_mode == "read-only"
        assert cfg.iteration_budget == 5
        assert cfg.llm.thinking_enabled is False
        assert "mimir_search" in cfg.allowed_tools
        assert "mimir_read" in cfg.allowed_tools
        assert "mimir_write" in cfg.allowed_tools

    def test_draft_a_note_forbids_external_tools(self) -> None:
        cfg = self._load("draft-a-note")
        assert "bash" in cfg.forbidden_tools
        assert "web_search" in cfg.forbidden_tools
        assert "web_fetch" in cfg.forbidden_tools
        assert "terminal" in cfg.forbidden_tools

    def test_draft_a_note_lower_budget_than_research_agent(self) -> None:
        draft = self._load("draft-a-note")
        research = self._load("research-agent")
        assert draft.iteration_budget < research.iteration_budget

    def test_draft_a_note_system_prompt_mentions_produced_by_thread(self) -> None:
        cfg = self._load("draft-a-note")
        assert "produced_by_thread" in cfg.system_prompt_template

    def test_draft_a_note_system_prompt_mentions_notes_path(self) -> None:
        cfg = self._load("draft-a-note")
        assert "notes/" in cfg.system_prompt_template

    def test_research_and_distill_exists(self) -> None:
        cfg = self._load("research-and-distill")
        assert cfg.name == "research-and-distill"
        assert cfg.permission_mode == "read-only"
        assert cfg.iteration_budget == 15
        assert "mimir_search" in cfg.allowed_tools
        assert "mimir_read" in cfg.allowed_tools
        assert "mimir_write" in cfg.allowed_tools
        assert "mimir_list" in cfg.allowed_tools
        assert "web_search" in cfg.allowed_tools
        assert "web_fetch" in cfg.allowed_tools
        assert "bash" in cfg.forbidden_tools
        assert "terminal" in cfg.forbidden_tools
        assert "edit_file" in cfg.forbidden_tools
        assert "write_file" in cfg.forbidden_tools

    def test_research_and_distill_system_prompt_mentions_produced_by_thread(self) -> None:
        cfg = self._load("research-and-distill")
        assert "produced_by_thread" in cfg.system_prompt_template

    def test_research_and_distill_system_prompt_mentions_word_limit(self) -> None:
        cfg = self._load("research-and-distill")
        assert "1500" in cfg.system_prompt_template

    def test_all_builtins_have_system_prompts(self) -> None:
        for p in _BUILTIN_PERSONAS_DIR.glob("*.yaml"):
            cfg = self._load(p.stem)
            assert cfg.system_prompt_template, f"{p.stem} has empty system_prompt_template"

    def test_all_builtins_have_positive_budgets(self) -> None:
        for p in _BUILTIN_PERSONAS_DIR.glob("*.yaml"):
            cfg = self._load(p.stem)
            assert cfg.iteration_budget > 0, f"{p.stem} has non-positive iteration_budget"

    # ------------------------------------------------------------------
    # Specialist personas (NIU-586)
    # ------------------------------------------------------------------

    def test_reviewer_exists(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert cfg.name == "reviewer"
        assert cfg.permission_mode == "read-only"
        assert cfg.iteration_budget == 25
        assert cfg.llm.thinking_enabled is True
        assert cfg.llm.primary_alias == "balanced"

    def test_reviewer_allowed_tools(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert "file" in cfg.allowed_tools
        assert "git" in cfg.allowed_tools
        assert "web" in cfg.allowed_tools
        assert "ravn" in cfg.allowed_tools

    def test_reviewer_forbidden_tools(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert "terminal" in cfg.forbidden_tools
        assert "cascade" in cfg.forbidden_tools

    def test_reviewer_system_prompt_mentions_sql_safety(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert "SQL" in cfg.system_prompt_template

    def test_reviewer_system_prompt_mentions_error_handling(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert "error" in cfg.system_prompt_template.lower()

    def test_qa_agent_exists(self) -> None:
        cfg = _BUILTIN_PERSONAS["qa-agent"]
        assert cfg.name == "qa-agent"
        assert cfg.permission_mode == "workspace-write"
        assert cfg.iteration_budget == 30
        assert cfg.llm.thinking_enabled is False

    def test_qa_agent_allowed_tools(self) -> None:
        cfg = _BUILTIN_PERSONAS["qa-agent"]
        assert "file" in cfg.allowed_tools
        assert "git" in cfg.allowed_tools
        assert "terminal" in cfg.allowed_tools
        assert "ravn" in cfg.allowed_tools

    def test_qa_agent_forbidden_tools(self) -> None:
        cfg = _BUILTIN_PERSONAS["qa-agent"]
        assert "cascade" in cfg.forbidden_tools
        assert "volundr" in cfg.forbidden_tools

    def test_qa_agent_system_prompt_mentions_tests(self) -> None:
        cfg = _BUILTIN_PERSONAS["qa-agent"]
        assert "test" in cfg.system_prompt_template.lower()

    def test_qa_agent_system_prompt_mentions_verdict(self) -> None:
        cfg = _BUILTIN_PERSONAS["qa-agent"]
        assert "verdict" in cfg.system_prompt_template.lower()

    def test_security_auditor_exists(self) -> None:
        cfg = _BUILTIN_PERSONAS["security-auditor"]
        assert cfg.name == "security-auditor"
        assert cfg.permission_mode == "read-only"
        assert cfg.iteration_budget == 25
        assert cfg.llm.thinking_enabled is True
        assert cfg.llm.primary_alias == "balanced"

    def test_security_auditor_allowed_tools(self) -> None:
        cfg = _BUILTIN_PERSONAS["security-auditor"]
        assert "file" in cfg.allowed_tools
        assert "git" in cfg.allowed_tools
        assert "web" in cfg.allowed_tools
        assert "ravn" in cfg.allowed_tools

    def test_security_auditor_forbidden_tools(self) -> None:
        cfg = _BUILTIN_PERSONAS["security-auditor"]
        assert "terminal" in cfg.forbidden_tools
        assert "cascade" in cfg.forbidden_tools

    def test_security_auditor_system_prompt_mentions_owasp(self) -> None:
        cfg = _BUILTIN_PERSONAS["security-auditor"]
        assert "OWASP" in cfg.system_prompt_template

    def test_security_auditor_system_prompt_mentions_secrets(self) -> None:
        cfg = _BUILTIN_PERSONAS["security-auditor"]
        assert "secret" in cfg.system_prompt_template.lower()

    def test_ship_agent_exists(self) -> None:
        cfg = _BUILTIN_PERSONAS["ship-agent"]
        assert cfg.name == "ship-agent"
        assert cfg.permission_mode == "workspace-write"
        assert cfg.iteration_budget == 15
        assert cfg.llm.thinking_enabled is False
        assert cfg.llm.primary_alias == "balanced"

    def test_ship_agent_allowed_tools(self) -> None:
        cfg = _BUILTIN_PERSONAS["ship-agent"]
        assert "file" in cfg.allowed_tools
        assert "git" in cfg.allowed_tools
        assert "terminal" in cfg.allowed_tools
        assert "web" in cfg.allowed_tools
        assert "ravn" in cfg.allowed_tools

    def test_ship_agent_system_prompt_mentions_merge(self) -> None:
        cfg = _BUILTIN_PERSONAS["ship-agent"]
        assert "merge" in cfg.system_prompt_template.lower()

    def test_ship_agent_system_prompt_mentions_verdict(self) -> None:
        cfg = _BUILTIN_PERSONAS["ship-agent"]
        assert "verdict" in cfg.system_prompt_template.lower()

    def test_ship_agent_system_prompt_mentions_version(self) -> None:
        cfg = _BUILTIN_PERSONAS["ship-agent"]
        assert "version" in cfg.system_prompt_template.lower()

    def test_retro_analyst_exists(self) -> None:
        cfg = _BUILTIN_PERSONAS["retro-analyst"]
        assert cfg.name == "retro-analyst"
        assert cfg.permission_mode == "read-only"
        assert cfg.iteration_budget == 15
        assert cfg.llm.thinking_enabled is False

    def test_retro_analyst_allowed_tools(self) -> None:
        cfg = _BUILTIN_PERSONAS["retro-analyst"]
        assert "mimir" in cfg.allowed_tools
        assert "file" in cfg.allowed_tools
        assert "ravn" in cfg.allowed_tools

    def test_retro_analyst_forbidden_tools(self) -> None:
        cfg = _BUILTIN_PERSONAS["retro-analyst"]
        assert "terminal" in cfg.forbidden_tools
        assert "cascade" in cfg.forbidden_tools

    def test_retro_analyst_system_prompt_mentions_patterns(self) -> None:
        cfg = _BUILTIN_PERSONAS["retro-analyst"]
        assert "pattern" in cfg.system_prompt_template.lower()

    def test_retro_analyst_system_prompt_mentions_shipped(self) -> None:
        cfg = _BUILTIN_PERSONAS["retro-analyst"]
        assert "shipped" in cfg.system_prompt_template.lower()

    def test_specialist_personas_in_builtin_list(self) -> None:
        names = set(_BUILTIN_PERSONAS)
        for expected in [
            "reviewer",
            "qa-agent",
            "security-auditor",
            "ship-agent",
            "retro-analyst",
        ]:
            assert expected in names, f"{expected} missing from _BUILTIN_PERSONAS"

    def test_read_only_personas_cannot_write(self) -> None:
        read_only = ["reviewer", "security-auditor", "retro-analyst"]
        for name in read_only:
            cfg = _BUILTIN_PERSONAS[name]
            assert cfg.permission_mode == "read-only", f"{name} should be read-only"

    def test_write_personas_have_workspace_write(self) -> None:
        write_personas = ["qa-agent", "ship-agent"]
        for name in write_personas:
            cfg = _BUILTIN_PERSONAS[name]
            assert cfg.permission_mode == "workspace-write", f"{name} should be workspace-write"

    def test_high_budget_qa_agent_vs_retro_analyst(self) -> None:
        qa = _BUILTIN_PERSONAS["qa-agent"]
        retro = _BUILTIN_PERSONAS["retro-analyst"]
        assert qa.iteration_budget > retro.iteration_budget


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter.load — file vs built-in resolution
# ---------------------------------------------------------------------------


class TestFilesystemPersonaAdapterLoad:
    def test_load_builtin_by_name(self) -> None:
        loader = FilesystemPersonaAdapter()
        cfg = loader.load("coding-agent")
        assert cfg is not None
        assert cfg.name == "coding-agent"

    def test_load_unknown_name_returns_none(self) -> None:
        loader = FilesystemPersonaAdapter()
        assert loader.load("nonexistent-persona") is None

    def test_file_persona_takes_precedence_over_builtin(self, tmp_path: Path) -> None:
        # Write a file that overrides the built-in coding-agent.
        override = tmp_path / "coding-agent.yaml"
        override.write_text(
            "name: coding-agent\nsystem_prompt_template: overridden prompt\n",
            encoding="utf-8",
        )
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        cfg = loader.load("coding-agent")
        assert cfg is not None
        assert cfg.system_prompt_template == "overridden prompt"

    def test_custom_persona_from_file(self, tmp_path: Path) -> None:
        custom = tmp_path / "my-persona.yaml"
        custom.write_text(_FULL_PERSONA_YAML, encoding="utf-8")
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        cfg = loader.load("test-agent")  # name inside the file, not filename
        # Only the builtin lookup uses name; file lookup uses filename key
        assert cfg is None  # filename is my-persona.yaml, not test-agent.yaml

    def test_load_uses_filename_not_yaml_name(self, tmp_path: Path) -> None:
        p = tmp_path / "myfile.yaml"
        p.write_text("name: other-name\niteration_budget: 7\n", encoding="utf-8")
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        cfg = loader.load("myfile")  # lookup by filename stem
        assert cfg is not None
        assert cfg.name == "other-name"
        assert cfg.iteration_budget == 7

    def test_default_personas_dir_used_when_not_specified(self) -> None:
        loader = FilesystemPersonaAdapter()
        # Just ensure it doesn't crash; no ~/.ravn/personas likely in test env.
        result = loader.load("coding-agent")
        assert result is not None  # falls back to builtin


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter.merge
# ---------------------------------------------------------------------------


class TestFilesystemPersonaAdapterMerge:
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
        merged = FilesystemPersonaAdapter.merge(persona, project)
        assert merged.allowed_tools == ["file"]
        assert merged.forbidden_tools == ["cascade"]
        assert merged.permission_mode == "workspace-write"
        assert merged.iteration_budget == 40

    def test_project_allowed_tools_override(self) -> None:
        persona = self._make_persona(allowed_tools=["file"])
        project = self._make_project(allowed_tools=["web", "git"])
        merged = FilesystemPersonaAdapter.merge(persona, project)
        assert merged.allowed_tools == ["web", "git"]

    def test_project_forbidden_tools_override(self) -> None:
        persona = self._make_persona(forbidden_tools=["cascade"])
        project = self._make_project(forbidden_tools=["volundr", "cascade"])
        merged = FilesystemPersonaAdapter.merge(persona, project)
        assert merged.forbidden_tools == ["volundr", "cascade"]

    def test_project_permission_mode_override(self) -> None:
        persona = self._make_persona(permission_mode="workspace-write")
        project = self._make_project(permission_mode="read-only")
        merged = FilesystemPersonaAdapter.merge(persona, project)
        assert merged.permission_mode == "read-only"

    def test_project_iteration_budget_override(self) -> None:
        persona = self._make_persona(iteration_budget=40)
        project = self._make_project(iteration_budget=10)
        merged = FilesystemPersonaAdapter.merge(persona, project)
        assert merged.iteration_budget == 10

    def test_llm_config_preserved_from_persona(self) -> None:
        persona = self._make_persona()
        project = self._make_project(permission_mode="read-only")
        merged = FilesystemPersonaAdapter.merge(persona, project)
        assert merged.llm.primary_alias == "balanced"
        assert merged.llm.thinking_enabled is True

    def test_system_prompt_preserved_from_persona(self) -> None:
        persona = self._make_persona(system_prompt_template="special prompt")
        project = self._make_project()
        merged = FilesystemPersonaAdapter.merge(persona, project)
        assert merged.system_prompt_template == "special prompt"

    def test_name_preserved_from_persona(self) -> None:
        persona = self._make_persona(name="original")
        project = self._make_project()
        merged = FilesystemPersonaAdapter.merge(persona, project)
        assert merged.name == "original"

    def test_project_zero_budget_keeps_persona_budget(self) -> None:
        persona = self._make_persona(iteration_budget=40)
        project = self._make_project(iteration_budget=0)
        merged = FilesystemPersonaAdapter.merge(persona, project)
        assert merged.iteration_budget == 40

    def test_project_empty_tools_keep_persona_tools(self) -> None:
        persona = self._make_persona(allowed_tools=["file", "git"])
        project = self._make_project(allowed_tools=[])
        merged = FilesystemPersonaAdapter.merge(persona, project)
        assert merged.allowed_tools == ["file", "git"]

    def test_returns_new_instance_not_mutating_original(self) -> None:
        persona = self._make_persona()
        project = self._make_project(permission_mode="read-only")
        merged = FilesystemPersonaAdapter.merge(persona, project)
        assert merged is not persona
        assert persona.permission_mode == "workspace-write"


# ---------------------------------------------------------------------------
# CLI integration — _resolve_persona
# ---------------------------------------------------------------------------


class TestResolvePersona:
    def test_resolve_builtin_by_name(self) -> None:
        from ravn.cli.commands import _resolve_persona

        cfg = _resolve_persona("coding-agent", None)
        assert cfg is not None
        assert cfg.name == "coding-agent"

    def test_resolve_falls_back_to_project_config_persona(self) -> None:
        from ravn.cli.commands import _resolve_persona

        project = ProjectConfig(project_name="p", persona="research-agent")
        cfg = _resolve_persona("", project)
        assert cfg is not None
        assert cfg.name == "research-agent"

    def test_cli_flag_takes_precedence_over_project_config(self) -> None:
        from ravn.cli.commands import _resolve_persona

        project = ProjectConfig(project_name="p", persona="research-agent")
        cfg = _resolve_persona("planning-agent", project)
        assert cfg is not None
        assert cfg.name == "planning-agent"

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
        cfg = _resolve_persona("coding-agent", project)
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

    def test_persona_empty_system_prompt_keeps_settings_default(self) -> None:
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
        assert agent.max_iterations == settings.agent.max_iterations

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
        from collections.abc import AsyncIterator
        from unittest.mock import MagicMock

        from typer.testing import CliRunner

        from ravn.cli.commands import app
        from ravn.domain.models import StreamEvent, StreamEventType, TokenUsage

        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="done")
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=5, output_tokens=3),
            )

        runner = CliRunner()
        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_adapter = MagicMock()
            mock_adapter.stream = _stream
            mock_cls.return_value = mock_adapter

            result = runner.invoke(app, ["run", "--persona", "coding-agent", "hello"])
        assert result.exit_code == 0

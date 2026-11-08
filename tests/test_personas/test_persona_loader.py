"""Tests for the persona configuration loader."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from personas.adapters.loader import (
    _BUILTIN_PERSONAS_DIR,
    FilesystemPersonaAdapter,
)
from personas.domain.permission_mode import PermissionMode
from personas.domain.persona import PersonaConfig, parse_persona, safe_bool, safe_int
from personas.domain.persona_document import PersonaDocumentError

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
# safe_bool
# ---------------------------------------------------------------------------


class TestSafeBool:
    def test_true_bool(self) -> None:
        assert safe_bool(True) is True

    def test_false_bool(self) -> None:
        assert safe_bool(False) is False

    def test_true_string(self) -> None:
        assert safe_bool("true") is True

    def test_yes_string(self) -> None:
        assert safe_bool("yes") is True

    def test_one_string(self) -> None:
        assert safe_bool("1") is True

    def test_false_string(self) -> None:
        assert safe_bool("false") is False

    def test_unexpected_type_returns_default(self) -> None:
        assert safe_bool(None, default=True) is True

    def test_default_false(self) -> None:
        assert safe_bool(None) is False


# ---------------------------------------------------------------------------
# safe_int
# ---------------------------------------------------------------------------


class TestSafeInt:
    def test_integer_value(self) -> None:
        assert safe_int(42) == 42

    def test_string_integer(self) -> None:
        assert safe_int("10") == 10

    def test_invalid_string_returns_default(self) -> None:
        assert safe_int("many") == 0

    def test_none_returns_default(self) -> None:
        assert safe_int(None, default=5) == 5

    def test_custom_default(self) -> None:
        assert safe_int(None, default=99) == 99


# ---------------------------------------------------------------------------
# parse_persona
# ---------------------------------------------------------------------------


class TestFilesystemPersonaAdapterParse:
    def test_full_yaml_parses_all_fields(self) -> None:
        cfg = parse_persona(_FULL_PERSONA_YAML)
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
        cfg = parse_persona(_MINIMAL_PERSONA_YAML)
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
        assert parse_persona(_NO_NAME_YAML) is None

    def test_invalid_yaml_returns_none(self) -> None:
        assert parse_persona(_INVALID_YAML) is None

    def test_empty_string_returns_none(self) -> None:
        assert parse_persona("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert parse_persona("   \n  ") is None

    def test_non_dict_yaml_returns_none(self) -> None:
        assert parse_persona(_NOT_A_DICT_YAML) is None

    def test_allowed_tools_non_list_becomes_empty(self) -> None:
        yaml = "name: x\nallowed_tools: not-a-list\n"
        cfg = parse_persona(yaml)
        assert cfg is not None
        assert cfg.allowed_tools == []

    def test_forbidden_tools_non_list_becomes_empty(self) -> None:
        yaml = "name: x\nforbidden_tools: not-a-list\n"
        cfg = parse_persona(yaml)
        assert cfg is not None
        assert cfg.forbidden_tools == []

    def test_llm_non_dict_uses_defaults(self) -> None:
        yaml = "name: x\nllm: some-string\n"
        cfg = parse_persona(yaml)
        assert cfg is not None
        assert cfg.llm.primary_alias == ""
        assert cfg.llm.thinking_enabled is False

    def test_thinking_enabled_string_true(self) -> None:
        yaml = "name: x\nllm:\n  thinking_enabled: 'yes'\n"
        cfg = parse_persona(yaml)
        assert cfg is not None
        assert cfg.llm.thinking_enabled is True

    def test_iteration_budget_invalid_string_becomes_zero(self) -> None:
        yaml = "name: x\niteration_budget: many\n"
        cfg = parse_persona(yaml)
        assert cfg is not None
        assert cfg.iteration_budget == 0

    def test_executor_block_is_refused_with_the_remedy(self) -> None:
        text = "name: x\nexecutor:\n  adapter: some.module.Executor\n  kwargs:\n    mode: default\n"
        with pytest.raises(ValueError, match="Persona 'x' declares an 'executor' block.*Remove"):
            parse_persona(text)

    @pytest.mark.parametrize("spelling", ["read-only", "read_only"])
    def test_permission_mode_is_parsed_at_load(self, spelling: str) -> None:
        cfg = parse_persona(f"name: x\npermission_mode: {spelling}\n")
        assert cfg is not None
        assert cfg.parsed_permission_mode is PermissionMode.READ_ONLY
        # The authored spelling is kept: persona content digests hash it.
        assert cfg.permission_mode == spelling
        assert cfg.to_dict()["permission_mode"] == spelling

    def test_unset_permission_mode_parses_to_none(self) -> None:
        cfg = parse_persona(_MINIMAL_PERSONA_YAML)
        assert cfg is not None
        assert cfg.parsed_permission_mode is None

    def test_null_permission_mode_is_unset(self) -> None:
        cfg = parse_persona("name: x\npermission_mode:\n")
        assert cfg is not None
        assert cfg.permission_mode == ""
        assert cfg.parsed_permission_mode is None

    def test_unknown_permission_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="Persona 'x': Unknown permission_mode 'superuser'"):
            parse_persona("name: x\npermission_mode: superuser\n")

    def test_non_string_permission_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown permission_mode '5'"):
            parse_persona("name: x\npermission_mode: 5\n")


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

    def test_unknown_permission_mode_raises_naming_the_file(self, tmp_path: Path) -> None:
        """Not a soft miss: skipping it would silently drop the persona's boundary."""
        p = tmp_path / "rogue.yaml"
        p.write_text("name: rogue\npermission_mode: read-onyl\n", encoding="utf-8")
        loader = FilesystemPersonaAdapter([str(tmp_path)], include_builtin=False)
        with pytest.raises(ValueError, match="rogue.yaml") as exc_info:
            loader.load("rogue")
        assert "read-onyl" in str(exc_info.value)
        assert "read-only" in str(exc_info.value)


# ---------------------------------------------------------------------------
# PersonaConfig permission mode
# ---------------------------------------------------------------------------


class TestPersonaConfigPermissionMode:
    def test_direct_construction_parses(self) -> None:
        cfg = PersonaConfig(name="x", permission_mode="full-access")
        assert cfg.parsed_permission_mode is PermissionMode.FULL_ACCESS

    def test_direct_construction_rejects_unknown(self) -> None:
        with pytest.raises(ValueError, match="Unknown permission_mode 'workspace-read'"):
            PersonaConfig(name="x", permission_mode="workspace-read")

    def test_replace_reparses(self) -> None:
        cfg = PersonaConfig(name="x", permission_mode="read-only")
        replaced = dataclasses.replace(cfg, permission_mode="workspace_write")
        assert replaced.parsed_permission_mode is PermissionMode.WORKSPACE_WRITE
        kept = dataclasses.replace(cfg, iteration_budget=3)
        assert kept.parsed_permission_mode is PermissionMode.READ_ONLY


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter.list_builtin_names
# ---------------------------------------------------------------------------


class TestExecutorBlockIsRefused:
    def test_a_persona_file_with_an_executor_names_the_file_and_the_remedy(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "bound.yaml"
        path.write_text("name: bound\nexecutor:\n  adapter: some.module.Executor\n")

        with pytest.raises(PersonaDocumentError, match=r"bound\.yaml: Persona 'bound'.*Remove"):
            FilesystemPersonaAdapter([str(tmp_path)]).load("bound")

    def test_no_bundled_persona_declares_an_executor(self) -> None:
        for path in _BUILTIN_PERSONAS_DIR.glob("*.yaml"):
            assert "executor" not in path.read_text(), path.name


class TestListBuiltinNames:
    def test_returns_expected_personas(self) -> None:
        names = FilesystemPersonaAdapter().list_builtin_names()
        assert "coder" in names
        assert "reviewer" in names
        assert "security-auditor" in names
        assert "postmortem-analyst" in names
        assert "sidekick" not in names


class TestTrackerDeliveryPersonas:
    def test_coder_uses_explicit_tracker_shim_path(self) -> None:
        cfg = _BUILTIN_PERSONAS["coder"]
        assert "./.skuld-tools/bin/tracker_issue" in cfg.system_prompt_template

    def test_reviewer_uses_explicit_tracker_shim_path(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert "./.skuld-tools/bin/tracker_issue" in cfg.system_prompt_template

    def test_closer_uses_explicit_tracker_shim_path(self) -> None:
        cfg = _BUILTIN_PERSONAS["closer"]
        assert "./.skuld-tools/bin/tracker_issue" in cfg.system_prompt_template

    def test_returns_sorted_list(self) -> None:
        names = FilesystemPersonaAdapter().list_builtin_names()
        assert "research-agent" not in names
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
        path = _BUILTIN_PERSONAS_DIR / f"{name}.yaml"
        cfg = self._loader.load_from_file(path)
        assert cfg is not None, f"Failed to load built-in persona {name!r}"
        return cfg

    def test_all_builtins_have_system_prompts(self) -> None:
        for p in _BUILTIN_PERSONAS_DIR.glob("*.yaml"):
            cfg = self._load(p.stem)
            assert cfg.system_prompt_template, f"{p.stem} has empty system_prompt_template"

    def test_all_builtins_have_positive_budgets(self) -> None:
        for p in _BUILTIN_PERSONAS_DIR.glob("*.yaml"):
            cfg = self._load(p.stem)
            if p.stem == "product-steward":
                assert cfg.iteration_budget == 0
                continue
            assert cfg.iteration_budget > 0, f"{p.stem} has non-positive iteration_budget"

    def test_specification_framer_leaves_publication_to_workflow_runtime(self) -> None:
        cfg = self._load("specification-framer")

        assert "workflow runtime owns durable artifact publication" in (
            cfg.system_prompt_template.lower()
        )
        assert "mimir_publish_files" not in cfg.allowed_tools

    def test_product_steward_uses_runtime_model(self) -> None:
        cfg = self._load("product-steward")
        assert cfg.llm.primary_alias == ""

    # ------------------------------------------------------------------
    # Specialist personas (NIU-586)
    # ------------------------------------------------------------------

    def test_reviewer_exists(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert cfg.name == "reviewer"
        assert cfg.permission_mode == "workspace_write"
        assert cfg.iteration_budget == 25
        assert cfg.llm.thinking_enabled is True
        assert cfg.llm.primary_alias == ""

    def test_reviewer_allowed_tools(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert "file" in cfg.allowed_tools
        assert "git" in cfg.allowed_tools
        assert "terminal" in cfg.allowed_tools
        assert "web" in cfg.allowed_tools
        assert "ravn" in cfg.allowed_tools
        assert "tracker_issue" in cfg.allowed_tools

    def test_reviewer_forbidden_tools(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert "cascade" in cfg.forbidden_tools

    def test_reviewer_system_prompt_mentions_sql_safety(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert "SQL" in cfg.system_prompt_template

    def test_reviewer_system_prompt_mentions_error_handling(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert "error" in cfg.system_prompt_template.lower()

    def test_reviewer_maps_requested_changes_to_followup_event(self) -> None:
        cfg = _BUILTIN_PERSONAS["reviewer"]
        assert cfg.produces.event_type == "review.completed"
        assert cfg.produces.event_type_map["pass"] == "review.passed"
        assert cfg.produces.event_type_map["needs_changes"] == "review.changes_requested"
        assert cfg.produces.event_type_map["fail"] == "review.changes_requested"

    def test_security_auditor_maps_requested_changes_to_followup_event(self) -> None:
        cfg = _BUILTIN_PERSONAS["security-auditor"]
        assert cfg.produces.event_type == "security.completed"
        assert cfg.produces.event_type_map["pass"] == "security.passed"
        assert cfg.produces.event_type_map["needs_review"] == "security.changes_requested"
        assert cfg.produces.event_type_map["fail"] == "security.changes_requested"

    def test_security_auditor_exists(self) -> None:
        cfg = _BUILTIN_PERSONAS["security-auditor"]
        assert cfg.name == "security-auditor"
        assert cfg.permission_mode == "read-only"
        assert cfg.iteration_budget == 25
        assert cfg.llm.thinking_enabled is True
        assert cfg.llm.primary_alias == ""

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


# ---------------------------------------------------------------------------
# FilesystemPersonaAdapter.load — file vs built-in resolution
# ---------------------------------------------------------------------------


class TestFilesystemPersonaAdapterLoad:
    def test_load_builtin_by_name(self) -> None:
        loader = FilesystemPersonaAdapter()
        cfg = loader.load("coder")
        assert cfg is not None
        assert cfg.name == "coder"

    def test_load_unknown_name_returns_none(self) -> None:
        loader = FilesystemPersonaAdapter()
        assert loader.load("nonexistent-persona") is None

    def test_file_persona_takes_precedence_over_builtin(self, tmp_path: Path) -> None:
        # Write a file that overrides the built-in coder.
        override = tmp_path / "coder.yaml"
        override.write_text(
            "name: coder\nsystem_prompt_template: overridden prompt\n",
            encoding="utf-8",
        )
        loader = FilesystemPersonaAdapter([str(tmp_path)])
        cfg = loader.load("coder")
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
        result = loader.load("coder")
        assert result is not None  # falls back to builtin

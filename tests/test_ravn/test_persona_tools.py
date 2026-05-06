"""Tests for PersonaValidateTool and PersonaSaveTool."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ravn.adapters.tools.persona_tools import PersonaSaveTool, PersonaValidateTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_YAML = """\
name: test-persona
system_prompt_template: |
  You are a test persona.
permission_mode: read-only
"""

_FULL_YAML = """\
name: reviewer
system_prompt_template: |
  You are a code reviewer. Read diffs and produce a structured verdict.
allowed_tools: [file, git, web, ravn]
forbidden_tools: [terminal, cascade]
permission_mode: read-only
llm:
  primary_alias: balanced
  thinking_enabled: true
iteration_budget: 20
produces:
  event_type: review.completed
  schema:
    verdict:
      type: enum
      values: [pass, fail, needs_changes]
      description: review verdict
    summary:
      type: string
      description: one-line review summary
consumes:
  event_types: [code.changed, review.requested]
  injects: [repo, branch, diff_url]
fan_in:
  strategy: all_must_pass
  contributes_to: review.verdict
"""


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# PersonaValidateTool
# ---------------------------------------------------------------------------


class TestPersonaValidateTool:
    def setup_method(self):
        self.tool = PersonaValidateTool()

    def test_valid_minimal_yaml_succeeds(self):
        result = _run(self.tool.execute({"yaml_content": _MINIMAL_YAML}))
        assert not result.is_error
        assert "test-persona" in result.content
        assert "valid" in result.content.lower()

    def test_valid_full_yaml_succeeds_with_summary(self):
        result = _run(self.tool.execute({"yaml_content": _FULL_YAML}))
        assert not result.is_error
        assert "reviewer" in result.content

    def test_missing_name_returns_error(self):
        yaml_no_name = "system_prompt_template: 'hello'\npermission_mode: read-only\n"
        result = _run(self.tool.execute({"yaml_content": yaml_no_name}))
        assert result.is_error
        assert "name" in result.content.lower()

    def test_invalid_yaml_syntax_returns_error_with_line_info(self):
        bad_yaml = "name: foo\n  bad: indent:\n  - wrong"
        result = _run(self.tool.execute({"yaml_content": bad_yaml}))
        assert result.is_error
        assert "syntax" in result.content.lower() or "error" in result.content.lower()

    def test_empty_string_returns_error(self):
        result = _run(self.tool.execute({"yaml_content": ""}))
        assert result.is_error
        assert "empty" in result.content.lower()

    def test_whitespace_only_returns_error(self):
        result = _run(self.tool.execute({"yaml_content": "   \n  "}))
        assert result.is_error

    def test_unknown_permission_mode_returns_warning(self):
        yaml_unknown_perm = "name: x\nsystem_prompt_template: hi\npermission_mode: superuser\n"
        result = _run(self.tool.execute({"yaml_content": yaml_unknown_perm}))
        assert not result.is_error
        assert "superuser" in result.content
        assert "warning" in result.content.lower() or "⚠" in result.content

    def test_empty_system_prompt_returns_warning(self):
        yaml_no_prompt = "name: x\npermission_mode: read-only\n"
        result = _run(self.tool.execute({"yaml_content": yaml_no_prompt}))
        assert not result.is_error
        assert "system_prompt_template" in result.content
        assert "⚠" in result.content or "warning" in result.content.lower()

    def test_unknown_llm_alias_returns_warning(self):
        yaml_bad_alias = "name: x\nsystem_prompt_template: hi\nllm:\n  primary_alias: turbo\n"
        result = _run(self.tool.execute({"yaml_content": yaml_bad_alias}))
        assert not result.is_error
        assert "turbo" in result.content
        assert "⚠" in result.content or "warning" in result.content.lower()

    def test_invalid_fan_in_strategy_returns_error(self):
        yaml_bad_fan_in = "name: x\nsystem_prompt_template: hi\nfan_in:\n  strategy: bad_strategy\n"
        result = _run(self.tool.execute({"yaml_content": yaml_bad_fan_in}))
        assert result.is_error
        assert "bad_strategy" in result.content

    def test_invalid_outcome_field_type_returns_error(self):
        yaml_bad_type = (
            "name: x\n"
            "system_prompt_template: hi\n"
            "produces:\n"
            "  event_type: test.done\n"
            "  schema:\n"
            "    result:\n"
            "      type: fancytype\n"
            "      description: something\n"
        )
        result = _run(self.tool.execute({"yaml_content": yaml_bad_type}))
        assert result.is_error
        assert "fancytype" in result.content

    def test_enum_field_without_values_returns_error(self):
        yaml_enum_no_vals = (
            "name: x\n"
            "system_prompt_template: hi\n"
            "produces:\n"
            "  event_type: test.done\n"
            "  schema:\n"
            "    verdict:\n"
            "      type: enum\n"
            "      description: the verdict\n"
        )
        result = _run(self.tool.execute({"yaml_content": yaml_enum_no_vals}))
        assert result.is_error
        assert "values" in result.content.lower() or "enum" in result.content.lower()

    def test_name_and_description_properties(self):
        assert self.tool.name == "persona_validate"
        assert self.tool.required_permission == "ravn:read"
        assert "validate" in self.tool.description.lower()

    def test_input_schema_has_yaml_content(self):
        schema = self.tool.input_schema
        assert "yaml_content" in schema["properties"]

    def test_non_dict_yaml_returns_error(self):
        # YAML that parses but is a list, not a dict
        result = _run(self.tool.execute({"yaml_content": "- foo\n- bar\n"}))
        assert result.is_error
        assert "mapping" in result.content.lower() or "dict" in result.content.lower()

    def test_schema_field_not_a_dict_returns_error(self):
        yaml_bad_schema_field = (
            "name: x\n"
            "system_prompt_template: hi\n"
            "produces:\n"
            "  event_type: test.done\n"
            "  schema:\n"
            "    verdict: just-a-string\n"  # field_def is a string, not a dict
        )
        result = _run(self.tool.execute({"yaml_content": yaml_bad_schema_field}))
        assert result.is_error
        assert "verdict" in result.content

    def test_errors_and_warnings_shown_together(self):
        # Invalid fan_in strategy (error) + unknown permission_mode (warning)
        yaml_both = (
            "name: x\n"
            "system_prompt_template: hi\n"
            "permission_mode: super-mode\n"
            "fan_in:\n"
            "  strategy: bad_strat\n"
        )
        result = _run(self.tool.execute({"yaml_content": yaml_both}))
        assert result.is_error
        assert "bad_strat" in result.content
        assert "super-mode" in result.content

    def test_produces_with_no_schema_dict_branch(self):
        # produces section with schema: null — branch 101->121 not taken in loop
        yaml_no_schema = (
            "name: x\nsystem_prompt_template: hi\nproduces:\n  event_type: test.done\n  schema: ~\n"
        )
        result = _run(self.tool.execute({"yaml_content": yaml_no_schema}))
        assert not result.is_error

    def test_persona_loader_parse_none_returns_error(self, monkeypatch):
        from ravn.adapters.personas import loader as _loader_module

        monkeypatch.setattr(
            _loader_module.FilesystemPersonaAdapter, "parse", staticmethod(lambda _: None)
        )
        # Valid YAML but mocked parse returns None
        result = _run(self.tool.execute({"yaml_content": _MINIMAL_YAML}))
        assert result.is_error
        assert "FilesystemPersonaAdapter" in result.content or "parse" in result.content.lower()


# ---------------------------------------------------------------------------
# PersonaSaveTool
# ---------------------------------------------------------------------------


class TestPersonaSaveTool:
    def setup_method(self):
        self.tool = PersonaSaveTool()

    def test_save_valid_yaml_writes_file(self, tmp_path: Path):
        result = _run(
            self.tool.execute(
                {
                    "yaml_content": _MINIMAL_YAML,
                    "directory": str(tmp_path),
                }
            )
        )
        assert not result.is_error
        assert "test-persona" in result.content
        saved = tmp_path / "test-persona.yaml"
        assert saved.exists()

    def test_save_to_custom_directory(self, tmp_path: Path):
        custom_dir = tmp_path / "custom" / "personas"
        result = _run(
            self.tool.execute(
                {
                    "yaml_content": _MINIMAL_YAML,
                    "directory": str(custom_dir),
                }
            )
        )
        assert not result.is_error
        assert (custom_dir / "test-persona.yaml").exists()

    def test_save_invalid_yaml_returns_error_no_file(self, tmp_path: Path):
        result = _run(
            self.tool.execute(
                {
                    "yaml_content": "system_prompt_template: hi\n",
                    "directory": str(tmp_path),
                }
            )
        )
        assert result.is_error
        assert not list(tmp_path.glob("*.yaml"))

    def test_save_roundtrip_verified(self, tmp_path: Path):
        result = _run(
            self.tool.execute(
                {
                    "yaml_content": _FULL_YAML,
                    "directory": str(tmp_path),
                }
            )
        )
        assert not result.is_error
        saved = tmp_path / "reviewer.yaml"
        assert saved.exists()

        from ravn.adapters.personas.loader import FilesystemPersonaAdapter

        loader = FilesystemPersonaAdapter(persona_dirs=[str(tmp_path)], include_builtin=False)
        persona = loader.load("reviewer")
        assert persona is not None
        assert persona.name == "reviewer"
        assert persona.produces.event_type == "review.completed"

    def test_save_missing_name_returns_error(self, tmp_path: Path):
        yaml_no_name = "system_prompt_template: hello\npermission_mode: read-only\n"
        result = _run(self.tool.execute({"yaml_content": yaml_no_name, "directory": str(tmp_path)}))
        assert result.is_error
        assert not list(tmp_path.glob("*.yaml"))

    def test_save_with_warnings_shows_them(self, tmp_path: Path):
        # Persona with unknown permission_mode → saves but shows warning
        yaml_with_warning = (
            "name: warn-persona\nsystem_prompt_template: hi\npermission_mode: super-mode\n"
        )
        result = _run(
            self.tool.execute({"yaml_content": yaml_with_warning, "directory": str(tmp_path)})
        )
        assert not result.is_error
        assert "warn-persona" in result.content
        assert "super-mode" in result.content or "⚠" in result.content

    def test_name_and_description_properties(self):
        assert self.tool.name == "persona_save"
        assert self.tool.required_permission == "ravn:write"
        assert "save" in self.tool.description.lower()

    def test_input_schema_has_yaml_content_and_directory(self):
        schema = self.tool.input_schema
        assert "yaml_content" in schema["properties"]
        assert "directory" in schema["properties"]

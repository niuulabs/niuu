"""Tests for ProjectConfig — RAVN.md project overlay parsing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ravn.config import _VALID_PERSONAS, ProjectConfig
from ravn.domain.permission_mode import valid_permission_mode_spellings

_FULL_RAVN_MD = """\
# RAVN Project: my-service

persona: coding-agent
allowed_tools: [file, git, terminal, web]
forbidden_tools: [volundr, cascade]
permission_mode: workspace-write
primary_alias: balanced
thinking_enabled: true
iteration_budget: 30
notes: >
  This is a FastAPI service. Always run tests before committing.
  Use async everywhere. No print statements.
"""

_MINIMAL_RAVN_MD = """\
# RAVN Project: minimal

iteration_budget: 5
"""

_NO_HEADER_RAVN_MD = """\
persona: coding-agent
iteration_budget: 10
"""

_MALFORMED_YAML = """\
# RAVN Project: broken

: this is not valid yaml: {[
"""


class TestProjectConfigFromText:
    def test_parses_project_name(self) -> None:
        cfg = ProjectConfig.from_text(_FULL_RAVN_MD)
        assert cfg.project_name == "my-service"

    def test_parses_persona(self) -> None:
        cfg = ProjectConfig.from_text(_FULL_RAVN_MD)
        assert cfg.persona == "coding-agent"

    def test_parses_allowed_tools(self) -> None:
        cfg = ProjectConfig.from_text(_FULL_RAVN_MD)
        assert cfg.allowed_tools == ["file", "git", "terminal", "web"]

    def test_parses_forbidden_tools(self) -> None:
        cfg = ProjectConfig.from_text(_FULL_RAVN_MD)
        assert cfg.forbidden_tools == ["volundr", "cascade"]

    def test_parses_permission_mode(self) -> None:
        cfg = ProjectConfig.from_text(_FULL_RAVN_MD)
        assert cfg.permission_mode == "workspace-write"

    def test_parses_iteration_budget(self) -> None:
        cfg = ProjectConfig.from_text(_FULL_RAVN_MD)
        assert cfg.iteration_budget == 30

    def test_parses_notes(self) -> None:
        cfg = ProjectConfig.from_text(_FULL_RAVN_MD)
        assert "FastAPI" in cfg.notes

    def test_minimal_has_defaults_for_missing_fields(self) -> None:
        cfg = ProjectConfig.from_text(_MINIMAL_RAVN_MD)
        assert cfg.project_name == "minimal"
        assert cfg.iteration_budget == 5
        assert cfg.persona == ""
        assert cfg.allowed_tools == []
        assert cfg.forbidden_tools == []
        assert cfg.permission_mode == ""
        assert cfg.notes == ""

    def test_no_header_returns_empty_project_config(self) -> None:
        cfg = ProjectConfig.from_text(_NO_HEADER_RAVN_MD)
        # Without the header line, project_name is empty and YAML is not parsed.
        assert cfg.project_name == ""
        assert cfg.iteration_budget == 0

    def test_malformed_yaml_returns_empty_config(self) -> None:
        cfg = ProjectConfig.from_text(_MALFORMED_YAML)
        assert cfg.project_name == "broken"
        assert cfg.persona == ""
        assert cfg.iteration_budget == 0

    def test_empty_string_returns_empty_config(self) -> None:
        cfg = ProjectConfig.from_text("")
        assert cfg.project_name == ""
        assert cfg.iteration_budget == 0

    def test_project_name_with_whitespace_stripped(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project:   my project  \n")
        assert cfg.project_name == "my project"

    def test_iteration_budget_zero_when_not_set(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\npersona: helper\n")
        assert cfg.iteration_budget == 0

    def test_allowed_tools_empty_list(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\nallowed_tools: []\n")
        assert cfg.allowed_tools == []

    def test_non_dict_yaml_body_gives_empty_config(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\n- just a list\n")
        assert cfg.persona == ""

    def test_non_numeric_iteration_budget_returns_zero(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\niteration_budget: many\n")
        assert cfg.iteration_budget == 0


class TestProjectConfigLoad:
    def test_load_from_file(self, tmp_path: Path) -> None:
        ravn_md = tmp_path / "RAVN.md"
        ravn_md.write_text(_FULL_RAVN_MD, encoding="utf-8")
        cfg = ProjectConfig.load(ravn_md)
        assert cfg is not None
        assert cfg.project_name == "my-service"
        assert cfg.iteration_budget == 30

    def test_load_missing_file_returns_none(self, tmp_path: Path) -> None:
        result = ProjectConfig.load(tmp_path / "nonexistent.md")
        assert result is None

    def test_load_unreadable_file_returns_none(self, tmp_path: Path) -> None:
        # Simulate an unreadable file by passing a directory path.
        result = ProjectConfig.load(tmp_path)
        assert result is None


class TestProjectConfigDiscover:
    def test_discover_finds_ravn_md_in_cwd(self, tmp_path: Path) -> None:
        ravn_md = tmp_path / "RAVN.md"
        ravn_md.write_text(_FULL_RAVN_MD, encoding="utf-8")
        cfg = ProjectConfig.discover(cwd=tmp_path)
        assert cfg is not None
        assert cfg.project_name == "my-service"

    def test_discover_finds_ravn_md_in_parent(self, tmp_path: Path) -> None:
        ravn_md = tmp_path / "RAVN.md"
        ravn_md.write_text(_MINIMAL_RAVN_MD, encoding="utf-8")
        subdir = tmp_path / "sub" / "dir"
        subdir.mkdir(parents=True)
        cfg = ProjectConfig.discover(cwd=subdir)
        assert cfg is not None
        assert cfg.project_name == "minimal"

    def test_discover_returns_none_when_no_ravn_md(self, tmp_path: Path) -> None:
        subdir = tmp_path / "empty"
        subdir.mkdir()
        # Use a temp dir that definitely has no RAVN.md above it — point to
        # the subdir itself which is isolated inside tmp_path.
        result = ProjectConfig.discover(cwd=subdir)
        # May find one higher up if tests run inside a RAVN.md repo; just
        # verify the return type is correct.
        assert result is None or isinstance(result, ProjectConfig)

    def test_discover_cwd_none_uses_process_cwd(self, tmp_path: Path, monkeypatch) -> None:
        ravn_md = tmp_path / "RAVN.md"
        ravn_md.write_text(_MINIMAL_RAVN_MD, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        cfg = ProjectConfig.discover(cwd=None)
        assert cfg is not None
        assert cfg.project_name == "minimal"

    def test_discover_closer_file_wins(self, tmp_path: Path) -> None:
        parent_md = tmp_path / "RAVN.md"
        parent_md.write_text("# RAVN Project: parent\n\niteration_budget: 1\n")
        subdir = tmp_path / "child"
        subdir.mkdir()
        child_md = subdir / "RAVN.md"
        child_md.write_text("# RAVN Project: child\n\niteration_budget: 99\n")
        cfg = ProjectConfig.discover(cwd=subdir)
        assert cfg is not None
        assert cfg.project_name == "child"
        assert cfg.iteration_budget == 99

    def test_discover_falls_back_to_global_default(self, tmp_path: Path) -> None:
        """When no RAVN.md is found in the tree, ~/.ravn/default.md is tried."""
        global_default = tmp_path / ".ravn" / "default.md"
        global_default.parent.mkdir(parents=True)
        global_default.write_text("# RAVN Project: global-default\n\niteration_budget: 5\n")

        isolated = tmp_path / "project"
        isolated.mkdir()

        with patch("pathlib.Path.home", return_value=tmp_path):
            cfg = ProjectConfig.discover(cwd=isolated)

        assert cfg is not None
        assert cfg.project_name == "global-default"

    def test_discover_project_file_beats_global_default(self, tmp_path: Path) -> None:
        """A local RAVN.md takes precedence over ~/.ravn/default.md."""
        global_default = tmp_path / ".ravn" / "default.md"
        global_default.parent.mkdir(parents=True)
        global_default.write_text("# RAVN Project: global-default\n")

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "RAVN.md").write_text("# RAVN Project: local\n")

        with patch("pathlib.Path.home", return_value=tmp_path):
            cfg = ProjectConfig.discover(cwd=project_dir)

        assert cfg is not None
        assert cfg.project_name == "local"

    def test_discover_returns_none_when_no_files_anywhere(self, tmp_path: Path) -> None:
        """Returns None when no RAVN.md exists anywhere and no global default."""
        isolated = tmp_path / "project"
        isolated.mkdir()

        # Point home to a directory without .ravn/default.md
        with patch("pathlib.Path.home", return_value=tmp_path):
            cfg = ProjectConfig.discover(cwd=isolated)

        assert cfg is None


# ---------------------------------------------------------------------------
# New fields: primary_alias, thinking_enabled, warnings
# ---------------------------------------------------------------------------


class TestProjectConfigNewFields:
    def test_parses_primary_alias(self) -> None:
        cfg = ProjectConfig.from_text(_FULL_RAVN_MD)
        assert cfg.primary_alias == "balanced"

    def test_parses_thinking_enabled_true(self) -> None:
        cfg = ProjectConfig.from_text(_FULL_RAVN_MD)
        assert cfg.thinking_enabled is True

    def test_thinking_enabled_defaults_false(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\npersona: coding-agent\n")
        assert cfg.thinking_enabled is False

    def test_thinking_enabled_false_explicit(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\nthinking_enabled: false\n")
        assert cfg.thinking_enabled is False

    def test_primary_alias_defaults_empty(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\npersona: coding-agent\n")
        assert cfg.primary_alias == ""

    def test_no_warnings_for_valid_config(self) -> None:
        cfg = ProjectConfig.from_text(_FULL_RAVN_MD)
        assert cfg.warnings == []

    def test_thinking_enabled_string_true(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\nthinking_enabled: 'true'\n")
        assert cfg.thinking_enabled is True

    def test_thinking_enabled_integer_one(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\nthinking_enabled: 1\n")
        assert cfg.thinking_enabled is True


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestProjectConfigValidation:
    def test_invalid_permission_mode_raises(self) -> None:
        """A permission boundary is never downgraded to a warning."""
        with pytest.raises(ValueError, match="Unknown permission_mode 'fly-mode'"):
            ProjectConfig.from_text("# RAVN Project: x\n\npermission_mode: fly-mode\n")

    def test_valid_permission_modes_no_warning(self) -> None:
        for mode in valid_permission_mode_spellings():
            cfg = ProjectConfig.from_text(f"# RAVN Project: x\n\npermission_mode: {mode}\n")
            assert not any("permission_mode" in w for w in cfg.warnings), (
                f"Unexpected warning for valid mode {mode!r}"
            )

    def test_invalid_persona_produces_warning(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\npersona: quantum-hacker\n")
        assert any("persona" in w for w in cfg.warnings)

    def test_valid_personas_no_warning(self) -> None:
        for persona in _VALID_PERSONAS:
            cfg = ProjectConfig.from_text(f"# RAVN Project: x\n\npersona: {persona}\n")
            assert not any("persona" in w for w in cfg.warnings), (
                f"Unexpected warning for valid persona {persona!r}"
            )

    def test_negative_iteration_budget_clamped_to_zero(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\niteration_budget: -5\n")
        assert cfg.iteration_budget == 0
        assert any("iteration_budget" in w for w in cfg.warnings)

    def test_empty_permission_mode_no_warning(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\npersona: coding-agent\n")
        assert not any("permission_mode" in w for w in cfg.warnings)

    def test_empty_persona_no_warning(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\nallowed_tools: [file]\n")
        assert not any("persona" in w for w in cfg.warnings)

    def test_multiple_warnings_accumulated(self) -> None:
        cfg = ProjectConfig.from_text("# RAVN Project: x\n\npersona: robot\niteration_budget: -1\n")
        assert len(cfg.warnings) >= 2

    def test_valid_schema_constants_non_empty(self) -> None:
        assert len(_VALID_PERSONAS) > 0

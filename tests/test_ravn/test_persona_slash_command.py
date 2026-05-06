"""Tests for the /persona slash command."""

from __future__ import annotations

from pathlib import Path

import pytest

from ravn.adapters.slash_commands import PersonaCommand, SlashCommandContext, default_registry
from ravn.domain.models import Session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CUSTOM_PERSONA_YAML = """\
name: custom-agent
system_prompt_template: |
  You are a custom agent for testing.
permission_mode: workspace-write
llm:
  primary_alias: balanced
iteration_budget: 15
"""


def _ctx() -> SlashCommandContext:
    return SlashCommandContext(session=Session())


def _make_custom_persona(tmp_path: Path, name: str = "custom-agent") -> Path:
    """Write a custom persona YAML to tmp_path and return the file path."""
    content = f"name: {name}\nsystem_prompt_template: 'Test persona {name}.'\n"
    path = tmp_path / f"{name}.yaml"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Unit tests for PersonaCommand
# ---------------------------------------------------------------------------


class TestPersonaCommandCreate:
    def setup_method(self):
        self.cmd = PersonaCommand()
        self.ctx = _ctx()

    def test_no_args_returns_guidance(self):
        result = self.cmd.handle("", self.ctx)
        assert "Describe" in result or "describe" in result
        assert "persona" in result.lower()

    def test_create_subcommand_returns_guidance(self):
        result = self.cmd.handle("create", self.ctx)
        assert "persona" in result.lower()
        assert len(result) > 10

    def test_unknown_subcommand_returns_usage(self):
        result = self.cmd.handle("explode", self.ctx)
        assert "explode" in result
        assert "Usage" in result or "usage" in result.lower()


class TestPersonaCommandList:
    def setup_method(self):
        self.cmd = PersonaCommand()
        self.ctx = _ctx()

    def test_list_returns_builtin_personas(self):
        result = self.cmd.handle("list", self.ctx)
        # Built-in personas should always be present
        assert "coding-agent" in result or "Personas" in result

    def test_list_shows_source(self):
        result = self.cmd.handle("list", self.ctx)
        # Should show either "[built-in]" or a file path for each persona
        assert "[built-in]" in result or "personas" in result.lower()

    def test_list_includes_custom_persona(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from ravn.adapters.personas import loader as _loader_module

        # Monkeypatch the bundled personas directory to include tmp_path
        monkeypatch.setattr(
            _loader_module,
            "_BUILTIN_PERSONAS_DIR",
            tmp_path,
        )
        _make_custom_persona(tmp_path, "my-test-persona")

        # FilesystemPersonaAdapter uses _DEFAULT_PERSONAS_DIR indirectly via Path.home()
        # We need to patch FilesystemPersonaAdapter construction inside PersonaCommand._list
        original_loader_init = _loader_module.FilesystemPersonaAdapter.__init__

        def patched_init(self_loader, persona_dirs=None, *, include_builtin=True, cwd=None):
            if persona_dirs is None:
                persona_dirs = [str(tmp_path)]
            original_loader_init(
                self_loader, persona_dirs=persona_dirs, include_builtin=include_builtin, cwd=cwd
            )

        monkeypatch.setattr(_loader_module.FilesystemPersonaAdapter, "__init__", patched_init)

        result = self.cmd.handle("list", self.ctx)
        assert "my-test-persona" in result


class TestPersonaCommandShow:
    def setup_method(self):
        self.cmd = PersonaCommand()
        self.ctx = _ctx()

    def test_show_existing_builtin(self):
        result = self.cmd.handle("show coding-agent", self.ctx)
        assert "coding-agent" in result
        assert "permission" in result.lower() or "Permission" in result

    def test_show_nonexistent_returns_not_found(self):
        result = self.cmd.handle("show nonexistent-persona-xyz", self.ctx)
        assert "not found" in result.lower()

    def test_show_no_name_returns_usage(self):
        result = self.cmd.handle("show", self.ctx)
        assert "Usage" in result or "usage" in result.lower()

    def test_show_displays_permission_mode(self):
        result = self.cmd.handle("show coding-agent", self.ctx)
        assert "workspace-write" in result or "permission" in result.lower()

    def test_show_displays_allowed_tools(self):
        result = self.cmd.handle("show coding-agent", self.ctx)
        assert "tools" in result.lower() or "file" in result

    def test_show_pipeline_persona_displays_produces(self):
        result = self.cmd.handle("show reviewer", self.ctx)
        assert "reviewer" in result
        # reviewer has produces
        assert "review.completed" in result or "produces" in result.lower() or "Produces" in result


class TestPersonaCommandDelete:
    def setup_method(self):
        self.cmd = PersonaCommand()
        self.ctx = _ctx()

    def test_delete_no_name_returns_usage(self):
        result = self.cmd.handle("delete", self.ctx)
        assert "Usage" in result or "usage" in result.lower()

    def test_delete_builtin_is_refused(self):
        result = self.cmd.handle("delete coding-agent", self.ctx)
        assert "built-in" in result.lower() or "Cannot" in result

    def test_delete_custom_persona_removes_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from ravn.adapters.personas import loader as _loader_module

        persona_name = "delete-test-persona"
        _make_custom_persona(tmp_path, persona_name)

        original_loader_init = _loader_module.FilesystemPersonaAdapter.__init__

        def patched_init(self_loader, persona_dirs=None, *, include_builtin=True, cwd=None):
            if persona_dirs is None:
                persona_dirs = [str(tmp_path)]
            original_loader_init(
                self_loader, persona_dirs=persona_dirs, include_builtin=include_builtin, cwd=cwd
            )

        monkeypatch.setattr(_loader_module.FilesystemPersonaAdapter, "__init__", patched_init)

        result = self.cmd.handle(f"delete {persona_name}", self.ctx)
        assert "deleted" in result.lower()
        assert not (tmp_path / f"{persona_name}.yaml").exists()

    def test_delete_nonexistent_returns_not_found(self):
        result = self.cmd.handle("delete nonexistent-xyz-abc", self.ctx)
        assert "not found" in result.lower()


class TestPersonaCommandRegistration:
    def test_persona_command_registered_in_default_registry(self):
        result = default_registry.handle("/persona", _ctx())
        assert result is not None
        assert "persona" in result.lower()

    def test_personas_alias_works(self):
        result = default_registry.handle("/personas", _ctx())
        assert result is not None
        assert "persona" in result.lower()

    def test_persona_appears_in_help(self):
        result = default_registry.handle("/help", _ctx())
        assert result is not None
        assert "/persona" in result

    def test_persona_list_via_registry(self):
        result = default_registry.handle("/persona list", _ctx())
        assert result is not None
        assert len(result) > 0

    def test_persona_show_via_registry(self):
        result = default_registry.handle("/persona show coding-agent", _ctx())
        assert result is not None
        assert "coding-agent" in result

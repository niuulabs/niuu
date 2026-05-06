"""Unit tests for the built-in tool registry (ravn.adapters.tools.builtin_registry)."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ravn.adapters.tools.builtin_registry import BUILTIN_TOOLS, BuiltinToolDef
from ravn.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime_ctx(tmp_path: Path, memory: object = None) -> dict:
    from ravn.domain.models import Session

    return {
        "workspace": tmp_path,
        "session": Session(),
        "llm": MagicMock(),
        "memory": memory,
        "iteration_budget": None,
        "persona_prefix": "",
    }


# ---------------------------------------------------------------------------
# Registry structure
# ---------------------------------------------------------------------------


class TestRegistryStructure:
    def test_all_entries_are_builtin_tool_def(self) -> None:
        for key, val in BUILTIN_TOOLS.items():
            assert isinstance(val, BuiltinToolDef), f"{key!r} is not a BuiltinToolDef"

    def test_all_entries_have_non_empty_adapter(self) -> None:
        for key, val in BUILTIN_TOOLS.items():
            assert val.adapter, f"{key!r} has empty adapter path"

    def test_all_entries_have_at_least_one_group(self) -> None:
        for key, val in BUILTIN_TOOLS.items():
            assert val.groups, f"{key!r} has no groups"

    def test_known_groups_only(self) -> None:
        valid_groups = {"core", "extended", "skill", "platform", "ravn"}
        for key, val in BUILTIN_TOOLS.items():
            unknown = val.groups - valid_groups
            assert not unknown, f"{key!r} has unknown groups: {unknown}"

    def test_core_tools_present(self) -> None:
        core_keys = {k for k, v in BUILTIN_TOOLS.items() if "core" in v.groups}
        expected = {
            "read_file",
            "write_file",
            "edit_file",
            "glob_search",
            "grep_search",
            "git_status",
            "git_diff",
            "git_add",
            "git_commit",
            "git_checkout",
            "git_log",
            "git_pr",
            "bash",
            "web_fetch",
            "todo_write",
            "todo_read",
            "ask_user",
            "terminal",
        }
        assert expected.issubset(core_keys)

    def test_extended_tools_present(self) -> None:
        extended_keys = {k for k, v in BUILTIN_TOOLS.items() if "extended" in v.groups}
        expected = {
            "web_search",
            "ravn_state",
            "ravn_reflect",
            "ravn_memory_search",
            "session_search",
        }
        assert expected.issubset(extended_keys)

    def test_skill_tools_present(self) -> None:
        skill_keys = {k for k, v in BUILTIN_TOOLS.items() if "skill" in v.groups}
        assert {"skill_list", "skill_run"}.issubset(skill_keys)

    def test_platform_tools_present(self) -> None:
        platform_keys = {k for k, v in BUILTIN_TOOLS.items() if "platform" in v.groups}
        assert {"volundr_session", "volundr_git", "tyr_saga", "tracker_issue"}.issubset(
            platform_keys
        )

    def test_terminal_docker_entry_exists(self) -> None:
        assert "terminal_docker" in BUILTIN_TOOLS
        assert BUILTIN_TOOLS["terminal_docker"].condition is not None

    def test_memory_tools_have_required_context(self) -> None:
        for key in ("ravn_memory_search", "session_search"):
            assert "memory" in BUILTIN_TOOLS[key].required_context, (
                f"{key!r} should require 'memory' context"
            )


# ---------------------------------------------------------------------------
# Adapter importability
# ---------------------------------------------------------------------------


class TestAdapterImportability:
    """Verify every adapter path in the registry points to an importable class."""

    @pytest.mark.parametrize("tool_key", list(BUILTIN_TOOLS.keys()))
    def test_adapter_is_importable(self, tool_key: str) -> None:
        adapter = BUILTIN_TOOLS[tool_key].adapter
        module_path, class_name = adapter.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name, None)
        assert cls is not None, f"Class {class_name!r} not found in {module_path!r}"


# ---------------------------------------------------------------------------
# Condition logic
# ---------------------------------------------------------------------------


class TestConditions:
    def test_terminal_local_condition(self, settings: Settings = None) -> None:
        if settings is None:
            settings = Settings()
        settings.tools.terminal.backend = "local"
        td = BUILTIN_TOOLS["terminal"]
        assert td.condition is None or td.condition(settings)

    def test_terminal_docker_condition(self) -> None:
        s = Settings()
        s.tools.terminal.backend = "docker"
        td_local = BUILTIN_TOOLS["terminal"]
        td_docker = BUILTIN_TOOLS["terminal_docker"]
        assert td_local.condition is not None
        assert not td_local.condition(s)
        assert td_docker.condition is not None
        assert td_docker.condition(s)

    def test_skill_tools_condition_disabled(self) -> None:
        s = Settings()
        s.skill.enabled = False
        for key in ("skill_list", "skill_run"):
            cond = BUILTIN_TOOLS[key].condition
            assert cond is not None
            assert not cond(s)

    def test_skill_tools_condition_enabled(self) -> None:
        s = Settings()
        s.skill.enabled = True
        for key in ("skill_list", "skill_run"):
            cond = BUILTIN_TOOLS[key].condition
            assert cond is not None
            assert cond(s)

    def test_platform_tools_condition(self) -> None:
        s = Settings()
        s.gateway.platform.enabled = False
        for key in ("volundr_session", "volundr_git", "tyr_saga", "tracker_issue"):
            cond = BUILTIN_TOOLS[key].condition
            assert cond is not None
            assert not cond(s)


# ---------------------------------------------------------------------------
# kwargs_fn smoke tests — verify callables return dicts without raising
# ---------------------------------------------------------------------------


class TestKwargsFn:
    @pytest.fixture()
    def settings(self) -> Settings:
        return Settings()

    def test_core_tools_return_dict(self, settings: Settings, tmp_path: Path) -> None:
        ctx = _make_runtime_ctx(tmp_path)
        core_keys = [k for k, v in BUILTIN_TOOLS.items() if "core" in v.groups]
        for key in core_keys:
            td = BUILTIN_TOOLS[key]
            if td.condition is not None and not td.condition(settings):
                continue
            result = td.kwargs_fn(settings, ctx)
            assert isinstance(result, dict), f"{key!r}: kwargs_fn did not return a dict"

    def test_web_search_kwargs_uses_mock_by_default(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        ctx = _make_runtime_ctx(tmp_path)
        kwargs = BUILTIN_TOOLS["web_search"].kwargs_fn(settings, ctx)
        assert "provider" in kwargs
        assert "num_results" in kwargs
        # Default is mock provider, so provider should be None
        assert kwargs["provider"] is None

    def test_ravn_state_kwargs_tool_names_empty(self, settings: Settings, tmp_path: Path) -> None:
        ctx = _make_runtime_ctx(tmp_path)
        kwargs = BUILTIN_TOOLS["ravn_state"].kwargs_fn(settings, ctx)
        assert kwargs["tool_names"] == []
        assert "permission_mode" in kwargs
        assert "model" in kwargs

    def test_ravn_reflect_kwargs(self, settings: Settings, tmp_path: Path) -> None:
        ctx = _make_runtime_ctx(tmp_path)
        kwargs = BUILTIN_TOOLS["ravn_reflect"].kwargs_fn(settings, ctx)
        assert kwargs["llm"] is ctx["llm"]
        assert kwargs["session"] is ctx["session"]
        assert "model" in kwargs

    def test_memory_tool_kwargs(self, settings: Settings, tmp_path: Path) -> None:
        mock_memory = MagicMock()
        ctx = _make_runtime_ctx(tmp_path, memory=mock_memory)
        for key in ("ravn_memory_search", "session_search"):
            kwargs = BUILTIN_TOOLS[key].kwargs_fn(settings, ctx)
            assert kwargs["memory"] is mock_memory


# ---------------------------------------------------------------------------
# required_context skipping
# ---------------------------------------------------------------------------


class TestRequiredContext:
    def test_memory_tools_skipped_when_memory_none(self) -> None:
        """Tools with required_context={"memory"} should be skipped if memory is None."""
        for key in ("ravn_memory_search", "session_search"):
            td = BUILTIN_TOOLS[key]
            assert "memory" in td.required_context

    def test_non_memory_tools_have_empty_required_context(self) -> None:
        for key in ("read_file", "write_file", "bash", "git_status"):
            td = BUILTIN_TOOLS[key]
            assert not td.required_context, f"{key!r} unexpectedly requires context"


# ---------------------------------------------------------------------------
# _build_skill_port helper
# ---------------------------------------------------------------------------


class TestBuildSkillPort:
    def test_file_backend_returns_file_registry(self, tmp_path: Path) -> None:
        from ravn.adapters.tools.builtin_registry import _build_skill_port

        s = Settings()
        s.skill.backend = "file"
        port = _build_skill_port(s, tmp_path)
        assert type(port).__name__ == "FileSkillRegistry"

    def test_sqlite_backend_returns_sqlite_adapter(self, tmp_path: Path) -> None:
        from ravn.adapters.tools.builtin_registry import _build_skill_port

        s = Settings()
        s.skill.backend = "sqlite"
        s.skill.path = str(tmp_path / "skills.db")
        port = _build_skill_port(s, tmp_path)
        assert type(port).__name__ == "SqliteSkillAdapter"


# ---------------------------------------------------------------------------
# _build_web_search_kwargs with non-mock provider
# ---------------------------------------------------------------------------


class TestBuildWebSearchKwargs:
    def test_mock_provider_returns_none(self, tmp_path: Path) -> None:
        from ravn.adapters.tools.builtin_registry import _build_web_search_kwargs

        s = Settings()
        ctx = _make_runtime_ctx(tmp_path)
        result = _build_web_search_kwargs(s, ctx)
        assert result["provider"] is None

    def test_nonexistent_provider_falls_back_to_none(self, tmp_path: Path) -> None:
        from ravn.adapters.tools.builtin_registry import _build_web_search_kwargs
        from ravn.config import ToolAdapterConfig

        s = Settings()
        s.tools.web.search.provider = ToolAdapterConfig(adapter="nonexistent.module.Provider")
        ctx = _make_runtime_ctx(tmp_path)
        result = _build_web_search_kwargs(s, ctx)
        # On failure, provider falls back to None (mock)
        assert result["provider"] is None

    def test_num_results_passed_through(self, tmp_path: Path) -> None:
        from ravn.adapters.tools.builtin_registry import _build_web_search_kwargs

        s = Settings()
        s.tools.web.search.num_results = 7
        ctx = _make_runtime_ctx(tmp_path)
        result = _build_web_search_kwargs(s, ctx)
        assert result["num_results"] == 7

    def test_valid_provider_class_instantiated(self, tmp_path: Path) -> None:
        from ravn.adapters.tools.builtin_registry import _build_web_search_kwargs
        from ravn.config import ToolAdapterConfig

        s = Settings()
        # Use a real importable class that takes no required args so the successful
        # load path (lines 94-102 in builtin_registry.py) is exercised.
        s.tools.web.search.provider = ToolAdapterConfig(
            adapter="ravn.adapters.tools.ask_user.AskUserTool",
            kwargs={},
        )
        ctx = _make_runtime_ctx(tmp_path)
        result = _build_web_search_kwargs(s, ctx)
        # AskUserTool is not a real web search provider, but it gets instantiated
        assert result["provider"] is not None

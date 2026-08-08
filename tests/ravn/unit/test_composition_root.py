"""Unit tests for the Ravn composition root (_build_* helpers in cli/commands.py)."""

from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ravn.adapters.personas.loader import PersonaExecutorConfig
from ravn.config import Settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _api_key():
    """Ensure an API key is available for builders that check it."""
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        yield


@pytest.fixture()
def _mock_anthropic():
    """Mock the AnthropicAdapter so no real HTTP calls are made."""
    with patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls:
        mock_cls.return_value = MagicMock()
        yield mock_cls


@pytest.fixture()
def settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# _build_llm
# ---------------------------------------------------------------------------


class TestBuildLlm:
    @pytest.mark.usefixtures("_api_key")
    def test_default_returns_anthropic_adapter(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_llm

        with patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls:
            mock_cls.return_value = MagicMock()
            llm = _build_llm(settings)

        mock_cls.assert_called_once()
        assert llm is mock_cls.return_value

    @pytest.mark.usefixtures("_api_key")
    def test_anthropic_receives_model_and_max_tokens(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_llm

        with patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls:
            mock_cls.return_value = MagicMock()
            _build_llm(settings)

        _, kwargs = mock_cls.call_args
        assert kwargs["model"] == settings.effective_model()
        assert kwargs["max_tokens"] == settings.effective_max_tokens()

    @pytest.mark.usefixtures("_api_key")
    def test_with_fallbacks_returns_fallback_adapter(self, settings: Settings) -> None:
        from ravn.adapters.llm.fallback import FallbackLLMAdapter
        from ravn.cli.commands import _build_llm
        from ravn.config import LLMProviderConfig

        settings.llm.fallbacks = [
            LLMProviderConfig(
                adapter="ravn.adapters.llm.anthropic.AnthropicAdapter",
                kwargs={},
            ),
        ]

        with patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls:
            mock_cls.return_value = MagicMock()
            llm = _build_llm(settings)

        assert isinstance(llm, FallbackLLMAdapter)

    @pytest.mark.usefixtures("_api_key")
    def test_custom_provider_adapter(self, settings: Settings) -> None:
        """A non-Anthropic adapter should be loaded from provider config."""
        from ravn.cli.commands import _build_llm

        settings.llm.provider.adapter = "ravn.adapters.llm.anthropic.AnthropicAdapter"
        settings.llm.provider.kwargs = {"api_key": "custom-key"}

        with patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls:
            mock_cls.return_value = MagicMock()
            _build_llm(settings)

        _, kwargs = mock_cls.call_args
        assert kwargs["api_key"] == "custom-key"


class TestBuildExecutor:
    def test_default_returns_agent_executor(self) -> None:
        from ravn.cli.commands import _build_executor

        executor = _build_executor()
        assert type(executor).__name__ == "AgentExecutor"

    def test_persona_executor_override_is_loaded(self) -> None:
        from ravn.cli.commands import _build_executor

        persona = MagicMock(
            executor=PersonaExecutorConfig(
                adapter="ravn.adapters.executors.agent.AgentExecutor",
                kwargs={"mode": "default"},
            )
        )
        executor = _build_executor(persona)
        assert type(executor).__name__ == "AgentExecutor"


# ---------------------------------------------------------------------------
# _build_memory
# ---------------------------------------------------------------------------


class TestBuildMemory:
    def test_none_backend_returns_none(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_memory

        settings.memory.backend = "none"

        mem = _build_memory(settings)
        assert mem is None

    def test_sqlite_backend_returns_adapter(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_memory

        settings.memory.backend = "sqlite"
        settings.memory.path = "/tmp/test_ravn_memory.db"
        settings.embedding.enabled = False

        mem = _build_memory(settings)
        assert mem is not None
        assert type(mem).__name__ == "SqliteMemoryAdapter"

    def test_postgres_no_dsn_returns_none(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_memory

        settings.memory.backend = "postgres"
        settings.memory.dsn = ""
        settings.memory.dsn_env = ""

        mem = _build_memory(settings)
        assert mem is None

    def test_custom_backend_failure_raises_instead_of_disabling_memory(
        self, settings: Settings
    ) -> None:
        # Was: returned None with a warning, so a typo'd class path left the
        # resident recording and recalling nothing while looking healthy.
        # 'none' is how to ask for no memory on purpose.
        from ravn.cli.commands import _build_memory

        settings.memory.backend = "nonexistent.module.Adapter"

        with pytest.raises(ValueError, match="not importable"):
            _build_memory(settings)

    def test_embedding_failure_falls_back_to_fts_only(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_memory

        settings.memory.backend = "sqlite"
        settings.memory.path = "/tmp/test_ravn_memory_fts.db"
        settings.embedding.enabled = True
        settings.embedding.adapter = "nonexistent.module.Embedding"

        mem = _build_memory(settings)
        # Memory still created, just without embedding
        assert mem is not None


# ---------------------------------------------------------------------------
# _build_permission
# ---------------------------------------------------------------------------


class TestBuildPermission:
    def test_allow_all_mode(self, settings: Settings, tmp_path: Path) -> None:
        from ravn.adapters.permission.allow_deny import AllowAllPermission
        from ravn.cli.commands import _build_permission

        settings.permission.mode = "allow_all"
        perm = _build_permission(settings, tmp_path, no_tools=False, persona_config=None)
        assert isinstance(perm, AllowAllPermission)

    def test_deny_all_mode(self, settings: Settings, tmp_path: Path) -> None:
        from ravn.adapters.permission.allow_deny import DenyAllPermission
        from ravn.cli.commands import _build_permission

        settings.permission.mode = "deny_all"
        perm = _build_permission(settings, tmp_path, no_tools=False, persona_config=None)
        assert isinstance(perm, DenyAllPermission)

    def test_workspace_write_mode(self, settings: Settings, tmp_path: Path) -> None:
        from ravn.adapters.permission.enforcer import PermissionEnforcer
        from ravn.cli.commands import _build_permission

        settings.permission.mode = "workspace_write"
        perm = _build_permission(settings, tmp_path, no_tools=False, persona_config=None)
        assert isinstance(perm, PermissionEnforcer)

    def test_no_tools_overrides_to_deny_all(self, settings: Settings, tmp_path: Path) -> None:
        from ravn.adapters.permission.allow_deny import DenyAllPermission
        from ravn.cli.commands import _build_permission

        settings.permission.mode = "allow_all"
        perm = _build_permission(settings, tmp_path, no_tools=True, persona_config=None)
        assert isinstance(perm, DenyAllPermission)

    def test_read_only_persona_overrides_to_enforcer(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.adapters.permission.enforcer import PermissionEnforcer
        from ravn.cli.commands import _build_permission

        persona = MagicMock(permission_mode="read-only")
        settings.permission.mode = "allow_all"
        perm = _build_permission(settings, tmp_path, no_tools=False, persona_config=persona)
        assert isinstance(perm, PermissionEnforcer)


# ---------------------------------------------------------------------------
# _build_tools
# ---------------------------------------------------------------------------


class TestBuildTools:
    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_default_returns_many_tools(self, settings: Settings, tmp_path: Path) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        session = Session()
        llm = MagicMock()
        tools = _build_tools(
            settings,
            tmp_path,
            session,
            llm,
            None,
            None,
            no_tools=False,
            persona_config=None,
        )
        # Expect at least 20 tools (file, git, bash, terminal, web, todo, ask_user, introspection)
        assert len(tools) >= 20

    def test_no_tools_returns_empty(self, settings: Settings, tmp_path: Path) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=True,
            persona_config=None,
        )
        assert tools == []

    @staticmethod
    def _write_learned_artifact(tmp_path: Path, name: str) -> None:
        from ravn.valkyrie_evolution.learned_tools import (
            write_learned_tool,
            write_learned_tool_artifact,
        )
        from ravn.valkyrie_evolution.models import LearnedToolArtifact, LearnedToolManifest

        artifact = LearnedToolArtifact(
            artifact_id=f"learned-tool:{name}",
            manifest=LearnedToolManifest(
                name=name,
                description="Read a persisted metric window.",
                input_schema={"type": "object"},
                required_permission="mimir:read",
                declared_reach=[],
            ),
            tool_code="def run(input):\n    return {'ok': True}\n",
        )
        write_learned_tool(tools_dir=tmp_path / ".ravn" / "learned_tools", artifact=artifact)
        write_learned_tool_artifact(
            artifacts_dir=tmp_path / ".ravn" / "learned_tool_artifacts",
            artifact=artifact,
        )

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_bulk_mode_loads_persisted_resident_agent_tools(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        settings.resident_evolution.learned_tool_injection_mode = "bulk"
        self._write_learned_artifact(tmp_path, "persisted_metric_window")

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
        )

        assert "persisted_metric_window" in {tool.name for tool in tools}

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_dispatch_mode_keeps_learned_tools_out_of_the_toolbox(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        """NIU-1118: the per-turn tool list must not grow with the catalog."""
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        for index in range(50):
            self._write_learned_artifact(tmp_path, f"persisted_metric_window_{index:02d}")

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
            permission=MagicMock(),
        )

        tool_names = {tool.name for tool in tools}
        assert not any(name.startswith("persisted_metric_window") for name in tool_names)
        assert "learned_tool_run" in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_dispatch_mode_tool_count_is_independent_of_catalog_size(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        def _build(workspace: Path) -> list:
            return _build_tools(
                settings,
                workspace,
                Session(),
                MagicMock(),
                None,
                None,
                no_tools=False,
                persona_config=None,
                permission=MagicMock(),
            )

        empty_workspace = tmp_path / "empty"
        empty_workspace.mkdir()
        loaded_workspace = tmp_path / "loaded"
        loaded_workspace.mkdir()
        for index in range(50):
            self._write_learned_artifact(loaded_workspace, f"tool_{index:02d}")

        assert [t.name for t in _build(empty_workspace)] == [
            t.name for t in _build(loaded_workspace)
        ]

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    async def test_learned_tool_is_discoverable_and_runnable_without_preloading(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        """NIU-1118 acceptance: a tool absent from the working set is found via
        capability_list and executed via learned_tool_run."""
        import json

        from ravn.adapters.permission.allow_deny import AllowAllPermission
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        # This unit exercises discovery/dispatch, not the OCI boundary. The
        # contained default is covered by the learned-tool runtime tests.
        settings.resident_evolution.learned_tool_execution_backend = "local"
        self._write_learned_artifact(tmp_path, "persisted_metric_window")

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
            permission=AllowAllPermission(),
        )
        by_name = {tool.name: tool for tool in tools}
        assert "persisted_metric_window" not in by_name

        listed = await by_name["capability_list"].execute({"kind": "tool", "tags": ["learned"]})
        assert not listed.is_error
        payload = json.loads(listed.content)
        assert payload["count"] == 1
        entry = payload["capabilities"][0]
        assert entry["tags"] == ["tool", "learned"]
        # Compact listing, but still enough to dispatch on: the listing tells the
        # resident how to run this without a second lookup.
        assert entry["invoke_via"] == "learned_tool_run"

        executed = await by_name["learned_tool_run"].execute(
            {"name": "persisted_metric_window", "input": {}}
        )
        assert not executed.is_error
        assert '"ok": true' in executed.content

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_learned_tool_run_skipped_without_permission_port(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
        )

        assert "learned_tool_run" not in {tool.name for tool in tools}

    def test_learned_tool_resolver_skipped_on_unknown_backend(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.tool_builders import _build_learned_tool_resolver

        # The Literal type blocks this via normal config loading; guard the
        # code path directly for settings objects mutated at runtime.
        object.__setattr__(settings.resident_evolution, "learned_tool_execution_backend", "qemu")

        assert _build_learned_tool_resolver(settings, tmp_path) is None

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_memory_tools_added_when_memory_present(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        mock_memory = MagicMock()
        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            mock_memory,
            None,
            no_tools=False,
            persona_config=None,
        )
        tool_names = {t.name for t in tools}
        assert "ravn_memory_search" in tool_names
        assert "session_search" in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_introspection_tools_present(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
        )
        tool_names = {t.name for t in tools}
        assert "ravn_state" in tool_names
        assert "ravn_reflect" in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_introspection_tools_can_be_disabled(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        settings.tools.disabled = ["ravn_state", "ravn_reflect"]
        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
        )
        tool_names = {t.name for t in tools}
        assert "ravn_state" not in tool_names
        assert "ravn_reflect" not in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_worker_profile_has_only_core_tools(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
            profile="worker",
        )
        tool_names = {t.name for t in tools}
        # Core tools should be present
        assert "read_file" in tool_names
        assert "bash" in tool_names
        # Extended tools should be absent in worker profile
        assert "ravn_state" not in tool_names
        assert "ravn_reflect" not in tool_names
        assert "web_search" not in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_default_profile_has_extended_tools(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
            profile="default",
        )
        tool_names = {t.name for t in tools}
        assert "ravn_state" in tool_names
        assert "ravn_reflect" in tool_names
        assert "web_search" in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_custom_profile_from_settings(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.config import ToolGroupConfig
        from ravn.domain.models import Session

        settings.tools.profiles["minimal"] = ToolGroupConfig(
            include_groups=["core"],
            include_mcp=False,
        )
        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
            profile="minimal",
        )
        tool_names = {t.name for t in tools}
        assert "read_file" in tool_names
        assert "ravn_state" not in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_unknown_profile_falls_back_to_default(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
            profile="nonexistent_profile",
        )
        # Falls back to default — should have extended tools
        tool_names = {t.name for t in tools}
        assert "ravn_state" in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_ravn_state_tool_names_populated_after_filtering(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
        )
        state_tool = next((t for t in tools if t.name == "ravn_state"), None)
        assert state_tool is not None
        expected_names = {t.name for t in tools}
        assert set(state_tool._tool_names) == expected_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_memory_tools_absent_when_memory_none(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
        )
        tool_names = {t.name for t in tools}
        assert "ravn_memory_search" not in tool_names
        assert "session_search" not in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_workflow_tools_absent_without_capability_sources(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.domain.models import Session

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
        )

        tool_names = {t.name for t in tools}
        assert "workflow_list" not in tool_names
        assert "workflow_launch" not in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_workflow_tools_added_from_capability_sources(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.config import CapabilitySourceConfig
        from ravn.domain.models import Session

        settings.environment.capability_sources = [
            CapabilitySourceConfig(
                adapter=("tests.test_ravn.test_workflow_tools.FakeWorkflowCapabilitySource")
            )
        ]

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
        )

        tool_names = {t.name for t in tools}
        assert {"workflow_list", "workflow_describe", "workflow_launch"}.issubset(tool_names)
        assert "capability_list" in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_workflow_tools_respect_exact_disabled_filter(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.cli.commands import _build_tools
        from ravn.config import CapabilitySourceConfig
        from ravn.domain.models import Session

        settings.environment.capability_sources = [
            CapabilitySourceConfig(
                adapter=("tests.test_ravn.test_workflow_tools.FakeWorkflowCapabilitySource")
            )
        ]
        settings.tools.disabled = ["workflow_launch"]

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=None,
        )

        tool_names = {t.name for t in tools}
        assert "workflow_list" in tool_names
        assert "workflow_launch" not in tool_names

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_valkyrie_persona_loads_capability_catalog_and_workflow_tools(
        self,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        from ravn.adapters.personas.loader import FilesystemPersonaAdapter
        from ravn.cli.commands import _build_tools
        from ravn.config import CapabilitySourceConfig
        from ravn.domain.models import Session

        settings.environment.type = "k8s"
        settings.environment.capability_sources = [
            CapabilitySourceConfig(
                adapter=("tests.test_ravn.test_workflow_tools.FakeWorkflowCapabilitySource")
            )
        ]
        persona = FilesystemPersonaAdapter().load("k8s-valkyrie")

        tools = _build_tools(
            settings,
            tmp_path,
            Session(),
            MagicMock(),
            None,
            None,
            no_tools=False,
            persona_config=persona,
        )

        tool_names = {t.name for t in tools}
        assert {"capability_list", "workflow_list", "workflow_launch"}.issubset(tool_names)


# ---------------------------------------------------------------------------
# _get_tool_group
# ---------------------------------------------------------------------------


class TestGetProfile:
    def test_default_profile_returned_for_default_name(self, settings: Settings) -> None:
        from ravn.cli.commands import _get_tool_group

        cfg = _get_tool_group(settings, "default")
        assert "core" in cfg.include_groups
        assert "extended" in cfg.include_groups
        assert cfg.include_mcp is True

    def test_worker_profile_returned_for_worker_name(self, settings: Settings) -> None:
        from ravn.cli.commands import _get_tool_group

        cfg = _get_tool_group(settings, "worker")
        assert "core" in cfg.include_groups
        assert "extended" not in cfg.include_groups
        assert cfg.include_mcp is False

    def test_custom_profile_overrides_builtin(self, settings: Settings) -> None:
        from ravn.cli.commands import _get_tool_group
        from ravn.config import ToolGroupConfig

        settings.tools.profiles["default"] = ToolGroupConfig(
            include_groups=["core"],
            include_mcp=False,
        )
        cfg = _get_tool_group(settings, "default")
        assert cfg.include_groups == ["core"]
        assert cfg.include_mcp is False

    def test_unknown_profile_falls_back_gracefully(self, settings: Settings) -> None:
        from ravn.cli.commands import _get_tool_group

        cfg = _get_tool_group(settings, "does_not_exist")
        # Falls back to default
        assert "extended" in cfg.include_groups


# ---------------------------------------------------------------------------
# _filter_tools
# ---------------------------------------------------------------------------


class TestFilterTools:
    def _make_tool(self, name: str) -> MagicMock:
        t = MagicMock()
        t.name = name
        return t

    def test_enabled_list_restricts(self, settings: Settings) -> None:
        from ravn.cli.commands import _filter_tools

        tools = [self._make_tool("a"), self._make_tool("b"), self._make_tool("c")]
        settings.tools.enabled = ["a", "c"]
        result = _filter_tools(tools, settings, None)
        assert [t.name for t in result] == ["a", "c"]

    def test_disabled_list_removes(self, settings: Settings) -> None:
        from ravn.cli.commands import _filter_tools

        tools = [self._make_tool("a"), self._make_tool("b"), self._make_tool("c")]
        settings.tools.disabled = ["b"]
        result = _filter_tools(tools, settings, None)
        assert [t.name for t in result] == ["a", "c"]

    def test_persona_forbidden_tools(self, settings: Settings) -> None:
        from ravn.cli.commands import _filter_tools

        tools = [self._make_tool("a"), self._make_tool("b"), self._make_tool("c")]
        persona = MagicMock(allowed_tools=None, forbidden_tools=["c"])
        result = _filter_tools(tools, settings, persona)
        assert [t.name for t in result] == ["a", "b"]

    def test_persona_allowed_tools(self, settings: Settings) -> None:
        from ravn.cli.commands import _filter_tools

        tools = [self._make_tool("a"), self._make_tool("b"), self._make_tool("c")]
        persona = MagicMock(allowed_tools=["a"], forbidden_tools=None)
        result = _filter_tools(tools, settings, persona)
        assert [t.name for t in result] == ["a"]

    def test_mimir_alias_expands_to_dynamic_tools(self, settings: Settings) -> None:
        from ravn.cli.commands import _filter_tools, _groups_for_persona

        tools = [
            self._make_tool("mimir_query"),
            self._make_tool("mimir_read_source"),
            self._make_tool("mimir_lint"),
            self._make_tool("web_fetch"),
        ]
        persona = MagicMock(allowed_tools=["mimir"], forbidden_tools=None)

        assert "mimir" in _groups_for_persona(persona)
        result = _filter_tools(tools, settings, persona)
        assert [t.name for t in result] == [
            "mimir_query",
            "mimir_read_source",
            "mimir_lint",
        ]

    def test_explicit_mimir_tools_enable_dynamic_group(self) -> None:
        from ravn.cli.commands import _groups_for_persona

        persona = MagicMock(
            allowed_tools=["mimir_query", "mimir_write", "web_search"],
            forbidden_tools=None,
        )

        groups = _groups_for_persona(persona)
        assert "mimir" in groups
        assert "extended" in groups

    def test_workflow_alias_expands_to_dynamic_tools(self, settings: Settings) -> None:
        from ravn.cli.commands import _filter_tools, _groups_for_persona

        tools = [
            self._make_tool("workflow_list"),
            self._make_tool("workflow_describe"),
            self._make_tool("workflow_launch"),
            self._make_tool("web_fetch"),
        ]
        persona = MagicMock(allowed_tools=["workflow"], forbidden_tools=None)

        assert "workflow" in _groups_for_persona(persona)
        result = _filter_tools(tools, settings, persona)
        assert [t.name for t in result] == [
            "workflow_list",
            "workflow_describe",
            "workflow_launch",
        ]


# ---------------------------------------------------------------------------
# _build_hooks
# ---------------------------------------------------------------------------


class TestBuildHooks:
    def test_empty_hooks_returns_empty_lists(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_hooks

        settings.hooks.pre_tool = []
        settings.hooks.post_tool = []
        pre, post = _build_hooks(settings)
        assert pre == []
        assert post == []

    def test_failed_hook_import_skipped(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_hooks
        from ravn.config import HookConfig

        settings.hooks.pre_tool = [
            HookConfig(adapter="nonexistent.module.Hook"),
        ]
        pre, post = _build_hooks(settings)
        assert pre == []


# ---------------------------------------------------------------------------
# _build_compressor & _build_prompt_builder
# ---------------------------------------------------------------------------


class TestBuildCompressor:
    @pytest.mark.usefixtures("_api_key")
    def test_returns_compressor(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_compressor
        from ravn.compression import ContextCompressor

        llm = MagicMock()
        compressor = _build_compressor(settings, llm)
        assert isinstance(compressor, ContextCompressor)


class TestBuildPromptBuilder:
    def test_returns_prompt_builder(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_prompt_builder
        from ravn.prompt_builder import PromptBuilder

        pb = _build_prompt_builder(settings)
        assert isinstance(pb, PromptBuilder)


# ---------------------------------------------------------------------------
# _build_agent (integration)
# ---------------------------------------------------------------------------


class TestBuildAgent:
    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_full_assembly(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_agent

        agent, channel = _build_agent(settings)
        assert agent is not None
        assert channel is not None
        # Verify tools are registered
        assert len(agent._tools) > 0
        # Verify session is set
        assert agent._session is not None

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_episode_max_chars_passed(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_agent

        settings.agent.episode_summary_max_chars = 999
        settings.agent.episode_task_max_chars = 111
        agent, _ = _build_agent(settings)
        assert agent._episode_summary_max_chars == 999
        assert agent._episode_task_max_chars == 111

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_no_tools_flag(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_agent

        agent, _ = _build_agent(settings, no_tools=True)
        assert len(agent._tools) == 0

    @pytest.mark.usefixtures("_api_key", "_mock_anthropic")
    def test_effective_model_used(self, settings: Settings) -> None:
        """Agent receives the model from effective_model(), not agent.model."""
        from ravn.cli.commands import _build_agent

        settings.llm.model = "claude-opus-4-6"
        agent, _ = _build_agent(settings)
        assert agent._model == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# Settings.effective_model / effective_max_tokens
# ---------------------------------------------------------------------------


class TestEffectiveModelResolution:
    def test_llm_model_takes_precedence(self) -> None:
        s = Settings()
        s.llm.model = "custom-model"
        s.agent.model = "old-model"
        assert s.effective_model() == "custom-model"

    def test_agent_model_backward_compat(self) -> None:
        s = Settings()
        # llm.model at default, agent.model explicitly set
        s.agent.model = "legacy-model"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert s.effective_model() == "legacy-model"

    def test_default_when_both_at_default(self) -> None:
        s = Settings()
        assert s.effective_model() == "claude-sonnet-4-6"

    def test_effective_max_tokens_from_llm(self) -> None:
        s = Settings()
        s.llm.max_tokens = 4096
        assert s.effective_max_tokens() == 4096

    def test_effective_max_tokens_backward_compat(self) -> None:
        s = Settings()
        s.agent.max_tokens = 2048
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert s.effective_max_tokens() == 2048


# ---------------------------------------------------------------------------
# _build_mimir — adapter factory (NIU-616)
# ---------------------------------------------------------------------------


class TestBuildMimir:
    def test_disabled_returns_none(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_mimir

        settings.mimir.enabled = False
        result = _build_mimir(settings)
        assert result is None

    def test_single_local_instance_returns_markdown_adapter(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        from mimir.adapters.markdown import MarkdownMimirAdapter
        from ravn.cli.commands import _build_mimir

        settings.mimir.enabled = True
        settings.mimir.instances = []
        settings.mimir.path = str(tmp_path / "mimir")
        result = _build_mimir(settings)
        assert isinstance(result, MarkdownMimirAdapter)

    def test_instance_with_path_returns_markdown_adapter(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        from mimir.adapters.markdown import MarkdownMimirAdapter
        from ravn.adapters.mimir.composite import CompositeMimirAdapter
        from ravn.cli.commands import _build_mimir
        from ravn.config import MimirInstanceConfig

        settings.mimir.enabled = True
        settings.mimir.instances = [
            MimirInstanceConfig(
                name="local",
                role="local",
                path=str(tmp_path / "mimir"),
            )
        ]
        result = _build_mimir(settings)
        assert isinstance(result, CompositeMimirAdapter)
        # The single mount should be a MarkdownMimirAdapter
        assert isinstance(result._mounts[0].port, MarkdownMimirAdapter)

    def test_instance_with_url_returns_http_adapter(self, settings: Settings) -> None:
        from ravn.adapters.mimir.composite import CompositeMimirAdapter
        from ravn.adapters.mimir.http import HttpMimirAdapter
        from ravn.cli.commands import _build_mimir
        from ravn.config import MimirInstanceConfig

        settings.mimir.enabled = True
        settings.mimir.instances = [
            MimirInstanceConfig(
                name="hosted",
                role="shared",
                url="https://mimir.niuu.internal",
            )
        ]
        result = _build_mimir(settings)
        assert isinstance(result, CompositeMimirAdapter)
        assert isinstance(result._mounts[0].port, HttpMimirAdapter)

    def test_mixed_instances_returns_composite(self, settings: Settings, tmp_path: Path) -> None:
        from mimir.adapters.markdown import MarkdownMimirAdapter
        from ravn.adapters.mimir.composite import CompositeMimirAdapter
        from ravn.adapters.mimir.http import HttpMimirAdapter
        from ravn.cli.commands import _build_mimir
        from ravn.config import MimirInstanceConfig

        settings.mimir.enabled = True
        settings.mimir.instances = [
            MimirInstanceConfig(
                name="local",
                role="local",
                path=str(tmp_path / "local"),
            ),
            MimirInstanceConfig(
                name="hosted",
                role="shared",
                url="https://mimir.niuu.internal",
            ),
        ]
        result = _build_mimir(settings)
        assert isinstance(result, CompositeMimirAdapter)
        assert len(result._mounts) == 2
        port_types = {m.name: type(m.port) for m in result._mounts}
        assert port_types["local"] is MarkdownMimirAdapter
        assert port_types["hosted"] is HttpMimirAdapter

    def test_instance_without_path_or_url_skipped(self, settings: Settings, tmp_path: Path) -> None:
        from mimir.adapters.markdown import MarkdownMimirAdapter
        from ravn.adapters.mimir.composite import CompositeMimirAdapter
        from ravn.cli.commands import _build_mimir
        from ravn.config import MimirInstanceConfig

        settings.mimir.enabled = True
        settings.mimir.instances = [
            MimirInstanceConfig(name="bad", role="local"),  # neither path nor url
            MimirInstanceConfig(
                name="local",
                role="local",
                path=str(tmp_path / "local"),
            ),
        ]
        result = _build_mimir(settings)
        assert isinstance(result, CompositeMimirAdapter)
        assert len(result._mounts) == 1
        assert isinstance(result._mounts[0].port, MarkdownMimirAdapter)

    def test_all_instances_invalid_returns_none(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_mimir
        from ravn.config import MimirInstanceConfig

        settings.mimir.enabled = True
        settings.mimir.instances = [
            MimirInstanceConfig(name="bad", role="local"),  # neither path nor url
        ]
        result = _build_mimir(settings)
        assert result is None

    def test_write_routing_applied_to_composite(self, settings: Settings, tmp_path: Path) -> None:
        from ravn.adapters.mimir.composite import CompositeMimirAdapter
        from ravn.cli.commands import _build_mimir
        from ravn.config import MimirInstanceConfig, MimirWriteRoutingConfig

        settings.mimir.enabled = True
        settings.mimir.instances = [
            MimirInstanceConfig(
                name="local",
                role="local",
                path=str(tmp_path / "local"),
            ),
            MimirInstanceConfig(
                name="hosted",
                role="shared",
                url="https://mimir.niuu.internal",
            ),
        ]
        settings.mimir.write_routing = MimirWriteRoutingConfig(
            rules=[
                {"prefix": "self/", "mounts": ["local"]},
                {"prefix": "project/", "mounts": ["hosted"]},
            ],
            default=["local"],
        )
        result = _build_mimir(settings)
        assert isinstance(result, CompositeMimirAdapter)
        # Routing: self/ → local, project/ → hosted, default → local
        assert result._write_routing.resolve("self/notes.md") == ["local"]
        assert result._write_routing.resolve("project/arch.md") == ["hosted"]
        assert result._write_routing.resolve("other/page.md") == ["local"]


class TestBuildMimirAuthWorkloadDefaults:
    """Workload Mímir auth threads gateway.platform config (config-first rule)."""

    def test_workload_auth_falls_back_to_platform_config(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_mimir_auth
        from ravn.config import MimirAuthConfig

        settings.gateway.platform.enabled = True
        settings.gateway.platform.workload_token_file = "/var/run/secrets/niuu-workload/token"
        settings.gateway.platform.workload_exchange_url = "http://volundr.svc/exchange"

        auth = _build_mimir_auth(settings, MimirAuthConfig(type="workload"))
        assert auth.type == "workload"
        assert auth.token_file == "/var/run/secrets/niuu-workload/token"
        assert auth.exchange_url == "http://volundr.svc/exchange"

    def test_workload_auth_derives_exchange_url_from_platform_base_url(
        self, settings: Settings
    ) -> None:
        from ravn.cli.commands import _build_mimir_auth
        from ravn.config import MimirAuthConfig

        settings.gateway.platform.enabled = True
        settings.gateway.platform.base_url = "http://volundr.svc/"
        settings.gateway.platform.workload_exchange_url = ""

        auth = _build_mimir_auth(settings, MimirAuthConfig(type="workload"))
        assert auth.exchange_url == "http://volundr.svc/api/v1/tokens/workload/exchange"

    def test_workload_auth_explicit_instance_values_win(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_mimir_auth
        from ravn.config import MimirAuthConfig

        settings.gateway.platform.enabled = True
        settings.gateway.platform.workload_token_file = "/platform/token"
        settings.gateway.platform.workload_exchange_url = "http://platform/exchange"

        auth = _build_mimir_auth(
            settings,
            MimirAuthConfig(
                type="workload",
                token_file="/instance/token",
                exchange_url="http://instance/exchange",
            ),
        )
        assert auth.token_file == "/instance/token"
        assert auth.exchange_url == "http://instance/exchange"

    def test_workload_auth_platform_disabled_keeps_none(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_mimir_auth
        from ravn.config import MimirAuthConfig

        settings.gateway.platform.enabled = False
        auth = _build_mimir_auth(settings, MimirAuthConfig(type="workload"))
        assert auth.token_file is None
        assert auth.exchange_url is None

    def test_bearer_auth_is_not_threaded(self, settings: Settings) -> None:
        from ravn.cli.commands import _build_mimir_auth
        from ravn.config import MimirAuthConfig

        settings.gateway.platform.enabled = True
        settings.gateway.platform.workload_exchange_url = "http://platform/exchange"

        auth = _build_mimir_auth(settings, MimirAuthConfig(type="bearer", token="tok"))
        assert auth.type == "bearer"
        assert auth.token == "tok"
        assert auth.exchange_url is None

"""Tests for Ravn CLI commands."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from ravn.adapters.personas.loader import PersonaConfig, PersonaExecutorConfig
from ravn.cli.commands import (
    _apply_profile,
    _build_executor,
    _build_iteration_budget,
    _chat,
    _dedupe_preserve_order,
    _derive_capabilities,
    _join_mode_to_fan_in,
    _mimir_ingest_event_fields_from_mcp_result,
    _mimir_mount_name_from_mcp_server_name,
    _mimir_write_event_fields_from_mcp_result,
    _parse_deployment_kwargs,
    _print_usage,
    _resolve_persona_model,
    _run_daemon,
    _run_turn,
    _split_workflow_edge_label,
    _stage_personas,
    _uses_cli_transport_executor,
    _uses_cli_transport_runtime,
    _wire_cron,
    _workflow_allowed_outcome_topics,
    _workflow_allowed_task_targets,
    _workflow_event_matches_filters,
    _workflow_graph,
    _workflow_runtime_for_persona,
    _workflow_stage_context,
    app,
    main,
)
from ravn.cli.flock import NodeDef, _write_node_config
from ravn.config import Settings
from ravn.domain.models import (
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolResult,
    TurnResult,
)
from ravn.domain.profile import RavnProfile
from ravn.ports.warden_deployer import WardenDeploymentError, WardenDeploymentResult
from ravn.warden import WardenSpec, WardenStore
from ravn.warden.artifacts import service_label, start_command, write_runtime_config
from ravn.warden.models import WardenSupervisor

runner = CliRunner()


class FakeWardenDeployer:
    def __init__(self, *, fail_on: str = "") -> None:
        self._fail_on = fail_on

    def install(self, spec: WardenSpec, *, warden_dir: Path, workspace_root: Path | None = None):
        if self._fail_on == "install":
            raise WardenDeploymentError("install failed")
        config_path = write_runtime_config(
            spec,
            warden_dir=warden_dir,
            workspace_root=workspace_root,
        )
        service_path = warden_dir / "warden.plist"
        service_path.write_text("service", encoding="utf-8")
        return WardenDeploymentResult(
            supervisor=WardenSupervisor(
                installed=True,
                service_label=service_label(spec.id),
                service_file=str(service_path),
                config_file=str(config_path),
                start_command=start_command(spec, config_path=config_path),
                last_install_at=datetime.now(UTC),
            ),
            runtime_state="idle",
        )

    def start(self, spec: WardenSpec, *, warden_dir: Path):
        if self._fail_on == "start":
            raise WardenDeploymentError("start failed")
        return WardenDeploymentResult(
            supervisor=spec.supervisor,
            runtime_state="active",
        )

    def stop(self, spec: WardenSpec, *, warden_dir: Path):
        if self._fail_on == "stop":
            raise WardenDeploymentError("stop failed")
        return WardenDeploymentResult(
            supervisor=spec.supervisor,
            runtime_state="idle",
        )

    def uninstall(self, spec: WardenSpec, *, warden_dir: Path):
        if self._fail_on == "uninstall":
            raise WardenDeploymentError("uninstall failed")
        return WardenDeploymentResult(
            supervisor=WardenSupervisor(),
            runtime_state="offline",
        )


def _build_test_warden_store(tmp_path: Path, *, fail_on: str = "") -> WardenStore:
    return WardenStore(
        root=tmp_path,
        deployer_factory=lambda spec: FakeWardenDeployer(fail_on=fail_on),
    )


class TestFlockNodeConfig:
    def test_default_node_config_uses_available_vllm_model(self, tmp_path: Path) -> None:
        flock_dir = tmp_path / ".flock"
        node = NodeDef(
            index=1,
            persona="reviewer",
            peer_id="flock-reviewer",
            pub_port=7482,
            rep_port=7483,
            handshake_port=7582,
            gateway_port=7682,
            config_path=str(flock_dir / "node-reviewer.yaml"),
            log_path=str(flock_dir / "reviewer.log"),
        )

        _write_node_config(
            node,
            flock_dir,
            discovery="static",
            mesh_transport="ipc",
            http_gateway_enabled=False,
        )

        config = (flock_dir / "node-reviewer.yaml").read_text(encoding="utf-8")
        assert "model: Qwen/Qwen3.6-35B-A3B-FP8" in config


class TestPrintUsage:
    def test_basic(self, capsys) -> None:
        _print_usage(TokenUsage(input_tokens=10, output_tokens=5))
        # Just verify it doesn't crash — the output goes through typer.echo.

    def test_with_cache_tokens(self) -> None:
        # Just verify no exception.
        _print_usage(
            TokenUsage(
                input_tokens=10,
                output_tokens=5,
                cache_read_tokens=100,
                cache_write_tokens=200,
            )
        )


class TestMcpMimirIngestEventFields:
    def test_parses_fields_from_mount_server_result(self) -> None:
        result = ToolResult(
            tool_call_id="",
            content=json.dumps(
                {
                    "source_id": "src_123",
                    "title": "NIU-901 postmortem",
                    "source_type": "document",
                    "pages_updated": ["wiki/postmortems/niu-901.md"],
                }
            ),
            is_error=False,
        )

        fields = _mimir_ingest_event_fields_from_mcp_result(
            server_name="mimir-tmp-mimir-test",
            arguments={},
            result=result,
        )

        assert fields == {
            "source_id": "src_123",
            "source_title": "NIU-901 postmortem",
            "source_type": "document",
            "page_paths": ["wiki/postmortems/niu-901.md"],
            "mcp_server_name": "mimir-tmp-mimir-test",
            "mount_name": "tmp-mimir-test",
            "mount_names": ["tmp-mimir-test"],
        }

    def test_falls_back_to_arguments_when_result_is_sparse(self) -> None:
        result = ToolResult(
            tool_call_id="",
            content=json.dumps({"source_id": "src_abc", "pages_updated": []}),
            is_error=False,
        )

        fields = _mimir_ingest_event_fields_from_mcp_result(
            server_name="mimir-tmp-mimir-test",
            arguments={"title": "Fallback title", "source_type": "conversation"},
            result=result,
        )

        assert fields is not None
        assert fields["source_title"] == "Fallback title"
        assert fields["source_type"] == "conversation"

    def test_returns_none_for_error_invalid_json_or_missing_source_id(self) -> None:
        assert (
            _mimir_ingest_event_fields_from_mcp_result(
                server_name="mimir-test",
                arguments={},
                result=ToolResult(tool_call_id="", content="{}", is_error=True),
            )
            is None
        )
        assert (
            _mimir_ingest_event_fields_from_mcp_result(
                server_name="mimir-test",
                arguments={},
                result=ToolResult(tool_call_id="", content="not-json", is_error=False),
            )
            is None
        )
        assert (
            _mimir_ingest_event_fields_from_mcp_result(
                server_name="mimir-test",
                arguments={},
                result=ToolResult(
                    tool_call_id="",
                    content=json.dumps({"title": "x"}),
                    is_error=False,
                ),
            )
            is None
        )


class TestMcpMimirWriteEventFields:
    def test_parses_page_path_and_mount_from_arguments(self) -> None:
        result = ToolResult(
            tool_call_id="",
            content="Page written: council/example/opinion-b.md",
            is_error=False,
        )

        fields = _mimir_write_event_fields_from_mcp_result(
            server_name="mimir-council-scratch-board",
            arguments={"path": "council/example/opinion-b.md"},
            result=result,
        )

        assert fields == {
            "page_path": "council/example/opinion-b.md",
            "mcp_server_name": "mimir-council-scratch-board",
            "mount_name": "council-scratch-board",
            "mount_names": ["council-scratch-board"],
        }

    def test_prefers_explicit_mimir_argument(self) -> None:
        result = ToolResult(
            tool_call_id="",
            content="Page written: research/example.md (routed to: tmp-mimir-test)",
            is_error=False,
        )

        fields = _mimir_write_event_fields_from_mcp_result(
            server_name="mimir-council-scratch-board",
            arguments={"path": "research/example.md", "mimir": "tmp-mimir-test"},
            result=result,
        )

        assert fields is not None
        assert fields["page_path"] == "research/example.md"
        assert fields["mount_name"] == "tmp-mimir-test"
        assert fields["mount_names"] == ["tmp-mimir-test"]

    def test_write_fields_return_none_when_page_path_cannot_be_resolved(self) -> None:
        assert (
            _mimir_write_event_fields_from_mcp_result(
                server_name="mimir-council-scratch-board",
                arguments={},
                result=ToolResult(tool_call_id="", content="", is_error=False),
            )
            is None
        )
        assert (
            _mimir_write_event_fields_from_mcp_result(
                server_name="mimir-council-scratch-board",
                arguments={"path": "research/example.md"},
                result=ToolResult(tool_call_id="", content="ignored", is_error=True),
            )
            is None
        )


class TestCliTransportHelpers:
    def test_parse_deployment_kwargs_parses_yaml_scalars(self) -> None:
        assert _parse_deployment_kwargs(["auto_commit=true", "replicas=2", "name=ravn-dev"]) == {
            "auto_commit": True,
            "replicas": 2,
            "name": "ravn-dev",
        }

    def test_parse_deployment_kwargs_rejects_invalid_entries(self) -> None:
        with pytest.raises(Exception):
            _parse_deployment_kwargs(["missing-separator"])

    def test_mimir_mount_name_from_server_name(self) -> None:
        assert _mimir_mount_name_from_mcp_server_name("mimir-council-scratch-board") == (
            "council-scratch-board"
        )
        assert _mimir_mount_name_from_mcp_server_name("not-mimir") is None

    def test_uses_cli_transport_runtime_from_environment(self) -> None:
        with patch.dict(os.environ, {"SKULD__TRANSPORT_ADAPTER": "adapter.path"}, clear=False):
            assert _uses_cli_transport_runtime() is True
        with patch.dict(os.environ, {"SKULD__TRANSPORT_ADAPTER": ""}, clear=False):
            assert _uses_cli_transport_runtime() is False

    def test_build_executor_passes_codex_ws_yolo_transport_kwargs(self) -> None:
        env = {
            "SKULD__TRANSPORT_ADAPTER": "skuld.transports.codex_ws.CodexWebSocketTransport",
            "SKULD__SKIP_PERMISSIONS": "true",
            "SKULD__APPROVAL_POLICY": "never",
            "SKULD__SANDBOX": "danger-full-access",
        }
        with patch.dict(os.environ, env, clear=False):
            executor = _build_executor()

        assert executor._transport_adapter == "skuld.transports.codex_ws.CodexWebSocketTransport"
        assert executor._transport_kwargs == {
            "skip_permissions": True,
            "approval_policy": "never",
            "sandbox": "danger-full-access",
        }

    def test_build_executor_merges_runtime_controls_into_persona_transport(self) -> None:
        persona = PersonaConfig(
            name="product-steward",
            system_prompt_template="hi",
            executor=PersonaExecutorConfig(
                adapter="ravn.adapters.executors.cli.CliTransportExecutor",
                kwargs={
                    "transport_adapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "transport_kwargs": {"sandbox": "workspace-write"},
                },
            ),
        )
        env = {
            "SKULD__SKIP_PERMISSIONS": "true",
            "SKULD__APPROVAL_POLICY": "never",
            "SKULD__SANDBOX": "danger-full-access",
        }

        with patch.dict(os.environ, env, clear=False):
            executor = _build_executor(persona)

        assert executor._transport_kwargs == {
            "skip_permissions": True,
            "approval_policy": "never",
            "sandbox": "workspace-write",
        }

    def test_uses_cli_transport_executor_prefers_persona_executor(self) -> None:
        persona = PersonaConfig(
            name="coder",
            system_prompt_template="hi",
            executor=PersonaExecutorConfig(
                adapter="ravn.adapters.executors.cli.CliTransportExecutor"
            ),
        )
        assert _uses_cli_transport_executor(persona) is True

        non_cli = PersonaConfig(
            name="coder",
            system_prompt_template="hi",
            executor=PersonaExecutorConfig(
                adapter="ravn.adapters.executors.default.DefaultExecutor"
            ),
        )
        assert _uses_cli_transport_executor(non_cli) is False

    def test_apply_profile_preserves_zero_iteration_budget_as_unbounded(self) -> None:
        settings = Settings()
        persona = PersonaConfig(name="product-steward", iteration_budget=0)

        _system_prompt, max_iterations, _max_tokens = _apply_profile(
            RavnProfile(name="default"), settings, persona_config=persona
        )

        assert max_iterations == 0

    def test_build_iteration_budget_disabled_for_unbounded_agent(self) -> None:
        settings = Settings()

        assert _build_iteration_budget(settings, 0) is None

    def test_resolve_persona_model_prefers_primary_alias(self) -> None:
        settings = Settings()
        persona = PersonaConfig(name="product-steward")
        persona.llm.primary_alias = "gpt-5.5"

        assert _resolve_persona_model(settings, persona) == "gpt-5.5"


class TestRunCommand:
    def test_no_api_key_exits(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os

            env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            with patch.dict("os.environ", env_without_key, clear=True):
                result = runner.invoke(app, ["run", "hi"])
                assert result.exit_code != 0

    def test_single_turn_with_mocked_agent(self) -> None:
        """Test that run_turn is called with the given prompt."""

        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="Hello!")
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            )

        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_adapter_cls,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_adapter = MagicMock()
            mock_adapter.stream = _stream
            mock_adapter_cls.return_value = mock_adapter

            result = runner.invoke(app, ["run", "Hello, Ravn!"])
            assert result.exit_code == 0

    def test_single_turn_with_show_usage(self) -> None:
        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="ok")
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=5, output_tokens=3),
            )

        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_adapter_cls,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_adapter = MagicMock()
            mock_adapter.stream = _stream
            mock_adapter_cls.return_value = mock_adapter

            result = runner.invoke(app, ["run", "Hello!", "--show-usage"])
            assert result.exit_code == 0
            assert "tokens" in result.output

    def test_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "ravn" in result.output.lower()

    def test_no_tools_flag(self) -> None:
        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="ok")
            yield StreamEvent(
                type=StreamEventType.MESSAGE_DONE,
                usage=TokenUsage(input_tokens=5, output_tokens=3),
            )

        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_adapter_cls,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_adapter = MagicMock()
            mock_adapter.stream = _stream
            mock_adapter_cls.return_value = mock_adapter

            result = runner.invoke(app, ["run", "Hello!", "--no-tools"])
            assert result.exit_code == 0


class TestRunTurnErrorHandling:
    def test_exception_exits_nonzero(self) -> None:
        async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
            raise RuntimeError("boom")
            yield  # make it a generator

        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_adapter_cls,
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_adapter = MagicMock()
            mock_adapter.stream = _stream
            mock_adapter_cls.return_value = mock_adapter

            result = runner.invoke(app, ["run", "Hello!"])
            assert result.exit_code != 0 or "error" in result.output.lower()

    async def test_repl_continues_after_error(self) -> None:
        """In REPL mode (single_turn=False), an error prints but does not exit."""
        import io
        from unittest.mock import AsyncMock

        from ravn.adapters.cli_channel import CliChannel

        agent = MagicMock()
        agent.run_turn = AsyncMock(side_effect=RuntimeError("boom"))
        channel = CliChannel(file=io.StringIO())

        # Must not raise SystemExit
        await _run_turn(agent, channel, "hi", show_usage=False, single_turn=False)

    async def test_single_turn_exception_calls_sys_exit(self) -> None:
        """In single_turn mode, an exception causes sys.exit(1)."""
        import io
        import sys
        from unittest.mock import AsyncMock, patch

        from ravn.adapters.cli_channel import CliChannel

        agent = MagicMock()
        agent.run_turn = AsyncMock(side_effect=RuntimeError("fatal"))
        channel = CliChannel(file=io.StringIO())

        with (
            patch("typer.echo"),
            patch.object(sys, "exit") as mock_exit,
        ):
            await _run_turn(agent, channel, "hello", show_usage=False, single_turn=True)
        mock_exit.assert_called_once_with(1)


class TestConfigFlag:
    def test_config_flag_sets_env_var(self, tmp_path) -> None:
        """--config sets RAVN_CONFIG before constructing Settings."""
        cfg = tmp_path / "custom.yaml"
        cfg.write_text("agent:\n  model: claude-custom\n")

        with (
            patch("ravn.adapters.llm.anthropic.AnthropicAdapter") as mock_cls,
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            mock_adapter = MagicMock()

            async def _stream(*args, **kwargs) -> AsyncIterator[StreamEvent]:
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="ok")
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_DONE,
                    usage=TokenUsage(input_tokens=5, output_tokens=3),
                )

            mock_adapter.stream = _stream
            mock_cls.return_value = mock_adapter

            result = runner.invoke(app, ["run", "Hello!", "--config", str(cfg)])

        assert result.exit_code == 0


class TestWardenCommands:
    def test_warden_list_empty(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            result = runner.invoke(app, ["warden", "list"])

        assert result.exit_code == 0
        assert "No wardens found." in result.output

    def test_warden_create_and_show(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            create_result = runner.invoke(
                app,
                [
                    "warden",
                    "create",
                    "Research Warden",
                    "--persona",
                    "research-and-distill",
                    "--mount",
                    "local",
                    "--mount",
                    "shared",
                    "--write-mount",
                    "local",
                    "--autostart",
                ],
            )
            show_result = runner.invoke(app, ["warden", "show", "research-warden"])

        assert create_result.exit_code == 0
        assert "Created warden research-warden" in create_result.output
        assert show_result.exit_code == 0
        assert "name: Research Warden" in show_result.output
        assert "persona: research-and-distill" in show_result.output
        assert "mounts: local, shared" in show_result.output

    def test_warden_create_parses_deployment_args(self, tmp_path: Path) -> None:
        store = _build_test_warden_store(tmp_path)
        with patch("ravn.warden.build_warden_store", return_value=store):
            result = runner.invoke(
                app,
                [
                    "warden",
                    "create",
                    "Cluster Warden",
                    "--deployment",
                    "k8s-gitops",
                    "--deployment-arg",
                    "repo_path=/tmp/gitops",
                    "--deployment-arg",
                    "auto_commit=true",
                    "--deployment-arg",
                    "namespace=ravn-dev",
                ],
            )

        assert result.exit_code == 0
        created = store.get("cluster-warden")
        assert created is not None
        assert created.deployment == "k8s-gitops"
        assert created.deployment_kwargs["repo_path"] == "/tmp/gitops"
        assert created.deployment_kwargs["auto_commit"] is True
        assert created.deployment_kwargs["namespace"] == "ravn-dev"

    def test_warden_list_json(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            runner.invoke(app, ["warden", "create", "Research Warden"])
            result = runner.invoke(app, ["warden", "list", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload[0]["id"] == "research-warden"

    def test_warden_list_nonempty_renders_summary(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            runner.invoke(
                app,
                [
                    "warden",
                    "create",
                    "Research Warden",
                    "--persona",
                    "mimir-warden",
                    "--mount",
                    "local",
                    "--write-mount",
                    "local",
                ],
            )
            result = runner.invoke(app, ["warden", "list"])

        assert result.exit_code == 0
        assert (
            "research-warden  persona=mimir-warden  write_mount=local  mounts=local"
            in result.output
        )

    def test_warden_show_json(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            runner.invoke(app, ["warden", "create", "Research Warden"])
            result = runner.invoke(app, ["warden", "show", "research-warden", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["id"] == "research-warden"
        assert payload["name"] == "Research Warden"

    def test_warden_create_json(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            result = runner.invoke(app, ["warden", "create", "Research Warden", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["id"] == "research-warden"

    def test_warden_show_missing_exits_nonzero(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            result = runner.invoke(app, ["warden", "show", "missing"])

        assert result.exit_code == 1
        assert "Warden not found: missing" in result.output

    def test_warden_create_rejects_invalid_deployment_args(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            result = runner.invoke(
                app,
                [
                    "warden",
                    "create",
                    "Broken Warden",
                    "--deployment-arg",
                    "not-a-pair",
                ],
            )

        assert result.exit_code != 0
        assert "expected key=value" in result.output

    def test_warden_install_and_start(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            runner.invoke(app, ["warden", "create", "Research Warden"])
            install_result = runner.invoke(app, ["warden", "install", "research-warden"])
            show_result = runner.invoke(app, ["warden", "show", "research-warden"])
            start_result = runner.invoke(app, ["warden", "start", "research-warden"])

        assert install_result.exit_code == 0
        assert "Installed warden research-warden" in install_result.output
        assert "installed: true" in show_result.output
        assert "state: idle" in show_result.output
        assert start_result.exit_code == 0
        assert "Started warden research-warden" in start_result.output

    def test_warden_install_start_stop_uninstall_json(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            runner.invoke(app, ["warden", "create", "Research Warden"])
            install_result = runner.invoke(app, ["warden", "install", "research-warden", "--json"])
            start_result = runner.invoke(app, ["warden", "start", "research-warden", "--json"])
            stop_result = runner.invoke(app, ["warden", "stop", "research-warden", "--json"])
            uninstall_result = runner.invoke(
                app, ["warden", "uninstall", "research-warden", "--json"]
            )

        assert json.loads(install_result.output)["supervisor"]["installed"] is True
        assert json.loads(start_result.output)["runtime"]["state"] == "active"
        assert json.loads(stop_result.output)["runtime"]["state"] == "idle"
        assert json.loads(uninstall_result.output)["runtime"]["state"] == "offline"

    def test_warden_stop_and_uninstall(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            runner.invoke(app, ["warden", "create", "Research Warden"])
            runner.invoke(app, ["warden", "install", "research-warden"])
            runner.invoke(app, ["warden", "start", "research-warden"])
            stop_result = runner.invoke(app, ["warden", "stop", "research-warden"])
            uninstall_result = runner.invoke(app, ["warden", "uninstall", "research-warden"])

        assert stop_result.exit_code == 0
        assert "Stopped warden research-warden" in stop_result.output
        assert uninstall_result.exit_code == 0
        assert "Uninstalled warden research-warden" in uninstall_result.output

    def test_warden_start_requires_install(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            runner.invoke(app, ["warden", "create", "Research Warden"])
            result = runner.invoke(app, ["warden", "start", "research-warden"])

        assert result.exit_code == 1
        assert "must be installed before it can be started" in result.output

    def test_warden_stop_requires_install(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            runner.invoke(app, ["warden", "create", "Research Warden"])
            result = runner.invoke(app, ["warden", "stop", "research-warden"])

        assert result.exit_code == 1
        assert "must be installed before it can be stopped" in result.output

    def test_warden_install_reports_deployer_failures(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path, fail_on="install"),
        ):
            runner.invoke(app, ["warden", "create", "Research Warden"])
            result = runner.invoke(app, ["warden", "install", "research-warden"])

        assert result.exit_code == 1
        assert "Failed to install warden research-warden: install failed" in result.output

    def test_warden_start_reports_deployer_failures(self, tmp_path: Path) -> None:
        install_store = _build_test_warden_store(tmp_path)
        failing_store = _build_test_warden_store(tmp_path, fail_on="start")
        with patch("ravn.warden.build_warden_store", return_value=install_store):
            runner.invoke(app, ["warden", "create", "Research Warden"])
            runner.invoke(app, ["warden", "install", "research-warden"])
        with patch("ravn.warden.build_warden_store", return_value=failing_store):
            result = runner.invoke(app, ["warden", "start", "research-warden"])

        assert result.exit_code == 1
        assert "Failed to start warden research-warden: start failed" in result.output

    def test_warden_stop_reports_deployer_failures(self, tmp_path: Path) -> None:
        install_store = _build_test_warden_store(tmp_path)
        failing_store = _build_test_warden_store(tmp_path, fail_on="stop")
        with patch("ravn.warden.build_warden_store", return_value=install_store):
            runner.invoke(app, ["warden", "create", "Research Warden"])
            runner.invoke(app, ["warden", "install", "research-warden"])
        with patch("ravn.warden.build_warden_store", return_value=failing_store):
            result = runner.invoke(app, ["warden", "stop", "research-warden"])

        assert result.exit_code == 1
        assert "Failed to stop warden research-warden: stop failed" in result.output

    def test_warden_uninstall_reports_deployer_failures(self, tmp_path: Path) -> None:
        install_store = _build_test_warden_store(tmp_path)
        failing_store = _build_test_warden_store(tmp_path, fail_on="uninstall")
        with patch("ravn.warden.build_warden_store", return_value=install_store):
            runner.invoke(app, ["warden", "create", "Research Warden"])
        with patch("ravn.warden.build_warden_store", return_value=failing_store):
            result = runner.invoke(app, ["warden", "uninstall", "research-warden"])

        assert result.exit_code == 1
        assert "Failed to uninstall warden research-warden: uninstall failed" in result.output

    def test_warden_install_and_uninstall_missing_exit_nonzero(self, tmp_path: Path) -> None:
        with patch(
            "ravn.warden.build_warden_store",
            return_value=_build_test_warden_store(tmp_path),
        ):
            install_result = runner.invoke(app, ["warden", "install", "missing"])
            uninstall_result = runner.invoke(app, ["warden", "uninstall", "missing"])

        assert install_result.exit_code == 1
        assert "Warden not found: missing" in install_result.output
        assert uninstall_result.exit_code == 1
        assert "Warden not found: missing" in uninstall_result.output


class TestReplMode:
    async def test_repl_exits_on_eoferror(self) -> None:
        """EOFError (Ctrl+D) exits the REPL loop cleanly."""
        import io
        from unittest.mock import AsyncMock

        from ravn.adapters.cli_channel import CliChannel

        agent = MagicMock()
        agent.run_turn = AsyncMock(
            return_value=TurnResult(
                response="hi",
                tool_calls=[],
                tool_results=[],
                usage=TokenUsage(input_tokens=5, output_tokens=3),
            )
        )
        channel = CliChannel(file=io.StringIO())

        with patch("builtins.input", side_effect=EOFError):
            await _chat(agent, channel, settings=Settings(), prompt="", show_usage=False)

    async def test_repl_skips_empty_input(self) -> None:
        """Empty lines in REPL are skipped without calling run_turn."""
        import io
        from unittest.mock import AsyncMock

        from ravn.adapters.cli_channel import CliChannel

        agent = MagicMock()
        agent.run_turn = AsyncMock(
            return_value=TurnResult(
                response="hi",
                tool_calls=[],
                tool_results=[],
                usage=TokenUsage(input_tokens=5, output_tokens=3),
            )
        )
        channel = CliChannel(file=io.StringIO())

        with patch("builtins.input", side_effect=["", EOFError]):
            await _chat(agent, channel, settings=Settings(), prompt="", show_usage=False)

        agent.run_turn.assert_not_called()

    async def test_repl_processes_message_then_exits(self) -> None:
        """A single message is processed before EOFError exits the REPL."""
        import io
        from unittest.mock import AsyncMock

        from ravn.adapters.cli_channel import CliChannel

        agent = MagicMock()
        agent.run_turn = AsyncMock(
            return_value=TurnResult(
                response="Hello!",
                tool_calls=[],
                tool_results=[],
                usage=TokenUsage(input_tokens=5, output_tokens=3),
            )
        )
        channel = CliChannel(file=io.StringIO())

        with patch("builtins.input", side_effect=["hi there", EOFError]):
            await _chat(agent, channel, settings=Settings(), prompt="", show_usage=False)

        agent.run_turn.assert_called_once_with("hi there")


class TestMainEntryPoint:
    def test_main_invokes_app(self) -> None:
        """main() is the package entry point — it delegates to the Typer app."""
        with patch("ravn.cli.commands.app") as mock_app:
            main()
            mock_app.assert_called_once()


class TestWorkflowRuntimeForPersona:
    def test_returns_incoming_event_types_and_join_mode(self) -> None:
        settings = Settings.model_validate(
            {
                "workflow": {
                    "workflow_id": "wf-1",
                    "name": "Code test",
                    "graph": {
                        "nodes": [
                            {
                                "id": "review-stage",
                                "kind": "stage",
                                "label": "Review",
                                "personaIds": ["reviewer"],
                                "joinMode": "all",
                            },
                            {
                                "id": "verify-stage",
                                "kind": "stage",
                                "label": "Verify",
                                "personaIds": ["verifier"],
                                "joinMode": "all",
                            },
                        ],
                        "edges": [
                            {
                                "id": "e1",
                                "source": "review-stage",
                                "target": "verify-stage",
                                "label": "review.completed -> review.completed",
                            },
                            {
                                "id": "e2",
                                "source": "security-stage",
                                "target": "verify-stage",
                                "label": "security.completed -> security.completed",
                            },
                        ],
                    },
                }
            }
        )

        runtime = _workflow_runtime_for_persona(settings, "verifier")

        assert runtime is not None
        assert runtime["event_types"] == ["review.completed", "security.completed"]
        assert runtime["fan_in_strategy"] == "all_must_pass"
        assert runtime["consumer_groups"] == [
            {
                "id": "verify-stage",
                "label": "Verify",
                "event_types": ["review.completed", "security.completed"],
                "fan_in_strategy": "all_must_pass",
            }
        ]

    def test_returns_none_when_workflow_graph_has_no_matching_persona(self) -> None:
        settings = Settings.model_validate(
            {
                "workflow": {
                    "workflow_id": "wf-1",
                    "name": "Code test",
                    "graph": {
                        "nodes": [
                            {
                                "id": "review-stage",
                                "kind": "stage",
                                "label": "Review",
                                "personaIds": ["reviewer"],
                            },
                        ],
                        "edges": [],
                    },
                }
            }
        )

        assert _workflow_runtime_for_persona(settings, "verifier") is None

    def test_prefers_stage_member_consumes_event_type_over_graph_edge(self) -> None:
        settings = Settings.model_validate(
            {
                "workflow": {
                    "workflow_id": "wf-1",
                    "name": "Code test",
                    "graph": {
                        "nodes": [
                            {
                                "id": "review-stage",
                                "kind": "stage",
                                "label": "Review",
                                "stageMembers": [
                                    {
                                        "personaId": "reviewer",
                                        "consumesEventTypes": ["review.requested"],
                                    }
                                ],
                            },
                        ],
                        "edges": [
                            {
                                "id": "e1",
                                "source": "coder-stage",
                                "target": "review-stage",
                                "label": "code.changed -> code.changed",
                            }
                        ],
                    },
                }
            }
        )

        runtime = _workflow_runtime_for_persona(settings, "reviewer")

        assert runtime is not None
        assert runtime["event_types"] == ["review.requested"]
        assert runtime["consumer_groups"] == [
            {
                "id": "review-stage",
                "label": "Review",
                "event_types": ["review.requested"],
                "fan_in_strategy": "merge",
            }
        ]

    def test_carries_stage_member_event_filters_into_consumer_groups(self) -> None:
        settings = Settings.model_validate(
            {
                "workflow": {
                    "workflow_id": "wf-1",
                    "name": "Memory curation",
                    "graph": {
                        "nodes": [
                            {
                                "id": "memory-curator-stage",
                                "kind": "stage",
                                "label": "Curate memory",
                                "stageMembers": [
                                    {
                                        "personaId": "mimir-memory-curator",
                                        "consumesEventTypes": ["mimir.source.ingested"],
                                        "eventFilters": {"mount_names": "tmp-mimir-test"},
                                    }
                                ],
                                "joinMode": "any",
                            }
                        ],
                        "edges": [],
                    },
                }
            }
        )

        runtime = _workflow_runtime_for_persona(settings, "mimir-memory-curator")

        assert runtime is not None
        assert runtime["event_types"] == ["mimir.source.ingested"]
        assert runtime["consumer_groups"] == [
            {
                "id": "memory-curator-stage",
                "label": "Curate memory",
                "event_types": ["mimir.source.ingested"],
                "fan_in_strategy": "merge",
                "event_filters": {"mount_names": "tmp-mimir-test"},
            }
        ]

    def test_returns_separate_consumer_groups_for_same_persona_across_nodes(self) -> None:
        settings = Settings.model_validate(
            {
                "workflow": {
                    "workflow_id": "wf-1",
                    "name": "Security flow",
                    "graph": {
                        "nodes": [
                            {
                                "id": "coordinator-start",
                                "kind": "stage",
                                "label": "Coordinate run",
                                "stageMembers": [{"personaId": "coordinator"}],
                                "joinMode": "any",
                            },
                            {
                                "id": "coordinator-finish",
                                "kind": "stage",
                                "label": "Finalize run",
                                "stageMembers": [{"personaId": "coordinator"}],
                                "joinMode": "all",
                            },
                        ],
                        "edges": [
                            {
                                "id": "e1",
                                "source": "dispatch-root",
                                "target": "coordinator-start",
                                "label": "run.requested -> run.requested",
                            },
                            {
                                "id": "e2",
                                "source": "review-stage",
                                "target": "coordinator-finish",
                                "label": "review.passed -> review.passed",
                            },
                            {
                                "id": "e3",
                                "source": "security-stage",
                                "target": "coordinator-finish",
                                "label": "security.passed -> security.passed",
                            },
                        ],
                    },
                }
            }
        )

        runtime = _workflow_runtime_for_persona(settings, "coordinator")

        assert runtime is not None
        assert runtime["event_types"] == ["run.requested", "review.passed", "security.passed"]
        assert runtime["fan_in_strategy"] == "merge"
        assert runtime["consumer_groups"] == [
            {
                "id": "coordinator-start",
                "label": "Coordinate run",
                "event_types": ["run.requested"],
                "fan_in_strategy": "merge",
            },
            {
                "id": "coordinator-finish",
                "label": "Finalize run",
                "event_types": ["review.passed", "security.passed"],
                "fan_in_strategy": "all_must_pass",
            },
        ]

    def test_workflow_allowed_task_targets_returns_downstream_stage_personas(self) -> None:
        settings = Settings.model_validate(
            {
                "workflow": {
                    "workflow_id": "wf-1",
                    "name": "Code test",
                    "graph": {
                        "nodes": [
                            {
                                "id": "coordinator-stage",
                                "kind": "stage",
                                "label": "Coordinate",
                                "stageMembers": [{"personaId": "coordinator"}],
                            },
                            {
                                "id": "coder-stage",
                                "kind": "stage",
                                "label": "Code",
                                "stageMembers": [{"personaId": "coder"}],
                            },
                            {
                                "id": "review-stage",
                                "kind": "stage",
                                "label": "Review",
                                "stageMembers": [{"personaId": "reviewer"}],
                            },
                        ],
                        "edges": [
                            {
                                "id": "e1",
                                "source": "coordinator-stage",
                                "target": "coder-stage",
                                "label": "code.requested -> code.requested",
                            },
                            {
                                "id": "e2",
                                "source": "review-stage",
                                "target": "coordinator-stage",
                                "label": "review.passed -> review.passed",
                            },
                        ],
                    },
                }
            }
        )

        assert _workflow_allowed_task_targets(settings, "coordinator") == {"coder"}
        assert _workflow_allowed_task_targets(settings, "reviewer") == {"coordinator"}
        assert _workflow_allowed_task_targets(
            settings,
            "coordinator",
            node_id="coordinator-stage",
        ) == {"coder"}
        assert _workflow_allowed_task_targets(
            settings,
            "reviewer",
            node_id="review-stage",
        ) == {"coordinator"}

    def test_workflow_graph_and_helpers_cover_edge_cases(self) -> None:
        settings = Settings.model_validate(
            {
                "workflow": {
                    "graph": {
                        "nodes": [
                            {"id": "stage-a", "kind": "stage", "personaIds": ["reviewer"]},
                            {
                                "id": "stage-b",
                                "kind": "stage",
                                "stageMembers": [
                                    {"personaId": "coder"},
                                    "ignored",
                                ],
                            },
                            "ignored",
                        ],
                        "edges": [
                            {
                                "id": "edge-1",
                                "source": "stage-a",
                                "target": "stage-b",
                                "label": "review.completed -> code.requested",
                            },
                            "ignored",
                        ],
                    }
                }
            }
        )

        nodes, edges = _workflow_graph(settings)
        assert [node["id"] for node in nodes] == ["stage-a", "stage-b"]
        assert [edge["id"] for edge in edges] == ["edge-1"]
        invalid_graph_settings = MagicMock()
        invalid_graph_settings.workflow.graph = []
        assert _workflow_graph(invalid_graph_settings) == ([], [])
        assert _split_workflow_edge_label("a -> b") == ("a", "b")
        assert _split_workflow_edge_label(123) == ("", "")
        assert _split_workflow_edge_label("missing-arrow") == ("", "")
        assert _stage_personas(nodes[1]) == {"coder"}
        assert _dedupe_preserve_order(["a", "", "b", "a", "c", "b"]) == ["a", "b", "c"]
        assert _join_mode_to_fan_in("all") == "all_must_pass"
        assert _join_mode_to_fan_in("any") == "any_pass"
        assert _join_mode_to_fan_in("merge") == "merge"

    def test_workflow_allowed_outcome_topics_and_filters(self) -> None:
        settings = Settings.model_validate(
            {
                "workflow": {
                    "graph": {
                        "nodes": [{"id": "review-stage", "kind": "stage"}],
                        "edges": [
                            {
                                "id": "edge-1",
                                "source": "review-stage",
                                "target": "coder-stage",
                                "label": "review.completed -> code.requested",
                            },
                            {
                                "id": "edge-2",
                                "source": "review-stage",
                                "target": "notify-stage",
                                "label": "review.changes_requested -> notify.requested",
                            },
                        ],
                    }
                }
            }
        )

        assert _workflow_allowed_outcome_topics(settings, node_id=None) is None
        assert _workflow_allowed_outcome_topics(settings, node_id="review-stage") == {
            "review.completed",
            "review.changes_requested",
        }
        assert _workflow_event_matches_filters({}, {}) is True
        payload = {
            "source_id": "src_1",
            "fields": {"mount_names": ["tmp-mimir-test"], "page_path": "research/demo.md"},
            "outcome": {"verdict": "pass"},
        }
        assert _workflow_event_matches_filters(payload, {"source_id": "src_1"}) is True
        assert _workflow_event_matches_filters(payload, {"mount_names": "tmp-mimir-test"}) is True
        assert _workflow_event_matches_filters(payload, {"verdict": "pass"}) is True
        assert _workflow_event_matches_filters(payload, {"page_path": "research/demo.md"}) is True
        assert _workflow_event_matches_filters(payload, {"mount_names": "missing"}) is False
        assert _workflow_event_matches_filters(payload, {"verdict": "fail"}) is False

    def test_workflow_stage_context_exposes_configured_stage_instructions(self) -> None:
        settings = Settings.model_validate(
            {
                "workflow": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "build-stage",
                                "kind": "stage",
                                "label": "Build capability",
                                "description": (
                                    "Write the canonical artifact to learned_tool.json."
                                ),
                            }
                        ],
                        "edges": [],
                    }
                }
            }
        )

        assert _workflow_stage_context(settings, node_id="build-stage") == (
            "Workflow stage: Build capability\n"
            "Stage instructions:\n"
            "Write the canonical artifact to learned_tool.json."
        )
        assert _workflow_stage_context(settings, node_id="missing") == ""

    def test_derive_capabilities_prefers_persona_allowed_tools(self) -> None:
        settings = Settings()
        persona = PersonaConfig(
            name="reviewer",
            allowed_tools=["mimir", "cascade", "ravn"],
            forbidden_tools=["cascade"],
        )
        assert _derive_capabilities(settings, persona, "default") == ["mimir", "ravn"]
        assert _derive_capabilities(settings, None, "default") == [
            "core",
            "extended",
            "skill",
            "platform",
            "cascade",
            "mimir",
            "workflow",
            "ravn",
        ]

    def test_wire_cron_registers_trigger_and_returns_tools(self, tmp_path: Path) -> None:
        drive_loop = MagicMock()
        trigger = MagicMock()
        store = MagicMock()
        cron_tools = [MagicMock(name="cron-tool")]
        initiative = Settings().initiative
        initiative.queue_journal_path = str(tmp_path / "queue" / "journal.json")

        with (
            patch("ravn.adapters.triggers.cron.make_cron_trigger", return_value=(trigger, store)),
            patch("ravn.adapters.tools.cron_tools.build_cron_tools", return_value=cron_tools),
        ):
            result = _wire_cron(
                drive_loop,
                [{"name": "nightly", "schedule": "0 3 * * *"}],
                initiative,
            )

        assert result == cron_tools
        drive_loop.register_trigger.assert_called_once_with(trigger)


class TestDaemonAgentFactory:
    async def test_run_daemon_passes_workspace_dir_to_event_driven_executor(self) -> None:
        settings = Settings()
        settings.initiative.enabled = True
        settings.gateway.channels.http.enabled = False
        settings.gateway.channels.telegram.enabled = False
        settings.gateway.channels.discord.enabled = False
        settings.gateway.channels.slack.enabled = False
        settings.gateway.channels.matrix.enabled = False
        settings.gateway.channels.whatsapp.enabled = False
        settings.sleipnir.enabled = False
        settings.mimir.source_trigger.enabled = False
        settings.mimir.staleness_trigger.enabled = False
        settings.thread.enabled = False
        settings.cascade.enabled = False
        settings.mcp_servers = [
            {
                "name": "mimir-local",
                "transport": "stdio",
                "command": "python3",
                "args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir/local"],
            }
        ]

        recorded: list[dict[str, object]] = []

        class _FakePublisher:
            def __init__(self) -> None:
                self.started = False

            async def start(self) -> None:
                self.started = True

            async def stop(self) -> None:
                self.started = False

        publisher = _FakePublisher()

        class _FakeExecutor:
            def build(self, **kwargs):  # noqa: ANN003
                recorded.append(kwargs)
                return MagicMock()

        class _FakeDriveLoop:
            def __init__(self, *, agent_factory, **kwargs):  # noqa: ANN003
                assert publisher.started
                self._triggers: list[object] = []
                agent_factory(MagicMock(), task_id="task-1", triggered_by="mesh:outcome:test")
                agent_factory(MagicMock(), task_id="task-2", triggered_by="mesh:outcome:test")

            async def run(self) -> None:
                return None

        persona = PersonaConfig(name="claude-mimir-researcher")

        with (
            patch("ravn.cli.commands._resolve_workspace", return_value=Path("/tmp/workspace")),
            patch(
                "ravn.cli.tool_builders._build_learned_tool_resolver",
                return_value=MagicMock(),
            ) as publish_inventory,
            patch("ravn.cli.commands._build_llm", return_value=MagicMock()),
            patch("ravn.cli.commands._build_memory", return_value=MagicMock()),
            patch("ravn.cli.commands._build_compressor", return_value=MagicMock()),
            patch(
                "ravn.cli.commands._build_environment_signal_publisher",
                return_value=publisher,
            ),
            patch(
                "ravn.cli.commands._build_prompt_builder",
                side_effect=lambda *_args: MagicMock(),
            ),
            patch("ravn.cli.commands._build_hooks", return_value=([], [])),
            patch("ravn.cli.commands._start_mcp_shared", new=AsyncMock(return_value=(None, []))),
            patch("ravn.cli.commands._build_mimir", return_value=None),
            patch("ravn.cli.commands._build_permission", return_value=MagicMock()),
            patch("ravn.cli.commands._build_tools", return_value=[]),
            patch(
                "ravn.cli.commands._get_tool_group",
                return_value=MagicMock(include_mcp=False, include_groups=[]),
            ),
            patch("ravn.cli.commands._apply_trust_filter", side_effect=lambda tools, *_: tools),
            patch("ravn.cli.commands._build_executor", return_value=_FakeExecutor()),
            patch("ravn.cli.commands._wire_triggers", return_value=[]),
            patch("ravn.cli.commands._wire_cron", return_value=[]),
            patch("ravn.cli.commands._shutdown_mcp", new=AsyncMock()),
            patch("ravn.drive_loop.DriveLoop", _FakeDriveLoop),
        ):
            await _run_daemon(settings, persona_config=persona)

        assert [call["workspace_dir"] for call in recorded] == [
            "/tmp/workspace",
            "/tmp/workspace",
        ]
        publish_inventory.assert_called_once_with(settings, Path("/tmp/workspace"))
        assert recorded[0]["prompt_builder"] is not recorded[1]["prompt_builder"]
        assert recorded[0]["mcp_servers"] == [
            {
                "name": "mimir-local",
                "type": "stdio",
                "command": "python3",
                "args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir/local"],
                "env": {},
                "url": "",
            }
        ]

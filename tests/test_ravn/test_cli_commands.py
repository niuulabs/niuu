"""Tests for Ravn CLI commands."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from ravn.adapters.personas.loader import PersonaConfig
from ravn.cli.flock import NodeDef, _write_node_config
from ravn.cli.commands import (
    _chat,
    _print_usage,
    _run_daemon,
    _run_turn,
    _workflow_runtime_for_persona,
    app,
    main,
)
from ravn.config import Settings
from ravn.domain.models import (
    StreamEvent,
    StreamEventType,
    TokenUsage,
    TurnResult,
)

runner = CliRunner()


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

        recorded: dict[str, object] = {}

        class _FakeExecutor:
            def build(self, **kwargs):  # noqa: ANN003
                recorded.update(kwargs)
                return MagicMock()

        class _FakeDriveLoop:
            def __init__(self, *, agent_factory, **kwargs):  # noqa: ANN003
                self._triggers: list[object] = []
                agent_factory(MagicMock(), task_id="task-1", triggered_by="mesh:outcome:test")

            async def run(self) -> None:
                return None

        persona = PersonaConfig(name="claude-mimir-researcher")

        with (
            patch("ravn.cli.commands._resolve_workspace", return_value=Path("/tmp/workspace")),
            patch("ravn.cli.commands._build_llm", return_value=MagicMock()),
            patch("ravn.cli.commands._build_memory", return_value=MagicMock()),
            patch("ravn.cli.commands._build_compressor", return_value=MagicMock()),
            patch("ravn.cli.commands._build_prompt_builder", return_value=MagicMock()),
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

        assert recorded["workspace_dir"] == "/tmp/workspace"
        assert recorded["mcp_servers"] == [
            {
                "name": "mimir-local",
                "type": "stdio",
                "command": "python3",
                "args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir/local"],
                "env": {},
                "url": "",
            }
        ]

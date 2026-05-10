"""Tests for Ravn CLI commands."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from ravn.adapters.personas.loader import PersonaConfig
from ravn.cli.commands import (
    _chat,
    _mimir_ingest_event_fields_from_mcp_result,
    _print_usage,
    _run_daemon,
    _run_turn,
    _workflow_allowed_task_targets,
    _workflow_runtime_for_persona,
    app,
    main,
)
from ravn.config import Settings
from ravn.domain.models import (
    StreamEvent,
    StreamEventType,
    TokenUsage,
    ToolResult,
    TurnResult,
)

runner = CliRunner()


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
                                "label": "Coordinate raid",
                                "stageMembers": [{"personaId": "coordinator"}],
                                "joinMode": "any",
                            },
                            {
                                "id": "coordinator-finish",
                                "kind": "stage",
                                "label": "Finalize raid",
                                "stageMembers": [{"personaId": "coordinator"}],
                                "joinMode": "all",
                            },
                        ],
                        "edges": [
                            {
                                "id": "e1",
                                "source": "dispatch-root",
                                "target": "coordinator-start",
                                "label": "raid.requested -> raid.requested",
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
        assert runtime["event_types"] == ["raid.requested", "review.passed", "security.passed"]
        assert runtime["fan_in_strategy"] == "merge"
        assert runtime["consumer_groups"] == [
            {
                "id": "coordinator-start",
                "label": "Coordinate raid",
                "event_types": ["raid.requested"],
                "fan_in_strategy": "merge",
            },
            {
                "id": "coordinator-finish",
                "label": "Finalize raid",
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

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from niuu.ports.cli import CLITransport, TransportCapabilities
from ravn.adapters.executors.cli import (
    CliTransportAgent,
    CliTransportExecutor,
    _sum_model_usage,
    _TransportBinding,
)
from ravn.domain.checkpoint import InterruptReason
from ravn.domain.events import RavnEvent
from ravn.domain.models import Message, Session, ToolResult
from ravn.ports.tool import ToolPort


class _CollectingChannel:
    def __init__(self) -> None:
        self.events: list[RavnEvent] = []

    async def emit(self, event: RavnEvent) -> None:
        self.events.append(event)


class DummyTool(ToolPort):
    @property
    def name(self) -> str:
        return "dummy_tool"

    @property
    def description(self) -> str:
        return "Dummy test tool."

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return "test:dummy"

    async def execute(self, input: dict) -> ToolResult:
        return ToolResult(tool_call_id="", content="ok")


class FakeResumableTransport(CLITransport):
    def __init__(
        self,
        workspace_dir: str,
        *,
        model: str = "",
        session_id: str = "",
        system_prompt: str = "",
        skip_permissions: bool = True,
        initial_prompt: str = "",
    ) -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self.model = model
        self._session_id = session_id
        self.system_prompt = system_prompt
        self.skip_permissions = skip_permissions
        self.initial_prompt = initial_prompt
        self.sent_messages: list[str] = []
        self._last_result: dict | None = None
        self.control_calls: list[tuple[str, dict]] = []
        self.stopped = False

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.stopped = True

    async def send_message(self, content: str) -> None:
        self.sent_messages.append(content)
        await self._emit(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Working "}}
        )
        await self._emit(
            {
                "type": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    }
                ],
            }
        )
        await self._emit(
            {
                "type": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "ok",
                        "is_error": False,
                    }
                ],
            }
        )
        self._last_result = {
            "type": "result",
            "result": "Done",
            "stop_reason": "end_turn",
            "modelUsage": {
                "fake-model": {
                    "inputTokens": 10,
                    "outputTokens": 5,
                    "cacheReadInputTokens": 2,
                    "cacheCreationInputTokens": 1,
                }
            },
        }
        await self._emit(self._last_result)

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        self.control_calls.append((subtype, kwargs))

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return True

    @property
    def is_turn_active(self) -> bool:
        return True

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            session_resume=True,
            interrupt=True,
            steer=True,
            steering_mode="interrupt_resume",
        )


class FakeStatelessTransport(CLITransport):
    def __init__(self, workspace_dir: str, *, model: str = "") -> None:
        super().__init__()
        self.workspace_dir = workspace_dir
        self.model = model
        self.sent_messages: list[str] = []
        self._last_result: dict | None = None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_message(self, content: str) -> None:
        self.sent_messages.append(content)
        self._last_result = {
            "type": "result",
            "result": "Stateless done",
            "stop_reason": "end_turn",
            "modelUsage": {"fake-model": {"inputTokens": 1, "outputTokens": 2}},
        }
        await self._emit(self._last_result)

    @property
    def session_id(self) -> str | None:
        return None

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    @property
    def is_alive(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_cli_executor_runs_turn_and_emits_ravn_events() -> None:
    channel = _CollectingChannel()
    executor = CliTransportExecutor(
        transport_adapter="tests.test_ravn.test_executor_cli.FakeResumableTransport"
    )
    agent = executor.build(
        channel=channel,
        system_prompt="You are a reviewer.",
        session=Session(),
        model="fake-model",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-1",
        persona="reviewer",
        workspace_dir="/tmp/workspace",
        permission_mode="read_only",
        tools=[],
    )

    result = await agent.run_turn("Review the patch")

    assert result.response == "Done"
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.usage.cache_read_tokens == 2
    assert result.usage.cache_write_tokens == 1
    assert [call.name for call in result.tool_calls] == ["Bash"]
    assert [tool.content for tool in result.tool_results] == ["ok"]
    assert result.episode is not None
    assert result.episode.tools_used == ["Bash"]
    assert result.episode.outcome.value == "success"

    assert [event.type.value for event in channel.events] == [
        "thought",
        "tool_start",
        "tool_result",
        "response",
    ]
    assert channel.events[-1].payload["text"] == "Done"

    transport = agent._transport
    assert transport is not None
    assert transport.sent_messages == ["Review the patch"]


@pytest.mark.asyncio
async def test_cli_executor_renders_system_prompt_for_stateless_transport() -> None:
    channel = _CollectingChannel()
    session = Session()
    session.add_message(Message(role="assistant", content="Earlier answer"))
    executor = CliTransportExecutor(
        transport_adapter="tests.test_ravn.test_executor_cli.FakeStatelessTransport"
    )
    agent = executor.build(
        channel=channel,
        system_prompt="You are a coder.",
        session=session,
        model="fake-model",
        max_iterations=2,
        checkpoint_port=None,
        task_id="task-2",
        persona="coder",
        workspace_dir="/tmp/workspace",
        permission_mode="workspace_write",
        tools=[],
    )

    result = await agent.run_turn("Write the fix")

    assert result.response == "Stateless done"
    transport = agent._transport
    assert transport is not None
    prompt = transport.sent_messages[0]
    assert "System instructions:\nYou are a coder." in prompt
    assert "Assistant:\nEarlier answer" in prompt
    assert "User:\nWrite the fix" in prompt


@pytest.mark.asyncio
async def test_cli_executor_interrupts_active_transport_when_supported() -> None:
    channel = _CollectingChannel()
    executor = CliTransportExecutor(
        transport_adapter="tests.test_ravn.test_executor_cli.FakeResumableTransport"
    )
    agent = executor.build(
        channel=channel,
        system_prompt="You are a reviewer.",
        session=Session(),
        model="fake-model",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-3",
        persona="reviewer",
        workspace_dir="/tmp/workspace",
        permission_mode="read_only",
        tools=[],
    )

    await agent._ensure_transport()
    agent.interrupt(InterruptReason.SIGINT)
    await asyncio.sleep(0)

    transport = agent._transport
    assert transport is not None
    assert transport.control_calls == [("interrupt", {})]


@pytest.mark.asyncio
async def test_cli_executor_steers_active_transport_when_supported() -> None:
    channel = _CollectingChannel()
    executor = CliTransportExecutor(
        transport_adapter="tests.test_ravn.test_executor_cli.FakeResumableTransport"
    )
    agent = executor.build(
        channel=channel,
        system_prompt="You are a reviewer.",
        session=Session(),
        model="fake-model",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-4",
        persona="reviewer",
        workspace_dir="/tmp/workspace",
        permission_mode="read_only",
        tools=[],
    )

    await agent._ensure_transport()
    steered = await agent.steer("Switch to a safer plan")

    transport = agent._transport
    assert steered is True
    assert transport is not None
    assert transport.control_calls == [("steer", {"content": "Switch to a safer plan"})]


@pytest.mark.asyncio
async def test_cli_executor_close_stops_and_releases_transport() -> None:
    agent, _ = _make_agent(
        binding=_TransportBinding(FakeResumableTransport, True),
    )

    await agent._ensure_transport()
    transport = agent._transport
    assert isinstance(transport, FakeResumableTransport)

    await agent.close()

    assert transport.stopped is True
    assert agent._transport is None
    assert agent._turn_runner is None
    assert agent._started is False

    await agent.close()


def _make_agent(
    *,
    binding: _TransportBinding | None = None,
    session: Session | None = None,
    channel: _CollectingChannel | None = None,
    session_join_manager: object | None = None,
) -> tuple[CliTransportAgent, _CollectingChannel]:
    bound_channel = channel or _CollectingChannel()
    agent = CliTransportAgent(
        transport_binding=binding or _TransportBinding(FakeStatelessTransport, False),
        transport_kwargs={
            "workspace_dir": "/tmp/workspace",
            "model": "fake-model",
            "extra": "x",
        },
        channel=bound_channel,
        system_prompt="You are helpful.",
        session=session or Session(),
        model="fake-model",
        max_iterations=4,
        checkpoint_port=None,
        task_id="task-helper",
        persona="reviewer",
        preloaded_tools=[type("Tool", (), {"name": "alpha"})()],
        session_join_manager=session_join_manager,
    )
    return agent, bound_channel


def test_sum_model_usage_handles_invalid_and_sparse_payloads() -> None:
    empty = _sum_model_usage(None)
    assert empty.input_tokens == 0
    assert empty.output_tokens == 0

    usage = _sum_model_usage(
        {
            "modelUsage": {
                "first": {
                    "inputTokens": 3,
                    "outputTokens": 5,
                    "cacheReadInputTokens": 1,
                    "cacheCreationInputTokens": 2,
                    "thinkingTokens": 4,
                },
                "second": "ignored",
            }
        }
    )
    assert usage.input_tokens == 3
    assert usage.output_tokens == 5
    assert usage.cache_read_tokens == 1
    assert usage.cache_write_tokens == 2
    assert usage.thinking_tokens == 4


@pytest.mark.asyncio
async def test_cli_transport_agent_helper_paths_and_failures() -> None:
    agent, _ = _make_agent()

    assert [tool.name for tool in agent.tools] == ["alpha"]
    assert agent.max_iterations == 4
    assert agent.llm_adapter_name == "FakeStatelessTransport"
    assert agent.checkpoint_port is None
    assert agent.task_id == "task-helper"

    agent.interrupt(InterruptReason.SIGINT)
    assert agent._interrupt_reason == InterruptReason.SIGINT
    with pytest.raises(RuntimeError, match="turn interrupted"):
        await agent.run_turn("hello")

    agent, _ = _make_agent(session=Session())
    agent._transport = FakeStatelessTransport("/tmp/workspace", model="fake-model")
    prompt = agent._build_prompt("Ship it")
    assert "System instructions:\nYou are helpful." in prompt
    assert prompt.endswith("User:\nShip it")

    agent._session.add_message(
        Message(
            role="assistant",
            content=[
                {"text": "Earlier answer"},
                {"content": "with detail"},
                "ignored",
            ],
        )
    )
    agent._session.add_message(Message(role="user", content="latest"))
    transcript = agent._render_transcript()
    assert "Assistant:\nEarlier answer\nwith detail" in transcript

    with pytest.raises(RuntimeError, match="boom"):
        agent._raise_if_transport_failed({"stop_reason": "error", "result": "boom"})
    with pytest.raises(RuntimeError, match="bad news"):
        agent._raise_if_transport_failed({"is_error": True, "content": "bad news"})


@pytest.mark.asyncio
async def test_cli_transport_agent_emits_event_variants_and_filters_transport_kwargs() -> None:
    channel = _CollectingChannel()
    agent, _ = _make_agent(channel=channel, session=Session())

    await agent._ensure_transport()
    assert isinstance(agent._transport, FakeStatelessTransport)
    assert agent._transport.workspace_dir == "/tmp/workspace"
    assert not hasattr(agent._transport, "extra")

    await agent._handle_transport_event(
        {"type": "content_block_delta", "delta": {"thinking": "hm"}}
    )
    await agent._handle_transport_event({"type": "content_block_delta", "delta": "ignored"})
    await agent._handle_transport_event({"type": "assistant", "message": {"content": "plain"}})
    await agent._handle_transport_event(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "block text"},
                    {"type": "tool_use", "name": "Bash", "input": "bad-input"},
                    {"type": "other"},
                ]
            },
        }
    )
    await agent._handle_transport_event(
        {
            "type": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tool_1", "content": "ok", "is_error": True},
                {"type": "note"},
            ],
        }
    )
    await agent._handle_transport_event({"type": "user", "content": "ignored"})
    await agent._handle_transport_event({"type": "error", "message": "kaboom"})

    assert [event.type.value for event in channel.events] == [
        "thought",
        "thought",
        "thought",
        "tool_start",
        "tool_result",
        "error",
    ]
    assert channel.events[-1].payload["message"] == "kaboom"
    assert [call.name for call in agent._turn_tool_calls] == ["Bash"]
    assert [result.is_error for result in agent._turn_tool_results] == [True]


@pytest.mark.asyncio
async def test_cli_transport_agent_records_durable_tool_metrics(monkeypatch) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider

    import niuu.observability as observability_module
    from niuu.observability import Observability

    metric_reader = InMemoryMetricReader()
    telemetry = Observability(
        tracer_provider=TracerProvider(),
        meter_provider=MeterProvider(metric_readers=[metric_reader]),
    )
    monkeypatch.setattr(observability_module, "_active", telemetry)
    agent, _ = _make_agent()

    await agent._emit_content_block(
        {
            "type": "tool_use",
            "id": "tool-1",
            "name": "mcp__ravn_tools__alpha",
            "input": {"value": "hello"},
        },
        "conversation-1",
    )
    await agent._record_tool_result_block(
        {
            "type": "tool_result",
            "tool_use_id": "tool-1",
            "content": "ok",
            "is_error": False,
        },
        "conversation-1",
    )

    metrics = {
        metric.name: metric
        for resource in metric_reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert {
        "ravn.agent.tool.calls",
        "ravn.agent.tool.duration",
        "ravn.trace.boundaries",
    } <= metrics.keys()
    call_point = metrics["ravn.agent.tool.calls"].data.data_points[0]
    assert call_point.attributes["gen_ai.tool.name"] == "alpha"
    assert call_point.attributes["ravn.tool.outcome"] == "success"
    assert call_point.attributes["ravn.tool.telemetry_source"] == "cli_transport"
    assert call_point.attributes["ravn.runtime.component"] == "resident"
    boundary_point = metrics["ravn.trace.boundaries"].data.data_points[0]
    assert boundary_point.attributes["ravn.trace.relationship"] == "remote_parent"
    assert boundary_point.attributes["ravn.runtime.component"] == "resident"
    telemetry.shutdown()


@pytest.mark.asyncio
async def test_cli_transport_agent_joins_ting_workflow_result() -> None:
    class JoinManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def join(self, session_id: str, chat_endpoint: str) -> dict:
            self.calls.append((session_id, chat_endpoint))
            return {"connected": True}

    manager = JoinManager()
    agent, channel = _make_agent(session_join_manager=manager)
    agent._current_tool_names["tool-1"] = "mcp__ravn_tools__ting_workflow"

    await agent._handle_transport_event(
        {
            "type": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": json.dumps(
                        {
                            "status": "ok",
                            "data": {
                                "sessionId": "sess-1",
                                "chatEndpoint": "wss://sessions.example/s/sess-1/session",
                            },
                        }
                    ),
                }
            ],
        }
    )
    await asyncio.sleep(0)

    assert manager.calls == [("sess-1", "wss://sessions.example/s/sess-1/session")]
    assert channel.events[-1].payload["tool_name"] == "mcp__ravn_tools__ting_workflow"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["ting_research", "ting_plan", "ting_spec", "ting_adhoc_workflow"],
)
async def test_cli_transport_agent_joins_durable_ting_tool_result(tool_name: str) -> None:
    class JoinManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def join(self, session_id: str, chat_endpoint: str) -> dict:
            self.calls.append((session_id, chat_endpoint))
            return {"connected": True}

    manager = JoinManager()
    agent, channel = _make_agent(session_join_manager=manager)
    agent._current_tool_names["tool-1"] = f"mcp__ravn_tools__{tool_name}"

    await agent._handle_transport_event(
        {
            "type": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": json.dumps(
                        {
                            "status": "ok",
                            "data": {
                                "sessionId": "sess-1",
                                "chatEndpoint": "wss://sessions.example/s/sess-1/session",
                            },
                        }
                    ),
                }
            ],
        }
    )
    await asyncio.sleep(0)

    assert manager.calls == [("sess-1", "wss://sessions.example/s/sess-1/session")]
    assert channel.events[-1].payload["tool_name"] == f"mcp__ravn_tools__{tool_name}"


@pytest.mark.asyncio
async def test_cli_transport_agent_joins_codex_ws_tool_result_block() -> None:
    class JoinManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def join(self, session_id: str, chat_endpoint: str) -> dict:
            self.calls.append((session_id, chat_endpoint))
            return {"connected": True}

    manager = JoinManager()
    agent, channel = _make_agent(session_join_manager=manager)
    agent._current_tool_names["tool-1"] = "mcp__ravn_tools__ting_workflow"

    await agent._handle_transport_event(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": json.dumps(
                    {
                        "sessionId": "sess-2",
                        "chatEndpoint": "wss://sessions.example/s/sess-2/session",
                    }
                ),
            },
        }
    )
    await asyncio.sleep(0)

    assert manager.calls == [("sess-2", "wss://sessions.example/s/sess-2/session")]
    assert channel.events[-1].payload["tool_name"] == "mcp__ravn_tools__ting_workflow"


@pytest.mark.asyncio
async def test_cli_transport_agent_joins_codex_ws_wrapped_mcp_tool_result() -> None:
    class JoinManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def join(self, session_id: str, chat_endpoint: str) -> dict:
            self.calls.append((session_id, chat_endpoint))
            return {"connected": True}

    manager = JoinManager()
    agent, _channel = _make_agent(session_join_manager=manager)
    agent._current_tool_names["tool-1"] = "ravn-tools/ting_workflow"

    await agent._handle_transport_event(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_result",
                "tool_use_id": "tool-1",
                "content": json.dumps(
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {
                                        "sessionId": "sess-3",
                                        "chatEndpoint": ("wss://sessions.example/s/sess-3/session"),
                                    }
                                ),
                            }
                        ],
                        "isError": False,
                    }
                ),
            },
        }
    )
    await asyncio.sleep(0)

    assert manager.calls == [("sess-3", "wss://sessions.example/s/sess-3/session")]


def test_cli_executor_passes_mcp_servers_to_transport() -> None:
    channel = _CollectingChannel()
    manager = object()
    executor = CliTransportExecutor(
        transport_adapter="tests.test_ravn.test_executor_cli.FakeResumableTransport"
    )
    agent = executor.build(
        channel=channel,
        system_prompt="You are a reviewer.",
        session=Session(),
        model="fake-model",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-mcp",
        persona="reviewer",
        workspace_dir="/tmp/workspace",
        permission_mode="read_only",
        tools=[],
        mcp_servers=[{"name": "mimir-local", "command": "python3", "args": ["-m", "mimir"]}],
        session_join_manager=manager,
    )

    assert agent._session_join_manager is manager
    assert agent._transport_kwargs["mcp_servers"] == [
        {"name": "mimir-local", "command": "python3", "args": ["-m", "mimir"]}
    ]


def test_cli_executor_passes_mcp_servers_to_codex_transport() -> None:
    channel = _CollectingChannel()
    executor = CliTransportExecutor(
        transport_adapter="skuld.transports.codex.CodexSubprocessTransport"
    )
    agent = executor.build(
        channel=channel,
        system_prompt="You are a researcher.",
        session=Session(),
        model="gpt-5.5",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-codex-mcp",
        persona="researcher",
        workspace_dir="/tmp/workspace",
        permission_mode="read_only",
        tools=[],
        mcp_servers=[{"name": "mimir-local", "command": "python3", "args": ["-m", "mimir"]}],
    )

    transport = agent._create_transport()
    assert transport._mcp_overrides == [
        ("mcp_servers.mimir-local.command", '"python3"'),
        ("mcp_servers.mimir-local.args", '["-m", "mimir"]'),
    ]


def test_cli_executor_adds_ravn_tools_mcp_server_when_tools_are_preloaded() -> None:
    channel = _CollectingChannel()
    executor = CliTransportExecutor(
        transport_adapter="skuld.transports.codex.CodexSubprocessTransport"
    )
    agent = executor.build(
        channel=channel,
        system_prompt="You are a researcher.",
        session=Session(),
        model="gpt-5.5",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-ravn-tool-mcp",
        persona="product-steward",
        workspace_dir="/tmp/workspace",
        permission_mode="read_only",
        tools=[DummyTool()],
        mcp_servers=[],
    )

    transport = agent._create_transport()
    assert any(
        key == "mcp_servers.ravn-tools.args" and '"tool-mcp"' in value
        for key, value in transport._mcp_overrides
    )
    assert (
        "mcp_servers.ravn-tools.tool_timeout_sec",
        "3600.0",
    ) in transport._mcp_overrides


def test_cli_executor_propagates_active_trace_to_ravn_tool_mcp(monkeypatch) -> None:
    import ravn.adapters.executors.cli as cli_module

    telemetry = MagicMock()
    telemetry.inject.return_value = {
        "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
        "tracestate": "vendor=value",
    }
    monkeypatch.setattr(cli_module, "get_observability", lambda: telemetry)

    agent = CliTransportExecutor(
        transport_adapter="skuld.transports.codex.CodexSubprocessTransport"
    ).build(
        channel=_CollectingChannel(),
        system_prompt="Observe.",
        session=Session(),
        model="gpt-5.5",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-traced-mcp",
        persona="ivaldi",
        workspace_dir="/tmp/workspace",
        permission_mode="read_only",
        tools=[DummyTool()],
        mcp_servers=[],
    )

    args = agent._transport_kwargs["mcp_servers"][0]["args"]
    assert args[args.index("--traceparent") + 1] == telemetry.inject.return_value["traceparent"]
    assert args[args.index("--tracestate") + 1] == "vendor=value"


def test_cli_executor_allows_ravn_tool_mcp_timeout_override() -> None:
    channel = _CollectingChannel()
    executor = CliTransportExecutor(
        transport_adapter="skuld.transports.codex.CodexSubprocessTransport",
        ravn_tool_mcp_timeout_seconds=900,
    )
    agent = executor.build(
        channel=channel,
        system_prompt="You are a researcher.",
        session=Session(),
        model="gpt-5.5",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-ravn-tool-mcp-timeout",
        persona="product-steward",
        workspace_dir="/tmp/workspace",
        permission_mode="read_only",
        tools=[DummyTool()],
        mcp_servers=[],
    )

    transport = agent._create_transport()
    assert (
        "mcp_servers.ravn-tools.tool_timeout_sec",
        "900.0",
    ) in transport._mcp_overrides


def test_cli_executor_resolves_ravn_tool_mcp_config_before_changing_workspace(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "configs" / "resident.yaml"
    config.parent.mkdir()
    config.write_text("state_dir: /tmp/resident-state\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAVN_CONFIG", "configs/resident.yaml")

    channel = _CollectingChannel()
    executor = CliTransportExecutor(
        transport_adapter="skuld.transports.codex.CodexSubprocessTransport"
    )
    agent = executor.build(
        channel=channel,
        system_prompt="You are a resident.",
        session=Session(),
        model="gpt-5.5",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-ravn-tool-mcp-config",
        persona="ivaldi",
        workspace_dir="/different/resident/workspace",
        permission_mode="read_only",
        tools=[DummyTool()],
        mcp_servers=[],
    )

    server = agent._transport_kwargs["mcp_servers"][0]
    assert server["args"][:7] == [
        "-m",
        "ravn",
        "tool-mcp",
        "--config",
        str(config),
        "--persona",
        "ivaldi",
    ]
    assert server["args"][7] == "--conversation-id"
    assert server["args"][8]
    assert server["args"][9:] == ["--task-id", "task-ravn-tool-mcp-config"]
    assert server["env"]["RAVN_CONFIG"] == str(config)


def test_cli_executor_delegates_codex_ws_permissions_to_codex_config() -> None:
    channel = _CollectingChannel()
    executor = CliTransportExecutor(
        transport_adapter="skuld.transports.codex_ws.CodexWebSocketTransport"
    )
    agent = executor.build(
        channel=channel,
        system_prompt="You are a researcher.",
        session=Session(),
        model="gpt-5.5",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-codex-ws-permissions",
        persona="researcher",
        workspace_dir="/tmp/workspace",
        permission_mode="workspace_write",
        tools=[],
    )

    assert "skip_permissions" not in agent._transport_kwargs


def test_cli_executor_allows_explicit_codex_ws_permission_override() -> None:
    channel = _CollectingChannel()
    executor = CliTransportExecutor(
        transport_adapter="skuld.transports.codex_ws.CodexWebSocketTransport",
        transport_kwargs={"skip_permissions": True},
    )
    agent = executor.build(
        channel=channel,
        system_prompt="You are a researcher.",
        session=Session(),
        model="gpt-5.5",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-codex-ws-explicit-permissions",
        persona="researcher",
        workspace_dir="/tmp/workspace",
        permission_mode="workspace_write",
        tools=[],
    )

    assert agent._transport_kwargs["skip_permissions"] is True

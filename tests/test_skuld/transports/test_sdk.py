"""Tests for the Claude SDK-backed transport."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk.types import (
    AssistantMessage,
    DeferredToolUse,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    ServerToolResultBlock,
    ServerToolUseBlock,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from niuu.adapters.cli.runtime import filter_cli_event
from skuld.transports.mcp_config import build_sdk_mcp_servers
from skuld.transports.sdk import SDKTransport, _content_block_to_dict, _to_stream_json


def _assistant_message(*blocks: object, session_id: str | None = None) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        model="claude-opus-4-20250514",
        usage={"input_tokens": 10, "output_tokens": 5},
        session_id=session_id,
    )


def _result_message(
    *,
    session_id: str = "sdk-session",
    result: str = "done",
    is_error: bool = False,
    subtype: str | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype or ("error_during_execution" if is_error else "success"),
        duration_ms=12,
        duration_api_ms=8,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        total_cost_usd=0.01,
        usage={"input_tokens": 10, "output_tokens": 5},
        result=result,
    )


class _FakeClient:
    def __init__(self, responses: list[list[object]]) -> None:
        self._responses = [list(batch) for batch in responses]
        self.entered = False
        self.exited = False
        self.query = AsyncMock()
        self.interrupt = AsyncMock()
        self.set_model = AsyncMock()
        self.set_permission_mode = AsyncMock()
        self.rewind_files = AsyncMock()

    async def __aenter__(self) -> _FakeClient:
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.exited = True
        return False

    async def receive_response(self) -> AsyncIterator[object]:
        batch = self._responses.pop(0) if self._responses else []
        for message in batch:
            yield message


class _ClientFactory:
    def __init__(self, responses: list[list[object]]) -> None:
        self._responses = responses
        self.client: _FakeClient | None = None
        self.options = None

    def __call__(self, options) -> _FakeClient:
        self.options = options
        self.client = _FakeClient(self._responses)
        return self.client


class _TimeoutRecoveryClient(_FakeClient):
    def __init__(self) -> None:
        super().__init__(responses=[])
        self._receive_calls = 0

    async def receive_response(self) -> AsyncIterator[object]:
        self._receive_calls += 1
        if self._receive_calls == 1:
            await asyncio.sleep(3600)
            return
        yield _assistant_message(TextBlock(text="recovered"), session_id="sdk-session")
        yield _result_message(result="recovered")


class _TimeoutRecoveryFactory:
    def __init__(self) -> None:
        self.client: _TimeoutRecoveryClient | None = None
        self.options = None

    def __call__(self, options) -> _TimeoutRecoveryClient:
        self.options = options
        self.client = _TimeoutRecoveryClient()
        return self.client


class _QueryTimeoutRecoveryClient(_FakeClient):
    def __init__(self) -> None:
        super().__init__(
            responses=[
                [
                    _assistant_message(TextBlock(text="recovered"), session_id="sdk-session"),
                    _result_message(result="recovered"),
                ]
            ]
        )
        self._query_calls = 0
        self.query = self._query_impl

    async def _query_impl(self, prompt: str) -> None:
        self._query_calls += 1
        if self._query_calls == 1:
            await asyncio.sleep(3600)
            return
        return None


class _QueryTimeoutRecoveryFactory:
    def __init__(self) -> None:
        self.client: _QueryTimeoutRecoveryClient | None = None
        self.options = None

    def __call__(self, options) -> _QueryTimeoutRecoveryClient:
        self.options = options
        self.client = _QueryTimeoutRecoveryClient()
        return self.client


@pytest.mark.asyncio
async def test_construction_accepts_standard_kwargs(tmp_path) -> None:
    transport = SDKTransport(
        workspace_dir=str(tmp_path),
        model="claude-opus-4-20250514",
        skip_permissions=False,
        agent_teams=True,
        system_prompt="Be precise.",
        initial_prompt="Warm up.",
        mcp_servers=[{"name": "linear", "command": "uvx", "args": ["linear-mcp"]}],
    )

    assert transport.workspace_dir == str(tmp_path)
    assert transport.session_id is None
    assert transport.last_result is None
    assert transport.is_alive is False


@pytest.mark.asyncio
async def test_start_and_stop_manage_sdk_context(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory([[]])
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(
        workspace_dir=str(tmp_path),
        model="claude-opus-4-20250514",
        skip_permissions=True,
        agent_teams=True,
        system_prompt="System prompt",
        mcp_servers=[{"name": "linear", "command": "uvx", "args": ["linear-mcp"]}],
    )

    await transport.start()

    assert factory.client is not None
    assert factory.client.entered is True
    assert transport.is_alive is True
    assert factory.options.model == "claude-opus-4-20250514"
    assert factory.options.permission_mode == "bypassPermissions"
    assert factory.options.system_prompt == "System prompt"
    assert factory.options.cwd == str(tmp_path)
    assert factory.options.mcp_servers == {
        "linear": {"command": "uvx", "args": ["linear-mcp"]},
    }
    assert factory.options.env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "1"

    await transport.stop()

    assert factory.client.exited is True
    assert transport.is_alive is False


@pytest.mark.asyncio
async def test_start_injects_tracker_shim_env(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory([[]])
    shim_env = {
        "PATH": f"{tmp_path}/.skuld-tools/bin:/usr/bin",
        "RAVN_WORKSPACE_DIR": str(tmp_path),
    }
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)
    monkeypatch.setattr(
        "skuld.transports.sdk.ensure_codex_tool_shims",
        lambda workspace_dir, mcp_servers=None: (tmp_path / ".skuld-tools" / "bin", shim_env),
    )

    transport = SDKTransport(workspace_dir=str(tmp_path))

    await transport.start()

    assert factory.options is not None
    assert factory.options.env["PATH"] == shim_env["PATH"]
    assert factory.options.env["RAVN_WORKSPACE_DIR"] == str(tmp_path)

    await transport.stop()


@pytest.mark.asyncio
async def test_send_message_emits_stream_json_events_and_tracks_last_result(
    monkeypatch, tmp_path
) -> None:
    factory = _ClientFactory(
        [
            [
                UserMessage(content="hello"),
                _assistant_message(TextBlock(text="world"), session_id="sdk-session"),
                _result_message(result="world"),
            ]
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    received: list[dict] = []

    async def on_event(event: dict) -> None:
        received.append(event)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    transport.on_event(on_event)

    await transport.start()
    await transport.send_message("hello")

    assert factory.client is not None
    factory.client.query.assert_awaited_once_with("hello")
    assert [event["type"] for event in received] == ["user", "assistant", "result"]
    assert received[0]["message"] == {"role": "user", "content": "hello"}
    assert received[1]["message"]["content"] == [{"type": "text", "text": "world"}]
    assert received[2]["result"] == "world"
    assert transport.last_result == received[2]
    assert transport.session_id == "sdk-session"


@pytest.mark.asyncio
async def test_send_message_recovers_once_after_turn_timeout(monkeypatch, tmp_path) -> None:
    factory = _TimeoutRecoveryFactory()
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    received: list[dict] = []

    async def on_event(event: dict) -> None:
        received.append(event)

    transport = SDKTransport(workspace_dir=str(tmp_path), turn_timeout_s=0.01)
    transport.on_event(on_event)

    await transport.start()
    await transport.send_message("review the change")

    assert factory.client is not None
    assert factory.client.query.await_count == 2
    assert factory.client.query.await_args_list[0].args == ("review the change",)
    assert (
        "Time budget reached. Stop exploring and conclude immediately"
        in (factory.client.query.await_args_list[1].args[0])
    )
    factory.client.interrupt.assert_awaited_once()
    assert [event["type"] for event in received] == ["assistant", "result"]
    assert received[-1]["result"] == "recovered"


@pytest.mark.asyncio
async def test_send_message_recovers_when_query_call_hangs(monkeypatch, tmp_path) -> None:
    factory = _QueryTimeoutRecoveryFactory()
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    received: list[dict] = []

    async def on_event(event: dict) -> None:
        received.append(event)

    transport = SDKTransport(workspace_dir=str(tmp_path), turn_timeout_s=0.01)
    transport.on_event(on_event)

    await transport.start()
    await transport.send_message("review the change")

    assert factory.client is not None
    assert factory.client._query_calls == 2
    factory.client.interrupt.assert_awaited_once()
    assert [event["type"] for event in received] == ["assistant", "result"]
    assert received[-1]["result"] == "recovered"


@pytest.mark.asyncio
async def test_tool_use_block_mapping(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory(
        [
            [
                _assistant_message(
                    ToolUseBlock(id="tool-1", name="read_file", input={"path": "README.md"})
                ),
                _result_message(),
            ]
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    received: list[dict] = []

    async def on_event(event: dict) -> None:
        received.append(event)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    transport.on_event(on_event)

    await transport.start()
    await transport.send_message("inspect")

    assert received[0] == {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "read_file",
                    "input": {"path": "README.md"},
                }
            ],
            "model": "claude-opus-4-20250514",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    }


@pytest.mark.asyncio
async def test_interrupt_calls_sdk_and_is_safe_when_disconnected(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory([[]])
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))

    await transport.interrupt()
    await transport.start()
    await transport.interrupt()

    assert factory.client is not None
    factory.client.interrupt.assert_awaited_once()


@pytest.mark.asyncio
async def test_session_id_captured_from_system_message(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory(
        [
            [
                SystemMessage(
                    subtype="init",
                    data={"type": "system", "subtype": "init", "session_id": "system-session"},
                ),
                _result_message(session_id="system-session"),
            ]
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    await transport.start()
    await transport.send_message("hello")

    assert transport.session_id == "system-session"


@pytest.mark.asyncio
async def test_event_callback_registration_receives_every_message(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory(
        [
            [
                UserMessage(content="hello"),
                _assistant_message(TextBlock(text="world")),
                _result_message(),
            ]
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    seen: list[str] = []

    async def on_event(event: dict) -> None:
        seen.append(event["type"])

    transport = SDKTransport(workspace_dir=str(tmp_path))
    transport.on_event(on_event)

    await transport.start()
    await transport.send_message("hello")

    assert seen == ["user", "assistant", "result"]


def test_capabilities() -> None:
    caps = SDKTransport("/tmp").capabilities

    assert caps.interrupt is True
    assert caps.steer is True
    assert caps.steering_mode == "interrupt_resume"
    # SDKTransport supports resume via ClaudeAgentOptions.resume.
    assert caps.session_resume is True
    assert caps.set_model is True
    assert caps.set_permission_mode is True


@pytest.mark.asyncio
async def test_send_control_updates_model_and_permission_mode(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory([[]])
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    await transport.start()
    await transport.send_control("set_model", model="claude-sonnet-4-6")
    await transport.send_control("set_permission_mode", permissionMode="plan")

    assert factory.client is not None
    factory.client.set_model.assert_awaited_once_with("claude-sonnet-4-6")
    factory.client.set_permission_mode.assert_awaited_once_with("plan")


@pytest.mark.asyncio
async def test_send_control_steer_interrupts_and_continues(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory(
        [
            [_assistant_message(TextBlock(text="first")), _result_message(result="partial")],
            [_assistant_message(TextBlock(text="second")), _result_message(result="final")],
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    await transport.start()
    assert factory.client is not None

    async def _query_side_effect(_prompt: str) -> None:
        await asyncio.sleep(0)

    factory.client.query.side_effect = _query_side_effect

    async def request_steer() -> None:
        while not transport.is_turn_active:
            await asyncio.sleep(0)
        await transport.send_control("steer", content="Use option B instead")

    steer_task = asyncio.create_task(request_steer())
    await transport.send_message("Start with option A")
    _ = await steer_task

    assert factory.client.query.await_args_list[0].args == ("Start with option A",)
    assert factory.client.query.await_args_list[1].args == ("Use option B instead",)
    factory.client.interrupt.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_control_redirect_interrupts_and_continues(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory(
        [
            [_assistant_message(TextBlock(text="first")), _result_message(result="partial")],
            [_assistant_message(TextBlock(text="second")), _result_message(result="final")],
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    await transport.start()
    assert factory.client is not None

    async def _query_side_effect(_prompt: str) -> None:
        await asyncio.sleep(0)

    factory.client.query.side_effect = _query_side_effect

    async def request_redirect() -> None:
        while not transport.is_turn_active:
            await asyncio.sleep(0)
        await transport.send_control("redirect", content="Actually use option B")

    redirect_task = asyncio.create_task(request_redirect())
    await transport.send_message("Start with option A")
    _ = await redirect_task

    assert factory.client.query.await_args_list[0].args == ("Start with option A",)
    assert factory.client.query.await_args_list[1].args == ("Actually use option B",)
    factory.client.interrupt.assert_awaited_once()


@pytest.mark.asyncio
async def test_transient_provider_error_does_not_retry_after_visible_output(
    monkeypatch, tmp_path
) -> None:
    factory = _ClientFactory(
        [
            [
                _assistant_message(
                    TextBlock(
                        text=(
                            "API Error: 500 Internal server error. This is a server-side issue, "
                            "usually temporary — try again in a moment. If it persists, "
                            "check the provider status page."
                        )
                    )
                ),
                _result_message(
                    result=(
                        "API Error: 500 Internal server error. This is a server-side issue, "
                        "usually temporary — try again in a moment. If it persists, "
                        "check the provider status page."
                    ),
                    is_error=True,
                ),
            ],
            [
                _assistant_message(TextBlock(text="CLAUDE-OK")),
                _result_message(result="CLAUDE-OK"),
            ],
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    received: list[dict] = []

    async def on_event(event: dict) -> None:
        received.append(event)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    transport.on_event(on_event)

    await transport.start()
    await transport.send_message("Please reply with exactly CLAUDE-OK.")

    assert factory.client is not None
    assert factory.client.query.await_count == 1
    assert [event["type"] for event in received] == ["assistant", "result"]
    assert "provider status page" in received[0]["message"]["content"][0]["text"]
    assert "provider status page" in received[1]["result"]


@pytest.mark.asyncio
async def test_sdk_events_pass_existing_runtime_filter(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory(
        [
            [
                _assistant_message(TextBlock(text="filtered")),
                _result_message(result="filtered"),
            ]
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    received: list[dict] = []

    async def on_event(event: dict) -> None:
        filtered = filter_cli_event(event)
        if filtered is not None:
            received.append(filtered)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    transport.on_event(on_event)

    await transport.start()
    await transport.send_message("hello")

    assert received == [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "filtered"}],
                "model": "claude-opus-4-20250514",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "duration_ms": 12,
            "duration_api_ms": 8,
            "is_error": False,
            "num_turns": 1,
            "session_id": "sdk-session",
            "total_cost_usd": 0.01,
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "result": "filtered",
        },
    ]


def test_build_sdk_mcp_servers() -> None:
    payload = build_sdk_mcp_servers(
        [
            {"name": "stdio", "type": "stdio", "command": "uvx", "args": ["mcp-stdio"]},
            {"name": "sse", "type": "sse", "url": "http://localhost:8000/sse"},
            {"name": "http", "type": "http", "url": "http://localhost:8000/mcp"},
        ]
    )

    assert payload == {
        "stdio": {"type": "stdio", "command": "uvx", "args": ["mcp-stdio"]},
        "sse": {"type": "sse", "url": "http://localhost:8000/sse"},
        "http": {"type": "http", "url": "http://localhost:8000/mcp"},
    }


def test_content_block_and_message_conversion_helpers() -> None:
    assert _content_block_to_dict(TextBlock(text="text")) == {"type": "text", "text": "text"}
    assert _content_block_to_dict(ThinkingBlock(thinking="plan", signature="sig")) == {
        "type": "thinking",
        "thinking": "plan",
        "signature": "sig",
    }
    assert _content_block_to_dict(ToolUseBlock(id="tool-1", name="read", input={"a": 1})) == {
        "type": "tool_use",
        "id": "tool-1",
        "name": "read",
        "input": {"a": 1},
    }
    assert _content_block_to_dict(
        ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=False)
    ) == {
        "type": "tool_result",
        "tool_use_id": "tool-1",
        "content": "ok",
        "is_error": False,
    }
    assert _content_block_to_dict(
        ServerToolUseBlock(id="srv-1", name="advisor", input={"topic": "sdk"})
    ) == {
        "type": "server_tool_use",
        "id": "srv-1",
        "name": "advisor",
        "input": {"topic": "sdk"},
    }
    assert _content_block_to_dict(
        ServerToolResultBlock(tool_use_id="srv-1", content={"type": "advisor_result"})
    ) == {
        "type": "advisor_tool_result",
        "tool_use_id": "srv-1",
        "content": {"type": "advisor_result"},
    }

    with pytest.raises(TypeError, match="Unsupported SDK content block"):
        _content_block_to_dict(object())

    user = UserMessage(
        content=[TextBlock(text="hello")],
        uuid="user-uuid",
        parent_tool_use_id="parent-1",
        tool_use_result={"ok": True},
    )
    assert _to_stream_json(user) == {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        "parent_tool_use_id": "parent-1",
        "tool_use_result": {"ok": True},
        "uuid": "user-uuid",
    }

    assistant = AssistantMessage(
        content=[
            TextBlock(text="answer"),
            ThinkingBlock(thinking="reason", signature="sig"),
            ToolResultBlock(tool_use_id="tool-1", content="done"),
            ServerToolUseBlock(id="srv-1", name="advisor", input={"topic": "sdk"}),
            ServerToolResultBlock(tool_use_id="srv-1", content={"type": "advisor_result"}),
        ],
        model="claude-opus-4-20250514",
        parent_tool_use_id="parent-1",
        error="rate_limit",
        usage={"input_tokens": 1},
        message_id="msg-1",
        stop_reason="end_turn",
        session_id="sess-1",
        uuid="assistant-uuid",
    )
    assert _to_stream_json(assistant) == {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "answer"},
                {"type": "thinking", "thinking": "reason", "signature": "sig"},
                {"type": "tool_result", "tool_use_id": "tool-1", "content": "done"},
                {
                    "type": "server_tool_use",
                    "id": "srv-1",
                    "name": "advisor",
                    "input": {"topic": "sdk"},
                },
                {
                    "type": "advisor_tool_result",
                    "tool_use_id": "srv-1",
                    "content": {"type": "advisor_result"},
                },
            ],
            "model": "claude-opus-4-20250514",
            "usage": {"input_tokens": 1},
            "id": "msg-1",
            "stop_reason": "end_turn",
        },
        "parent_tool_use_id": "parent-1",
        "error": "rate_limit",
        "session_id": "sess-1",
        "uuid": "assistant-uuid",
    }

    result = ResultMessage(
        subtype="success",
        duration_ms=12,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id="sess-1",
        stop_reason="end_turn",
        total_cost_usd=0.01,
        usage={"input_tokens": 1},
        result="done",
        structured_output={"ok": True},
        model_usage={"claude-opus": {"input_tokens": 1}},
        permission_denials=[{"tool": "edit"}],
        deferred_tool_use=DeferredToolUse(id="tool-2", name="edit", input={"path": "a.txt"}),
        errors=["soft warning"],
        uuid="result-uuid",
    )
    assert _to_stream_json(result) == {
        "type": "result",
        "subtype": "success",
        "duration_ms": 12,
        "duration_api_ms": 8,
        "is_error": False,
        "num_turns": 1,
        "session_id": "sess-1",
        "stop_reason": "end_turn",
        "total_cost_usd": 0.01,
        "usage": {"input_tokens": 1},
        "result": "done",
        "structured_output": {"ok": True},
        "modelUsage": {"claude-opus": {"input_tokens": 1}},
        "permission_denials": [{"tool": "edit"}],
        "deferred_tool_use": {"id": "tool-2", "name": "edit", "input": {"path": "a.txt"}},
        "errors": ["soft warning"],
        "uuid": "result-uuid",
    }

    assert _to_stream_json(
        StreamEvent(
            uuid="stream-uuid",
            session_id="sess-1",
            event={"type": "content_block_delta", "delta": {"text": "part"}},
            parent_tool_use_id="parent-1",
        )
    ) == {
        "type": "content_block_delta",
        "delta": {"text": "part"},
        "session_id": "sess-1",
        "uuid": "stream-uuid",
        "parent_tool_use_id": "parent-1",
    }

    assert _to_stream_json(
        StreamEvent(
            uuid="start-uuid",
            session_id="sess-2",
            event={
                "type": "message_start",
                "message": {
                    "id": "msg_123",
                    "content": [],
                    "model": "claude-sonnet-4-6",
                    "usage": {"input_tokens": 2},
                    "stop_reason": "end_turn",
                },
            },
        )
    ) == {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [],
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 2},
            "id": "msg_123",
            "stop_reason": "end_turn",
        },
        "session_id": "sess-2",
        "uuid": "start-uuid",
    }

    assert _to_stream_json(
        RateLimitEvent(
            rate_limit_info=RateLimitInfo(
                status="allowed_warning",
                resets_at=123,
                rate_limit_type="requests_per_minute",
                utilization=0.9,
                overage_status="allowed",
                overage_resets_at=456,
                overage_disabled_reason=None,
                raw={"status": "allowed_warning", "custom": "value"},
            ),
            uuid="rate-uuid",
            session_id="sess-1",
        )
    ) == {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed_warning",
            "resetsAt": 123,
            "rateLimitType": "requests_per_minute",
            "utilization": 0.9,
            "overageStatus": "allowed",
            "overageResetsAt": 456,
            "overageDisabledReason": None,
            "custom": "value",
        },
        "session_id": "sess-1",
        "uuid": "rate-uuid",
    }

    assert _to_stream_json(SystemMessage(subtype="init", data={"type": "system"})) == {
        "type": "system"
    }
    assert _to_stream_json(object()) is None


@pytest.mark.asyncio
async def test_start_resets_initial_prompt_flag_when_send_fails(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory([[]])
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path), initial_prompt="setup")
    transport.send_message = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="boom"):
        await transport.start()

    assert transport._initial_prompt_sent is False


@pytest.mark.asyncio
async def test_stop_swallows_client_exit_errors(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory([[]])
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    await transport.start()
    assert factory.client is not None

    factory.client.__aexit__ = AsyncMock(side_effect=RuntimeError("stop failed"))  # type: ignore[method-assign]

    await transport.stop()

    assert transport.is_alive is False


@pytest.mark.asyncio
async def test_translate_and_emit_ignore_unknown_messages_and_callback_failures(tmp_path) -> None:
    transport = SDKTransport(workspace_dir=str(tmp_path))

    async def bad_callback(event: dict) -> None:
        raise RuntimeError(f"bad callback for {event['type']}")

    transport.on_event(bad_callback)

    translated = await transport._translate_sdk_message(_assistant_message(TextBlock(text="hello")))
    assert translated is not None
    await transport._emit_event(translated)
    assert await transport._translate_sdk_message(object()) is None

    assert transport.last_result is None


@pytest.mark.asyncio
async def test_send_control_updates_sdk_client_and_buffers_active_steers(
    monkeypatch, tmp_path
) -> None:
    factory = _ClientFactory([[]])
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    await transport.start()
    assert factory.client is not None

    await transport.send_control("set_model", model="claude-sonnet-4-6")
    await transport.send_control("set_permission_mode", permissionMode="plan")
    await transport.send_control("rewind_files", user_message_id="msg_123")

    transport._turn_active = True
    await transport.send_control("steer", content="Focus on tests first")
    await transport.send_control("steer", content="Then summarize the findings")

    factory.client.set_model.assert_awaited_once_with("claude-sonnet-4-6")
    factory.client.set_permission_mode.assert_awaited_once_with("plan")
    factory.client.rewind_files.assert_awaited_once_with("msg_123")
    factory.client.interrupt.assert_awaited_once()
    assert transport._pending_steers == [
        "Focus on tests first",
        "Then summarize the findings",
    ]


@pytest.mark.asyncio
async def test_send_control_redirect_without_active_turn_uses_send_message(
    monkeypatch, tmp_path
) -> None:
    factory = _ClientFactory([[]])
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    await transport.start()
    transport.send_message = AsyncMock()  # type: ignore[method-assign]

    await transport.send_control("redirect", content="Use the fallback plan")
    await transport.send_control("steer", content="")  # no-op branch

    transport.send_message.assert_awaited_once_with("Use the fallback plan")


@pytest.mark.asyncio
async def test_send_control_handles_disconnect_and_interrupt_variants(
    monkeypatch, tmp_path
) -> None:
    factory = _ClientFactory([[]])
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    assert transport.is_turn_active is False
    await transport.send_control("interrupt")

    await transport.start()
    assert factory.client is not None

    await transport.send_control("interrupt")
    await transport.send_control("steer", content="Stay focused")

    factory.client.interrupt.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_message_retries_transient_provider_errors_without_visible_output(
    monkeypatch, tmp_path
) -> None:
    factory = _ClientFactory(
        [
            [_result_message(result="Internal server error, try again in a moment", is_error=True)],
            [_assistant_message(TextBlock(text="Recovered")), _result_message(result="Recovered")],
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)
    sleep_mock = AsyncMock()
    monkeypatch.setattr("skuld.transports.sdk.asyncio.sleep", sleep_mock)

    received: list[dict] = []

    async def on_event(event: dict) -> None:
        received.append(event)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    transport.on_event(on_event)

    await transport.start()
    await transport.send_message("hello")

    assert factory.client is not None
    assert factory.client.query.await_count == 2
    sleep_mock.assert_awaited_once_with(1.0)
    assert received[-1]["result"] == "Recovered"
    assert transport.last_result == received[-1]


@pytest.mark.asyncio
async def test_send_message_consumes_pending_steers_as_continuation(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory(
        [
            [_result_message(result="first")],
            [_result_message(result="second")],
        ]
    )
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    transport._pending_steers = ["follow the new direction"]

    await transport.start()
    await transport.send_message("initial prompt")

    assert factory.client is not None
    assert [call.args[0] for call in factory.client.query.await_args_list] == [
        "initial prompt",
        "follow the new direction",
    ]


def test_pending_steers_and_visibility_helpers() -> None:
    transport = SDKTransport("/tmp")
    transport._pending_steers = ["one", "two"]

    merged = transport._consume_pending_steers()
    assert "multiple steering updates" in merged
    assert "- one" in merged
    assert "- two" in merged
    assert transport._consume_pending_steers() is None

    assert (
        transport._is_visible_output_event({"type": "assistant", "message": {"content": []}})
        is False
    )
    assert (
        transport._is_visible_output_event(
            {"type": "content_block_start", "content_block": {"type": "thinking"}}
        )
        is True
    )
    assert (
        transport._is_visible_output_event(
            {"type": "content_block_delta", "delta": {"thinking": "plan"}}
        )
        is True
    )
    assert (
        transport._should_retry_transient_result(
            _result_message(result="provider status page says overloaded", is_error=True),
            {"type": "result", "result": "provider status page says overloaded"},
        )
        is True
    )
    assert (
        transport._should_retry_transient_result(
            _result_message(result="hard failure", is_error=False),
            {"type": "result", "result": "hard failure"},
        )
        is False
    )
    assert (
        transport._should_retry_transient_result(
            _result_message(result="", is_error=True),
            {"type": "result", "result": ""},
        )
        is False
    )
    assert transport._is_visible_output_event({"type": "assistant", "message": "bad"}) is False
    assert transport._is_visible_output_event({"type": "content_block_delta", "delta": []}) is False


@pytest.mark.asyncio
async def test_resume_session_id_sets_resume_option_and_skips_initial_prompt(
    monkeypatch, tmp_path
) -> None:
    """A seeded resume id reloads the prior conversation: ClaudeAgentOptions.resume
    is set and the initial prompt is NOT replayed (it is already in history)."""
    factory = _ClientFactory([[]])
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(
        workspace_dir=str(tmp_path),
        initial_prompt="Warm up.",
        resume_session_id="sess-resume-1",
    )

    await transport.start()

    assert factory.options.resume == "sess-resume-1"
    assert factory.client.query.await_count == 0


@pytest.mark.asyncio
async def test_no_resume_option_without_seed(monkeypatch, tmp_path) -> None:
    factory = _ClientFactory([[]])
    monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", factory)

    transport = SDKTransport(workspace_dir=str(tmp_path))
    await transport.start()

    assert factory.options.resume is None

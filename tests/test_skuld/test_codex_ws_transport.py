"""Tests for CodexWebSocketTransport (Codex app-server over WebSocket)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.transports.codex_ws import (
    CodexWebSocketTransport,
    _pick_free_port,
    _rpc_notification,
    _rpc_request,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transport(tmp_path, **kwargs):
    """Create a transport with defaults suitable for testing."""
    defaults = {
        "workspace_dir": str(tmp_path),
        "model": "o4-mini",
        "codex_port": 19999,
    }
    defaults.update(kwargs)
    return CodexWebSocketTransport(**defaults)


def _collect_emits(transport):
    """Attach an AsyncMock to _emit and return it for assertion."""
    mock = AsyncMock()
    transport._emit = mock
    return mock


def _emitted_events(mock):
    """Return all events passed to _emit as a list."""
    return [call[0][0] for call in mock.call_args_list]


def _events_of_type(mock, event_type):
    """Filter emitted events by type."""
    return [e for e in _emitted_events(mock) if e.get("type") == event_type]


class FakeWebSocket:
    """Simulates a websockets ClientConnection for testing."""

    def __init__(self):
        self.sent: list[str] = []
        self._closed = False
        self._recv_queue: asyncio.Queue = asyncio.Queue()

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self._closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await asyncio.wait_for(self._recv_queue.get(), timeout=0.1)
        except TimeoutError:
            raise StopAsyncIteration

    def inject(self, msg: dict) -> None:
        """Push a message into the receive queue."""
        self._recv_queue.put_nowait(json.dumps(msg))


# ---------------------------------------------------------------------------
# Unit tests: RPC helpers
# ---------------------------------------------------------------------------


class TestRpcHelpers:
    def test_rpc_request_structure(self):
        rid, msg = _rpc_request("test/method", {"key": "val"})
        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] == rid
        assert msg["method"] == "test/method"
        assert msg["params"] == {"key": "val"}

    def test_rpc_request_no_params(self):
        rid, msg = _rpc_request("test/method")
        assert "params" not in msg

    def test_rpc_notification_structure(self):
        msg = _rpc_notification("initialized")
        assert msg["jsonrpc"] == "2.0"
        assert msg["method"] == "initialized"
        assert "id" not in msg

    def test_pick_free_port(self):
        port = _pick_free_port()
        assert isinstance(port, int)
        assert 1024 <= port <= 65535


# ---------------------------------------------------------------------------
# Transport construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults(self, tmp_path):
        t = _make_transport(tmp_path)
        assert t.workspace_dir == str(tmp_path)
        assert t._model == "o4-mini"
        assert t._codex_port == 19999
        assert t.session_id is None
        assert t.last_result is None
        assert t.is_alive is False

    def test_capabilities(self, tmp_path):
        t = _make_transport(tmp_path)
        caps = t.capabilities
        assert caps.session_resume is True
        assert caps.interrupt is True
        assert caps.set_model is True
        assert caps.permission_requests is True
        assert caps.cli_websocket is False
        assert caps.set_thinking_tokens is False
        assert caps.rewind_files is False
        assert caps.mcp_set_servers is False
        assert caps.slash_commands is False
        assert caps.skills is False

    def test_init_with_mcp_servers(self, tmp_path):
        t = _make_transport(
            tmp_path,
            mcp_servers=[
                {
                    "name": "mimir-local",
                    "command": "python3",
                    "args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir"],
                }
            ],
        )
        assert any(
            key == "mcp_servers.mimir-local.command" and value == '"python3"'
            for key, value in t._mcp_overrides
        )


# ---------------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------------


class TestHandshake:
    @pytest.mark.asyncio
    async def test_handshake_sends_initialize_and_thread_start(self, tmp_path):
        t = _make_transport(tmp_path)
        ws = FakeWebSocket()
        t._ws = ws
        t._alive = True

        original_params = []

        async def fake_send_rpc(method, params=None):
            original_params.append((method, params))
            if method == "initialize":
                return {"userAgent": "codex/0.114.0"}
            if method == "thread/start":
                return {"thread": {"id": "thread-abc-123"}}
            return {}

        t._send_rpc = fake_send_rpc
        t._send_notification = AsyncMock()
        emit = _collect_emits(t)

        await t._handshake()

        assert original_params[0][0] == "initialize"
        assert original_params[0][1]["clientInfo"]["name"] == "skuld"
        t._send_notification.assert_called_once_with("initialized")
        assert original_params[1][0] == "thread/start"
        assert t._thread_id == "thread-abc-123"

        init_event = emit.call_args[0][0]
        assert init_event["type"] == "system"
        assert init_event["subtype"] == "init"
        assert init_event["session_id"] == "thread-abc-123"

    @pytest.mark.asyncio
    async def test_handshake_resumes_seeded_thread(self, tmp_path):
        """A seeded resume id reattaches via thread/resume instead of thread/start."""
        t = _make_transport(tmp_path, resume_session_id="thread-imported-1")
        t._ws = FakeWebSocket()
        t._alive = True

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            if method == "initialize":
                return {"userAgent": "codex"}
            if method == "thread/resume":
                return {"thread": {"id": "thread-imported-1"}}
            return {}

        t._send_rpc = fake_send_rpc
        t._send_notification = AsyncMock()
        emit = _collect_emits(t)

        await t._handshake()

        methods = [method for method, _ in calls]
        assert "thread/resume" in methods
        assert "thread/start" not in methods
        resume_params = calls[1][1]
        assert resume_params["threadId"] == "thread-imported-1"
        assert t._thread_id == "thread-imported-1"

        init_event = emit.call_args[0][0]
        assert init_event["type"] == "system"
        assert init_event["session_id"] == "thread-imported-1"

    @pytest.mark.asyncio
    async def test_handshake_with_system_prompt(self, tmp_path):
        t = _make_transport(tmp_path, system_prompt="Be helpful")
        t._ws = FakeWebSocket()
        t._alive = True

        params_captured = []

        async def fake_send_rpc(method, params=None):
            params_captured.append((method, params))
            if method == "initialize":
                return {"userAgent": "codex"}
            if method == "thread/start":
                return {"thread": {"id": "t-1"}}
            return {}

        t._send_rpc = fake_send_rpc
        t._send_notification = AsyncMock()
        _collect_emits(t)

        await t._handshake()

        thread_params = params_captured[1][1]
        assert thread_params["baseInstructions"] == "Be helpful"

    @pytest.mark.asyncio
    async def test_handshake_skip_permissions(self, tmp_path):
        t = _make_transport(tmp_path, skip_permissions=True)
        t._ws = FakeWebSocket()
        t._alive = True

        params_captured = []

        async def fake_send_rpc(method, params=None):
            params_captured.append((method, params))
            if method == "initialize":
                return {"userAgent": "codex"}
            if method == "thread/start":
                return {"thread": {"id": "t-1"}}
            return {}

        t._send_rpc = fake_send_rpc
        t._send_notification = AsyncMock()
        _collect_emits(t)

        await t._handshake()

        thread_params = params_captured[1][1]
        assert thread_params["approvalPolicy"] == "never"
        assert thread_params["sandbox"] == "danger-full-access"

    @pytest.mark.asyncio
    async def test_handshake_configured_permission_params(self, tmp_path):
        t = _make_transport(
            tmp_path,
            skip_permissions=False,
            approval_policy="untrusted",
            sandbox="workspace-write",
        )
        t._ws = FakeWebSocket()
        t._alive = True

        params_captured = []

        async def fake_send_rpc(method, params=None):
            params_captured.append((method, params))
            if method == "initialize":
                return {"userAgent": "codex"}
            if method == "thread/start":
                return {"thread": {"id": "t-1"}}
            return {}

        t._send_rpc = fake_send_rpc
        t._send_notification = AsyncMock()
        _collect_emits(t)

        await t._handshake()

        thread_params = params_captured[1][1]
        assert thread_params["approvalPolicy"] == "untrusted"
        assert thread_params["sandbox"] == "workspace-write"

    @pytest.mark.asyncio
    async def test_handshake_without_overrides_defers_to_codex_config(self, tmp_path):
        t = _make_transport(tmp_path, skip_permissions=False)
        t._ws = FakeWebSocket()
        t._alive = True

        params_captured = []

        async def fake_send_rpc(method, params=None):
            params_captured.append((method, params))
            if method == "initialize":
                return {"userAgent": "codex"}
            if method == "thread/start":
                return {"thread": {"id": "t-1"}}
            return {}

        t._send_rpc = fake_send_rpc
        t._send_notification = AsyncMock()
        _collect_emits(t)

        await t._handshake()

        thread_params = params_captured[1][1]
        assert "approvalPolicy" not in thread_params
        assert "sandbox" not in thread_params


class TestSpawnAppServer:
    @pytest.mark.asyncio
    async def test_spawn_app_server_passes_mcp_overrides(self, tmp_path):
        t = _make_transport(
            tmp_path,
            mcp_servers=[
                {
                    "name": "mimir-local",
                    "command": "python3",
                    "args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir"],
                }
            ],
        )

        mock_process = MagicMock()
        mock_process.stdout = None
        mock_process.stderr = None
        mock_process.pid = 12345

        with patch(
            "skuld.transports.codex_ws.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec, patch(
            "skuld.transports.codex_ws.resolve_codex_cli",
            return_value="/Applications/Codex.app/Contents/Resources/codex",
        ), patch(
            "skuld.transports.codex_ws.ensure_codex_tool_shims",
            return_value=(tmp_path / ".skuld-tools" / "bin", {"PATH": "/tmp/shims:/usr/bin"}),
        ):
            mock_exec.return_value = mock_process
            await t._spawn_app_server()

            call_args = mock_exec.call_args[0]
            assert call_args[:3] == (
                "/Applications/Codex.app/Contents/Resources/codex",
                "app-server",
                "--listen",
            )
            assert call_args[3].startswith("unix://")
            assert t._codex_socket_path is not None
            assert call_args[3] == f"unix://{t._codex_socket_path}"
            assert "-c" in call_args
            assert any(
                arg == 'mcp_servers.mimir-local.command="python3"' for arg in call_args
            )
            assert mock_exec.call_args.kwargs["env"]["PATH"] == "/tmp/shims:/usr/bin"

    @pytest.mark.asyncio
    async def test_spawn_app_server_defaults_codex_home_from_home(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("CODEX_HOME", raising=False)
        t = _make_transport(tmp_path)

        mock_process = MagicMock()
        mock_process.stdout = None
        mock_process.stderr = None
        mock_process.pid = 12345

        with patch(
            "skuld.transports.codex_ws.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec, patch(
            "skuld.transports.codex_ws.resolve_codex_cli",
            return_value="/Applications/Codex.app/Contents/Resources/codex",
        ), patch(
            "skuld.transports.codex_ws.ensure_codex_tool_shims",
            return_value=(tmp_path / ".skuld-tools" / "bin", {}),
        ):
            mock_exec.return_value = mock_process
            await t._spawn_app_server()

        assert mock_exec.call_args.kwargs["env"]["CODEX_HOME"] == str(home / ".codex")

    @pytest.mark.asyncio
    async def test_spawn_app_server_preserves_existing_codex_home(self, tmp_path, monkeypatch):
        codex_home = tmp_path / "custom-codex-home"
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        t = _make_transport(tmp_path)

        mock_process = MagicMock()
        mock_process.stdout = None
        mock_process.stderr = None
        mock_process.pid = 12345

        with patch(
            "skuld.transports.codex_ws.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec, patch(
            "skuld.transports.codex_ws.resolve_codex_cli",
            return_value="/Applications/Codex.app/Contents/Resources/codex",
        ), patch(
            "skuld.transports.codex_ws.ensure_codex_tool_shims",
            return_value=(tmp_path / ".skuld-tools" / "bin", {}),
        ):
            mock_exec.return_value = mock_process
            await t._spawn_app_server()

        assert mock_exec.call_args.kwargs["env"]["CODEX_HOME"] == str(codex_home)


class TestFallbackTransport:
    @pytest.mark.asyncio
    async def test_start_falls_back_to_subprocess_when_app_server_startup_fails(self, tmp_path):
        t = _make_transport(tmp_path, initial_prompt="Investigate this")
        emit = AsyncMock()
        t.on_event(emit)
        t._spawn_app_server = AsyncMock()
        t._connect_ws = AsyncMock(side_effect=RuntimeError("uds handshake failed"))
        t._handshake = AsyncMock()

        fallback = MagicMock()
        fallback.start = AsyncMock()
        fallback.send_message = AsyncMock()
        fallback.stop = AsyncMock()
        fallback.session_id = None
        fallback.last_result = None
        fallback.is_alive = True
        fallback.is_turn_active = False

        with patch(
            "skuld.transports.codex_ws.CodexSubprocessTransport",
            return_value=fallback,
        ) as mock_fallback_cls:
            await t.start()

        mock_fallback_cls.assert_called_once()
        fallback.on_event.assert_called_once_with(emit)
        fallback.start.assert_called_once()
        fallback.send_message.assert_called_once_with("Investigate this")
        assert t._fallback_transport is fallback


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_calls_turn_start(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        t._alive = True

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {}

        t._send_rpc = fake_send_rpc

        await t.send_message("hello world")

        assert len(calls) == 1
        assert calls[0][0] == "turn/start"
        params = calls[0][1]
        assert params["threadId"] == "thread-1"
        assert params["input"][0]["type"] == "text"
        assert params["input"][0]["text"] == "hello world"
        assert params["input"][0]["textElements"] == []

    @pytest.mark.asyncio
    async def test_send_message_resets_state(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        t._last_result = {"old": True}
        t._last_usage = {"old": True}
        t._block_index = 5

        async def fake_send_rpc(method, params=None):
            return {}

        t._send_rpc = fake_send_rpc

        await t.send_message("test")

        assert t._last_result is None
        assert t._last_usage is None
        assert t._block_index == 0

    @pytest.mark.asyncio
    async def test_send_message_without_thread_raises(self, tmp_path):
        t = _make_transport(tmp_path)
        with pytest.raises(RuntimeError, match="No active thread"):
            await t.send_message("test")


# ---------------------------------------------------------------------------
# Event normalization (notifications)
# ---------------------------------------------------------------------------


class TestEventNormalization:
    @pytest.mark.asyncio
    async def test_agent_message_delta(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "t1",
                    "turnId": "turn1",
                    "itemId": "i1",
                    "delta": "Hello ",
                },
            }
        )

        events = _emitted_events(emit)
        assert len(events) == 1
        assert events[0]["type"] == "content_block_delta"
        assert events[0]["delta"]["type"] == "text_delta"
        assert events[0]["delta"]["text"] == "Hello "

    @pytest.mark.asyncio
    async def test_reasoning_delta(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "item/reasoning/textDelta",
                "params": {"threadId": "t1", "turnId": "turn1", "delta": "thinking..."},
            }
        )

        event = emit.call_args[0][0]
        assert event["delta"]["type"] == "thinking_delta"
        assert event["delta"]["thinking"] == "thinking..."

    @pytest.mark.asyncio
    async def test_reasoning_summary_delta(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "item/reasoning/summaryTextDelta",
                "params": {"threadId": "t1", "turnId": "turn1", "delta": "summary"},
            }
        )

        event = emit.call_args[0][0]
        assert event["delta"]["type"] == "thinking_delta"

    @pytest.mark.asyncio
    async def test_turn_started_emits_assistant_event(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "t1",
                    "turn": {"id": "turn-42", "items": [], "status": "running", "error": None},
                },
            }
        )

        assert t._current_turn_id == "turn-42"
        assert t._block_index == 0

        # Should emit an assistant event to start a new streaming message
        events = _events_of_type(emit, "assistant")
        assert len(events) == 1
        assert events[0]["message"]["model"] == "o4-mini"
        assert events[0]["message"]["content"] == []

    @pytest.mark.asyncio
    async def test_turn_completed_emits_result_with_usage(self, tmp_path):
        t = _make_transport(tmp_path)
        t._current_turn_id = "turn-42"
        # Simulate usage arriving before turn/completed
        t._last_usage = {
            "o4-mini": {
                "inputTokens": 500,
                "outputTokens": 200,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
            }
        }
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t1",
                    "turn": {
                        "id": "turn-42",
                        "items": [],
                        "status": "completed",
                        "error": None,
                    },
                },
            }
        )

        assert t._current_turn_id is None
        result_events = _events_of_type(emit, "result")
        assert len(result_events) == 1
        result = result_events[0]
        assert result["stop_reason"] == "end_turn"
        assert result["modelUsage"]["o4-mini"]["inputTokens"] == 500
        assert result["modelUsage"]["o4-mini"]["outputTokens"] == 200

    @pytest.mark.asyncio
    async def test_turn_completed_without_usage(self, tmp_path):
        t = _make_transport(tmp_path)
        t._current_turn_id = "turn-1"
        t._last_usage = None
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t1",
                    "turn": {"id": "turn-1", "items": [], "status": "completed", "error": None},
                },
            }
        )

        result = _events_of_type(emit, "result")[0]
        assert result["modelUsage"] == {}

    @pytest.mark.asyncio
    async def test_token_usage_saves_and_emits_message_delta(self, tmp_path):
        t = _make_transport(tmp_path, model="o4-mini")
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "t1",
                    "turnId": "turn1",
                    "tokenUsage": {
                        "total": {
                            "totalTokens": 1500,
                            "inputTokens": 1000,
                            "cachedInputTokens": 200,
                            "outputTokens": 300,
                            "reasoningOutputTokens": 0,
                        },
                        "last": {
                            "totalTokens": 500,
                            "inputTokens": 400,
                            "cachedInputTokens": 50,
                            "outputTokens": 100,
                            "reasoningOutputTokens": 0,
                        },
                    },
                },
            }
        )

        # Usage saved for later result event
        assert t._last_usage is not None
        usage = t._last_usage["o4-mini"]
        # Should prefer "last" over "total"
        assert usage["inputTokens"] == 400
        assert usage["outputTokens"] == 100
        assert usage["cacheReadInputTokens"] == 50

        # message_delta emitted for browser token counter
        delta_events = _events_of_type(emit, "message_delta")
        assert len(delta_events) == 1
        assert delta_events[0]["usage"]["output_tokens"] == 100

    @pytest.mark.asyncio
    async def test_error_notification_uses_error_field(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "error",
                "params": {
                    "error": {"message": "rate limit exceeded"},
                    "willRetry": False,
                    "threadId": "t1",
                    "turnId": "turn1",
                },
            }
        )

        event = emit.call_args[0][0]
        assert event["type"] == "error"
        assert event["error"] == "rate limit exceeded"

    @pytest.mark.asyncio
    async def test_thread_closed_sets_not_alive(self, tmp_path):
        t = _make_transport(tmp_path)
        t._alive = True
        _collect_emits(t)

        await t._handle_server_message({"method": "thread/closed", "params": {"threadId": "t1"}})

        assert t._alive is False


# ---------------------------------------------------------------------------
# Item lifecycle (tool calls) — browser + broker event shapes
# ---------------------------------------------------------------------------


class TestItemLifecycle:
    @pytest.mark.asyncio
    async def test_command_execution_started_emits_assistant_and_blocks(self, tmp_path):
        """Tool start should emit both assistant (broker) and content_block (browser) events."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_started(
            {
                "type": "commandExecution",
                "id": "cmd-1",
                "command": "ls -la",
                "cwd": "/workspace",
            }
        )

        events = _emitted_events(emit)

        # 1. assistant event for broker artifact tracking
        assistant_events = [e for e in events if e.get("type") == "assistant"]
        assert len(assistant_events) == 1
        msg = assistant_events[0]["message"]
        assert msg["model"] == "o4-mini"
        tool_block = msg["content"][0]
        assert tool_block["type"] == "tool_use"
        assert tool_block["id"] == "cmd-1"
        assert tool_block["name"] == "Bash"
        assert tool_block["input"]["command"] == "ls -la"

        # 2. content_block_start for browser rendering
        block_starts = [e for e in events if e.get("type") == "content_block_start"]
        assert len(block_starts) == 1
        assert block_starts[0]["content_block"]["type"] == "tool_use"
        assert block_starts[0]["content_block"]["name"] == "Bash"

        # 3. input_json_delta for browser tool input
        deltas = [
            e
            for e in events
            if e.get("type") == "content_block_delta"
            and e.get("delta", {}).get("type") == "input_json_delta"
        ]
        assert len(deltas) == 1
        parsed = json.loads(deltas[0]["delta"]["partial_json"])
        assert parsed["command"] == "ls -la"

    @pytest.mark.asyncio
    async def test_file_change_started(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_started(
            {
                "type": "fileChange",
                "id": "fc-1",
                "changes": [{"path": "foo.py", "diff": "+hello"}],
            }
        )

        assistant_events = _events_of_type(emit, "assistant")
        assert assistant_events[0]["message"]["content"][0]["name"] == "Edit"

    @pytest.mark.asyncio
    async def test_command_execution_completed_emits_tool_result(self, tmp_path):
        """Command completion should close the tool_use block and emit a
        tool_result content block paired by id, so the UI groups it under
        the call instead of leaking output into the chat as plain text.
        """
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_completed(
            {
                "type": "commandExecution",
                "id": "cmd-1",
                "aggregatedOutput": "file1.py\nfile2.py",
                "exitCode": 0,
            }
        )

        events = _emitted_events(emit)

        # First stop closes the tool_use input block; second closes the result.
        stops = [e for e in events if e.get("type") == "content_block_stop"]
        assert len(stops) == 2

        starts = [e for e in events if e.get("type") == "content_block_start"]
        assert len(starts) == 1
        result_block = starts[0]["content_block"]
        assert result_block["type"] == "tool_result"
        assert result_block["tool_use_id"] == "cmd-1"
        assert "file1.py" in result_block["content"]
        assert "is_error" not in result_block

        # No fresh text content_block — that was the bug.
        text_starts = [s for s in starts if s["content_block"].get("type") == "text"]
        assert text_starts == []

    @pytest.mark.asyncio
    async def test_command_execution_failed_shows_exit_code(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_completed(
            {
                "type": "commandExecution",
                "id": "cmd-1",
                "aggregatedOutput": "error: not found",
                "exitCode": 1,
            }
        )

        events = _emitted_events(emit)
        starts = [e for e in events if e.get("type") == "content_block_start"]
        assert len(starts) == 1
        result_block = starts[0]["content_block"]
        assert result_block["type"] == "tool_result"
        assert result_block["tool_use_id"] == "cmd-1"
        assert "[exit code 1]" in result_block["content"]
        assert result_block["is_error"] is True

    @pytest.mark.asyncio
    async def test_agent_message_started_emits_text_block(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_started({"type": "agentMessage", "id": "msg-1", "text": ""})

        block_starts = _events_of_type(emit, "content_block_start")
        assert len(block_starts) == 1
        assert block_starts[0]["content_block"]["type"] == "text"

    @pytest.mark.asyncio
    async def test_agent_message_completed_emits_stop(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_completed({"type": "agentMessage", "id": "msg-1", "text": "done"})

        stops = _events_of_type(emit, "content_block_stop")
        assert len(stops) == 1

    @pytest.mark.asyncio
    async def test_reasoning_started_emits_thinking_block(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_started({"type": "reasoning", "id": "r-1"})

        block_starts = _events_of_type(emit, "content_block_start")
        assert block_starts[0]["content_block"]["type"] == "thinking"

    @pytest.mark.asyncio
    async def test_reasoning_completed_emits_stop(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_completed({"type": "reasoning", "id": "r-1"})

        stops = _events_of_type(emit, "content_block_stop")
        assert len(stops) == 1

    @pytest.mark.asyncio
    async def test_mcp_tool_call_started(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_started(
            {
                "type": "mcpToolCall",
                "id": "mcp-1",
                "server": "mimir",
                "tool": "read_file",
                "arguments": {"path": "/tmp/test.py"},
            }
        )

        assistant_events = _events_of_type(emit, "assistant")
        assert assistant_events[0]["message"]["content"][0]["name"] == "Read"

    @pytest.mark.asyncio
    async def test_web_search_started(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_started({"type": "webSearch", "id": "ws-1", "query": "python async"})

        assistant_events = _events_of_type(emit, "assistant")
        assert assistant_events[0]["message"]["content"][0]["name"] == "WebSearch"
        assert assistant_events[0]["message"]["content"][0]["input"]["query"] == "python async"

    @pytest.mark.asyncio
    async def test_block_index_increments(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_started({"type": "agentMessage", "id": "m1", "text": ""})
        await t._handle_item_started({"type": "reasoning", "id": "r1"})

        block_starts = _events_of_type(emit, "content_block_start")
        assert block_starts[0]["index"] == 0
        assert block_starts[1]["index"] == 1


# ---------------------------------------------------------------------------
# Control: interrupt, set_model
# ---------------------------------------------------------------------------


class TestControl:
    @pytest.mark.asyncio
    async def test_interrupt_sends_turn_interrupt(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        t._current_turn_id = "turn-5"

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {}

        t._send_rpc = fake_send_rpc

        await t.send_control("interrupt")

        assert calls[0][0] == "turn/interrupt"
        assert calls[0][1]["threadId"] == "thread-1"
        assert calls[0][1]["turnId"] == "turn-5"

    @pytest.mark.asyncio
    async def test_interrupt_no_turn_does_nothing(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        t._current_turn_id = None

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {}

        t._send_rpc = fake_send_rpc

        await t.send_control("interrupt")
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_set_model(self, tmp_path):
        t = _make_transport(tmp_path, model="o4-mini")
        await t.send_control("set_model", model="o3")
        assert t._model == "o3"

    @pytest.mark.asyncio
    async def test_steer_sends_turn_steer(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        t._current_turn_id = "turn-5"

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {}

        t._send_rpc = fake_send_rpc

        await t.send_control("steer", content="Focus on the smaller patch")

        assert calls[0][0] == "turn/steer"
        assert calls[0][1]["threadId"] == "thread-1"
        assert calls[0][1]["expectedTurnId"] == "turn-5"
        assert calls[0][1]["input"][0]["text"] == "Focus on the smaller patch"

    @pytest.mark.asyncio
    async def test_redirect_interrupts_active_turn(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        t._current_turn_id = "turn-5"

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {}

        t._send_rpc = fake_send_rpc

        await t.send_control("redirect", content="Actually do option B instead")

        assert t._pending_redirects == ["Actually do option B instead"]
        assert t._redirect_interrupt_requested is True
        assert calls[0][0] == "turn/interrupt"
        assert calls[0][1]["threadId"] == "thread-1"
        assert calls[0][1]["turnId"] == "turn-5"

    @pytest.mark.asyncio
    async def test_redirect_without_active_turn_starts_new_turn(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        t._current_turn_id = None

        send_message = AsyncMock()
        t.send_message = send_message

        await t.send_control("redirect", content="Start fresh")

        send_message.assert_awaited_once_with("Start fresh")

    @pytest.mark.asyncio
    async def test_redirect_normalizes_structured_content(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        t._current_turn_id = "turn-5"

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {}

        t._send_rpc = fake_send_rpc

        await t.send_control(
            "redirect",
            content=[
                {"type": "text", "text": "Use the attached screenshot instead"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "a" * 1000,
                    },
                },
            ],
        )

        assert t._pending_redirects == [
            (
                "Use the attached screenshot instead\n\n"
                "[User attached 1 image attachment. This transport forwards text only.]"
            )
        ]
        assert t._redirect_interrupt_requested is True
        assert calls[0][0] == "turn/interrupt"

    @pytest.mark.asyncio
    async def test_turn_completed_restarts_with_pending_redirect(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        t._current_turn_id = "turn-5"
        t._pending_redirects = ["Do option B instead"]
        t._redirect_interrupt_requested = True
        emit = _collect_emits(t)

        send_message = AsyncMock()
        t.send_message = send_message

        await t._handle_server_message({"method": "turn/completed", "params": {}})
        await asyncio.sleep(0)

        assert t._current_turn_id is None
        assert t._redirect_interrupt_requested is False
        assert t._pending_redirects == []
        assert _events_of_type(emit, "result")

    @pytest.mark.asyncio
    async def test_send_message_rejects_oversized_payload_before_ws_send(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        t._ws = FakeWebSocket()

        oversized = "x" * 1045000

        with pytest.raises(RuntimeError, match="payload too large"):
            await t.send_message(oversized)

        assert t._ws.sent == []


# ---------------------------------------------------------------------------
# Approval / permission requests
# ---------------------------------------------------------------------------


class TestApprovals:
    @pytest.mark.asyncio
    async def test_command_approval_emits_control_request(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_request(
            {
                "id": 42,
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": "t1",
                    "turnId": "turn1",
                    "itemId": "cmd-1",
                    "command": "rm -rf /tmp/test",
                },
            }
        )

        event = emit.call_args[0][0]
        assert event["type"] == "control_request"
        assert event["tool"] == "Bash"
        assert event["input"]["command"] == "rm -rf /tmp/test"
        assert "42" in t._pending_approvals

    @pytest.mark.asyncio
    async def test_file_change_approval(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_request(
            {
                "id": 99,
                "method": "item/fileChange/requestApproval",
                "params": {"threadId": "t1", "turnId": "turn1", "itemId": "fc-1"},
            }
        )

        event = emit.call_args[0][0]
        assert event["type"] == "control_request"
        assert event["tool"] == "Edit"
        assert "99" in t._pending_approvals

    @pytest.mark.asyncio
    async def test_exec_command_approval_uses_review_decision_shape(self, tmp_path):
        t = _make_transport(tmp_path)
        t._ws = FakeWebSocket()
        emit = _collect_emits(t)

        await t._handle_server_request(
            {
                "id": 43,
                "method": "execCommandApproval",
                "params": {
                    "conversationId": "thread-1",
                    "callId": "call-1",
                    "approvalId": None,
                    "command": ["/bin/zsh", "-lc", "echo hi"],
                    "cwd": str(tmp_path),
                    "reason": "needs shell",
                    "parsedCmd": [],
                },
            }
        )

        event = emit.call_args[0][0]
        assert event["type"] == "control_request"
        assert event["tool"] == "Bash"
        assert event["input"]["command"] == "/bin/zsh -lc 'echo hi'"

        await t.send_control_response("43", {"behavior": "allow"})

        sent = json.loads(t._ws.sent[0])
        assert sent["id"] == 43
        assert sent["result"]["decision"] == "approved"

    @pytest.mark.asyncio
    async def test_send_control_response_approves(self, tmp_path):
        t = _make_transport(tmp_path)
        t._ws = FakeWebSocket()
        t._pending_approvals["42"] = 42

        await t.send_control_response("42", {"behavior": "allow"})

        sent = json.loads(t._ws.sent[0])
        assert sent["id"] == 42
        assert sent["result"]["decision"] == "accept"
        assert "42" not in t._pending_approvals

    @pytest.mark.asyncio
    async def test_send_control_response_denies(self, tmp_path):
        t = _make_transport(tmp_path)
        t._ws = FakeWebSocket()
        t._pending_approvals["42"] = 42

        await t.send_control_response("42", {"behavior": "deny"})

        sent = json.loads(t._ws.sent[0])
        assert sent["result"]["decision"] == "decline"

    @pytest.mark.asyncio
    async def test_unknown_request_auto_approved(self, tmp_path):
        t = _make_transport(tmp_path)
        t._ws = FakeWebSocket()
        _collect_emits(t)

        await t._handle_server_request(
            {
                "id": 77,
                "method": "some/unknown/request",
                "params": {},
            }
        )

        sent = json.loads(t._ws.sent[0])
        assert sent["id"] == 77
        assert sent["result"]["decision"] == "accept"

    @pytest.mark.asyncio
    async def test_dynamic_shell_command_call_executes_via_command_exec(self, tmp_path):
        t = _make_transport(tmp_path)
        t._ws = FakeWebSocket()
        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {"exitCode": 0, "stdout": "hello\n", "stderr": ""}

        t._send_rpc = fake_send_rpc

        await t._handle_server_request(
            {
                "id": 123,
                "method": "item/tool/call",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "callId": "call-1",
                    "namespace": None,
                    "tool": "shell_command",
                    "arguments": {
                        "command": "echo hello",
                        "workdir": str(tmp_path),
                        "timeout_ms": 10000,
                    },
                },
            }
        )
        await asyncio.sleep(0.01)

        assert calls == [
            (
                "command/exec",
                {
                    "command": ["/bin/zsh", "-lc", "echo hello"],
                    "cwd": str(tmp_path),
                    "timeoutMs": 10000,
                },
            )
        ]
        sent = json.loads(t._ws.sent[0])
        assert sent["id"] == 123
        assert sent["result"]["success"] is True
        assert sent["result"]["contentItems"][0]["text"] == "Exit code: 0\nstdout:\nhello\n"

    @pytest.mark.asyncio
    async def test_dynamic_shell_command_failure_returns_tool_error(self, tmp_path):
        t = _make_transport(tmp_path)
        t._ws = FakeWebSocket()

        async def fake_send_rpc(method, params=None):
            return {"exitCode": 2, "stdout": "", "stderr": "nope\n"}

        t._send_rpc = fake_send_rpc

        await t._handle_server_request(
            {
                "id": 124,
                "method": "item/tool/call",
                "params": {
                    "tool": "shell_command",
                    "arguments": {"command": "false", "workdir": str(tmp_path)},
                },
            }
        )
        await asyncio.sleep(0.01)

        sent = json.loads(t._ws.sent[0])
        assert sent["result"]["success"] is False
        assert sent["result"]["contentItems"][0]["text"] == "Exit code: 2\nstderr:\nnope\n"

    @pytest.mark.asyncio
    async def test_raw_function_shell_command_injects_output(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        emits = _collect_emits(t)
        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            if method == "command/exec":
                return {"exitCode": 0, "stdout": "hello\n", "stderr": ""}
            if method == "thread/inject_items":
                return {}
            raise AssertionError(method)

        t._send_rpc = fake_send_rpc

        await t._handle_server_message(
            {
                "method": "rawResponseItem/completed",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {
                        "type": "function_call",
                        "name": "shell_command",
                        "arguments": json.dumps(
                            {
                                "command": "echo hello",
                                "workdir": str(tmp_path),
                                "timeout_ms": 10000,
                            }
                        ),
                        "call_id": "call-raw-1",
                    },
                },
            }
        )
        await asyncio.sleep(0.01)

        assert calls == [
            (
                "command/exec",
                {
                    "command": ["/bin/zsh", "-lc", "echo hello"],
                    "cwd": str(tmp_path),
                    "timeoutMs": 10000,
                },
            ),
            (
                "thread/inject_items",
                {
                    "threadId": "thread-1",
                    "items": [
                        {
                            "type": "function_call_output",
                            "call_id": "call-raw-1",
                            "output": "Exit code: 0\nstdout:\nhello\n",
                        }
                    ],
                },
            ),
        ]
        events = _emitted_events(emits)
        assert any(
            event.get("type") == "assistant"
            and event.get("message", {}).get("content", [{}])[0].get("type") == "tool_use"
            for event in events
        )
        assert any(
            event.get("type") == "content_block_start"
            and event.get("content_block", {}).get("type") == "tool_result"
            and event.get("content_block", {}).get("tool_use_id") == "call-raw-1"
            for event in events
        )

    @pytest.mark.asyncio
    async def test_response_item_shell_command_frame_injects_output(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "thread-1"
        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            if method == "command/exec":
                return {"exitCode": 0, "stdout": str(tmp_path), "stderr": ""}
            if method == "thread/inject_items":
                return {}
            raise AssertionError(method)

        t._send_rpc = fake_send_rpc
        _collect_emits(t)

        await t._handle_response_item_frame(
            {
                "type": "function_call",
                "name": "shell_command",
                "arguments": json.dumps(
                    {
                        "command": "pwd",
                        "workdir": str(tmp_path),
                    }
                ),
                "call_id": "call-response-1",
            }
        )
        await asyncio.sleep(0.01)

        assert calls[0] == (
            "command/exec",
            {
                "command": ["/bin/zsh", "-lc", "pwd"],
                "cwd": str(tmp_path),
            },
        )
        assert calls[1][0] == "thread/inject_items"
        assert calls[1][1]["items"][0]["call_id"] == "call-response-1"


# ---------------------------------------------------------------------------
# Session resume
# ---------------------------------------------------------------------------


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_sends_thread_resume(self, tmp_path):
        t = _make_transport(tmp_path)

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {"thread": {"id": "resumed-thread"}}

        t._send_rpc = fake_send_rpc
        emit = _collect_emits(t)

        await t.resume("old-thread-id")

        assert calls[0][0] == "thread/resume"
        assert calls[0][1]["threadId"] == "old-thread-id"
        assert t._thread_id == "resumed-thread"

        init_event = emit.call_args[0][0]
        assert init_event["type"] == "system"
        assert init_event["session_id"] == "resumed-thread"


# ---------------------------------------------------------------------------
# Receive loop
# ---------------------------------------------------------------------------


class TestReceiveLoop:
    @pytest.mark.asyncio
    async def test_rpc_response_resolves_future(self, tmp_path):
        t = _make_transport(tmp_path)

        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        t._pending[1] = fut

        t._resolve_pending({"id": 1, "result": {"ok": True}})

        assert fut.done()
        assert fut.result() == {"ok": True}

    @pytest.mark.asyncio
    async def test_rpc_error_sets_exception(self, tmp_path):
        t = _make_transport(tmp_path)
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        t._pending[2] = fut

        t._resolve_pending({"id": 2, "error": {"code": -32600, "message": "bad request"}})

        assert fut.done()
        with pytest.raises(RuntimeError, match="bad request"):
            fut.result()


# ---------------------------------------------------------------------------
# Stop / cleanup
# ---------------------------------------------------------------------------


class TestStopCleanup:
    @pytest.mark.asyncio
    async def test_stop_closes_ws_and_process(self, tmp_path):
        t = _make_transport(tmp_path)
        ws = FakeWebSocket()
        t._ws = ws
        t._alive = True

        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)
        mock_process.pid = 12345
        t._process = mock_process

        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        t._pending[99] = fut

        await t.stop()

        assert t._alive is False
        assert t._ws is None
        assert t._process is None
        assert ws._closed is True
        assert fut.cancelled()


# ---------------------------------------------------------------------------
# End-to-end: full turn simulation
# ---------------------------------------------------------------------------


class TestFullTurnFlow:
    """Simulate a complete Codex turn and verify the browser sees the right event sequence."""

    @pytest.mark.asyncio
    async def test_text_turn_lifecycle(self, tmp_path):
        """turn/started → item/started(agentMessage) → deltas → item/completed → turn/completed."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        # 1. Turn starts
        await t._handle_server_message(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "t1",
                    "turn": {"id": "turn-1", "items": [], "status": "running", "error": None},
                },
            }
        )

        # 2. Agent message item starts
        await t._handle_server_message(
            {
                "method": "item/started",
                "params": {
                    "item": {"type": "agentMessage", "id": "msg-1", "text": "", "phase": None},
                    "threadId": "t1",
                    "turnId": "turn-1",
                },
            }
        )

        # 3. Text deltas
        await t._handle_server_message(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "t1",
                    "turnId": "turn-1",
                    "itemId": "msg-1",
                    "delta": "Hello ",
                },
            }
        )
        await t._handle_server_message(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "t1",
                    "turnId": "turn-1",
                    "itemId": "msg-1",
                    "delta": "world!",
                },
            }
        )

        # 4. Item completed
        await t._handle_server_message(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "agentMessage",
                        "id": "msg-1",
                        "text": "Hello world!",
                        "phase": None,
                    },
                    "threadId": "t1",
                    "turnId": "turn-1",
                },
            }
        )

        # 5. Token usage
        await t._handle_server_message(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "t1",
                    "turnId": "turn-1",
                    "tokenUsage": {
                        "total": {
                            "totalTokens": 100,
                            "inputTokens": 80,
                            "cachedInputTokens": 0,
                            "outputTokens": 20,
                            "reasoningOutputTokens": 0,
                        },
                        "last": {},
                    },
                },
            }
        )

        # 6. Turn completed
        await t._handle_server_message(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "t1",
                    "turn": {"id": "turn-1", "items": [], "status": "completed", "error": None},
                },
            }
        )

        events = _emitted_events(emit)
        types = [e["type"] for e in events]

        # Verify expected sequence
        assert "assistant" in types  # Turn start signal
        assert "content_block_start" in types  # Text block opens
        assert types.count("content_block_delta") >= 2  # Text deltas
        assert "content_block_stop" in types  # Text block closes
        assert "message_delta" in types  # Token counter
        assert "result" in types  # Turn complete

        # Result has usage
        result = _events_of_type(emit, "result")[0]
        assert result["modelUsage"]["o4-mini"]["inputTokens"] == 80

    @pytest.mark.asyncio
    async def test_tool_turn_lifecycle(self, tmp_path):
        """Tool call: item/started(commandExecution) → output → item/completed."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        # Turn starts
        await t._handle_server_message(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "t1",
                    "turn": {"id": "turn-2", "items": [], "status": "running", "error": None},
                },
            }
        )

        # Command execution starts
        await t._handle_server_message(
            {
                "method": "item/started",
                "params": {
                    "item": {
                        "type": "commandExecution",
                        "id": "cmd-1",
                        "command": "git status",
                        "cwd": "/workspace",
                    },
                    "threadId": "t1",
                    "turnId": "turn-2",
                },
            }
        )

        # Command output delta
        await t._handle_server_message(
            {
                "method": "item/commandExecution/outputDelta",
                "params": {
                    "threadId": "t1",
                    "turnId": "turn-2",
                    "itemId": "cmd-1",
                    "delta": "On branch main\n",
                },
            }
        )

        # Command completed
        await t._handle_server_message(
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "commandExecution",
                        "id": "cmd-1",
                        "command": "git status",
                        "cwd": "/workspace",
                        "aggregatedOutput": "On branch main\nnothing to commit",
                        "exitCode": 0,
                    },
                    "threadId": "t1",
                    "turnId": "turn-2",
                },
            }
        )

        events = _emitted_events(emit)

        # Assistant event for broker tracking
        assistant_events = _events_of_type(emit, "assistant")
        assert len(assistant_events) >= 2  # Turn start + tool use
        tool_assistant = [e for e in assistant_events if e.get("message", {}).get("content")]
        assert len(tool_assistant) >= 1
        tool_block = tool_assistant[-1]["message"]["content"][0]
        assert tool_block["name"] == "Bash"
        assert tool_block["id"] == "cmd-1"

        # content_block_start for tool_use
        block_starts = _events_of_type(emit, "content_block_start")
        tool_starts = [b for b in block_starts if b["content_block"].get("type") == "tool_use"]
        assert len(tool_starts) >= 1

        # Output should stay attached to the tool result, not leak as plain
        # assistant text.
        text_deltas = [
            e
            for e in events
            if e.get("type") == "content_block_delta"
            and e.get("delta", {}).get("type") == "text_delta"
        ]
        assert text_deltas == []

        result_blocks = [
            b["content_block"]
            for b in block_starts
            if b["content_block"].get("type") == "tool_result"
        ]
        assert len(result_blocks) == 1
        assert "On branch main" in result_blocks[0]["content"]


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _send_rpc timeout
# ---------------------------------------------------------------------------


class TestSendRpcTimeout:
    @pytest.mark.asyncio
    async def test_send_rpc_timeout_raises_runtime_error(self, tmp_path):
        """When the RPC future times out, _send_rpc should raise RuntimeError."""
        t = _make_transport(tmp_path)
        ws = FakeWebSocket()
        t._ws = ws

        # Patch wait_for to always raise TimeoutError
        async def fake_wait_for(fut, timeout):
            raise TimeoutError

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "wait_for", fake_wait_for)
            with pytest.raises(RuntimeError, match="RPC timeout"):
                await t._send_rpc("test/method", {"foo": "bar"})

        # The pending future should have been cleaned up
        # (the rid was popped from _pending on timeout)
        assert len(t._pending) == 0

    @pytest.mark.asyncio
    async def test_send_rpc_ws_none_raises(self, tmp_path):
        """_send_rpc with no websocket raises RuntimeError."""
        t = _make_transport(tmp_path)
        t._ws = None

        with pytest.raises(RuntimeError, match="WebSocket not connected"):
            await t._send_rpc("test/method")


# ---------------------------------------------------------------------------
# _send_notification when ws is None
# ---------------------------------------------------------------------------


class TestSendNotification:
    @pytest.mark.asyncio
    async def test_send_notification_ws_none_is_noop(self, tmp_path):
        """When ws is None, _send_notification should silently do nothing."""
        t = _make_transport(tmp_path)
        t._ws = None

        # Should not raise
        await t._send_notification("initialized")

    @pytest.mark.asyncio
    async def test_send_notification_with_ws_sends(self, tmp_path):
        """When ws is set, _send_notification should send the message."""
        t = _make_transport(tmp_path)
        ws = FakeWebSocket()
        t._ws = ws

        await t._send_notification("initialized")

        assert len(ws.sent) == 1
        msg = json.loads(ws.sent[0])
        assert msg["method"] == "initialized"
        assert "id" not in msg


# ---------------------------------------------------------------------------
# send_message edge cases
# ---------------------------------------------------------------------------


class TestSendMessageEdgeCases:
    @pytest.mark.asyncio
    async def test_send_message_without_model(self, tmp_path):
        """When model is empty string, params should not include 'model'."""
        t = _make_transport(tmp_path, model="")
        t._thread_id = "thread-1"

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {}

        t._send_rpc = fake_send_rpc

        await t.send_message("hello")

        params = calls[0][1]
        assert "model" not in params


# ---------------------------------------------------------------------------
# _handle_server_message with unknown notification
# ---------------------------------------------------------------------------


class TestUnknownNotification:
    @pytest.mark.asyncio
    async def test_unknown_notification_ignored(self, tmp_path):
        """Unknown notification methods should be silently ignored (no emit)."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {"method": "some/totally/unknown/notification", "params": {}}
        )

        # Nothing should have been emitted
        assert emit.call_count == 0

    @pytest.mark.asyncio
    async def test_thread_status_changed_ignored(self, tmp_path):
        """Informational notifications like thread/status/changed are no-ops."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {"method": "thread/status/changed", "params": {"status": "idle"}}
        )

        assert emit.call_count == 0

    @pytest.mark.asyncio
    async def test_thread_name_updated_ignored(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {"method": "thread/name/updated", "params": {"name": "new name"}}
        )

        assert emit.call_count == 0


# ---------------------------------------------------------------------------
# _resolve_pending edge cases
# ---------------------------------------------------------------------------


class TestResolvePendingEdgeCases:
    @pytest.mark.asyncio
    async def test_resolve_unknown_id_is_noop(self, tmp_path):
        """Resolving a response with an unknown id should not raise."""
        t = _make_transport(tmp_path)

        # No pending futures at all
        t._resolve_pending({"id": 999, "result": {"ok": True}})
        # Should not raise

    @pytest.mark.asyncio
    async def test_resolve_already_done_future_is_noop(self, tmp_path):
        """If the future is already done (e.g. cancelled), _resolve_pending skips it."""
        t = _make_transport(tmp_path)
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        fut.cancel()  # Mark as done
        t._pending[5] = fut

        # Should not raise even though future is cancelled
        t._resolve_pending({"id": 5, "result": {"ok": True}})

    @pytest.mark.asyncio
    async def test_resolve_pending_with_no_result_key(self, tmp_path):
        """Response with 'result' key missing should resolve with empty dict."""
        t = _make_transport(tmp_path)
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        t._pending[10] = fut

        # data has "id" but result is missing — falls through to data.get("result", {})
        t._resolve_pending({"id": 10, "jsonrpc": "2.0"})

        assert fut.done()
        assert fut.result() == {}


# ---------------------------------------------------------------------------
# capabilities property values
# ---------------------------------------------------------------------------


class TestCapabilitiesValues:
    def test_steer_is_true(self, tmp_path):
        t = _make_transport(tmp_path)
        assert t.capabilities.steer is True

    def test_steering_mode_is_live(self, tmp_path):
        t = _make_transport(tmp_path)
        assert t.capabilities.steering_mode == "live"

    def test_set_permission_mode_is_false(self, tmp_path):
        t = _make_transport(tmp_path)
        assert t.capabilities.set_permission_mode is False

    def test_session_resume_is_true(self, tmp_path):
        t = _make_transport(tmp_path)
        assert t.capabilities.session_resume is True

    def test_permission_requests_is_true(self, tmp_path):
        t = _make_transport(tmp_path)
        assert t.capabilities.permission_requests is True


# ---------------------------------------------------------------------------
# stop() edge cases
# ---------------------------------------------------------------------------


class TestStopEdgeCases:
    @pytest.mark.asyncio
    async def test_stop_already_stopped_is_safe(self, tmp_path):
        """Calling stop() when already stopped (no ws, no process) should not raise."""
        t = _make_transport(tmp_path)
        t._alive = False
        t._ws = None
        t._process = None
        t._receive_task = None
        t._pending.clear()

        await t.stop()

        assert t._alive is False
        assert t._ws is None
        assert t._process is None

    @pytest.mark.asyncio
    async def test_stop_with_receive_task_already_done(self, tmp_path):
        """If receive_task is already done, stop() should not try to cancel it."""
        t = _make_transport(tmp_path)
        t._alive = True
        t._ws = None
        t._process = None

        # Create an already-finished task
        async def noop():
            pass

        task = asyncio.ensure_future(noop())
        _ = await task  # Let it finish
        t._receive_task = task

        await t.stop()
        assert t._alive is False

    @pytest.mark.asyncio
    async def test_stop_ws_close_error_handled(self, tmp_path):
        """If ws.close() raises, stop() should still complete."""
        t = _make_transport(tmp_path)
        t._alive = True
        t._process = None
        t._receive_task = None

        ws = FakeWebSocket()

        async def bad_close():
            raise ConnectionError("already closed")

        ws.close = bad_close
        t._ws = ws

        await t.stop()

        assert t._ws is None
        assert t._alive is False


# ---------------------------------------------------------------------------
# _emit_tool_use with different tool types
# ---------------------------------------------------------------------------


class TestEmitToolUse:
    @pytest.mark.asyncio
    async def test_emit_tool_use_bash(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._emit_tool_use("id-1", "Bash", {"command": "ls"})

        events = _emitted_events(emit)
        # Should have: assistant, content_block_start, content_block_delta
        assert len(events) == 3
        assert events[0]["type"] == "assistant"
        assert events[0]["message"]["content"][0]["name"] == "Bash"
        assert events[1]["type"] == "content_block_start"
        assert events[1]["content_block"]["name"] == "Bash"
        assert events[2]["type"] == "content_block_delta"
        assert events[2]["delta"]["type"] == "input_json_delta"

    @pytest.mark.asyncio
    async def test_emit_tool_use_edit(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._emit_tool_use("id-2", "Edit", {"path": "foo.py"})

        events = _emitted_events(emit)
        assert events[0]["message"]["content"][0]["name"] == "Edit"
        assert events[0]["message"]["content"][0]["input"] == {"path": "foo.py"}

    @pytest.mark.asyncio
    async def test_emit_tool_use_websearch(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._emit_tool_use("id-3", "WebSearch", {"query": "test"})

        events = _emitted_events(emit)
        assert events[0]["message"]["content"][0]["name"] == "WebSearch"

    @pytest.mark.asyncio
    async def test_emit_tool_use_custom_tool(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._emit_tool_use("id-4", "CustomTool", {"key": "value"})

        events = _emitted_events(emit)
        assert events[0]["message"]["content"][0]["name"] == "CustomTool"
        # Verify the input_json_delta contains the serialized input
        partial = json.loads(events[2]["delta"]["partial_json"])
        assert partial == {"key": "value"}


# ---------------------------------------------------------------------------
# _handle_item_completed for fileChange, webSearch, mcpToolCall
# ---------------------------------------------------------------------------


class TestItemCompletedEdgeCases:
    @pytest.mark.asyncio
    async def test_file_change_completed_emits_stop(self, tmp_path):
        """fileChange completion should emit content_block_stop."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_completed({"type": "fileChange", "id": "fc-1", "changes": []})

        stops = _events_of_type(emit, "content_block_stop")
        assert len(stops) == 1

    @pytest.mark.asyncio
    async def test_web_search_completed_emits_stop(self, tmp_path):
        """webSearch completion should emit content_block_stop."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_completed({"type": "webSearch", "id": "ws-1", "query": "test"})

        stops = _events_of_type(emit, "content_block_stop")
        assert len(stops) == 1

    @pytest.mark.asyncio
    async def test_mcp_tool_call_completed_emits_stop(self, tmp_path):
        """mcpToolCall completion should emit content_block_stop."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_completed({"type": "mcpToolCall", "id": "mcp-1", "tool": "read_file"})

        stops = _events_of_type(emit, "content_block_stop")
        assert len(stops) == 1

    @pytest.mark.asyncio
    async def test_command_completed_no_output_no_text_block(self, tmp_path):
        """Command with empty output should still emit stop but no text block."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_completed(
            {"type": "commandExecution", "id": "cmd-1", "aggregatedOutput": "", "exitCode": 0}
        )

        events = _emitted_events(emit)
        stops = _events_of_type(emit, "content_block_stop")
        # Only one stop (for the tool_use block), no text block started
        assert len(stops) == 1
        # No text delta emitted
        text_deltas = [
            e
            for e in events
            if e.get("type") == "content_block_delta"
            and e.get("delta", {}).get("type") == "text_delta"
        ]
        assert len(text_deltas) == 0

    @pytest.mark.asyncio
    async def test_command_completed_none_output_no_text_block(self, tmp_path):
        """Command with null aggregated output should still emit stop and not crash."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_completed(
            {"type": "commandExecution", "id": "cmd-1", "aggregatedOutput": None, "exitCode": 0}
        )

        events = _emitted_events(emit)
        stops = _events_of_type(emit, "content_block_stop")
        assert len(stops) == 1
        text_deltas = [
            e
            for e in events
            if e.get("type") == "content_block_delta"
            and e.get("delta", {}).get("type") == "text_delta"
        ]
        assert len(text_deltas) == 0

    @pytest.mark.asyncio
    async def test_command_completed_uses_buffered_output_when_aggregate_missing(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "item/commandExecution/outputDelta",
                "params": {"itemId": "cmd-1", "delta": "line one\n"},
            }
        )
        await t._handle_server_message(
            {
                "method": "item/commandExecution/outputDelta",
                "params": {"itemId": "cmd-1", "delta": "line two\n"},
            }
        )

        await t._handle_item_completed(
            {"type": "commandExecution", "id": "cmd-1", "aggregatedOutput": None, "exitCode": 0}
        )

        starts = _events_of_type(emit, "content_block_start")
        result_blocks = [
            e["content_block"] for e in starts if e["content_block"].get("type") == "tool_result"
        ]
        assert len(result_blocks) == 1
        assert result_blocks[0]["content"] == "line one\nline two\n"

    @pytest.mark.asyncio
    async def test_unknown_item_completed_no_emit(self, tmp_path):
        """An unknown item type completing should not emit anything."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_item_completed({"type": "unknownType", "id": "x-1"})

        assert emit.call_count == 0


# ---------------------------------------------------------------------------
# _send_rpc_response
# ---------------------------------------------------------------------------


class TestSendRpcResponse:
    @pytest.mark.asyncio
    async def test_send_rpc_response_sends_json(self, tmp_path):
        t = _make_transport(tmp_path)
        ws = FakeWebSocket()
        t._ws = ws

        await t._send_rpc_response(42, {"decision": "accept"})

        msg = json.loads(ws.sent[0])
        assert msg["jsonrpc"] == "2.0"
        assert msg["id"] == 42
        assert msg["result"]["decision"] == "accept"

    @pytest.mark.asyncio
    async def test_send_rpc_response_ws_none_is_noop(self, tmp_path):
        """If ws is None, _send_rpc_response should silently do nothing."""
        t = _make_transport(tmp_path)
        t._ws = None

        # Should not raise
        await t._send_rpc_response(42, {"decision": "accept"})


# ---------------------------------------------------------------------------
# resume edge cases
# ---------------------------------------------------------------------------


class TestResumeEdgeCases:
    @pytest.mark.asyncio
    async def test_resume_with_skip_permissions(self, tmp_path):
        t = _make_transport(tmp_path, skip_permissions=True)

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {"thread": {"id": "resumed-t"}}

        t._send_rpc = fake_send_rpc
        _collect_emits(t)

        await t.resume("old-id")

        params = calls[0][1]
        assert params["approvalPolicy"] == "never"
        assert params["sandbox"] == "danger-full-access"

    @pytest.mark.asyncio
    async def test_resume_with_configured_permission_params(self, tmp_path):
        t = _make_transport(
            tmp_path,
            skip_permissions=False,
            approval_policy="untrusted",
            sandbox="read-only",
        )

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {"thread": {"id": "resumed-t"}}

        t._send_rpc = fake_send_rpc
        _collect_emits(t)

        await t.resume("old-id")

        params = calls[0][1]
        assert params["approvalPolicy"] == "untrusted"
        assert params["sandbox"] == "read-only"

    @pytest.mark.asyncio
    async def test_resume_with_model(self, tmp_path):
        t = _make_transport(tmp_path, model="gpt-4")

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {"thread": {"id": "resumed-t"}}

        t._send_rpc = fake_send_rpc
        _collect_emits(t)

        await t.resume("old-id")

        params = calls[0][1]
        assert params["model"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_resume_without_model(self, tmp_path):
        t = _make_transport(tmp_path, model="")

        calls = []

        async def fake_send_rpc(method, params=None):
            calls.append((method, params))
            return {"thread": {"id": "resumed-t"}}

        t._send_rpc = fake_send_rpc
        _collect_emits(t)

        await t.resume("old-id")

        params = calls[0][1]
        assert "model" not in params

    @pytest.mark.asyncio
    async def test_resume_fallback_thread_id(self, tmp_path):
        """When the response thread has no id, resume uses the passed thread_id."""
        t = _make_transport(tmp_path)

        async def fake_send_rpc(method, params=None):
            return {"thread": {}}  # No "id" in thread

        t._send_rpc = fake_send_rpc
        _collect_emits(t)

        await t.resume("fallback-id")

        assert t._thread_id == "fallback-id"


# ---------------------------------------------------------------------------
# send_control_response edge cases
# ---------------------------------------------------------------------------


class TestSendControlResponseEdgeCases:
    @pytest.mark.asyncio
    async def test_unknown_request_id_logs_warning(self, tmp_path):
        """Responding to an unknown request_id should be a no-op (warning logged)."""
        t = _make_transport(tmp_path)
        t._ws = FakeWebSocket()

        await t.send_control_response("nonexistent", {"behavior": "allow"})

        # Nothing sent
        assert len(t._ws.sent) == 0

    @pytest.mark.asyncio
    async def test_allow_forever_maps_to_accept(self, tmp_path):
        t = _make_transport(tmp_path)
        t._ws = FakeWebSocket()
        t._pending_approvals["10"] = 10

        await t.send_control_response("10", {"behavior": "allowForever"})

        msg = json.loads(t._ws.sent[0])
        assert msg["result"]["decision"] == "accept"


# ---------------------------------------------------------------------------
# send_control edge cases
# ---------------------------------------------------------------------------


class TestSendControlEdgeCases:
    @pytest.mark.asyncio
    async def test_unknown_subtype_is_noop(self, tmp_path):
        """Unknown control subtypes should not raise."""
        t = _make_transport(tmp_path)
        await t.send_control("totally_unknown")

    @pytest.mark.asyncio
    async def test_set_model_non_string_ignored(self, tmp_path):
        t = _make_transport(tmp_path, model="o4-mini")
        await t.send_control("set_model", model=123)
        assert t._model == "o4-mini"  # Unchanged

    @pytest.mark.asyncio
    async def test_set_model_none_ignored(self, tmp_path):
        t = _make_transport(tmp_path, model="o4-mini")
        await t.send_control("set_model", model=None)
        assert t._model == "o4-mini"  # Unchanged


# ---------------------------------------------------------------------------
# _handle_server_message dispatches to _handle_server_request
# ---------------------------------------------------------------------------


class TestServerMessageWithId:
    @pytest.mark.asyncio
    async def test_message_with_id_dispatches_to_server_request(self, tmp_path):
        """If a message has both 'method' and 'id' (no result/error), it's a server request."""
        t = _make_transport(tmp_path)
        t._ws = FakeWebSocket()
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "id": 55,
                "method": "item/commandExecution/requestApproval",
                "params": {"command": "echo hi"},
            }
        )

        event = emit.call_args[0][0]
        assert event["type"] == "control_request"
        assert event["tool"] == "Bash"


# ---------------------------------------------------------------------------
# thread/started notification sets thread_id
# ---------------------------------------------------------------------------


class TestThreadStartedNotification:
    @pytest.mark.asyncio
    async def test_thread_started_sets_thread_id(self, tmp_path):
        t = _make_transport(tmp_path)
        _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "thread/started",
                "params": {"thread": {"id": "new-thread-123"}},
            }
        )

        assert t._thread_id == "new-thread-123"

    @pytest.mark.asyncio
    async def test_thread_started_no_id_keeps_existing(self, tmp_path):
        t = _make_transport(tmp_path)
        t._thread_id = "existing"
        _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "thread/started",
                "params": {"thread": {}},
            }
        )

        # tid was falsy, so _thread_id not updated
        assert t._thread_id == "existing"


# ---------------------------------------------------------------------------
# fileChange/outputDelta notification
# ---------------------------------------------------------------------------


class TestFileChangeOutputDelta:
    @pytest.mark.asyncio
    async def test_file_change_output_delta_without_item_id_is_ignored(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "item/fileChange/outputDelta",
                "params": {"delta": "patching file.py"},
            }
        )

        assert emit.call_count == 0

    @pytest.mark.asyncio
    async def test_file_change_output_delta_is_buffered_until_completion(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "item/fileChange/outputDelta",
                "params": {"itemId": "fc-1", "delta": "patching file.py"},
            }
        )
        await t._handle_item_completed({"type": "fileChange", "id": "fc-1", "changes": []})

        starts = _events_of_type(emit, "content_block_start")
        result_blocks = [
            e["content_block"] for e in starts if e["content_block"].get("type") == "tool_result"
        ]
        assert len(result_blocks) == 1
        assert result_blocks[0]["content"] == "patching file.py"

    @pytest.mark.asyncio
    async def test_file_change_output_delta_empty_ignored(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_server_message(
            {
                "method": "item/fileChange/outputDelta",
                "params": {"delta": ""},
            }
        )

        assert emit.call_count == 0


# ---------------------------------------------------------------------------
# _emit_text_delta edge cases
# ---------------------------------------------------------------------------


class TestEmitTextDelta:
    @pytest.mark.asyncio
    async def test_empty_text_not_emitted(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._emit_text_delta("")

        assert emit.call_count == 0

    @pytest.mark.asyncio
    async def test_nonempty_text_emitted(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._emit_text_delta("hello")

        assert emit.call_count == 1
        event = emit.call_args[0][0]
        assert event["delta"]["type"] == "text_delta"
        assert event["delta"]["text"] == "hello"


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_codex_ws_cli_type_resolves_adapter(self):
        from skuld.config import SkuldSettings

        settings = SkuldSettings(cli_type="codex-ws")
        assert settings.transport_adapter == "skuld.transports.codex_ws.CodexWebSocketTransport"

    def test_codex_cli_type_still_works(self):
        from skuld.config import SkuldSettings

        settings = SkuldSettings(cli_type="codex")
        assert settings.transport_adapter == "skuld.transports.codex.CodexSubprocessTransport"

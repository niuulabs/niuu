"""Tests for OpenCodeHttpTransport (OpenCode via HTTP REST + SSE)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from skuld.transports.opencode import OpenCodeHttpTransport, _pick_free_port

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transport(tmp_path, **kwargs):
    defaults = {
        "workspace_dir": str(tmp_path),
        "model": "",
        "opencode_port": 19998,
    }
    defaults.update(kwargs)
    return OpenCodeHttpTransport(**defaults)


def _collect_emits(transport):
    mock = AsyncMock()
    transport._emit = mock
    return mock


def _emitted_events(mock):
    return [call[0][0] for call in mock.call_args_list]


def _events_of_type(mock, event_type):
    return [e for e in _emitted_events(mock) if e.get("type") == event_type]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_defaults(self, tmp_path):
        t = _make_transport(tmp_path)
        assert t.workspace_dir == str(tmp_path)
        assert t._opencode_port == 19998
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

    def test_pick_free_port(self):
        port = _pick_free_port()
        assert 1024 <= port <= 65535


# ---------------------------------------------------------------------------
# SSE event normalization
# ---------------------------------------------------------------------------


class TestSSEEventNormalization:
    @pytest.mark.asyncio
    async def test_text_delta(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "message.part.delta",
                "properties": {
                    "sessionID": "s1",
                    "messageID": "m1",
                    "partID": "p1",
                    "field": "text",
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
    async def test_thinking_delta(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "message.part.delta",
                "properties": {
                    "sessionID": "s1",
                    "messageID": "m1",
                    "partID": "p1",
                    "field": "thinking",
                    "delta": "reasoning...",
                },
            }
        )

        event = emit.call_args[0][0]
        assert event["delta"]["type"] == "thinking_delta"
        assert event["delta"]["thinking"] == "reasoning..."

    @pytest.mark.asyncio
    async def test_empty_delta_ignored(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "message.part.delta",
                "properties": {"field": "text", "delta": ""},
            }
        )

        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_idle_emits_result(self, tmp_path):
        t = _make_transport(tmp_path, model="claude-sonnet-4-6")
        t._last_usage = {
            "claude-sonnet-4-6": {
                "inputTokens": 100,
                "outputTokens": 50,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
            }
        }
        emit = _collect_emits(t)

        await t._handle_sse_event({"type": "session.idle", "properties": {"sessionID": "s1"}})

        result = _events_of_type(emit, "result")[0]
        assert result["stop_reason"] == "end_turn"
        assert result["modelUsage"]["claude-sonnet-4-6"]["inputTokens"] == 100

    @pytest.mark.asyncio
    async def test_session_error(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "session.error",
                "properties": {"error": "model rate limited"},
            }
        )

        event = emit.call_args[0][0]
        assert event["type"] == "error"
        assert event["error"] == "model rate limited"

    @pytest.mark.asyncio
    async def test_session_status_analyzing_emits_assistant(self, tmp_path):
        t = _make_transport(tmp_path, model="gpt-4o")
        emit = _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "session.status",
                "properties": {"status": "analyzing"},
            }
        )

        assistant_events = _events_of_type(emit, "assistant")
        assert len(assistant_events) == 1
        assert assistant_events[0]["message"]["model"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_permission_request(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "question.asked",
                "properties": {
                    "id": "perm-42",
                    "tool": "shell",
                    "question": "Run: rm -rf /tmp/test",
                },
            }
        )

        event = emit.call_args[0][0]
        assert event["type"] == "control_request"
        assert event["request_id"] == "perm-42"
        assert event["tool"] == "Bash"
        assert "perm-42" in t._pending_permissions

    @pytest.mark.asyncio
    async def test_heartbeat_ignored(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_sse_event({"type": "server.heartbeat", "properties": {}})

        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_connected_ignored(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_sse_event({"type": "server.connected", "properties": {}})

        emit.assert_not_called()


# ---------------------------------------------------------------------------
# Part handling
# ---------------------------------------------------------------------------


class TestPartHandling:
    @pytest.mark.asyncio
    async def test_tool_invocation_emits_assistant_and_blocks(self, tmp_path):
        t = _make_transport(tmp_path, model="gpt-4o")
        emit = _collect_emits(t)

        await t._handle_part_updated(
            {
                "type": "tool-invocation",
                "id": "tool-1",
                "toolName": "shell",
                "args": {"command": "ls -la"},
            },
            {},
        )

        events = _emitted_events(emit)

        # Assistant event for broker tracking
        assistant_events = _events_of_type(emit, "assistant")
        assert len(assistant_events) == 1
        tool_block = assistant_events[0]["message"]["content"][0]
        assert tool_block["name"] == "Bash"
        assert tool_block["input"]["command"] == "ls -la"

        # content_block_start for browser
        block_starts = _events_of_type(emit, "content_block_start")
        assert block_starts[0]["content_block"]["type"] == "tool_use"

        # input_json_delta
        deltas = [
            e
            for e in events
            if e.get("type") == "content_block_delta"
            and e.get("delta", {}).get("type") == "input_json_delta"
        ]
        assert len(deltas) == 1

    @pytest.mark.asyncio
    async def test_tool_result_emits_stop_and_text(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_part_updated(
            {
                "type": "tool-result",
                "result": "file1.py\nfile2.py",
                "isError": False,
            },
            {},
        )

        events = _emitted_events(emit)

        stops = _events_of_type(emit, "content_block_stop")
        assert len(stops) >= 1

        text_deltas = [
            e
            for e in events
            if e.get("type") == "content_block_delta"
            and e.get("delta", {}).get("type") == "text_delta"
        ]
        assert any("file1.py" in d["delta"]["text"] for d in text_deltas)

    @pytest.mark.asyncio
    async def test_tool_result_error(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_part_updated(
            {
                "type": "tool-result",
                "result": "command not found",
                "isError": True,
            },
            {},
        )

        text_deltas = [
            e
            for e in _emitted_events(emit)
            if e.get("type") == "content_block_delta"
            and e.get("delta", {}).get("type") == "text_delta"
        ]
        assert any("[error]" in d["delta"]["text"] for d in text_deltas)

    @pytest.mark.asyncio
    async def test_text_part_emits_block_start(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_part_updated(
            {"type": "text", "text": "Initial text"},
            {},
        )

        block_starts = _events_of_type(emit, "content_block_start")
        assert block_starts[0]["content_block"]["type"] == "text"

    @pytest.mark.asyncio
    async def test_reasoning_part_emits_thinking_block(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_part_updated(
            {"type": "reasoning"},
            {},
        )

        block_starts = _events_of_type(emit, "content_block_start")
        assert block_starts[0]["content_block"]["type"] == "thinking"

    @pytest.mark.asyncio
    async def test_finish_part_saves_usage(self, tmp_path):
        t = _make_transport(tmp_path, model="gpt-4o")
        _collect_emits(t)

        await t._handle_part_updated(
            {"type": "finish", "reason": "end_turn"},
            {},
        )

        assert t._last_usage is not None
        assert "gpt-4o" in t._last_usage

    @pytest.mark.asyncio
    async def test_block_index_increments(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_part_updated({"type": "text", "text": ""}, {})
        await t._handle_part_updated({"type": "reasoning"}, {})

        block_starts = _events_of_type(emit, "content_block_start")
        assert block_starts[0]["index"] == 0
        assert block_starts[1]["index"] == 1


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_without_session_raises(self, tmp_path):
        t = _make_transport(tmp_path)
        with pytest.raises(RuntimeError, match="No active session"):
            await t.send_message("test")

    @pytest.mark.asyncio
    async def test_send_message_resets_state(self, tmp_path):
        t = _make_transport(tmp_path)
        t._session_id = "s1"
        t._last_result = {"old": True}
        t._last_usage = {"old": True}
        t._block_index = 5

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t.send_message("test")

        assert t._last_result is None
        assert t._last_usage is None
        assert t._block_index == 0

    @pytest.mark.asyncio
    async def test_send_message_posts_prompt_async(self, tmp_path):
        t = _make_transport(tmp_path, model="gpt-4o", system_prompt="Be helpful")
        t._session_id = "s1"

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t.send_message("What is 2+2?")

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/session/s1/prompt_async"
        body = call_args[1]["json"]
        assert body["parts"][0]["type"] == "text"
        assert body["parts"][0]["text"] == "What is 2+2?"
        assert body["system"] == "Be helpful"
        assert body["model"]["modelID"] == "gpt-4o"


# ---------------------------------------------------------------------------
# Control: interrupt, set_model, permissions
# ---------------------------------------------------------------------------


class TestControl:
    @pytest.mark.asyncio
    async def test_set_model(self, tmp_path):
        t = _make_transport(tmp_path)
        await t.send_control("set_model", model="claude-opus-4-6")
        assert t._model == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_interrupt_posts_abort(self, tmp_path):
        t = _make_transport(tmp_path)
        t._session_id = "s1"

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t.send_control("interrupt")

        mock_client.post.assert_called_once_with("/session/s1/abort")


class TestPermissions:
    @pytest.mark.asyncio
    async def test_permission_allow(self, tmp_path):
        t = _make_transport(tmp_path)
        t._pending_permissions["perm-1"] = {"id": "perm-1"}

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t.send_control_response("perm-1", {"behavior": "allow"})

        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/permission/perm-1/reply"
        assert call_args[1]["json"]["reply"] == "allow"
        assert "perm-1" not in t._pending_permissions

    @pytest.mark.asyncio
    async def test_permission_deny(self, tmp_path):
        t = _make_transport(tmp_path)
        t._pending_permissions["perm-2"] = {"id": "perm-2"}

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t.send_control_response("perm-2", {"behavior": "deny"})

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["reply"] == "deny"


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


class TestResume:
    @pytest.mark.asyncio
    async def test_resume(self, tmp_path):
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t.resume("existing-session-id")

        assert t._session_id == "existing-session-id"
        init_event = emit.call_args[0][0]
        assert init_event["type"] == "system"
        assert init_event["session_id"] == "existing-session-id"


# ---------------------------------------------------------------------------
# Stop / cleanup
# ---------------------------------------------------------------------------


class TestStopCleanup:
    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, tmp_path):
        t = _make_transport(tmp_path)
        t._alive = True

        mock_client = AsyncMock()
        t._client = mock_client

        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)
        mock_process.pid = 12345
        t._process = mock_process

        await t.stop()

        assert t._alive is False
        assert t._client is None
        assert t._process is None
        mock_client.aclose.assert_called_once()


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_opencode_cli_type_resolves_adapter(self):
        from skuld.config import SkuldSettings

        settings = SkuldSettings(cli_type="opencode")
        assert settings.transport_adapter == "skuld.transports.opencode.OpenCodeHttpTransport"


# ---------------------------------------------------------------------------
# End-to-end: full turn simulation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# send_message body variants
# ---------------------------------------------------------------------------


class TestSendMessageBody:
    @pytest.mark.asyncio
    async def test_send_message_with_model_includes_model_field(self, tmp_path):
        """When model is set, body should contain model.modelID."""
        t = _make_transport(tmp_path, model="anthropic/claude-sonnet-4-6")
        t._session_id = "s1"

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t.send_message("hello")

        body = mock_client.post.call_args[1]["json"]
        assert body["model"]["modelID"] == "anthropic/claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_send_message_without_model_omits_model_field(self, tmp_path):
        """When model is empty string, body should not contain model key."""
        t = _make_transport(tmp_path, model="")
        t._session_id = "s1"

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t.send_message("hello")

        body = mock_client.post.call_args[1]["json"]
        assert "model" not in body

    @pytest.mark.asyncio
    async def test_send_message_without_system_prompt_omits_system(self, tmp_path):
        """When system_prompt is empty, body should not contain system key."""
        t = _make_transport(tmp_path, system_prompt="")
        t._session_id = "s1"

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t.send_message("hello")

        body = mock_client.post.call_args[1]["json"]
        assert "system" not in body

    @pytest.mark.asyncio
    async def test_send_message_error_response_emits_error(self, tmp_path):
        """When server returns non-200/204, an error event is emitted."""
        t = _make_transport(tmp_path)
        t._session_id = "s1"
        emit = _collect_emits(t)

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t.send_message("hello")

        error_events = _events_of_type(emit, "error")
        assert len(error_events) == 1
        assert "Internal Server Error" in error_events[0]["error"]


# ---------------------------------------------------------------------------
# send_control edge cases
# ---------------------------------------------------------------------------


class TestControlEdgeCases:
    @pytest.mark.asyncio
    async def test_send_control_unknown_subtype_is_noop(self, tmp_path):
        """Unknown control subtypes are logged but otherwise ignored."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t.send_control("unknown_subtype", foo="bar")

        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_interrupt_without_session_is_noop(self, tmp_path):
        """Interrupt with no session_id should not call client."""
        t = _make_transport(tmp_path)
        t._session_id = None

        mock_client = AsyncMock()
        t._client = mock_client

        await t.send_control("interrupt")

        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_interrupt_exception_is_swallowed(self, tmp_path):
        """If the abort POST throws, the exception is caught."""
        t = _make_transport(tmp_path)
        t._session_id = "s1"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        t._client = mock_client

        # Should not raise
        await t.send_control("interrupt")


# ---------------------------------------------------------------------------
# send_control_response edge cases
# ---------------------------------------------------------------------------


class TestControlResponseEdgeCases:
    @pytest.mark.asyncio
    async def test_permission_not_found_is_noop(self, tmp_path):
        """Responding to a permission not in pending is still fine (no-op pop)."""
        t = _make_transport(tmp_path)

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        # No pending permission for "nonexistent"
        await t.send_control_response("nonexistent", {"behavior": "allow"})

        # Still posts the reply
        mock_client.post.assert_called_once()
        assert "nonexistent" not in t._pending_permissions

    @pytest.mark.asyncio
    async def test_permission_reply_error_status_logged(self, tmp_path):
        """Non-200/204 reply status is handled gracefully."""
        t = _make_transport(tmp_path)
        t._pending_permissions["perm-x"] = {"id": "perm-x"}

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        # Should not raise
        await t.send_control_response("perm-x", {"behavior": "allow"})

    @pytest.mark.asyncio
    async def test_permission_reply_exception_swallowed(self, tmp_path):
        """If the permission POST throws, the exception is caught."""
        t = _make_transport(tmp_path)
        t._pending_permissions["perm-y"] = {"id": "perm-y"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("timeout"))
        t._client = mock_client

        # Should not raise
        await t.send_control_response("perm-y", {"behavior": "deny"})

    @pytest.mark.asyncio
    async def test_permission_allow_forever(self, tmp_path):
        """allowForever maps to 'allow' reply."""
        t = _make_transport(tmp_path)
        t._pending_permissions["perm-z"] = {"id": "perm-z"}

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t.send_control_response("perm-z", {"behavior": "allowForever"})

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["reply"] == "allow"


# ---------------------------------------------------------------------------
# _handle_sse_event: message.updated model tracking
# ---------------------------------------------------------------------------


class TestMessageUpdated:
    @pytest.mark.asyncio
    async def test_message_updated_sets_model(self, tmp_path):
        """message.updated with model should set transport model when unset."""
        t = _make_transport(tmp_path, model="")
        emit = _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "message.updated",
                "properties": {
                    "info": {
                        "id": "m1",
                        "role": "assistant",
                        "model": "gpt-4o-mini",
                    }
                },
            }
        )

        assert t._model == "gpt-4o-mini"
        # message.updated returns early, no emit
        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_updated_does_not_overwrite_existing_model(self, tmp_path):
        """message.updated should NOT overwrite an already-set model."""
        t = _make_transport(tmp_path, model="claude-sonnet-4-6")
        _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "message.updated",
                "properties": {
                    "info": {
                        "id": "m1",
                        "role": "assistant",
                        "model": "gpt-4o",
                    }
                },
            }
        )

        assert t._model == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_message_updated_tracks_user_message_ids(self, tmp_path):
        """User messages should be tracked so their deltas are skipped."""
        t = _make_transport(tmp_path)
        _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "message.updated",
                "properties": {
                    "info": {
                        "id": "user-msg-1",
                        "role": "user",
                    }
                },
            }
        )

        assert "user-msg-1" in t._user_message_ids

    @pytest.mark.asyncio
    async def test_user_message_deltas_skipped(self, tmp_path):
        """Deltas for user messages (echoed prompt) should be ignored."""
        t = _make_transport(tmp_path)
        t._user_message_ids.add("user-msg-1")
        emit = _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "message.part.delta",
                "properties": {
                    "messageID": "user-msg-1",
                    "partID": "p1",
                    "field": "text",
                    "delta": "echoed prompt",
                },
            }
        )

        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_message_parts_skipped(self, tmp_path):
        """Parts for user messages should be ignored."""
        t = _make_transport(tmp_path)
        t._user_message_ids.add("user-msg-1")
        emit = _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "message.part.updated",
                "properties": {
                    "messageID": "user-msg-1",
                    "part": {"type": "text", "text": "echoed"},
                },
            }
        )

        emit.assert_not_called()


# ---------------------------------------------------------------------------
# session.status "executing" emits assistant
# ---------------------------------------------------------------------------


class TestSessionStatusExecuting:
    @pytest.mark.asyncio
    async def test_session_status_executing_emits_assistant(self, tmp_path):
        """session.status with status=executing should emit assistant event."""
        t = _make_transport(tmp_path, model="claude-sonnet-4-6")
        emit = _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "session.status",
                "properties": {"status": "executing"},
            }
        )

        assistant_events = _events_of_type(emit, "assistant")
        assert len(assistant_events) == 1
        assert assistant_events[0]["message"]["model"] == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_session_status_other_no_emit(self, tmp_path):
        """session.status with unknown status should not emit."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_sse_event(
            {
                "type": "session.status",
                "properties": {"status": "idle"},
            }
        )

        emit.assert_not_called()


# ---------------------------------------------------------------------------
# Part handling: tool-invocation with string args
# ---------------------------------------------------------------------------


class TestPartHandlingStringArgs:
    @pytest.mark.asyncio
    async def test_tool_invocation_string_args_json_parse(self, tmp_path):
        """tool-invocation with string args that are valid JSON should be parsed."""
        t = _make_transport(tmp_path, model="gpt-4o")
        emit = _collect_emits(t)

        await t._handle_part_updated(
            {
                "type": "tool-invocation",
                "id": "tool-2",
                "toolName": "shell",
                "args": '{"command": "echo hi"}',
            },
            {},
        )

        assistant_events = _events_of_type(emit, "assistant")
        tool_block = assistant_events[0]["message"]["content"][0]
        assert tool_block["input"] == {"command": "echo hi"}

    @pytest.mark.asyncio
    async def test_tool_invocation_string_args_invalid_json(self, tmp_path):
        """tool-invocation with non-JSON string args wraps in {command: ...}."""
        t = _make_transport(tmp_path, model="gpt-4o")
        emit = _collect_emits(t)

        await t._handle_part_updated(
            {
                "type": "tool-invocation",
                "id": "tool-3",
                "toolName": "shell",
                "args": "echo hello world",
            },
            {},
        )

        assistant_events = _events_of_type(emit, "assistant")
        tool_block = assistant_events[0]["message"]["content"][0]
        assert tool_block["input"] == {"command": "echo hello world"}


# ---------------------------------------------------------------------------
# Part handling: tool-result with empty content
# ---------------------------------------------------------------------------


class TestPartHandlingEmptyResult:
    @pytest.mark.asyncio
    async def test_tool_result_empty_content_only_stop(self, tmp_path):
        """tool-result with empty content emits only content_block_stop, no text."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_part_updated(
            {
                "type": "tool-result",
                "result": "",
                "isError": False,
            },
            {},
        )

        events = _emitted_events(emit)
        # Should only have the stop event, no text block
        assert len(events) == 1
        assert events[0]["type"] == "content_block_stop"

    @pytest.mark.asyncio
    async def test_tool_result_none_content_only_stop(self, tmp_path):
        """tool-result with no result key emits only stop."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_part_updated(
            {
                "type": "tool-result",
                "isError": False,
            },
            {},
        )

        events = _emitted_events(emit)
        assert len(events) == 1
        assert events[0]["type"] == "content_block_stop"


# ---------------------------------------------------------------------------
# stop() edge cases
# ---------------------------------------------------------------------------


class TestStopEdgeCases:
    @pytest.mark.asyncio
    async def test_stop_no_process_no_client(self, tmp_path):
        """stop() with no process and no client should not raise."""
        t = _make_transport(tmp_path)
        t._alive = True
        t._process = None
        t._client = None
        t._sse_task = None

        await t.stop()

        assert t._alive is False

    @pytest.mark.asyncio
    async def test_stop_cancels_sse_task(self, tmp_path):
        """stop() should cancel the SSE task if running."""
        t = _make_transport(tmp_path)
        t._alive = True
        t._process = None
        t._client = None

        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        t._sse_task = mock_task

        await t.stop()

        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_skips_done_sse_task(self, tmp_path):
        """stop() should not cancel an already-done SSE task."""
        t = _make_transport(tmp_path)
        t._alive = True
        t._process = None
        t._client = None

        mock_task = MagicMock()
        mock_task.done.return_value = True
        mock_task.cancel = MagicMock()
        t._sse_task = mock_task

        await t.stop()

        mock_task.cancel.assert_not_called()


# ---------------------------------------------------------------------------
# _emit_text_delta edge cases
# ---------------------------------------------------------------------------


class TestEmitTextDelta:
    @pytest.mark.asyncio
    async def test_empty_string_noop(self, tmp_path):
        """_emit_text_delta with empty string should not emit anything."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._emit_text_delta("")

        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_nonempty_string_emits(self, tmp_path):
        """_emit_text_delta with content should emit a text_delta event."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._emit_text_delta("hello")

        assert emit.call_count == 1
        event = emit.call_args[0][0]
        assert event["type"] == "content_block_delta"
        assert event["delta"]["type"] == "text_delta"
        assert event["delta"]["text"] == "hello"


# ---------------------------------------------------------------------------
# _next_block_index
# ---------------------------------------------------------------------------


class TestNextBlockIndex:
    def test_increments_correctly(self, tmp_path):
        t = _make_transport(tmp_path)
        assert t._next_block_index() == 0
        assert t._next_block_index() == 1
        assert t._next_block_index() == 2
        assert t._block_index == 3

    @pytest.mark.asyncio
    async def test_reset_after_send_message(self, tmp_path):
        """send_message resets _block_index to 0."""
        t = _make_transport(tmp_path)
        t._block_index = 5
        t._session_id = "s1"

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 204
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t.send_message("test")

        assert t._block_index == 0


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


class TestResumeExtended:
    @pytest.mark.asyncio
    async def test_resume_sets_session_id_and_emits_init(self, tmp_path):
        """resume() sets session_id and emits system/init with model and tools."""
        t = _make_transport(tmp_path, model="claude-sonnet-4-6")
        emit = _collect_emits(t)

        await t.resume("resumed-session-42")

        assert t._session_id == "resumed-session-42"
        events = _emitted_events(emit)
        assert len(events) == 1
        init = events[0]
        assert init["type"] == "system"
        assert init["subtype"] == "init"
        assert init["session_id"] == "resumed-session-42"
        assert init["model"] == "claude-sonnet-4-6"
        assert init["tools"] == []


# ---------------------------------------------------------------------------
# Reasoning part ID tracking across delta events
# ---------------------------------------------------------------------------


class TestReasoningPartTracking:
    @pytest.mark.asyncio
    async def test_reasoning_part_routes_deltas_as_thinking(self, tmp_path):
        """After a reasoning part.updated, subsequent deltas with field=text
        should still be routed as thinking_delta because the partID is tracked."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        # Register reasoning part
        await t._handle_part_updated(
            {"type": "reasoning", "id": "reason-1"},
            {"partID": "reason-1"},
        )

        # Now a delta with field="text" but for a reasoning partID
        await t._handle_sse_event(
            {
                "type": "message.part.delta",
                "properties": {
                    "messageID": "m1",
                    "partID": "reason-1",
                    "field": "text",
                    "delta": "deep thought",
                },
            }
        )

        events = _emitted_events(emit)
        thinking_deltas = [
            e
            for e in events
            if e.get("type") == "content_block_delta"
            and e.get("delta", {}).get("type") == "thinking_delta"
        ]
        assert len(thinking_deltas) == 1
        assert thinking_deltas[0]["delta"]["thinking"] == "deep thought"


# ---------------------------------------------------------------------------
# Misc coverage: _base_url, unhandled events, set_model edge cases
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# start() and _create_session with mocked internals
# ---------------------------------------------------------------------------


class TestStartAndCreateSession:
    @pytest.mark.asyncio
    async def test_start_calls_spawn_connect_create(self, tmp_path):
        """start() orchestrates _spawn_server, _connect, _create_session."""
        t = _make_transport(tmp_path)
        t._spawn_server = AsyncMock()
        t._connect = AsyncMock()
        t._create_session = AsyncMock()

        await t.start()

        t._spawn_server.assert_awaited_once()
        t._connect.assert_awaited_once()
        t._create_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_with_initial_prompt_sends_message(self, tmp_path):
        """start() with initial_prompt calls send_message after setup."""
        t = _make_transport(tmp_path, initial_prompt="bootstrap prompt")
        t._spawn_server = AsyncMock()
        t._connect = AsyncMock()
        t._create_session = AsyncMock()
        t.send_message = AsyncMock()

        await t.start()

        t.send_message.assert_awaited_once_with("bootstrap prompt")

    @pytest.mark.asyncio
    async def test_start_without_initial_prompt_no_send(self, tmp_path):
        """start() without initial_prompt does not call send_message."""
        t = _make_transport(tmp_path, initial_prompt="")
        t._spawn_server = AsyncMock()
        t._connect = AsyncMock()
        t._create_session = AsyncMock()
        t.send_message = AsyncMock()

        await t.start()

        t.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_session_extracts_id(self, tmp_path):
        """_create_session POSTs to /session and extracts session ID."""
        t = _make_transport(tmp_path, model="test-model")
        emit = _collect_emits(t)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "new-session-123"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t._create_session()

        assert t._session_id == "new-session-123"
        mock_client.post.assert_awaited_once_with("/session", json={})

        events = _emitted_events(emit)
        assert len(events) == 1
        assert events[0]["type"] == "system"
        assert events[0]["subtype"] == "init"
        assert events[0]["session_id"] == "new-session-123"

    @pytest.mark.asyncio
    async def test_create_session_extracts_session_id_key(self, tmp_path):
        """_create_session falls back to sessionID key."""
        t = _make_transport(tmp_path)
        _collect_emits(t)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"sessionID": "fallback-456"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        t._client = mock_client

        await t._create_session()

        assert t._session_id == "fallback-456"


class TestMiscCoverage:
    def test_base_url_property(self, tmp_path):
        t = _make_transport(tmp_path, opencode_port=12345)
        assert t._base_url == "http://127.0.0.1:12345"

    @pytest.mark.asyncio
    async def test_unhandled_event_type_no_emit(self, tmp_path):
        """Unknown event types are logged but produce no emit."""
        t = _make_transport(tmp_path)
        emit = _collect_emits(t)

        await t._handle_sse_event({"type": "some.future.event", "properties": {"foo": "bar"}})

        emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_model_non_string_ignored(self, tmp_path):
        """set_model with non-string model should not update _model."""
        t = _make_transport(tmp_path, model="original")

        await t.send_control("set_model", model=123)

        assert t._model == "original"

    @pytest.mark.asyncio
    async def test_set_model_none_ignored(self, tmp_path):
        """set_model with model=None should not update _model."""
        t = _make_transport(tmp_path, model="original")

        await t.send_control("set_model", model=None)

        assert t._model == "original"


# ---------------------------------------------------------------------------
# End-to-end: full turn simulation
# ---------------------------------------------------------------------------


class TestFullTurnFlow:
    @pytest.mark.asyncio
    async def test_text_turn_lifecycle(self, tmp_path):
        """session.status → text block → deltas → session.idle."""
        t = _make_transport(tmp_path, model="claude-sonnet-4-6")
        emit = _collect_emits(t)

        # 1. Status: analyzing
        await t._handle_sse_event({"type": "session.status", "properties": {"status": "analyzing"}})

        # 2. Text part created
        await t._handle_sse_event(
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {"type": "text", "text": ""},
                    "sessionID": "s1",
                    "messageID": "m1",
                    "partID": "p1",
                },
            }
        )

        # 3. Text deltas
        await t._handle_sse_event(
            {
                "type": "message.part.delta",
                "properties": {
                    "sessionID": "s1",
                    "messageID": "m1",
                    "partID": "p1",
                    "field": "text",
                    "delta": "Four.",
                },
            }
        )

        # 4. Finish part
        await t._handle_sse_event(
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {"type": "finish", "reason": "end_turn"},
                    "sessionID": "s1",
                    "messageID": "m1",
                    "partID": "p2",
                },
            }
        )

        # 5. Session idle
        await t._handle_sse_event({"type": "session.idle", "properties": {"sessionID": "s1"}})

        events = _emitted_events(emit)
        types = [e["type"] for e in events]

        assert "assistant" in types
        assert "content_block_start" in types
        assert "content_block_delta" in types
        assert "result" in types

        result = _events_of_type(emit, "result")[0]
        assert result["stop_reason"] == "end_turn"
        assert "claude-sonnet-4-6" in result["modelUsage"]

    @pytest.mark.asyncio
    async def test_tool_turn_lifecycle(self, tmp_path):
        """Tool call: tool-invocation → tool-result → session.idle."""
        t = _make_transport(tmp_path, model="gpt-4o")
        emit = _collect_emits(t)

        # Status
        await t._handle_sse_event({"type": "session.status", "properties": {"status": "executing"}})

        # Tool invocation
        await t._handle_sse_event(
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "type": "tool-invocation",
                        "id": "tool-1",
                        "toolName": "read_file",
                        "args": {"path": "/tmp/test.py"},
                    },
                    "sessionID": "s1",
                    "messageID": "m1",
                    "partID": "p1",
                },
            }
        )

        # Tool result
        await t._handle_sse_event(
            {
                "type": "message.part.updated",
                "properties": {
                    "part": {
                        "type": "tool-result",
                        "result": "print('hello')",
                        "isError": False,
                    },
                    "sessionID": "s1",
                    "messageID": "m1",
                    "partID": "p2",
                },
            }
        )

        events = _emitted_events(emit)

        # Assistant event for broker
        assistant_events = _events_of_type(emit, "assistant")
        assert len(assistant_events) >= 1
        tool_assistants = [e for e in assistant_events if e.get("message", {}).get("content")]
        tool_block = tool_assistants[-1]["message"]["content"][0]
        assert tool_block["name"] == "Read"

        # Output visible
        text_deltas = [
            e
            for e in events
            if e.get("type") == "content_block_delta"
            and e.get("delta", {}).get("type") == "text_delta"
        ]
        assert any("hello" in d["delta"]["text"] for d in text_deltas)

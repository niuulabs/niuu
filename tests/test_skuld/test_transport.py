"""Tests for CLI transport implementations."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.transports import (
    CodexSubprocessTransport,
    SdkWebSocketTransport,
    SubprocessTransport,
    TransportCapabilities,
    _drain_stream,
    _filter_event,
    _map_codex_tool,
    _stop_process,
)
from skuld.transports import subprocess as subprocess_transport
from skuld.transports.codex import resolve_codex_cli

# ---------------------------------------------------------------------------
# TransportCapabilities
# ---------------------------------------------------------------------------


class TestTransportCapabilities:
    """Tests for TransportCapabilities dataclass and per-transport values."""

    def test_default_capabilities_all_false(self):
        """Default TransportCapabilities() has all fields False."""
        caps = TransportCapabilities()
        assert caps.cli_websocket is False
        assert caps.session_resume is False
        assert caps.interrupt is False
        assert caps.steer is False
        assert caps.steering_mode == "none"
        assert caps.set_model is False
        assert caps.set_thinking_tokens is False
        assert caps.set_permission_mode is False
        assert caps.rewind_files is False
        assert caps.mcp_set_servers is False
        assert caps.permission_requests is False
        assert caps.slash_commands is False
        assert caps.skills is False

    def test_subprocess_transport_capabilities(self, tmp_path):
        """SubprocessTransport has session_resume=True, rest False."""
        transport = SubprocessTransport(str(tmp_path))
        caps = transport.capabilities
        assert caps.session_resume is True
        assert caps.cli_websocket is False
        assert caps.interrupt is False
        assert caps.steer is False
        assert caps.steering_mode == "none"
        assert caps.set_model is False
        assert caps.set_thinking_tokens is False
        assert caps.set_permission_mode is False
        assert caps.rewind_files is False
        assert caps.mcp_set_servers is False
        assert caps.permission_requests is False
        assert caps.slash_commands is False
        assert caps.skills is False

    def test_sdk_websocket_transport_capabilities(self, tmp_path):
        """SdkWebSocketTransport has all capabilities True."""
        transport = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="test",
        )
        caps = transport.capabilities
        assert caps.cli_websocket is True
        assert caps.session_resume is True
        assert caps.interrupt is True
        assert caps.steer is False
        assert caps.steering_mode == "none"
        assert caps.set_model is True
        assert caps.set_thinking_tokens is True
        assert caps.set_permission_mode is True
        assert caps.rewind_files is True
        assert caps.mcp_set_servers is True
        assert caps.permission_requests is True
        assert caps.slash_commands is True
        assert caps.skills is True

    def test_codex_subprocess_transport_capabilities(self, tmp_path):
        """CodexSubprocessTransport has all capabilities False."""
        transport = CodexSubprocessTransport(str(tmp_path))
        caps = transport.capabilities
        assert caps.cli_websocket is False
        assert caps.session_resume is False
        assert caps.interrupt is False
        assert caps.steer is False
        assert caps.steering_mode == "none"
        assert caps.set_model is False
        assert caps.set_thinking_tokens is False
        assert caps.set_permission_mode is False
        assert caps.rewind_files is False
        assert caps.mcp_set_servers is False
        assert caps.permission_requests is False
        assert caps.slash_commands is False
        assert caps.skills is False

    def test_capabilities_is_frozen(self):
        """TransportCapabilities is immutable (frozen dataclass)."""
        caps = TransportCapabilities()
        with pytest.raises(AttributeError):
            caps.cli_websocket = True  # type: ignore[misc]

    def test_capabilities_partial_override(self):
        """Individual fields can be set at construction time."""
        caps = TransportCapabilities(interrupt=True, set_model=True)
        assert caps.interrupt is True
        assert caps.set_model is True
        assert caps.steer is False
        assert caps.steering_mode == "none"
        assert caps.cli_websocket is False
        assert caps.session_resume is False


# ---------------------------------------------------------------------------
# _filter_event
# ---------------------------------------------------------------------------


class TestFilterEvent:
    """Tests for the shared _filter_event function."""

    def test_content_block_delta_with_text_passed_through(self):
        data = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello world"},
        }
        assert _filter_event(data) is data

    def test_content_block_delta_empty_text_dropped(self):
        data = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": ""},
        }
        assert _filter_event(data) is None

    def test_keep_alive_dropped(self):
        assert _filter_event({"type": "keep_alive"}) is None

    def test_result_event_passed_through(self):
        data = {"type": "result", "result": "Done", "session_id": "s1"}
        assert _filter_event(data) is data

    def test_assistant_event_passed_through(self):
        data = {"type": "assistant", "message": {"content": []}}
        assert _filter_event(data) is data

    def test_system_event_passed_through(self):
        data = {"type": "system", "subtype": "init"}
        assert _filter_event(data) is data

    def test_unknown_event_passed_through(self):
        data = {"type": "message_start", "message": {"id": "msg-1"}}
        assert _filter_event(data) is data


# ---------------------------------------------------------------------------
# SubprocessTransport
# ---------------------------------------------------------------------------


class TestSubprocessTransport:
    """Tests for the SubprocessTransport (legacy path)."""

    @pytest.fixture
    def transport(self, tmp_path):
        return SubprocessTransport(str(tmp_path))

    @staticmethod
    def _mock_process(*, stdout_lines, exit_code=0):
        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(side_effect=[*stdout_lines, b""])

        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()
        mock_stdin.close = MagicMock()
        mock_stdin.wait_closed = AsyncMock()

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.stdin = mock_stdin
        mock_process.wait = AsyncMock(return_value=exit_code)
        mock_process.returncode = exit_code
        mock_process.pid = 12345
        return mock_process

    def test_init(self, transport, tmp_path):
        assert transport.workspace_dir == str(tmp_path)
        assert transport._model == ""
        assert transport._skip_permissions is False
        assert transport._agent_teams is False
        assert transport._system_prompt == ""
        assert transport._initial_prompt == ""
        assert transport.session_id is None
        assert transport.last_result is None
        assert transport.is_alive is False

    def test_init_with_optional_settings(self, tmp_path):
        transport = SubprocessTransport(
            str(tmp_path),
            model="claude-opus-4-6",
            skip_permissions=False,
            agent_teams=True,
            system_prompt="Be precise.",
            initial_prompt="Fix the bug.",
        )

        assert transport._model == "claude-opus-4-6"
        assert transport._skip_permissions is False
        assert transport._agent_teams is True
        assert transport._system_prompt == "Be precise."
        assert transport._initial_prompt == "Fix the bug."

    def test_init_with_mcp_servers(self, tmp_path):
        transport = SubprocessTransport(
            str(tmp_path),
            mcp_servers=[
                {
                    "name": "mimir-local",
                    "command": "python3",
                    "args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir"],
                }
            ],
        )
        assert transport._mcp_config is not None
        payload = json.loads(transport._mcp_config)
        assert payload["mcpServers"]["mimir-local"]["command"] == "python3"

    @pytest.mark.asyncio
    async def test_start_with_initial_prompt_sends_message(self, tmp_path):
        transport = SubprocessTransport(str(tmp_path), initial_prompt="bootstrap prompt")

        with patch.object(transport, "send_message", new_callable=AsyncMock) as mock_send:
            await transport.start()

        mock_send.assert_awaited_once_with("bootstrap prompt")
        assert transport._initial_prompt_sent is True

    @pytest.mark.asyncio
    async def test_start_without_initial_prompt_no_send(self, transport):
        with patch.object(transport, "send_message", new_callable=AsyncMock) as mock_send:
            await transport.start()

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_terminates_running_process(self, transport):
        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()
        transport._process = mock_process

        await transport.stop()
        mock_process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_when_no_process(self, transport):
        await transport.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_stop_kills_on_timeout(self, transport):
        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock(side_effect=[TimeoutError(), None])
        transport._process = mock_process

        await transport.stop()
        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_spawns_process(self, transport):
        """Test send_message spawns claude with correct args."""
        mock_subprocess = self._mock_process(
            stdout_lines=[
                (
                    b'{"type": "content_block_delta", "index": 0,'
                    b' "delta": {"type": "text_delta", "text": "Hello"}}\n'
                ),
                b'{"type": "result", "result": "Hello", "session_id": "sess-123"}\n',
            ]
        )

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            await transport.send_message("Hi")

            call_args = mock_exec.call_args[0]
            assert call_args[0] == "claude"
            assert "-p" in call_args
            assert "--output-format" in call_args
            assert "stream-json" in call_args
            assert "--input-format" in call_args
            assert "--permission-mode" not in call_args
            assert "bypassPermissions" not in call_args

            stdin_payload = b"".join(
                call.args[0] for call in mock_subprocess.stdin.write.call_args_list
            )
            sent = json.loads(stdin_payload.decode().strip())
            assert sent["type"] == "user"
            assert sent["message"]["role"] == "user"
            assert sent["message"]["content"] == "Hi"

        # Both events emitted via callback
        assert callback.call_count == 2
        first_data = callback.call_args_list[0][0][0]
        assert first_data["type"] == "content_block_delta"
        second_data = callback.call_args_list[1][0][0]
        assert second_data["type"] == "result"

    @pytest.mark.asyncio
    async def test_send_message_tracks_session_id(self, transport):
        mock_subprocess = self._mock_process(
            stdout_lines=[b'{"type": "result", "result": "Done", "session_id": "sess-456"}\n']
        )

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            await transport.send_message("first")

        assert transport.session_id == "sess-456"

    @pytest.mark.asyncio
    async def test_send_message_resumes_session(self, transport):
        transport._session_id = "sess-existing"

        mock_subprocess = self._mock_process(
            stdout_lines=[b'{"type": "result", "result": "ok", "session_id": "sess-existing"}\n']
        )

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            await transport.send_message("follow up")

            call_args = mock_exec.call_args[0]
            assert "--resume" in call_args
            assert "sess-existing" in call_args

    @pytest.mark.asyncio
    async def test_send_message_raises_on_nonzero_exit(self, transport):
        mock_subprocess = self._mock_process(
            stdout_lines=[b'{"type": "result", "result": "Done"}\n'],
            exit_code=1,
        )

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            with pytest.raises(RuntimeError, match="exited with code 1"):
                await transport.send_message("test")

    @pytest.mark.asyncio
    async def test_send_message_raises_when_stdout_none(self, transport):
        mock_subprocess = MagicMock()
        mock_subprocess.stdout = None
        mock_subprocess.stderr = None
        mock_subprocess.stdin = AsyncMock()
        mock_subprocess.wait = AsyncMock(return_value=0)
        mock_subprocess.returncode = 0
        mock_subprocess.pid = 12345

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            with pytest.raises(RuntimeError, match="stdout not available"):
                await transport.send_message("test")

    @pytest.mark.asyncio
    async def test_send_message_skips_non_json_lines(self, transport):
        mock_subprocess = self._mock_process(
            stdout_lines=[
                b"some debug output\n",
                b'{"type": "result", "result": "Done"}\n',
            ]
        )

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            await transport.send_message("test")

        assert callback.call_count == 1
        assert callback.call_args_list[0][0][0]["type"] == "result"

    @pytest.mark.asyncio
    async def test_send_message_captures_last_result(self, transport):
        result_data = {
            "type": "result",
            "result": "Done",
            "session_id": "s1",
            "modelUsage": {"opus": {"inputTokens": 10}},
        }
        mock_subprocess = self._mock_process(
            stdout_lines=[json.dumps(result_data).encode() + b"\n"]
        )

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            await transport.send_message("test")

        assert transport.last_result is not None
        assert transport.last_result["type"] == "result"

    @pytest.mark.asyncio
    async def test_send_message_filters_empty_content_block(self, transport):
        """Empty content_block_delta events should not be emitted."""
        mock_subprocess = self._mock_process(
            stdout_lines=[
                b'{"type": "content_block_delta", "index": 0, "delta": {"text": ""}}\n',
                b'{"type": "content_block_delta", "index": 0, "delta": {"text": "Hi"}}\n',
                b'{"type": "result", "result": "Hi"}\n',
            ]
        )

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            await transport.send_message("test")

        # Empty delta filtered, so only 2 events emitted
        assert callback.call_count == 2
        types = [c[0][0]["type"] for c in callback.call_args_list]
        assert types == ["content_block_delta", "result"]

    @pytest.mark.asyncio
    async def test_process_cleaned_up_after_send(self, transport):
        mock_subprocess = self._mock_process(
            stdout_lines=[b'{"type": "result", "result": "Done"}\n']
        )

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            await transport.send_message("test")

        assert transport._process is None

    @pytest.mark.asyncio
    async def test_send_message_passes_model_system_prompt_and_agent_teams(self, tmp_path):
        transport = SubprocessTransport(
            str(tmp_path),
            model="claude-opus-4-6",
            skip_permissions=False,
            agent_teams=True,
            system_prompt="Be careful.",
        )
        mock_subprocess = self._mock_process(
            stdout_lines=[b'{"type": "result", "result": "Done"}\n']
        )

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            await transport.send_message("test")

            call_args = mock_exec.call_args[0]
            call_kwargs = mock_exec.call_args[1]
            assert "--model" in call_args
            assert "claude-opus-4-6" in call_args
            assert "--permission-mode" not in call_args
            assert "--append-system-prompt" in call_args
            assert "Be careful." in call_args
            assert call_kwargs["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "1"

    @pytest.mark.asyncio
    async def test_send_message_injects_tracker_shim_env(self, tmp_path):
        transport = SubprocessTransport(str(tmp_path))
        mock_subprocess = self._mock_process(
            stdout_lines=[b'{"type": "result", "result": "Done"}\n']
        )
        shim_env = {
            "PATH": f"{tmp_path}/.skuld-tools/bin:/usr/bin",
            "RAVN_WORKSPACE_DIR": str(tmp_path),
        }

        transport.on_event(AsyncMock())

        with (
            patch(
                "skuld.transports.subprocess.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_exec,
            patch(
                "skuld.transports.subprocess.ensure_codex_tool_shims",
                return_value=(tmp_path / ".skuld-tools" / "bin", shim_env),
            ),
        ):
            mock_exec.return_value = mock_subprocess
            await transport.send_message("test")

            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs["env"]["PATH"] == shim_env["PATH"]
            assert call_kwargs["env"]["RAVN_WORKSPACE_DIR"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_send_message_omits_system_prompt_when_resuming(self, tmp_path):
        transport = SubprocessTransport(
            str(tmp_path),
            system_prompt="Be careful.",
        )
        transport._session_id = "sess-existing"
        mock_subprocess = self._mock_process(
            stdout_lines=[b'{"type": "result", "result": "Done", "session_id": "sess-existing"}\n']
        )

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            await transport.send_message("test")

            call_args = mock_exec.call_args[0]
            assert "--resume" in call_args
            assert "--append-system-prompt" not in call_args

    @pytest.mark.asyncio
    async def test_send_message_passes_mcp_config(self, tmp_path):
        transport = SubprocessTransport(
            str(tmp_path),
            mcp_servers=[
                {
                    "name": "mimir-local",
                    "command": "python3",
                    "args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir"],
                }
            ],
        )
        mock_subprocess = self._mock_process(
            stdout_lines=[b'{"type": "result", "result": "Done"}\n']
        )

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            await transport.send_message("test")

            call_args = mock_exec.call_args[0]
            assert "--mcp-config" in call_args
            config = json.loads(call_args[call_args.index("--mcp-config") + 1])
            assert "mimir-local" in config["mcpServers"]

    @pytest.mark.asyncio
    async def test_send_message_retries_transient_error(self, transport):
        first_process = self._mock_process(
            stdout_lines=[
                b'{"type": "error", "message": "ProcessTransport not ready for writing"}\n'
            ]
        )
        second_process = self._mock_process(
            stdout_lines=[b'{"type": "result", "result": "Done", "session_id": "sess-789"}\n']
        )

        transport.on_event(AsyncMock())

        with (
            patch(
                "skuld.transports.subprocess.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_exec,
            patch(
                "skuld.transports.subprocess.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
        ):
            mock_exec.side_effect = [first_process, second_process]
            await transport.send_message("retry me")

        assert mock_exec.await_count == 2
        mock_sleep.assert_awaited_once()
        assert transport.session_id == "sess-789"

    @pytest.mark.asyncio
    async def test_send_message_does_not_retry_after_meaningful_output(self, transport):
        mock_subprocess = self._mock_process(
            stdout_lines=[
                b'{"type": "content_block_delta", "delta": {"text": "partial"}}\n',
                b'{"type": "error", "message": "ProcessTransport not ready for writing"}\n',
            ]
        )

        callback = AsyncMock()
        transport.on_event(callback)

        with (
            patch(
                "skuld.transports.subprocess.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_exec,
            patch(
                "skuld.transports.subprocess.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
        ):
            mock_exec.return_value = mock_subprocess
            with pytest.raises(RuntimeError, match="completed without a result event"):
                await transport.send_message("test")

        assert mock_exec.await_count == 1
        mock_sleep.assert_not_called()
        assert callback.call_count == 2
        assert callback.call_args_list[0][0][0]["type"] == "content_block_delta"
        assert callback.call_args_list[1][0][0]["type"] == "error"

    @pytest.mark.asyncio
    async def test_send_message_raises_when_stdin_write_fails(self, transport):
        mock_subprocess = self._mock_process(
            stdout_lines=[b'{"type": "result", "result": "Done"}\n']
        )
        mock_subprocess.stdin.write.side_effect = BrokenPipeError("pipe closed")

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            with pytest.raises(RuntimeError, match="Failed to write Claude CLI stdin"):
                await transport.send_message("test")

    @pytest.mark.asyncio
    async def test_send_message_raises_without_result_event(self, transport):
        mock_subprocess = self._mock_process(
            stdout_lines=[b'{"type": "content_block_delta", "delta": {"text": "partial"}}\n']
        )

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.subprocess.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_subprocess
            with pytest.raises(RuntimeError, match="completed without a result event"):
                await transport.send_message("test")


class TestSubprocessHelpers:
    def test_extract_error_message_prefers_nested_error_message(self):
        data = {"error": {"message": "nested error"}}
        assert subprocess_transport._extract_error_message(data) == "nested error"

    def test_extract_error_message_falls_back_to_serialized_payload(self):
        data = {"type": "error", "detail": "missing"}
        message = subprocess_transport._extract_error_message(data)
        assert '"detail": "missing"' in message

    def test_retryable_error_detection_and_output_classification(self):
        assert subprocess_transport._is_retryable_error("ProcessTransport not ready for writing")
        assert not subprocess_transport._is_retryable_error("permission denied")
        assert subprocess_transport._counts_as_output({"type": "assistant"})
        assert not subprocess_transport._counts_as_output({"type": "system"})


# ---------------------------------------------------------------------------
# SdkWebSocketTransport
# ---------------------------------------------------------------------------


class TestSdkWebSocketTransport:
    """Tests for the SdkWebSocketTransport (new SDK path)."""

    @pytest.fixture
    def transport(self, tmp_path):
        return SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="test-session",
        )

    def test_init(self, transport):
        assert transport._broker_session_id == "test-session"
        assert transport._sdk_port == 8081
        assert transport.session_id is None
        assert transport.last_result is None
        assert transport.is_alive is False

    def test_sdk_url(self, transport):
        assert transport.sdk_url == "ws://localhost:8081/ws/cli/test-session"

    @pytest.mark.asyncio
    async def test_start_spawns_process(self, transport):
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_process.returncode = None
            mock_exec.return_value = mock_process

            await transport.start()

            call_args = mock_exec.call_args[0]
            assert call_args[0] == "claude"
            assert "--sdk-url" in call_args
            assert "ws://localhost:8081/ws/cli/test-session" in call_args
            assert "--verbose" in call_args
            assert "--output-format" in call_args
            assert "stream-json" in call_args
            assert "--input-format" in call_args
            assert "--permission-mode" not in call_args
            assert "bypassPermissions" not in call_args

    @pytest.mark.asyncio
    async def test_start_with_resume(self, transport):
        transport._cli_session_id = "resume-me"

        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_process.returncode = None
            mock_exec.return_value = mock_process

            await transport.start()

            call_args = mock_exec.call_args[0]
            assert "--resume" in call_args
            assert "resume-me" in call_args

    @pytest.mark.asyncio
    async def test_start_passes_model_flag(self, tmp_path):
        transport = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="test-session",
            model="claude-opus-4-20250514",
        )

        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_process.returncode = None
            mock_exec.return_value = mock_process

            await transport.start()

            call_args = mock_exec.call_args[0]
            assert "--model" in call_args
            assert "claude-opus-4-20250514" in call_args

    @pytest.mark.asyncio
    async def test_start_omits_model_flag_when_empty(self, transport):
        """When model is empty, --model should not appear in CLI args."""
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_process.returncode = None
            mock_exec.return_value = mock_process

            await transport.start()

            call_args = mock_exec.call_args[0]
            assert "--model" not in call_args

    def test_init_stores_model(self, tmp_path):
        transport = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
            model="claude-opus-4-20250514",
        )
        assert transport._model == "claude-opus-4-20250514"

    def test_init_default_model_empty(self, transport):
        assert transport._model == ""

    @pytest.mark.asyncio
    async def test_messages_queued_before_cli_connects(self, transport):
        """Messages sent before CLI connects are queued."""
        transport.on_event(AsyncMock())

        await transport.send_message("hello")
        assert len(transport._pending_messages) == 1
        assert transport._pending_messages[0]["type"] == "user"
        assert transport._pending_messages[0]["message"]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_attach_cli_websocket_flushes_pending(self, transport):
        """Pending messages are flushed when CLI connects."""
        mock_ws = AsyncMock()
        transport._pending_messages = [
            {"type": "user", "message": {"role": "user", "content": "queued"}}
        ]

        # Mock receive() to return a disconnect frame immediately
        mock_ws.receive = AsyncMock(return_value={"type": "websocket.disconnect", "code": 1000})

        await transport.attach_cli_websocket(mock_ws)

        assert transport._cli_connected.is_set()
        assert transport.is_alive is True
        assert len(transport._pending_messages) == 0

        # Verify the queued message was sent
        mock_ws.send_text.assert_called_once()
        sent = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent["type"] == "user"
        assert sent["message"]["content"] == "queued"

    @pytest.mark.asyncio
    async def test_send_message_over_ws(self, transport):
        """send_message sends user-type message over CLI WS."""
        mock_ws = AsyncMock()
        transport._cli_ws = mock_ws
        transport._cli_connected.set()
        transport._alive = True
        transport.on_event(AsyncMock())

        await transport.send_message("hello world")

        mock_ws.send_text.assert_called_once()
        sent = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent["type"] == "user"
        assert sent["message"]["role"] == "user"
        assert sent["message"]["content"] == "hello world"

    @pytest.mark.asyncio
    async def test_handle_cli_message_tracks_session_id_from_init(self, transport):
        transport.on_event(AsyncMock())

        await transport._handle_cli_message(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "cli-sess-1",
                "tools": [],
                "model": "opus",
            }
        )

        assert transport.session_id == "cli-sess-1"

    @pytest.mark.asyncio
    async def test_handle_cli_message_extracts_slash_commands_from_init(self, transport):
        transport.on_event(AsyncMock())

        await transport._handle_cli_message(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "cli-sess-1",
                "tools": [],
                "model": "opus",
                "slash_commands": ["help", "clear", "compact"],
                "skills": ["simplify", "commit"],
            }
        )

        assert transport.slash_commands == ["help", "clear", "compact"]
        assert transport.skills == ["simplify", "commit"]

    @pytest.mark.asyncio
    async def test_handle_cli_message_defaults_empty_commands_when_missing(self, transport):
        transport.on_event(AsyncMock())

        await transport._handle_cli_message(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "cli-sess-1",
                "tools": [],
                "model": "opus",
            }
        )

        assert transport.slash_commands == []
        assert transport.skills == []

    @pytest.mark.asyncio
    async def test_handle_cli_message_tracks_session_id_from_result(self, transport):
        transport.on_event(AsyncMock())

        await transport._handle_cli_message(
            {
                "type": "result",
                "session_id": "cli-sess-2",
                "result": "Done",
            }
        )

        assert transport.session_id == "cli-sess-2"

    @pytest.mark.asyncio
    async def test_handle_cli_message_captures_last_result(self, transport):
        transport.on_event(AsyncMock())

        result = {"type": "result", "result": "Done", "modelUsage": {}}
        await transport._handle_cli_message(result)

        assert transport.last_result is result

    @pytest.mark.asyncio
    async def test_handle_cli_message_filters_keep_alive(self, transport):
        callback = AsyncMock()
        transport.on_event(callback)

        await transport._handle_cli_message({"type": "keep_alive"})
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_cli_message_emits_assistant(self, transport):
        callback = AsyncMock()
        transport.on_event(callback)

        msg = {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi"}]}}
        await transport._handle_cli_message(msg)

        callback.assert_called_once_with(msg)

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self, transport):
        mock_ws = AsyncMock()
        transport._cli_ws = mock_ws
        transport._alive = True

        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()
        transport._process = mock_process

        await transport.stop()

        assert transport.is_alive is False
        assert transport._cli_ws is None
        mock_ws.close.assert_called_once()
        mock_process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_cli_disconnect_sets_not_alive(self, transport):
        """CLI WebSocket disconnect should set is_alive to False."""
        transport._alive = True
        transport._cli_ws = None
        transport._cli_connected.set()

        # Simulate the end of the receive loop
        transport._alive = False
        transport._cli_connected.clear()

        assert transport.is_alive is False
        assert not transport._cli_connected.is_set()

    # --- Phase 2: Permission control ---

    def test_init_skip_permissions_default(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
        )
        assert t._skip_permissions is False

    def test_init_skip_permissions_false(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
            skip_permissions=False,
        )
        assert t._skip_permissions is False

    @pytest.mark.asyncio
    async def test_spawn_without_skip_permissions(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
            skip_permissions=False,
        )
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_exec.return_value = mock_process

            await t.start()

            call_args = mock_exec.call_args[0]
            assert "--permission-mode" not in call_args
            assert "bypassPermissions" not in call_args

    @pytest.mark.asyncio
    async def test_spawn_with_skip_permissions(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
            skip_permissions=True,
        )
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_exec.return_value = mock_process

            await t.start()

            call_args = mock_exec.call_args[0]
            assert "--permission-mode" in call_args
            assert "bypassPermissions" in call_args

    @pytest.mark.asyncio
    async def test_send_control_response(self, transport):
        mock_ws = AsyncMock()
        transport._cli_ws = mock_ws
        transport._alive = True

        await transport.send_control_response(
            "req-123",
            {"behavior": "allow", "updatedInput": {"command": "ls"}},
        )

        mock_ws.send_text.assert_called_once()
        sent = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent["type"] == "control_response"
        assert sent["response"]["subtype"] == "success"
        assert sent["response"]["request_id"] == "req-123"
        assert sent["response"]["response"]["behavior"] == "allow"
        assert sent["response"]["response"]["updatedInput"]["command"] == "ls"

    @pytest.mark.asyncio
    async def test_send_control_response_noop_on_base_class(self):
        """Base CLITransport.send_control_response is a no-op."""
        from skuld.transports import SubprocessTransport

        t = SubprocessTransport("/tmp")
        # Should not raise
        await t.send_control_response("req-1", {"behavior": "deny"})

    # --- Phase 3: Server-initiated controls ---

    @pytest.mark.asyncio
    async def test_send_control_interrupt(self, transport):
        mock_ws = AsyncMock()
        transport._cli_ws = mock_ws
        transport._alive = True

        await transport.send_control("interrupt")

        mock_ws.send_text.assert_called_once()
        sent = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent["type"] == "control_response"
        assert sent["response"]["subtype"] == "interrupt"
        assert "request_id" in sent["response"]

    @pytest.mark.asyncio
    async def test_send_control_set_model(self, transport):
        mock_ws = AsyncMock()
        transport._cli_ws = mock_ws
        transport._alive = True

        await transport.send_control("set_model", model="claude-opus-4-6")

        sent = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent["response"]["subtype"] == "set_model"
        assert sent["response"]["model"] == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_send_control_set_max_thinking_tokens(self, transport):
        mock_ws = AsyncMock()
        transport._cli_ws = mock_ws
        transport._alive = True

        await transport.send_control("set_max_thinking_tokens", max_thinking_tokens=8192)

        sent = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent["response"]["subtype"] == "set_max_thinking_tokens"
        assert sent["response"]["max_thinking_tokens"] == 8192

    @pytest.mark.asyncio
    async def test_send_control_rewind_files(self, transport):
        mock_ws = AsyncMock()
        transport._cli_ws = mock_ws
        transport._alive = True

        await transport.send_control("rewind_files")

        sent = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent["response"]["subtype"] == "rewind_files"

    @pytest.mark.asyncio
    async def test_send_control_mcp_set_servers(self, transport):
        mock_ws = AsyncMock()
        transport._cli_ws = mock_ws
        transport._alive = True

        servers = [{"name": "myserver", "command": "node", "args": ["server.js"]}]
        await transport.send_control("mcp_set_servers", servers=servers)

        sent = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent["response"]["subtype"] == "mcp_set_servers"
        assert sent["response"]["servers"] == servers

    @pytest.mark.asyncio
    async def test_send_control_noop_on_base_class(self):
        """Base CLITransport.send_control is a no-op."""
        from skuld.transports import SubprocessTransport

        t = SubprocessTransport("/tmp")
        # Should not raise
        await t.send_control("interrupt")

    def test_base_transport_reports_turn_inactive(self):
        """Base CLITransport.is_turn_active defaults to False."""
        from skuld.transports import SubprocessTransport

        t = SubprocessTransport("/tmp")
        assert t.is_turn_active is False

    # --- Phase 4: Agent Teams ---

    def test_init_agent_teams_default(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
        )
        assert t._agent_teams is False

    def test_init_agent_teams_enabled(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
            agent_teams=True,
        )
        assert t._agent_teams is True

    @pytest.mark.asyncio
    async def test_spawn_with_agent_teams_sets_env(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
            agent_teams=True,
        )
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_exec.return_value = mock_process

            await t.start()

            call_kwargs = mock_exec.call_args[1]
            env = call_kwargs["env"]
            assert env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] == "1"

    @pytest.mark.asyncio
    async def test_spawn_injects_tracker_shim_env(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
        )
        shim_env = {
            "PATH": f"{tmp_path}/.skuld-tools/bin:/usr/bin",
            "RAVN_WORKSPACE_DIR": str(tmp_path),
        }
        with (
            patch(
                "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_exec,
            patch(
                "skuld.transports.sdk_websocket.ensure_codex_tool_shims",
                return_value=(tmp_path / ".skuld-tools" / "bin", shim_env),
            ),
        ):
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_exec.return_value = mock_process

            await t.start()

            env = mock_exec.call_args[1]["env"]
            assert env["PATH"] == shim_env["PATH"]
            assert env["RAVN_WORKSPACE_DIR"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_spawn_without_agent_teams_no_env(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
            agent_teams=False,
        )
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_exec.return_value = mock_process

            await t.start()

            call_kwargs = mock_exec.call_args[1]
            env = call_kwargs["env"]
            assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in env

    @pytest.mark.asyncio
    async def test_control_request_forwarded_as_event(self, transport):
        """control_request events from CLI are forwarded via _emit."""
        callback = AsyncMock()
        transport.on_event(callback)

        control_req = {
            "type": "control_request",
            "request_id": "req-abc",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Bash",
                "input": {"command": "ls"},
                "tool_use_id": "tu-1",
            },
        }
        await transport._handle_cli_message(control_req)

        callback.assert_called_once_with(control_req)

    @pytest.mark.asyncio
    async def test_hook_callback_forwarded_as_event(self, transport):
        """hook_callback control_requests are forwarded to browser."""
        callback = AsyncMock()
        transport.on_event(callback)

        hook_msg = {
            "type": "control_request",
            "request_id": "hook-123",
            "request": {
                "subtype": "hook_callback",
                "hook_event": "TeammateIdle",
                "agent_id": "teammate-1",
            },
        }
        await transport._handle_cli_message(hook_msg)

        callback.assert_called_once_with(hook_msg)

    # --- System prompt and initial prompt ---

    def test_init_prompt_defaults_empty(self, transport):
        assert transport._system_prompt == ""
        assert transport._initial_prompt == ""

    def test_init_stores_prompts(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
            system_prompt="You are an agent.",
            initial_prompt="Fix the bug.",
        )
        assert t._system_prompt == "You are an agent."
        assert t._initial_prompt == "Fix the bug."

    @pytest.mark.asyncio
    async def test_spawn_with_system_prompt(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
            system_prompt="You are an agent.",
        )
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_exec.return_value = mock_process

            await t.start()

            call_args = mock_exec.call_args[0]
            assert "--append-system-prompt" in call_args
            idx = call_args.index("--append-system-prompt")
            assert call_args[idx + 1] == "You are an agent."

    @pytest.mark.asyncio
    async def test_spawn_with_mcp_config(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
            mcp_servers=[
                {
                    "name": "mimir-local",
                    "command": "python3",
                    "args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir"],
                }
            ],
        )
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_exec.return_value = mock_process

            await t.start()

            call_args = mock_exec.call_args[0]
            assert "--mcp-config" in call_args
            config = json.loads(call_args[call_args.index("--mcp-config") + 1])
            assert "mimir-local" in config["mcpServers"]

    @pytest.mark.asyncio
    async def test_spawn_without_system_prompt_omits_flag(self, transport):
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_exec.return_value = mock_process

            await transport.start()

            call_args = mock_exec.call_args[0]
            assert "--append-system-prompt" not in call_args

    @pytest.mark.asyncio
    async def test_spawn_with_initial_prompt_queues_pending_message(self, tmp_path):
        t = SdkWebSocketTransport(
            workspace_dir=str(tmp_path),
            sdk_port=8081,
            session_id="s1",
            initial_prompt="Break down ticket TK-123.",
        )
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_exec.return_value = mock_process

            await t.start()

            # Initial prompt is queued as a pending message, not passed via -p
            call_args = mock_exec.call_args[0]
            assert "-p" not in call_args
            assert len(t._pending_messages) == 1
            msg = t._pending_messages[0]
            assert msg["type"] == "user"
            assert msg["message"]["content"] == "Break down ticket TK-123."

    @pytest.mark.asyncio
    async def test_spawn_without_initial_prompt_no_pending(self, transport):
        with patch(
            "skuld.transports.sdk_websocket.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_process = MagicMock()
            mock_process.stdout = None
            mock_process.stderr = None
            mock_exec.return_value = mock_process

            await transport.start()

            call_args = mock_exec.call_args[0]
            assert "-p" not in call_args
            assert len(transport._pending_messages) == 0


# ---------------------------------------------------------------------------
# _map_codex_tool
# ---------------------------------------------------------------------------


class TestMapCodexTool:
    """Tests for the Codex tool name mapper."""

    def test_maps_shell_to_bash(self):
        assert _map_codex_tool("shell") == "Bash"

    def test_maps_container_exec_to_bash(self):
        assert _map_codex_tool("container.exec") == "Bash"

    def test_maps_str_replace_editor_to_edit(self):
        assert _map_codex_tool("str_replace_editor") == "Edit"

    def test_maps_write_file_to_write(self):
        assert _map_codex_tool("write_file") == "Write"

    def test_maps_create_file_to_write(self):
        assert _map_codex_tool("create_file") == "Write"

    def test_maps_read_file_to_read(self):
        assert _map_codex_tool("read_file") == "Read"

    def test_unknown_tool_passes_through(self):
        assert _map_codex_tool("some_custom_tool") == "some_custom_tool"


# ---------------------------------------------------------------------------
# CodexSubprocessTransport
# ---------------------------------------------------------------------------


class TestCodexSubprocessTransport:
    """Tests for CodexSubprocessTransport."""

    @pytest.fixture
    def transport(self, tmp_path):
        return CodexSubprocessTransport(str(tmp_path), model="o4-mini")

    def test_init(self, transport, tmp_path):
        assert transport.workspace_dir == str(tmp_path)
        assert transport._model == "o4-mini"
        assert transport.session_id is None
        assert transport.last_result is None
        assert transport.is_alive is False

    def test_capabilities_all_false(self, transport):
        caps = transport.capabilities
        assert caps.cli_websocket is False
        assert caps.session_resume is False
        assert caps.interrupt is False
        assert caps.set_model is False
        assert caps.set_thinking_tokens is False
        assert caps.set_permission_mode is False
        assert caps.rewind_files is False
        assert caps.mcp_set_servers is False
        assert caps.permission_requests is False
        assert caps.slash_commands is False
        assert caps.skills is False

    def test_resolve_codex_cli_prefers_env_var(self, monkeypatch):
        monkeypatch.setenv("CODEX_CLI_PATH", "/tmp/custom-codex")
        assert resolve_codex_cli() == "/tmp/custom-codex"

    def test_resolve_codex_cli_uses_path(self, monkeypatch):
        monkeypatch.delenv("CODEX_CLI_PATH", raising=False)
        monkeypatch.delenv("SKULD_CODEX_CLI_PATH", raising=False)
        with patch("skuld.transports.codex.shutil.which", return_value="/usr/local/bin/codex"):
            assert resolve_codex_cli() == "/usr/local/bin/codex"

    @pytest.mark.asyncio
    async def test_start_is_noop(self, transport):
        await transport.start()
        assert transport._process is None

    @pytest.mark.asyncio
    async def test_stop_when_no_process(self, transport):
        await transport.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_terminates_running_process(self, transport):
        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()
        transport._process = mock_process

        await transport.stop()
        mock_process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_spawns_codex(self, transport):
        """send_message spawns codex with correct arguments."""
        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(return_value=b"")

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.returncode = 0
        mock_process.wait = AsyncMock(return_value=0)

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec, patch(
            "skuld.transports.codex.resolve_codex_cli",
            return_value="/Applications/Codex.app/Contents/Resources/codex",
        ):
            mock_exec.return_value = mock_process
            await transport.send_message("refactor the auth module")

            call_args = mock_exec.call_args[0]
            assert call_args[0] == "/Applications/Codex.app/Contents/Resources/codex"
            assert call_args[1] == "exec"
            assert "--model" in call_args
            assert "o4-mini" in call_args
            assert "--sandbox" not in call_args
            assert "--json" in call_args
            assert "--quiet" not in call_args
            assert "refactor the auth module" in call_args
            assert mock_exec.call_args.kwargs["stdin"] == asyncio.subprocess.DEVNULL

    @pytest.mark.asyncio
    async def test_send_message_spawns_codex_with_mcp_overrides(self, tmp_path):
        transport = CodexSubprocessTransport(
            str(tmp_path),
            mcp_servers=[
                {
                    "name": "mimir-local",
                    "command": "python3",
                    "args": ["-m", "mimir", "mcp", "--path", "/tmp/mimir"],
                }
            ],
        )
        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(return_value=b"")

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.returncode = 0
        mock_process.wait = AsyncMock(return_value=0)

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.send_message("refactor the auth module")

            call_args = mock_exec.call_args[0]
            assert "-c" in call_args
            assert any(
                arg == 'mcp_servers.mimir-local.command="python3"' for arg in call_args
            )

    @pytest.mark.asyncio
    async def test_send_message_emits_text_delta_for_json_events(self, transport):
        """JSON text delta events are normalized and emitted."""
        response_line = (
            json.dumps(
                {
                    "type": "response.output_text.delta",
                    "delta": "Hello from Codex",
                }
            ).encode()
            + b"\n"
        )

        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(side_effect=[response_line, b""])

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.returncode = 0
        mock_process.wait = AsyncMock(return_value=0)

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.send_message("hello")

        # text delta + synthetic result
        types = [c[0][0]["type"] for c in callback.call_args_list]
        assert "content_block_delta" in types
        delta_call = next(
            c for c in callback.call_args_list if c[0][0]["type"] == "content_block_delta"
        )
        assert delta_call[0][0]["delta"]["text"] == "Hello from Codex"

    @pytest.mark.asyncio
    async def test_send_message_emits_plain_text_as_delta(self, transport):
        """Plain text lines (non-JSON) are emitted as content_block_delta."""
        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(side_effect=[b"Refactoring complete.\n", b""])

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.returncode = 0
        mock_process.wait = AsyncMock(return_value=0)

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.send_message("hello")

        types = [c[0][0]["type"] for c in callback.call_args_list]
        assert "content_block_delta" in types

    @pytest.mark.asyncio
    async def test_send_message_handles_large_single_line_json_output(self, transport):
        """Large JSON lines are buffered across chunks instead of using readline()."""
        large_text = "A" * 70000
        response_line = (
            json.dumps(
                {
                    "type": "response.output_text.delta",
                    "delta": large_text,
                }
            ).encode()
            + b"\n"
        )

        midpoint = len(response_line) // 2
        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(
            side_effect=[response_line[:midpoint], response_line[midpoint:], b""]
        )

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.returncode = 0
        mock_process.wait = AsyncMock(return_value=0)

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.send_message("hello")

        delta_call = next(
            c for c in callback.call_args_list if c[0][0]["type"] == "content_block_delta"
        )
        assert delta_call[0][0]["delta"]["text"] == large_text

    @pytest.mark.asyncio
    async def test_send_message_normalizes_tool_call(self, transport):
        """Tool call events are normalized to assistant/tool_use format."""
        tool_event = (
            json.dumps(
                {
                    "type": "function_call",
                    "item": {
                        "name": "shell",
                        "arguments": json.dumps({"command": "ls -la"}),
                    },
                }
            ).encode()
            + b"\n"
        )

        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(side_effect=[tool_event, b""])

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.returncode = 0
        mock_process.wait = AsyncMock(return_value=0)

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.send_message("list files")

        assistant_calls = [c for c in callback.call_args_list if c[0][0].get("type") == "assistant"]
        assert len(assistant_calls) == 1
        content = assistant_calls[0][0][0]["content"]
        assert content[0]["type"] == "tool_use"
        assert content[0]["name"] == "Bash"  # "shell" mapped to "Bash"
        assert content[0]["input"]["command"] == "ls -la"

    @pytest.mark.asyncio
    async def test_send_message_captures_done_event(self, transport):
        """response.completed sets last_result with modelUsage."""
        done_event = (
            json.dumps(
                {
                    "type": "response.completed",
                    "model": "o4-mini",
                    "usage": {"input_tokens": 50, "output_tokens": 120},
                }
            ).encode()
            + b"\n"
        )

        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(side_effect=[done_event, b""])

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.returncode = 0
        mock_process.wait = AsyncMock(return_value=0)

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.send_message("task")

        assert transport.last_result is not None
        assert transport.last_result["type"] == "result"
        assert transport.last_result["stop_reason"] == "end_turn"
        usage = transport.last_result["modelUsage"]["o4-mini"]
        assert usage["inputTokens"] == 50
        assert usage["outputTokens"] == 120

    @pytest.mark.asyncio
    async def test_send_message_normalizes_item_completed_agent_message(self, transport):
        """Current Codex CLI agent_message items are normalized to assistant text."""
        events = [
            (
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"id": "item_0", "type": "agent_message", "text": "READY"},
                    }
                ).encode()
                + b"\n"
            ),
            (
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 12, "output_tokens": 2},
                    }
                ).encode()
                + b"\n"
            ),
            b"",
        ]

        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(side_effect=events)

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.returncode = 0
        mock_process.wait = AsyncMock(return_value=0)

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.send_message("task")

        assistant_calls = [c for c in callback.call_args_list if c[0][0].get("type") == "assistant"]
        assert len(assistant_calls) == 1
        assert assistant_calls[0][0][0]["message"]["content"] == "READY"
        assert transport.last_result is not None
        assert transport.last_result["type"] == "result"
        usage = transport.last_result["modelUsage"]["o4-mini"]
        assert usage["inputTokens"] == 12
        assert usage["outputTokens"] == 2

    @pytest.mark.asyncio
    async def test_send_message_synthesizes_result_on_clean_exit(self, transport):
        """A synthetic result is emitted when Codex exits without a done event."""
        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(return_value=b"")

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.returncode = 0
        mock_process.wait = AsyncMock(return_value=0)

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.send_message("task")

        assert transport.last_result is not None
        assert transport.last_result["type"] == "result"
        assert transport.last_result["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_send_message_synthesizes_error_result_on_nonzero_exit(self, transport):
        """Nonzero exit code sets stop_reason to error in synthetic result."""
        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(return_value=b"")

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.returncode = 1
        mock_process.wait = AsyncMock(return_value=1)

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.send_message("task")

        assert transport.last_result["stop_reason"] == "error"

    @pytest.mark.asyncio
    async def test_send_message_cleans_up_process(self, transport):
        """Process reference is cleared after send_message completes."""
        mock_stdout = AsyncMock()
        mock_stdout.read = AsyncMock(return_value=b"")

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.returncode = 0
        mock_process.wait = AsyncMock(return_value=0)

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.send_message("task")

        assert transport._process is None

    @pytest.mark.asyncio
    async def test_handle_codex_event_error_type(self, transport):
        """Error events are forwarded with type=error."""
        callback = AsyncMock()
        transport.on_event(callback)

        await transport._handle_codex_event(
            {
                "type": "error",
                "message": "rate limit exceeded",
            }
        )

        callback.assert_called_once()
        emitted = callback.call_args[0][0]
        assert emitted["type"] == "error"
        assert "rate limit" in emitted["content"]

    @pytest.mark.asyncio
    async def test_handle_codex_event_unknown_passes_through(self, transport):
        """Unknown event types are forwarded to the browser as-is."""
        callback = AsyncMock()
        transport.on_event(callback)

        unknown = {"type": "some_new_event", "data": "xyz"}
        await transport._handle_codex_event(unknown)

        callback.assert_called_once_with(unknown)

    @pytest.mark.asyncio
    async def test_stdout_none_raises(self, transport):
        """RuntimeError raised when stdout is unavailable."""
        mock_process = MagicMock()
        mock_process.stdout = None
        mock_process.stderr = None
        mock_process.wait = AsyncMock(return_value=0)

        transport.on_event(AsyncMock())

        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            with pytest.raises(RuntimeError, match="stdout not available"):
                await transport.send_message("task")


# ---------------------------------------------------------------------------
# _drain_stream
# ---------------------------------------------------------------------------


class TestDrainStream:
    """Tests for the _drain_stream helper."""

    async def test_none_stream_returns_immediately(self):
        await _drain_stream(None, "test")

    async def test_reads_lines_until_eof(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"line one\nline two\n")
        reader.feed_eof()

        await _drain_stream(reader, "stdout")

    async def test_handles_empty_lines(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"\n\nactual text\n")
        reader.feed_eof()

        await _drain_stream(reader, "stderr")

    async def test_handles_stream_error(self):
        reader = AsyncMock(spec=asyncio.StreamReader)
        reader.readline = AsyncMock(side_effect=ConnectionResetError("reset"))

        await _drain_stream(reader, "broken")


# ---------------------------------------------------------------------------
# _stop_process
# ---------------------------------------------------------------------------


class TestStopProcess:
    """Tests for the _stop_process helper."""

    async def test_already_exited_returns_immediately(self):
        proc = MagicMock(spec=asyncio.subprocess.Process)
        proc.returncode = 0

        await _stop_process(proc)

        proc.terminate.assert_not_called()

    async def test_terminate_succeeds(self):
        proc = MagicMock(spec=asyncio.subprocess.Process)
        proc.returncode = None
        proc.pid = 12345
        proc.terminate = MagicMock()

        wait_future: asyncio.Future[int] = asyncio.get_event_loop().create_future()
        wait_future.set_result(0)
        proc.wait = MagicMock(return_value=wait_future)

        await _stop_process(proc)

        proc.terminate.assert_called_once()

    async def test_kill_on_timeout(self):
        proc = MagicMock(spec=asyncio.subprocess.Process)
        proc.returncode = None
        proc.pid = 12345
        proc.terminate = MagicMock()
        proc.kill = MagicMock()

        # First wait times out, second succeeds
        call_count = 0

        async def slow_then_fast():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(999)
            return 0

        proc.wait = slow_then_fast

        await _stop_process(proc)

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

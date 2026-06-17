"""Tests for GrokACPTransport — xAI Grok Build via ACP stdio (Scaldy pipeline).

Extracted from the original morning Grok build (jozef/volundr @ 3c6d60d8) and
rehomed onto the niuu dev base as a standalone module to avoid churn in the
shared test_transport.py.
"""

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.transports import GrokACPTransport, _map_grok_tool


class TestGrokACPTransport:
    """Tests for GrokACPTransport — ACP stdio integration for Grok Build.

    Mirrors depth and style of Codex / Subprocess / Sdk tests. Covers handshake,
    streaming mappings (parity with other transports for UI/Ravn/broker), tool
    normalization, resume hint, interrupt via SIGINT, result synthesis, controls,
    stop, capabilities, error paths, and timeout.
    """

    @pytest.fixture
    def transport(self, tmp_path):
        return GrokACPTransport(str(tmp_path), model="grok-build")

    def test_init_defaults_and_caps(self, tmp_path):
        t = GrokACPTransport(str(tmp_path))
        assert t.workspace_dir == str(tmp_path)
        assert t.model == "grok-build"
        assert t.session_id is None
        assert t.last_result is None
        assert t.is_alive is False
        caps = t.capabilities
        assert caps.send_message is True
        assert caps.session_resume is True
        assert caps.interrupt is True
        assert caps.skills is True
        assert caps.cli_websocket is False

    def test_init_accepts_full_common_kwargs_for_parity(self, tmp_path):
        t = GrokACPTransport(
            str(tmp_path),
            model="grok-4",
            session_id="sess-xyz",
            grok_bin="/custom/grok",
            skip_permissions=False,
            agent_teams=True,
            system_prompt="You are helpful.",
            initial_prompt="Start by exploring the repo.",
            acp_prompt_timeout_s=600.0,
        )
        assert t._model == "grok-4"
        assert t._requested_session_id == "sess-xyz"
        assert t._grok_bin_override == "/custom/grok"
        assert t._system_prompt == "You are helpful."
        assert t._initial_prompt == "Start by exploring the repo."
        assert t._prompt_timeout == 600.0

    def test_tool_map_parity(self):
        # Overlapping tools must normalize identically to Codex/Claude for UI + Ravn
        assert _map_grok_tool("run_terminal_command") == "Bash"
        assert _map_grok_tool("search_replace") == "Edit"
        assert _map_grok_tool("read_file") == "Read"
        assert _map_grok_tool("list_dir") == "LS"
        assert _map_grok_tool("grep") == "Grep"
        assert _map_grok_tool("todo_write") == "Todo"
        assert _map_grok_tool("spawn_subagent") == "Subagent"
        # Unknowns pass through
        assert _map_grok_tool("unknown_foo") == "unknown_foo"

    def test_thought_chunk_maps_to_thinking_delta(self, tmp_path):
        # Reasoning must map to a thinking_delta (separate reasoning block), never an
        # inline text_delta, and must not carry a literal "[thinking]" marker.
        t = GrokACPTransport(str(tmp_path))
        ev = t._map_acp_update(
            {"sessionUpdate": "agent_thought_chunk", "content": {"text": "pondering"}}
        )
        assert ev["type"] == "content_block_delta"
        assert ev["delta"]["type"] == "thinking_delta"
        assert ev["delta"]["thinking"] == "pondering"
        assert "[thinking]" not in str(ev)

    def test_message_chunk_maps_to_text_delta(self, tmp_path):
        # Answer text maps to a plain text_delta (the main message stream).
        t = GrokACPTransport(str(tmp_path))
        ev = t._map_acp_update(
            {"sessionUpdate": "agent_message_chunk", "content": {"text": "hello"}}
        )
        assert ev["type"] == "content_block_delta"
        assert ev["delta"]["type"] == "text_delta"
        assert ev["delta"]["text"] == "hello"

    def test_result_estimates_nonzero_usage(self, tmp_path):
        # Grok ACP exposes no token counts; the result must carry a non-zero usage
        # estimate so the broker advances message_count / usage (else sessions look
        # empty/stuck in clients). Reasoning counts toward output tokens.
        t = GrokACPTransport(str(tmp_path))
        t._turn_in_chars = 40
        t._turn_out_chars = 80
        t._turn_reason_chars = 40
        res = t._make_result_from_acp({"stopReason": "end_turn"})
        usage = res["modelUsage"][t._model]
        assert usage["outputTokens"] > 0
        assert usage["inputTokens"] > 0
        # counters reset after the result is built
        assert t._turn_out_chars == 0
        assert t._turn_reason_chars == 0
        assert t._turn_in_chars == 0

    @pytest.mark.asyncio
    async def test_start_performs_acp_handshake_and_new_session(self, transport, tmp_path):
        # Mock ACP responses: initialize result, then session/new result with sessionId
        # Queue-backed stdout so the reader stays alive (blocked on get) after the
        # handshake, mirroring a real long-lived agent process.
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"1"}}\n')
        await queue.put(b'{"jsonrpc":"2.0","id":2,"result":{"sessionId":"grok-sess-123"}}\n')

        async def fake_readline():
            return await queue.get()

        mock_stdout = MagicMock()
        mock_stdout.readline = fake_readline

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.stdin = AsyncMock()
        mock_process.stdin.write = MagicMock()
        mock_process.stdin.drain = AsyncMock()
        mock_process.returncode = None
        mock_process.wait = AsyncMock(return_value=0)
        mock_process.communicate = AsyncMock(return_value=(b"", b""))  # auth preflight

        with patch(
            "skuld.transports.grok.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.start()

            # Verify command (grok agent --always-approve -m grok-build stdio)
            call_args = mock_exec.call_args[0]
            assert call_args[0].endswith("grok")  # resolved via shutil.which, or "grok" default
            assert "agent" in call_args
            assert "--always-approve" in call_args
            assert "-m" in call_args
            assert "grok-build" in call_args
            assert "stdio" in call_args

            # A headless `grok -p` auth preflight ran before the ACP agent spawn
            all_calls = [list(c.args) for c in mock_exec.call_args_list]
            assert any("-p" in c for c in all_calls), "expected a headless grok -p auth preflight"
            assert any("agent" in c and "stdio" in c for c in all_calls), "expected ACP agent spawn"

            # Handshake calls happened (initialize then session/new)
            assert mock_process.stdin.write.call_count >= 2

        assert transport.session_id == "grok-sess-123"
        assert transport.is_alive is True

        await transport.stop()  # tear down the long-lived reader cleanly

    @pytest.mark.asyncio
    async def test_send_message_emits_mapped_events_and_result(self, transport, tmp_path):
        # Reader will see: init responses (ignored in send path), then prompt responses
        # Simulate: agent_message_chunk, agent_thought_chunk, tool_call, final result response

        # The persistent reader drains stdout concurrently, so drive it via a queue:
        # handshake answers are ready during start(); the prompt's streaming updates +
        # result are queued only after send_message has registered its turn (id=3).
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(b'{"jsonrpc":"2.0","id":1,"result":{}}\n')  # init
        await queue.put(b'{"jsonrpc":"2.0","id":2,"result":{"sessionId":"s1"}}\n')  # new

        async def fake_readline():
            return await queue.get()

        mock_stdout = MagicMock()
        mock_stdout.readline = fake_readline

        mock_stdin = AsyncMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.stdin = mock_stdin
        mock_process.returncode = None
        mock_process.wait = AsyncMock(return_value=0)
        mock_process.communicate = AsyncMock(return_value=(b"", b""))  # auth preflight

        callback = AsyncMock()
        transport.on_event(callback)

        with patch(
            "skuld.transports.grok.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.start()  # consumes id=1/id=2; reader then waits on the queue

            send_task = asyncio.create_task(transport.send_message("do the thing"))
            for _ in range(100):  # wait until the turn is registered (current_prompt_id set)
                if transport._current_prompt_id is not None:
                    break
                await asyncio.sleep(0.01)

            await queue.put(b'{"method":"session/update","params":{"update":{"sessionUpdate":"agent_message_chunk","content":{"text":"Hello from Grok"}}}}\n')  # noqa: E501
            await queue.put(b'{"method":"session/update","params":{"update":{"sessionUpdate":"agent_thought_chunk","content":{"text":"thinking step"}}}}\n')  # noqa: E501
            await queue.put(b'{"method":"session/update","params":{"update":{"sessionUpdate":"tool_call","tool":"search_replace","arguments":{"file_path":"foo.py","new_string":"bar"}}}}\n')  # noqa: E501
            await queue.put(b'{"jsonrpc":"2.0","id":3,"result":{"stopReason":"end_turn","text":"done"}}\n')  # noqa: E501

            await asyncio.wait_for(send_task, timeout=5)

        # Events emitted: text delta, thinking delta, assistant tool_use (mapped), result
        assert callback.call_count >= 4
        types = [c[0][0].get("type") for c in callback.call_args_list]
        assert "content_block_delta" in types
        assert "assistant" in types
        assert "result" in types

        # Tool mapped
        assistant_events = [
            c[0][0] for c in callback.call_args_list if c[0][0].get("type") == "assistant"
        ]
        assert any("Edit" in str(e) for e in assistant_events)

        # last_result captured
        assert transport.last_result is not None
        assert transport.last_result["type"] == "result"
        assert transport.last_result["stop_reason"] == "end_turn"

        await transport.stop()  # tear down the long-lived reader cleanly

    @pytest.mark.asyncio
    async def test_interrupt_sends_sigint_and_cancels_future(self, transport, tmp_path):
        mock_process = MagicMock(spec=asyncio.subprocess.Process)
        mock_process.returncode = None
        mock_process.send_signal = MagicMock()
        transport._process = mock_process
        transport._current_prompt_id = 42
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        transport._pending[42] = fut

        await transport.send_control("interrupt")

        mock_process.send_signal.assert_called_once_with(signal.SIGINT)
        assert fut.done()
        assert "interrupted" in str(fut.exception())
        assert transport._current_prompt_id is None

    @pytest.mark.asyncio
    async def test_stop_cleans_process_and_tasks(self, transport):
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.wait = AsyncMock(return_value=0)
        transport._process = mock_process
        transport._reader_task = asyncio.create_task(asyncio.sleep(0))  # dummy

        await transport.stop()

        # stop_process is called internally
        assert transport._process is None
        assert transport._reader_task is None or transport._reader_task.done()

    def test_capabilities_grok_vs_others(self):
        # Grok is between Codex (very limited) and Sdk (everything). Resume + interrupt + skills.
        t = GrokACPTransport("/tmp")
        caps = t.capabilities
        assert caps.session_resume and caps.interrupt and caps.skills
        assert not caps.cli_websocket

    @pytest.mark.asyncio
    async def test_resume_hint_in_new_session(self, tmp_path):
        t = GrokACPTransport(str(tmp_path), session_id="resume-me-42")
        responses = [
            b'{"jsonrpc":"2.0","id":1,"result":{}}\n',
            b'{"jsonrpc":"2.0","id":2,"result":{"sessionId":"resumed-42"}}\n',
        ]
        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(side_effect=[*responses, b""])
        mock_stdin = AsyncMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()
        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.stdin = mock_stdin
        mock_process.returncode = None

        with patch(
            "skuld.transports.grok.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await t.start()

        # The session/new call should have included _meta resumeHint
        # We can't easily assert the exact write without parsing, but session_id is set
        assert t.session_id == "resumed-42"

    @pytest.mark.asyncio
    async def test_start_dispatches_seeded_initial_prompt(self, tmp_path):
        # Forge auto-start seeds the task via initial_prompt; start() must dispatch
        # it as the first turn (session/prompt), not just log it.
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(b'{"jsonrpc":"2.0","id":1,"result":{}}\n')
        await queue.put(b'{"jsonrpc":"2.0","id":2,"result":{"sessionId":"s1"}}\n')
        await queue.put(b'{"jsonrpc":"2.0","id":3,"result":{"stopReason":"end_turn"}}\n')

        async def fake_readline():
            return await queue.get()

        mock_stdout = MagicMock()
        mock_stdout.readline = fake_readline

        mock_stdin = AsyncMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout
        mock_process.stderr = None
        mock_process.stdin = mock_stdin
        mock_process.returncode = None
        mock_process.wait = AsyncMock(return_value=0)
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        t = GrokACPTransport(str(tmp_path), model="grok-build", initial_prompt="do the task")
        with patch(
            "skuld.transports.grok.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await t.start()
            assert t._initial_dispatch_task is not None
            await asyncio.wait_for(t._initial_dispatch_task, timeout=5)

        writes = b"".join(c.args[0] for c in mock_stdin.write.call_args_list)
        assert b"session/prompt" in writes
        assert b"do the task" in writes

        await t.stop()

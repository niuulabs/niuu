"""Tests for GrokACPTransport — xAI Grok Build via ACP stdio (Scaldy pipeline).

Extracted from the original morning Grok build (jozef/volundr @ 3c6d60d8) and
rehomed onto the niuu dev base as a standalone module to avoid churn in the
shared test_transport.py.
"""

import asyncio
import json
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.transports import GrokACPTransport, _map_grok_tool


def _acp_update(update: dict) -> bytes:
    """Encode an ACP session/update notification frame for the fake reader."""
    return (json.dumps({"method": "session/update", "params": {"update": update}}) + "\n").encode()


def _acp_response(req_id: int, result: dict) -> bytes:
    """Encode an ACP JSON-RPC response frame for the fake reader."""
    return (json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n").encode()


class TestGrokACPTransport:
    """Tests for GrokACPTransport — ACP stdio integration for Grok Build.

    Mirrors depth and style of Codex / Subprocess / Sdk tests. Covers handshake,
    streaming mappings (parity with other transports for UI/Ravn/broker), tool
    normalization, resume hint, interrupt via SIGINT, result synthesis, controls,
    stop, capabilities, error paths, and timeout.
    """

    @pytest.fixture
    def transport(self, tmp_path):
        return GrokACPTransport(str(tmp_path), model="grok-4.6")

    def test_init_defaults_and_caps(self, tmp_path):
        t = GrokACPTransport(str(tmp_path))
        assert t.workspace_dir == str(tmp_path)
        # The default MUST be a real `grok models` id — "grok-build" never was, and the
        # CLI rejects an unknown id while exiting 0, so the breakage was silent.
        assert t.model == "grok-4.6"
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
            model="grok-4.5",
            session_id="sess-xyz",
            grok_bin="/custom/grok",
            skip_permissions=False,
            agent_teams=True,
            system_prompt="You are helpful.",
            initial_prompt="Start by exploring the repo.",
            acp_prompt_timeout_s=600.0,
        )
        assert t._model == "grok-4.5"  # a REAL id passes through untouched
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
        assert _map_grok_tool("todo_write") == "TodoWrite"
        assert _map_grok_tool("spawn_subagent") == "Task"
        # opencode-namespace tools (Grok 1.0.x) — previously unmapped, so a Write
        # rendered as the raw "write".
        assert _map_grok_tool("write") == "Write"
        assert _map_grok_tool("read") == "Read"
        assert _map_grok_tool("bash") == "Bash"
        assert _map_grok_tool("edit") == "Edit"
        # Unknown WITH a CLI-supplied label uses the label, not the identifier.
        assert _map_grok_tool("some_new_tool", "Some New Tool") == "Some New Tool"
        # Unknowns with no label pass through
        assert _map_grok_tool("unknown_foo") == "unknown_foo"

    # ------------------------------------------------------------------
    # Hierarchical-mode parity (tool_use id + paired tool_result + timing)
    #
    # Every frame below is a VERBATIM capture from `grok agent … stdio` v1.0.4
    # (see the module docstring). Writing them from the real wire is the point:
    # the previous shape was invented and drifted from what Grok actually sends.
    # ------------------------------------------------------------------

    # Real tool_call frame — note _meta["x.ai/tool"] and the opencode namespace.
    REAL_TOOL_CALL = {
        "sessionUpdate": "tool_call",
        "toolCallId": "call-ef6a308a-ca23-4dc5-ac29-d23cb138b73b-0",
        "title": "write",
        "rawInput": {"file_path": "/tmp/grok-smoke/hello.txt", "content": "hello\n"},
        "_meta": {
            "x.ai/tool": {
                "version": 1,
                "name": "write",
                "kind": "write",
                "namespace": "opencode",
                "label": "Write",
                "read_only": False,
            }
        },
    }

    def test_tool_call_carries_the_acp_id_so_a_result_can_pair_to_it(self, tmp_path):
        """Without an id nothing downstream can pair call->result.

        This is the whole reason hierarchical mode showed Grok tool calls with no
        output: the block was emitted with name+input only, so the transcript
        reducer had no tool_use_id to match and per-tool timing had no start.
        """
        t = GrokACPTransport(str(tmp_path))
        ev = t._map_acp_update(dict(self.REAL_TOOL_CALL))
        block = ev["content"][0]
        assert block["type"] == "tool_use"
        assert block["id"] == "call-ef6a308a-ca23-4dc5-ac29-d23cb138b73b-0"
        # Name comes from _meta.name through the map — NOT the raw title.
        assert block["name"] == "Write"
        # rawInput is the real argument carrier in ACP.
        assert block["input"]["file_path"] == "/tmp/grok-smoke/hello.txt"

    def test_completed_update_becomes_a_paired_tool_result_not_a_second_tool_use(self, tmp_path):
        """A terminal update is a tool_result, correlated by tool_use_id.

        Emitting another tool_use per update meant output never attached to its
        call AND every tool tally multi-counted: a real 4-tool turn produced 13
        tool_use blocks (4 calls + 9 progress updates).
        """
        t = GrokACPTransport(str(tmp_path))
        t._map_acp_update(dict(self.REAL_TOOL_CALL))  # open the call first
        ev = t._map_acp_update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "call-ef6a308a-ca23-4dc5-ac29-d23cb138b73b-0",
                "status": "completed",
                "rawOutput": {"type": "Write", "path": "/tmp/grok-smoke/hello.txt"},
            }
        )
        block = ev["content"][0]
        assert block["type"] == "tool_result", "must be a result, never a second tool_use"
        assert block["tool_use_id"] == "call-ef6a308a-ca23-4dc5-ac29-d23cb138b73b-0"
        assert "Write" in block["content"]
        assert not block.get("is_error")

    def test_in_progress_updates_are_dropped(self, tmp_path):
        """Progress is not a result — forwarding it is what inflated the counts."""
        t = GrokACPTransport(str(tmp_path))
        t._map_acp_update(dict(self.REAL_TOOL_CALL))
        assert (
            t._map_acp_update(
                {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": self.REAL_TOOL_CALL["toolCallId"],
                    "status": "in_progress",
                }
            )
            is None
        )

    def test_failed_update_marks_the_result_as_an_error(self, tmp_path):
        t = GrokACPTransport(str(tmp_path))
        t._map_acp_update(dict(self.REAL_TOOL_CALL))
        ev = t._map_acp_update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": self.REAL_TOOL_CALL["toolCallId"],
                "status": "failed",
                "rawOutput": "permission denied",
            }
        )
        assert ev["content"][0]["is_error"] is True

    def test_result_carries_per_tool_timing(self, tmp_path):
        """D1 timing — the stamps hierarchical rows read for 'ran in 3m 12s'."""
        from niuu.domain.transcript_reducer import TOOL_ENDED_AT

        t = GrokACPTransport(str(tmp_path))
        t._map_acp_update(dict(self.REAL_TOOL_CALL))
        ev = t._map_acp_update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": self.REAL_TOOL_CALL["toolCallId"],
                "status": "completed",
                "rawOutput": "ok",
            }
        )
        block = ev["content"][0]
        assert TOOL_ENDED_AT in block, "no end stamp => no duration on the row"
        assert "started_at" in block, "start comes from the remembered open call"
        assert block["started_at"] <= block[TOOL_ENDED_AT]

    def test_uncorrelatable_update_is_dropped_rather_than_double_counted(self, tmp_path):
        """No id => cannot become a tool_result; a bare tool_use would re-inflate."""
        t = GrokACPTransport(str(tmp_path))
        assert (
            t._map_acp_update({"sessionUpdate": "tool_call_update", "status": "completed"}) is None
        )

    def test_four_real_tool_calls_yield_exactly_four_tool_uses(self, tmp_path):
        """The regression in aggregate, on the real 1.0.4 event mix.

        The captured turn was 4 tool_call + 9 tool_call_update frames. Before this
        fix that rendered as 13 tool_use blocks; the truth is 4 calls and 4 results.
        """
        t = GrokACPTransport(str(tmp_path))
        uses = results = 0
        for i in range(4):
            call = dict(self.REAL_TOOL_CALL, toolCallId=f"call-{i}")
            ev = t._map_acp_update(call)
            uses += sum(1 for b in ev["content"] if b["type"] == "tool_use")
            # two progress updates then a terminal one, exactly as Grok streams them
            for status in ("pending", "in_progress", "completed"):
                out = t._map_acp_update(
                    {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": f"call-{i}",
                        "status": status,
                        "rawOutput": "done",
                    }
                )
                if out:
                    results += sum(1 for b in out["content"] if b["type"] == "tool_result")
        assert (uses, results) == (4, 4)

    def test_tool_result_rides_in_a_user_frame_not_an_assistant_one(self, tmp_path):
        """Role is load-bearing: the reducer only harvests tool_result from `user` frames."""
        t = GrokACPTransport(str(tmp_path))
        t._map_acp_update(dict(self.REAL_TOOL_CALL))
        ev = t._map_acp_update(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": self.REAL_TOOL_CALL["toolCallId"],
                "status": "completed",
                "rawOutput": "ok",
            }
        )
        assert ev["type"] == "user", (
            "a tool_result in an `assistant` frame is silently dropped by the transcript "
            "reducer — the session then stores calls with no results"
        )

    def test_seam_transport_output_reduces_to_paired_transcript_parts(self, tmp_path):
        """THE test that would have caught it: transport output -> the REAL reducer.

        Every unit test above asserts the block this transport BUILDS. None of them
        asserted the turn the reducer builds FROM it — which is the thing the app
        renders. That gap is exactly how tool_result blocks shipped in the wrong role
        with 25 green tests: correct block, wrong envelope, silently discarded.

        So this drives the actual `reduce_frames` and asserts the durable parts.
        """
        from dataclasses import dataclass

        from niuu.domain.transcript_reducer import TOOL_ENDED_AT, reduce_frames

        @dataclass
        class _Frame:
            seq: int
            kind: str
            payload: dict
            request_id: str | None = None
            ts: str | None = "2026-08-15T11:00:00Z"
            session_id: str | None = "s1"

        t = GrokACPTransport(str(tmp_path))
        frames, seq = [], 0
        for i, (raw, label) in enumerate(
            [("write", "Write"), ("run_terminal_command", "Run Command")]
        ):
            call = {
                "sessionUpdate": "tool_call",
                "toolCallId": f"call-{i}",
                "title": raw,
                "rawInput": {"file_path": f"/tmp/{i}.txt"},
                "_meta": {"x.ai/tool": {"name": raw, "label": label}},
            }
            done = {
                "sessionUpdate": "tool_call_update",
                "toolCallId": f"call-{i}",
                "status": "completed",
                "rawOutput": {"ok": True},
            }
            for update in (call, done):
                ev = t._map_acp_update(update)
                if ev:
                    seq += 1
                    frames.append(_Frame(seq=seq, kind=ev.get("type", "unknown"), payload=ev))

        parts = [p for turn in reduce_frames(frames).turns for p in (turn.get("parts") or [])]
        uses = [p for p in parts if p.get("type") == "tool_use"]
        results = [p for p in parts if p.get("type") == "tool_result"]

        assert len(uses) == 2, f"expected 2 durable tool_use parts, got {len(uses)}"
        assert len(results) == 2, f"expected 2 durable tool_result parts, got {len(results)}"
        # Names normalized the cross-engine way, so hierarchical rows classify them.
        assert {u["name"] for u in uses} == {"Write", "Bash"}
        # Paired by id, and timed on both ends — what a row needs to say "ran in Xs".
        assert {r["tool_use_id"] for r in results} == {u["id"] for u in uses}
        assert all(u.get("started_at") and u.get(TOOL_ENDED_AT) for u in uses)

    @pytest.mark.asyncio
    async def test_prompt_timeout_finalizes_the_turn(self, transport, tmp_path):
        """A timeout must EMIT a result, or the session wedges forever.

        The broker closes a turn only when a `result` event arrives. The old path
        logged a warning and returned silently, so a turn whose tools never came back
        stayed `in_progress` for good and every later message queued behind it —
        exactly what happened to `lexi-frontend-voice` (2026-08-16): six parallel
        Read/Grep calls at 19:03:28 that never returned, still wedged 2.5 h later.
        """
        events: list[dict] = []
        transport.on_event(lambda ev: events.append(ev))
        transport._prompt_timeout = 0.05  # trip it immediately
        transport._session_id = "sess-1"

        writes: list[bytes] = []
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdin.write = writes.append
        proc.stdin.drain = AsyncMock()
        transport._process = proc

        await transport.send_message("do something slow")

        results = [e for e in events if e.get("type") == "result"]
        assert results, "a timed-out turn emitted no result — the session would wedge"
        assert "timeout" in json.dumps(results[-1]).lower()
        # And the agent is told to abandon the turn rather than run on unheard.
        assert any(b"session/cancel" in w for w in writes), "no cancel sent to the agent"
        # State is clean, so the next message starts a fresh turn.
        assert transport._current_prompt_id is None

    @pytest.mark.asyncio
    async def test_teardown_mid_turn_still_finalizes(self, transport):
        """CancelledError is a BaseException — it slipped past `except Exception`.

        Stopping a session mid-turn cancelled the coroutine awaiting the prompt, so
        nothing was emitted and the turn stayed open forever. A stopped session that
        can never be re-used is the same failure as a timed-out one.
        """
        events: list[dict] = []
        transport.on_event(lambda ev: events.append(ev))
        transport._session_id = "sess-x"
        await transport._finalize_stranded_turn("cancelled")
        results = [e for e in events if e.get("type") == "result"]
        assert results, "teardown emitted no result — the turn would stay open"
        assert "cancelled" in json.dumps(results[-1]).lower()

    def test_plan_update_survives_for_the_plan_dock(self, tmp_path):
        """Grok emits real ACP `plan` entries — the todo dock's data."""
        t = GrokACPTransport(str(tmp_path))
        ev = t._map_acp_update(
            {
                "sessionUpdate": "plan",
                "entries": [
                    {
                        "content": "Run `echo hi` in bash",
                        "priority": "medium",
                        "status": "in_progress",
                    },
                    {"content": "Write plan.txt", "priority": "medium", "status": "pending"},
                ],
            }
        )
        assert ev["content"][0]["type"] == "plan"
        assert len(ev["content"][0]["entries"]) == 2

    def test_retired_model_ids_are_aliased_to_a_current_one(self, tmp_path):
        """A client in the field keeps sending the id it shipped with.

        `grok-build` is baked into every iOS build before 2227 and was never a real
        `grok models` entry, so the CLI rejects it and the session dies at its first
        prompt with nothing in the UI to explain why (live: `lexi-frontend-voice`,
        2026-08-16 — 0 messages, never active). The server knows the catalogue and the
        client cannot, so a retired id is upgraded rather than fatal.
        """
        assert GrokACPTransport(str(tmp_path), model="grok-build")._model == "grok-4.6"
        assert GrokACPTransport(str(tmp_path), model="GROK-BUILD")._model == "grok-4.6"
        assert GrokACPTransport(str(tmp_path), model="")._model == "grok-4.6"
        # Real ids are never rewritten.
        assert GrokACPTransport(str(tmp_path), model="grok-4.5")._model == "grok-4.5"
        assert GrokACPTransport(str(tmp_path), model="grok-4.6")._model == "grok-4.6"

    def test_start_is_idempotent_under_concurrency(self, tmp_path):
        """Two concurrent start() calls must spawn ONE agent.

        The old guard tested `self._process is not None` before awaiting the auth
        preflight (up to 60 s) and only assigned `_process` after the spawn — so both
        callers passed the check and both spawned. Two reader loops then raced the same
        stdout: `readuntil() called while another coroutine is already waiting`, seen
        live on `lexi-frontend-voice` with "Spawning Grok ACP agent" logged twice.
        """
        t = GrokACPTransport(str(tmp_path))
        assert t._starting is False
        t._starting = True  # first caller has entered start() and is awaiting preflight
        # The second caller must bail immediately rather than spawn a rival agent.
        asyncio.run(t.start())
        assert t._process is None, "a concurrent start() spawned a second agent"

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

            # Verify command (grok agent --always-approve -m grok-4.6 stdio)
            call_args = mock_exec.call_args[0]
            assert call_args[0].endswith("grok")  # resolved via shutil.which, or "grok" default
            assert "agent" in call_args
            assert "--always-approve" in call_args
            assert "-m" in call_args
            assert "grok-4.6" in call_args
            assert "stdio" in call_args
            # ORDERING IS LOAD-BEARING: agent-level flags must precede the `stdio`
            # subcommand or clap rejects them ("unexpected argument '--always-approve'").
            args = list(call_args)
            assert args.index("--always-approve") < args.index("stdio")
            assert args.index("-m") < args.index("stdio")

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

            await queue.put(
                _acp_update(
                    {"sessionUpdate": "agent_message_chunk", "content": {"text": "Hello from Grok"}}
                )
            )
            await queue.put(
                _acp_update(
                    {"sessionUpdate": "agent_thought_chunk", "content": {"text": "thinking step"}}
                )
            )
            await queue.put(
                _acp_update(
                    {
                        "sessionUpdate": "tool_call",
                        "tool": "search_replace",
                        "arguments": {"file_path": "foo.py", "new_string": "bar"},
                    }
                )
            )
            await queue.put(_acp_response(3, {"stopReason": "end_turn", "text": "done"}))

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

    def test_capabilities_advertise_interrupt_resume_steering(self, tmp_path):
        # ACP turns are sequential, so steering is interrupt+resume (not native).
        caps = GrokACPTransport(str(tmp_path)).capabilities
        assert caps.steer is True
        assert caps.steering_mode == "interrupt_resume"

    @pytest.mark.asyncio
    async def test_is_turn_active_tracks_current_prompt(self, transport):
        assert transport.is_turn_active is False
        transport._current_prompt_id = 7
        assert transport.is_turn_active is True
        transport._current_prompt_id = None
        assert transport.is_turn_active is False

    @pytest.mark.asyncio
    async def test_steer_when_idle_starts_a_fresh_turn(self, transport, tmp_path):
        # No turn in flight: a steer/redirect just issues a normal prompt.
        sent = []

        async def fake_send(text):
            sent.append(text)

        transport.send_message = fake_send  # type: ignore[assignment]
        transport._current_prompt_id = None

        await transport.send_control("steer", content="hello there")

        assert sent == ["hello there"]

    @pytest.mark.asyncio
    async def test_steer_interrupts_turn_and_resumes_with_new_prompt(self, transport, tmp_path):
        queue: asyncio.Queue = asyncio.Queue()
        await queue.put(b'{"jsonrpc":"2.0","id":1,"result":{}}\n')
        await queue.put(b'{"jsonrpc":"2.0","id":2,"result":{"sessionId":"s1"}}\n')

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
        mock_process.send_signal = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        async def _wait_prompt(prompt_id: int) -> None:
            for _ in range(200):
                if transport._current_prompt_id == prompt_id:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError(f"prompt {prompt_id} never went in flight")

        with patch(
            "skuld.transports.grok.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = mock_process
            await transport.start()

            send_task = asyncio.create_task(transport.send_message("first task"))
            await _wait_prompt(3)
            assert transport.is_turn_active is True

            # Steer mid-turn: interrupts the running prompt and queues the new
            # text. The send loop resumes with it as a fresh prompt (id=4) — it
            # is NOT blocked behind the per-turn lock.
            await transport.send_control("steer", content="actually do X")
            await _wait_prompt(4)

            await queue.put(b'{"jsonrpc":"2.0","id":4,"result":{"stopReason":"end_turn"}}\n')
            await asyncio.wait_for(send_task, timeout=5)

        mock_process.send_signal.assert_called_with(signal.SIGINT)
        writes = b"".join(c.args[0] for c in mock_stdin.write.call_args_list)
        assert b"first task" in writes
        assert b"actually do X" in writes
        assert transport._pending_steers == []

        await transport.stop()

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
        t._preflight_auth = AsyncMock()
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

"""Tests for MuseMSPTransport — Meta Muse Code via the Muse Session Protocol over stdio.

Every wire frame below is either copied from the MSP conformance transcripts
(github.com/meta-models/muse-code-sdk, schema/msp/transcripts/*) or captured live from
``muse serve`` 1.0.2 on 2026-09-02, so the fold is tested against what the host really
emits rather than what we believe it emits.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from niuu.domain.transcript_reducer import TOOL_ENDED_AT
from skuld.transports import MuseMSPTransport, _map_muse_tool
from skuld.transports.muse import (
    MUSE_DEFAULT_MODEL,
    MuseProtocolError,
    _claude_questions,
    _msp_answers,
    _pick_choice,
    _resolve_approval_mode,
    _resolve_reasoning_effort,
    _uuid7,
)

SID = "0198f0aa-1111-7000-8000-0000000000aa"
TURN = "018f6a1e-9b3c-7c21-a54a-2f30bd3c9f10"


def _frame(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


def _notification(method: str, params: dict) -> bytes:
    return _frame({"jsonrpc": "2.0", "method": method, "params": params, "emittedAtMs": 1})


def _response(req_id: int, result: dict) -> bytes:
    return _frame({"jsonrpc": "2.0", "id": req_id, "result": result})


def _initialize_result() -> dict:
    return {
        "serverInfo": {"name": "muse", "version": "1.0.2"},
        "userAgent": "muse-build/1.0.2 (non-interactive; linux-aarch64)",
        "museHome": "/home/fixture/.muse",
        "platformFamily": "unix",
        "platformOs": "linux",
        "schema": {"version": 1, "fingerprint": "sha256:0331"},
        "grantedCapabilities": [],
        "experimentalApi": False,
        "sessionDurability": "durable",
    }


def _session(status: str = "idle", active: str | None = None) -> dict:
    return {
        "sessionId": SID,
        "status": status,
        "turnCount": 0,
        "path": f"/home/fixture/.muse/sessions/2026/08/07/{SID}/session.jsonl",
        "createdAt": "2026-08-07T18:02:11.412Z",
        "updatedAt": "2026-08-07T18:02:11.412Z",
        "workspaceRoot": "/home/me/src/proj",
        "providerId": "meta",
        "modelId": "muse-spark-1.3",
        "approvalMode": {"mode": "allowAll", "source": "startup", "lastCommandId": None},
        "forkedFrom": None,
        "activeTurnId": active,
    }


class _FakeHost:
    """A queue-backed stdio pair standing in for ``muse serve``.

    stdout frames are pulled by the transport's reader from ``queue``; every frame the
    transport writes lands in ``writes`` (parsed) so tests assert on the wire.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.writes: list[dict] = []
        self.process = MagicMock()
        self.process.stdout = MagicMock()
        self.process.stdout.readline = self._readline
        self.process.stderr = None
        self.process.stdin = MagicMock()
        self.process.stdin.write = self._write
        self.process.stdin.drain = AsyncMock()
        self.process.stdin.is_closing = MagicMock(return_value=False)
        self.process.stdin.close = MagicMock()
        self.process.returncode = None
        self.process.wait = AsyncMock(return_value=0)
        self.process.terminate = MagicMock()
        self.process.kill = MagicMock()

    async def _readline(self) -> bytes:
        return await self.queue.get()

    def _write(self, data: bytes) -> None:
        self.writes.append(json.loads(data.decode()))

    def sent(self, method: str) -> list[dict]:
        return [w for w in self.writes if w.get("method") == method]

    async def push(self, *frames: bytes) -> None:
        for fr in frames:
            await self.queue.put(fr)

    async def accept_turn(self, *, disposition: str = "started") -> str:
        """Ack the most recent turn/start the real way: turnId == commandId (tdd SS3.1.4)."""
        for _ in range(50):
            reqs = self.sent("turn/start")
            if reqs:
                cid = reqs[-1]["params"]["commandId"]
                await self.queue.put(
                    _response(
                        reqs[-1]["id"],
                        {
                            "commandId": cid,
                            "status": "accepted",
                            "turnId": cid,
                            "startedNewTurn": disposition == "started",
                            "disposition": disposition,
                        },
                    )
                )
                return cid
            await asyncio.sleep(0.01)
        raise AssertionError(f"transport never sent turn/start; writes={self.writes}")

    async def answer(self, method: str, result: dict, *, nth: int = -1, expect: int = 1) -> None:
        """Respond to the transport's most recent request for ``method`` (by its own id),
        once at least ``expect`` such requests have been written."""
        for _ in range(50):
            reqs = self.sent(method)
            if len(reqs) >= expect:
                await self.queue.put(_response(reqs[nth]["id"], result))
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"transport never sent {method}; writes={self.writes}")


async def _started_transport(tmp_path, **kwargs) -> tuple[MuseMSPTransport, _FakeHost, list[dict]]:
    """A transport past its handshake + session/start, driven by a fake host."""
    host = _FakeHost()
    t = MuseMSPTransport(str(tmp_path), **kwargs)
    events: list[dict] = []
    t.on_event(lambda ev: events.append(ev))

    async def run_start() -> None:
        with patch(
            "skuld.transports.muse.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_exec.return_value = host.process
            await t.start()

    start_task = asyncio.create_task(run_start())
    await host.answer("initialize", _initialize_result())
    await host.answer(
        "model/list",
        {"models": [], "profileId": None, "providerId": "meta", "source": "bundledCatalog"},
    )
    if kwargs.get("resume_session_id"):
        await host.answer(
            "session/resume",
            {
                "session": _session(),
                "viewCursor": f"v:{SID}:3",
                "history": {
                    "mode": "none",
                    "items": None,
                    "snapshot": None,
                    "noneReason": "cursorSuffix",
                },
                "pendingRequests": [],
            },
        )
    else:
        await host.answer("session/start", {"session": _session(), "viewCursor": f"v:{SID}:3"})
    await asyncio.wait_for(start_task, timeout=5)
    return t, host, events


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_uuid7_is_version_7_and_time_ordered(self):
        import uuid

        a, b = _uuid7(), _uuid7()
        assert uuid.UUID(a).version == 7
        assert a[:8] <= b[:8]

    def test_tool_map_parity(self):
        # The cross-engine canonical spellings the hierarchical row classifier keys on.
        assert _map_muse_tool("shell") == "Bash"
        assert _map_muse_tool("write_file") == "Write"
        assert _map_muse_tool("edit_file") == "Edit"
        assert _map_muse_tool("read_file") == "Read"
        assert _map_muse_tool("web_search") == "WebSearch"
        assert _map_muse_tool("request_user_input") == "AskUserQuestion"
        assert _map_muse_tool("spawn_subagent") == "Task"
        # Unknown tools keep their own name rather than collapsing to "tool".
        assert _map_muse_tool("compile_shaders") == "compile_shaders"
        assert _map_muse_tool("") == "tool"

    def test_reasoning_effort_validation(self):
        assert _resolve_reasoning_effort("ultra") == "ultra"
        assert _resolve_reasoning_effort("max") == "ultra"
        assert _resolve_reasoning_effort("XHIGH") == "xhigh"
        assert _resolve_reasoning_effort("") is None
        # The Meta provider rejects "none"; an unknown value falls back to the host default.
        assert _resolve_reasoning_effort("none") == "none"
        with pytest.raises(ValueError):
            _resolve_reasoning_effort("turbo")

    def test_approval_mode_aliases(self):
        assert _resolve_approval_mode("allowAll", default="onRequest") == "allowAll"
        assert _resolve_approval_mode("bypassPermissions", default="onRequest") == "allowAll"
        assert _resolve_approval_mode("default", default="allowAll") == "onRequest"
        assert _resolve_approval_mode("untrusted", default="allowAll") == "promptUnmatched"
        assert _resolve_approval_mode("plan", default="allowAll") == "denyUnmatched"
        assert _resolve_approval_mode("bogus", default="onRequest") == "onRequest"

    def test_questions_round_trip(self):
        qs = [
            {
                "id": "db_choice",
                "header": "Database",
                "question": "Choose the database",
                "options": [
                    {"label": "Postgres", "description": "relational"},
                    {"label": "SQLite"},
                ],
                "selection": {"mode": "single"},
            },
            {
                "id": "feats",
                "header": "Features",
                "question": "Pick features",
                "options": [{"label": "A"}, {"label": "B"}],
                "selection": {"mode": "multiple", "minSelections": 1},
            },
        ]
        claude = _claude_questions(qs)
        assert claude[0]["multiSelect"] is False and claude[1]["multiSelect"] is True
        assert claude[0]["options"][0] == {"label": "Postgres", "description": "relational"}
        # Client answers `{"answer": ...}` aligned to questions -> exactly one MSP field each.
        assert _msp_answers(qs, [{"answer": "Postgres"}, {"answer": ["A", "B"]}]) == [
            {"questionId": "db_choice", "selectedLabel": "Postgres"},
            {"questionId": "feats", "selectedLabels": ["A", "B"]},
        ]
        # An "Other" free-text answer becomes freeText (never an invalid label).
        assert _msp_answers(qs, ["MySQL please", "none"]) == [
            {"questionId": "db_choice", "freeText": "MySQL please"},
            {"questionId": "feats", "freeText": "none"},
        ]

    def test_pick_choice_prefers_server_minted_decisions(self):
        choices = [
            {"choiceId": "allow_once", "decision": "approved", "scope": "once", "label": "Allow"},
            {
                "choiceId": "allow_session",
                "decision": "approvedForSession",
                "scope": "session",
                "label": "Always",
            },
            {
                "choiceId": "deny",
                "decision": "denied",
                "scope": "once",
                "label": "Deny",
                "acceptsFeedback": True,
            },
        ]
        assert _pick_choice(choices, "allow")["choiceId"] == "allow_once"
        assert _pick_choice(choices, "allowForever")["choiceId"] == "allow_session"
        assert _pick_choice(choices, "deny")["choiceId"] == "deny"
        assert _pick_choice([], "allow") is None


# ---------------------------------------------------------------------------
# Construction + capabilities
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_init_defaults_and_caps(self, tmp_path):
        t = MuseMSPTransport(str(tmp_path))
        assert t.workspace_dir == str(tmp_path)
        assert t.model == MUSE_DEFAULT_MODEL == "muse-spark-1.3"
        assert t.session_id is None
        assert t.last_result is None
        assert t.is_alive is False
        assert t.is_turn_active is False
        caps = t.capabilities
        assert caps.send_message is True
        assert caps.session_resume is True
        assert caps.interrupt is True
        assert caps.steer is True
        assert caps.steering_mode == "native"
        assert caps.set_model is True
        assert caps.set_permission_mode is True
        assert caps.skills is True
        assert caps.cli_websocket is False
        # skip_permissions defaults on: approvals are auto-decided, nothing is surfaced.
        assert caps.permission_requests is False
        assert t.approval_mode == "allowAll"

    def test_init_accepts_the_broker_kwarg_superset(self, tmp_path):
        t = MuseMSPTransport(
            str(tmp_path),
            model="muse-spark-1.2",
            session_id="skuld-sess",
            resume_session_id="",
            skip_permissions=False,
            agent_teams=True,
            system_prompt="",
            initial_prompt="do the thing",
            reasoning_effort="ultra",
            acp_prompt_timeout_s=12.0,
            sdk_port=8765,
            mcp_servers=[],
            ask_user_question_enabled=True,
        )
        assert t.model == "muse-spark-1.2"
        assert t.approval_mode == "onRequest"
        assert t.capabilities.permission_requests is True
        assert t._reasoning_effort == "ultra"
        assert t._prompt_timeout == 12.0

    def test_retired_model_ids_are_aliased_to_a_current_one(self, tmp_path):
        assert MuseMSPTransport(str(tmp_path), model="muse").model == MUSE_DEFAULT_MODEL
        assert MuseMSPTransport(str(tmp_path), model="").model == MUSE_DEFAULT_MODEL
        assert MuseMSPTransport(str(tmp_path), model="muse-spark-1.2").model == "muse-spark-1.2"

    def test_serve_command_puts_host_flags_after_the_verb(self, tmp_path):
        # `serve` MUST come first: `muse --disable-sandbox serve` is rejected as TUI options.
        t = MuseMSPTransport(str(tmp_path))
        cmd = t._serve_command("/opt/muse")
        assert cmd[:2] == ["/opt/muse", "serve"]
        assert "--disable-sandbox" in cmd and "--trust-workspace" in cmd
        sandboxed = MuseMSPTransport(
            str(tmp_path), disable_sandbox=False, sandbox_network="restricted"
        )
        assert "--disable-sandbox" not in sandboxed._serve_command("muse")
        assert sandboxed._serve_command("muse")[-3:] == [
            "--sandbox-network",
            "restricted",
            "--trust-workspace",
        ]


# ---------------------------------------------------------------------------
# Handshake + session lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_performs_msp_handshake_and_session_start(self, tmp_path):
        t, host, _ = await _started_transport(tmp_path, reasoning_effort="high")
        try:
            init = host.sent("initialize")[0]
            # clientInfo.name must match ^[a-z0-9_]+$ — the live host rejects "skuld-probe".
            assert init["params"]["clientInfo"]["name"] == "skuld"
            # `initialized` is a NOTIFICATION (no id) and follows the initialize result.
            initialized = host.sent("initialized")[0]
            assert "id" not in initialized
            assert host.writes.index(initialized) > host.writes.index(init)
            start = host.sent("session/start")[0]["params"]
            assert start["workspaceRoot"] == str(tmp_path)
            assert start["modelId"] == MUSE_DEFAULT_MODEL
            assert start["approvalMode"] == "allowAll"
            assert len(start["commandId"]) == 36
            assert t.session_id == SID
            assert t.is_alive is True
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_start_is_idempotent_under_concurrency(self, tmp_path):
        host = _FakeHost()
        t = MuseMSPTransport(str(tmp_path))
        with patch(
            "skuld.transports.muse.asyncio.create_subprocess_exec", new_callable=AsyncMock
        ) as mock_exec:
            mock_exec.return_value = host.process
            first = asyncio.create_task(t.start())
            second = asyncio.create_task(t.start())
            await host.answer("initialize", _initialize_result())
            await host.answer(
                "model/list", {"models": [], "providerId": "meta", "source": "bundledCatalog"}
            )
            await host.answer("session/start", {"session": _session(), "viewCursor": "v:1"})
            await asyncio.gather(first, second)
            assert mock_exec.await_count == 1, "two concurrent start() calls spawned two hosts"
        await t.stop()

    @pytest.mark.asyncio
    async def test_resume_uses_session_resume_and_adopts_a_running_turn(self, tmp_path):
        host = _FakeHost()
        t = MuseMSPTransport(str(tmp_path), resume_session_id=SID)
        events: list[dict] = []
        t.on_event(lambda ev: events.append(ev))
        with patch(
            "skuld.transports.muse.asyncio.create_subprocess_exec", new_callable=AsyncMock
        ) as mock_exec:
            mock_exec.return_value = host.process
            task = asyncio.create_task(t.start())
            await host.answer("initialize", _initialize_result())
            await host.answer(
                "model/list", {"models": [], "providerId": "meta", "source": "bundledCatalog"}
            )
            await host.answer(
                "session/resume",
                {
                    "session": _session(status="running", active=TURN),
                    "viewCursor": f"v:{SID}:412",
                    "history": {
                        "mode": "none",
                        "items": None,
                        "snapshot": None,
                        "noneReason": "cursorSuffix",
                    },
                    "pendingRequests": [],
                },
            )
            await task
        try:
            resume = host.sent("session/resume")[0]["params"]
            assert resume["sessionId"] == SID and resume["excludeItems"] is True
            assert not host.sent("session/start")
            assert t.session_id == SID
            # The in-flight turn was adopted: its terminal closes a real turn.
            assert t.is_turn_active is True
            await host.push(
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:9",
                        "turnId": TURN,
                        "terminal": "completed",
                        "durationMs": 5,
                    },
                )
            )
            await _settle()
            assert [e for e in events if e.get("type") == "result"]
            assert t.is_turn_active is False
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_resume_failure_propagates_without_replacing_native_identity(self, tmp_path):
        host = _FakeHost()
        t = MuseMSPTransport(str(tmp_path), resume_session_id="dead-session")
        with patch(
            "skuld.transports.muse.asyncio.create_subprocess_exec", new_callable=AsyncMock
        ) as mock_exec:
            mock_exec.return_value = host.process
            task = asyncio.create_task(t.start())
            await host.answer("initialize", _initialize_result())
            await host.answer(
                "model/list", {"models": [], "providerId": "meta", "source": "bundledCatalog"}
            )
            for _ in range(50):
                if host.sent("session/resume"):
                    break
                await asyncio.sleep(0.01)
            rid = host.sent("session/resume")[0]["id"]
            await host.push(
                _frame(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {
                            "code": -32020,
                            "message": "session not found",
                            "data": {"kind": "sessionNotFound"},
                        },
                    }
                )
            )
            with pytest.raises(Exception, match="session not found"):
                await task
        assert not host.sent("session/start")
        assert t._process is None
        await t.stop()

    @pytest.mark.asyncio
    async def test_stop_closes_stdin_first_and_cleans_up(self, tmp_path):
        t, host, _ = await _started_transport(tmp_path)
        await t.stop()
        host.process.stdin.close.assert_called_once()
        assert t._process is None
        assert t._reader_task is None
        assert t.is_alive is False


# ---------------------------------------------------------------------------
# A turn: submit, stream, tools, result
# ---------------------------------------------------------------------------


class TestTurn:
    @pytest.mark.asyncio
    async def test_send_message_streams_text_and_emits_a_result_with_real_usage(self, tmp_path):
        t, host, events = await _started_transport(tmp_path, reasoning_effort="ultra")
        try:
            send = asyncio.create_task(
                t.send_message("Run the agent test suite and summarize failures")
            )
            for _ in range(50):
                if host.sent("turn/start"):
                    break
                await asyncio.sleep(0.01)
            start = host.sent("turn/start")[0]["params"]
            assert start["sessionId"] == SID
            assert start["input"] == [
                {"type": "text", "text": "Run the agent test suite and summarize failures"}
            ]
            assert start["ifBusy"] == "queue"
            assert start["reasoningEffort"] == "ultra"
            cid = start["commandId"]
            await host.answer(
                "turn/start",
                {
                    "commandId": cid,
                    "status": "accepted",
                    "turnId": cid,
                    "startedNewTurn": True,
                    "disposition": "started",
                },
            )
            # The recorded text-run-single-turn transcript, id-substituted.
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                ),
                _notification(
                    "item/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:5",
                        "item": {
                            "itemId": "u1",
                            "kind": "userMessage",
                            "turnId": cid,
                            "revision": 1,
                            "status": "completed",
                            "text": "Run the agent test suite and summarize failures",
                        },
                    },
                ),
                _notification(
                    "item/started",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:6",
                        "item": {
                            "itemId": "a1",
                            "kind": "agentMessage",
                            "turnId": cid,
                            "revision": 1,
                            "status": "inProgress",
                            "text": "",
                        },
                    },
                ),
                _notification(
                    "item/delta",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:7",
                        "itemId": "a1",
                        "field": "text",
                        "delta": "All 214 tests pass",
                    },
                ),
                _notification(
                    "item/delta",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:8",
                        "itemId": "a1",
                        "delta": " except two in tbh-agent...",
                    },
                ),
                _notification(
                    "item/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:9",
                        "item": {
                            "itemId": "a1",
                            "kind": "agentMessage",
                            "turnId": cid,
                            "revision": 2,
                            "status": "completed",
                            "text": "All 214 tests pass except two in tbh-agent... (tail)",
                        },
                    },
                ),
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:10",
                        "turnId": cid,
                        "terminal": "completed",
                        "durationMs": 48211,
                        "timeToFirstTokenMs": 902,
                        "usage": {
                            "inputTokens": 48210,
                            "outputTokens": 1211,
                            "cachedTokens": 40100,
                            "reasoningTokens": 384,
                        },
                    },
                ),
            )
            await asyncio.wait_for(send, timeout=5)

            deltas = [
                e["delta"]["text"]
                for e in events
                if e.get("type") == "content_block_delta" and "text" in e["delta"]
            ]
            # Deltas stream as they arrive, and the completed item TOPS UP what the deltas
            # missed (the tail) instead of re-emitting the whole text.
            assert "".join(deltas) == "All 214 tests pass except two in tbh-agent... (tail)"
            # No user-turn echo: the userMessage item is our own prompt.
            assert not [e for e in events if e.get("type") == "user"]
            results = [e for e in events if e.get("type") == "result"]
            assert len(results) == 1
            result = results[0]
            assert result["stop_reason"] == "end_turn"
            assert result["duration_ms"] == 48211
            assert result["time_to_first_token_ms"] == 902
            assert result["sessionId"] == SID
            assert "is_error" not in result
            usage = result["modelUsage"]["muse-spark-1.3"]
            # cachedTokens (40100) sits INSIDE inputTokens (48210): uncached = the difference.
            assert usage == {
                "inputTokens": 8110,
                "outputTokens": 1211,
                "cacheReadInputTokens": 40100,
                "cacheCreationInputTokens": 0,
                "costUSD": pytest.approx((8110 * 1.25 + 40100 * 0.15 + 1211 * 4.25) / 1_000_000),
            }
            assert t.last_result is result
            assert t.is_turn_active is False
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_tool_call_becomes_a_paired_tool_use_and_tool_result(self, tmp_path):
        t, host, events = await _started_transport(tmp_path)
        try:
            send = asyncio.create_task(t.send_message("Update Cargo.toml to bump the version"))
            cid = await host.accept_turn()
            # The approval-round-trip transcript's tool call (args verbatim JSON string).
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                ),
                _notification(
                    "item/started",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:6",
                        "item": {
                            "itemId": "tc1",
                            "kind": "toolCall",
                            "turnId": cid,
                            "revision": 1,
                            "status": "inProgress",
                            "tool": "write_file",
                            "callId": "call_9f21",
                            "args": '{"path":"Cargo.toml"}',
                        },
                    },
                ),
                _notification(
                    "item/delta",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:7",
                        "itemId": "tc1",
                        "field": "output",
                        "delta": "Cargo.toml ",
                    },
                ),
                _notification(
                    "item/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:9",
                        "item": {
                            "itemId": "tc1",
                            "kind": "toolCall",
                            "turnId": cid,
                            "revision": 2,
                            "status": "completed",
                            "tool": "write_file",
                            "callId": "call_9f21",
                            "args": '{"path":"Cargo.toml"}',
                            "visibleOutput": "Cargo.toml updated\n",
                            "truncated": False,
                        },
                    },
                ),
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:10",
                        "turnId": cid,
                        "terminal": "completed",
                        "durationMs": 10,
                    },
                ),
            )
            await asyncio.wait_for(send, timeout=5)

            uses = [
                b
                for e in events
                if e.get("type") == "assistant"
                for b in e["message"]["content"]
                if b["type"] == "tool_use"
            ]
            results = [
                b
                for e in events
                if e.get("type") == "user"
                for b in e["message"]["content"]
                if b["type"] == "tool_result"
            ]
            assert len(uses) == 1 and len(results) == 1
            # The block id is the MSP item id — the join key the reducer pairs on.
            assert uses[0] == {
                "type": "tool_use",
                "id": "tc1",
                "name": "Write",
                "input": {"path": "Cargo.toml"},
            }
            assert results[0]["tool_use_id"] == "tc1"
            assert results[0]["content"] == "Cargo.toml updated\n"
            assert "is_error" not in results[0]
            # D1 per-tool timing on both ends.
            assert results[0]["started_at"] and results[0][TOOL_ENDED_AT]
            # The assistant frame carries message.content (the shape the LIVE broker reads).
            assistant = next(e for e in events if e.get("type") == "assistant")
            assert assistant["message"]["model"] == MUSE_DEFAULT_MODEL
            assert assistant["message"]["content"] == assistant["content"]
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_failed_and_cancelled_tool_calls_are_error_results(self, tmp_path):
        t, host, events = await _started_transport(tmp_path)
        try:
            send = asyncio.create_task(t.send_message("Run the flaky integration suite"))
            cid = await host.accept_turn()
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                ),
                # A tool whose open we never saw (single-shot / after a gap) still pairs.
                _notification(
                    "item/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:8",
                        "item": {
                            "itemId": "tc9",
                            "kind": "toolCall",
                            "turnId": cid,
                            "revision": 2,
                            "status": "cancelled",
                            "tool": "shell",
                            "callId": "call_9f2c",
                            "args": '{"command":"cargo test -p tbh-agent"}',
                            "failureReason": "turn cancelled",
                            "visibleOutput": "   Compiling tbh-agent v0.9.4\n",
                        },
                    },
                ),
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:9",
                        "turnId": cid,
                        "terminal": "cancelled",
                        "reason": "cancelled during tool execution",
                        "durationMs": 5100,
                    },
                ),
            )
            await asyncio.wait_for(send, timeout=5)
            uses = [
                b
                for e in events
                if e.get("type") == "assistant"
                for b in e["message"]["content"]
                if b["type"] == "tool_use"
            ]
            results = [
                b
                for e in events
                if e.get("type") == "user"
                for b in e["message"]["content"]
                if b["type"] == "tool_result"
            ]
            assert uses[0]["name"] == "Bash" and uses[0]["input"] == {
                "command": "cargo test -p tbh-agent"
            }
            assert results[0]["is_error"] is True
            assert "turn cancelled" in results[0]["content"]
            result = next(e for e in events if e.get("type") == "result")
            assert result["stop_reason"] == "cancelled"
            assert result["reason"] == "cancelled during tool execution"
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_provider_failure_is_an_error_result_not_a_wedge(self, tmp_path):
        """Captured live 2026-09-02: a host with no credential ends every turn `failed`."""
        t, host, events = await _started_transport(tmp_path)
        try:
            send = asyncio.create_task(t.send_message("hello"))
            cid = await host.accept_turn()
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                ),
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:5",
                        "turnId": cid,
                        "terminal": "failed",
                        "durationMs": 68,
                        "error": {
                            "kind": "modelError",
                            "message": "not logged in: run /login to add an API key",
                            "retryable": True,
                        },
                        "reason": "not logged in: run /login to add an API key",
                    },
                ),
            )
            await asyncio.wait_for(send, timeout=5)
            result = next(e for e in events if e.get("type") == "result")
            assert result["stop_reason"] == "error"
            assert result["is_error"] is True
            assert "not logged in" in result["error"]
            # With no assistant text the reason IS the turn's text, so the user sees why.
            assert "not logged in" in result["result"]
            # Usage still advances (estimated) so message_count moves and the session
            # reads as active rather than silently stuck.
            usage = next(iter(result["modelUsage"].values()))
            assert usage["inputTokens"] >= 1 and usage["outputTokens"] >= 1
            assert t.is_turn_active is False
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_reasoning_streams_as_thinking_deltas_not_text(self, tmp_path):
        t, host, events = await _started_transport(tmp_path)
        try:
            send = asyncio.create_task(t.send_message("think"))
            cid = await host.accept_turn()
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                ),
                _notification(
                    "item/started",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:5",
                        "item": {
                            "itemId": "r1",
                            "kind": "reasoning",
                            "turnId": cid,
                            "revision": 1,
                            "status": "inProgress",
                        },
                    },
                ),
                _notification(
                    "item/delta",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:6",
                        "itemId": "r1",
                        "field": "summary.0",
                        "delta": "Considering the",
                    },
                ),
                _notification(
                    "item/delta",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:7",
                        "itemId": "r1",
                        "field": "summary.0",
                        "delta": " options",
                    },
                ),
                _notification(
                    "item/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:8",
                        "item": {
                            "itemId": "r1",
                            "kind": "reasoning",
                            "turnId": cid,
                            "revision": 2,
                            "status": "completed",
                            "summary": ["Considering the options"],
                        },
                    },
                ),
                # A reasoning item that never streamed (provider exposes only committed text).
                _notification(
                    "item/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:9",
                        "item": {
                            "itemId": "r2",
                            "kind": "reasoning",
                            "turnId": cid,
                            "revision": 1,
                            "status": "completed",
                            "text": "raw thought",
                        },
                    },
                ),
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:10",
                        "turnId": cid,
                        "terminal": "completed",
                    },
                ),
            )
            await asyncio.wait_for(send, timeout=5)
            thinking = [
                e["delta"]["thinking"]
                for e in events
                if e.get("type") == "content_block_delta" and "thinking" in e["delta"]
            ]
            text = [
                e for e in events if e.get("type") == "content_block_delta" and "text" in e["delta"]
            ]
            assert "".join(thinking) == "Considering the optionsraw thought"
            assert not text, "reasoning leaked into the answer stream"
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_subagent_and_todo_list_surface_as_task_and_plan(self, tmp_path):
        t, host, events = await _started_transport(tmp_path)
        try:
            send = asyncio.create_task(t.send_message("fan out"))
            cid = await host.accept_turn()
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                ),
                _notification(
                    "item/started",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:5",
                        "item": {
                            "itemId": "sa1",
                            "kind": "subagent",
                            "turnId": cid,
                            "revision": 1,
                            "status": "inProgress",
                            "role": "reviewer",
                            "objective": "review the diff",
                            "subagentId": "sub-1",
                        },
                    },
                ),
                _notification(
                    "session/todoListChanged",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:6",
                        "items": [
                            {"id": "1", "content": "Review", "status": "inProgress"},
                            {"id": "2", "content": "Fix", "status": "pending"},
                        ],
                    },
                ),
                _notification(
                    "item/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:7",
                        "item": {
                            "itemId": "sa1",
                            "kind": "subagent",
                            "turnId": cid,
                            "revision": 3,
                            "status": "completed",
                            "role": "reviewer",
                            "objective": "review the diff",
                            "result": {"summary": "LGTM"},
                        },
                    },
                ),
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:8",
                        "turnId": cid,
                        "terminal": "completed",
                    },
                ),
            )
            await asyncio.wait_for(send, timeout=5)
            uses = [
                b
                for e in events
                if e.get("type") == "assistant"
                for b in e["message"]["content"]
                if b["type"] == "tool_use"
            ]
            results = [
                b
                for e in events
                if e.get("type") == "user"
                for b in e["message"]["content"]
                if b["type"] == "tool_result"
            ]
            assert uses[0]["name"] == "Task" and uses[0]["input"]["objective"] == "review the diff"
            assert results[0]["tool_use_id"] == "sa1" and "LGTM" in results[0]["content"]
            plan = next(e for e in events if e.get("type") == "plan")
            assert plan["tasks"] == [
                {"content": "Review", "status": "in_progress", "id": "1"},
                {"content": "Fix", "status": "pending", "id": "2"},
            ]
            assert plan["counts"] == {"total": 2, "pending": 1, "in_progress": 1, "completed": 0}
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_session_token_usage_beats_the_turn_aggregate(self, tmp_path):
        t, host, events = await _started_transport(tmp_path)
        try:
            send = asyncio.create_task(t.send_message("count"))
            cid = await host.accept_turn()
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                ),
                _notification(
                    "session/tokenUsage",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:5",
                        "turnId": cid,
                        "modelId": "muse-spark-1.2",
                        "promptTokens": 1000,
                        "totalTokens": 1100,
                        "usage": {
                            "inputTokens": 1000,
                            "outputTokens": 100,
                            "cachedTokens": 300,
                            "reasoningTokens": 10,
                        },
                        "cumulative": {"promptTokens": 1000},
                    },
                ),
                _notification(
                    "session/tokenUsage",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:6",
                        "turnId": cid,
                        "modelId": "muse-spark-1.2",
                        "promptTokens": 2000,
                        "totalTokens": 2200,
                        "usage": {
                            "inputTokens": 2000,
                            "outputTokens": 200,
                            "cachedTokens": 700,
                            "reasoningTokens": 20,
                        },
                        "cumulative": {"promptTokens": 3000},
                    },
                ),
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:7",
                        "turnId": cid,
                        "terminal": "completed",
                        "usage": {
                            "inputTokens": 1,
                            "outputTokens": 1,
                            "cachedTokens": 0,
                            "reasoningTokens": 0,
                        },
                    },
                ),
            )
            await asyncio.wait_for(send, timeout=5)
            result = next(e for e in events if e.get("type") == "result")
            # The model that actually ran is the usage key (a mid-session switch is honest).
            usage = result["modelUsage"]["muse-spark-1.2"]
            assert usage["inputTokens"] == 3000 - 1000
            assert usage["cacheReadInputTokens"] == 1000
            assert usage["outputTokens"] == 300
        finally:
            await t.stop()


# ---------------------------------------------------------------------------
# Steering, interrupt, timeout, teardown
# ---------------------------------------------------------------------------


class TestSteeringAndControl:
    @pytest.mark.asyncio
    async def test_redirect_steers_the_running_turn_natively(self, tmp_path):
        t, host, events = await _started_transport(tmp_path)
        try:
            send = asyncio.create_task(t.send_message("first"))
            cid = await host.accept_turn()
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                )
            )
            await _settle()
            assert t.is_turn_active is True

            steer = asyncio.create_task(
                t.send_control("redirect", content="also do X", msg_id="m-1", request_id="r-1")
            )
            await host.answer(
                "turn/start",
                {
                    "commandId": "c2",
                    "status": "accepted",
                    "turnId": cid,
                    "startedNewTurn": False,
                    "disposition": "steered",
                },
                expect=2,
            )
            await asyncio.wait_for(steer, timeout=5)
            steer_params = host.sent("turn/start")[-1]["params"]
            assert steer_params["ifBusy"] == "steer"
            assert steer_params["input"] == [{"type": "text", "text": "also do X"}]
            # The steer was absorbed by the running turn: NO interrupt, NO new turn, and the
            # broker is told the message was consumed so it flips pending -> active.
            assert not host.sent("turn/interrupt")
            consumed = [e for e in events if e.get("type") == "user_consumed"]
            assert consumed == [
                {
                    "type": "user_consumed",
                    "event_type": "muse.turn.accepted",
                    "msg_id": "m-1",
                    "request_id": "r-1",
                }
            ]

            await host.push(
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:9",
                        "turnId": cid,
                        "terminal": "completed",
                    },
                )
            )
            await asyncio.wait_for(send, timeout=5)
            assert len([e for e in events if e.get("type") == "result"]) == 1
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_redirect_when_idle_starts_a_turn_and_flips_on_turn_started(self, tmp_path):
        t, host, events = await _started_transport(tmp_path)
        try:
            steer = asyncio.create_task(
                t.send_control("redirect", content="go", msg_id="m-2", request_id=None)
            )
            # turn/started races AHEAD of the ack on the reader: the bubble must still flip.
            for _ in range(50):
                if host.sent("turn/start"):
                    break
                await asyncio.sleep(0.01)
            cid = host.sent("turn/start")[-1]["params"]["commandId"]
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                )
            )
            await host.answer(
                "turn/start",
                {
                    "commandId": cid,
                    "status": "accepted",
                    "turnId": cid,
                    "startedNewTurn": True,
                    "disposition": "started",
                },
            )
            await asyncio.wait_for(steer, timeout=5)
            await _settle()
            consumed = [e for e in events if e.get("type") == "user_consumed"]
            assert consumed == [
                {"type": "user_consumed", "event_type": "muse.turn.accepted", "msg_id": "m-2"}
            ]
            assert t.is_turn_active is True
            await host.push(
                _notification(
                    "turn/completed",
                    {"sessionId": SID, "viewCursor": "v:9", "turnId": cid, "terminal": "completed"},
                )
            )
            await _settle()
            assert [e for e in events if e.get("type") == "result"]
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_interrupt_sends_turn_interrupt_for_the_active_turn(self, tmp_path):
        t, host, _ = await _started_transport(tmp_path)
        try:
            send = asyncio.create_task(t.send_message("long"))
            cid = await host.accept_turn()
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                )
            )
            await _settle()
            intr = asyncio.create_task(t.send_control("interrupt"))
            await host.answer(
                "turn/interrupt", {"commandId": "x", "status": "accepted", "turnId": cid}
            )
            await asyncio.wait_for(intr, timeout=5)
            params = host.sent("turn/interrupt")[0]["params"]
            assert params["sessionId"] == SID and params["turnId"] == cid
            await host.push(
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:9",
                        "turnId": cid,
                        "terminal": "cancelled",
                    },
                )
            )
            await asyncio.wait_for(send, timeout=5)
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_prompt_timeout_interrupts_then_finalizes_the_turn(self, tmp_path):
        """A timeout must EMIT a result, or the session wedges forever."""
        t, host, events = await _started_transport(
            tmp_path, acp_prompt_timeout_s=0.05, msp_interrupt_grace_s=0.05
        )
        try:
            send = asyncio.create_task(t.send_message("do something slow"))
            cid = await host.accept_turn()
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                )
            )
            # The host never answers the interrupt and never completes the turn.
            await asyncio.wait_for(send, timeout=5)
            results = [e for e in events if e.get("type") == "result"]
            assert results, "a timed-out turn emitted no result — the session would wedge"
            assert results[-1]["stop_reason"] == "timeout"
            assert results[-1]["is_error"] is True
            assert host.sent("turn/interrupt"), "no interrupt sent to the host"
            assert t.is_turn_active is False
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_stop_mid_turn_still_finalizes(self, tmp_path):
        t, host, events = await _started_transport(tmp_path)
        send = asyncio.create_task(t.send_message("x"))
        cid = await host.accept_turn()
        await host.push(
            _notification(
                "turn/started",
                {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
            )
        )
        await _settle()
        await t.stop()
        await asyncio.wait_for(send, timeout=5)
        results = [e for e in events if e.get("type") == "result"]
        assert results and results[-1]["stop_reason"] == "cancelled"

    @pytest.mark.asyncio
    async def test_host_eof_mid_turn_finalizes_with_an_error(self, tmp_path):
        t, host, events = await _started_transport(tmp_path)
        send = asyncio.create_task(t.send_message("x"))
        cid = await host.accept_turn()
        await host.push(
            _notification(
                "turn/started",
                {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
            )
        )
        await _settle()
        await host.push(b"")  # EOF
        await asyncio.wait_for(send, timeout=5)
        results = [e for e in events if e.get("type") == "result"]
        assert results and results[-1]["stop_reason"] == "error"
        assert "host exited" in results[-1]["error"]
        await t.stop()

    @pytest.mark.asyncio
    async def test_set_model_and_permission_mode_controls(self, tmp_path):
        t, host, _ = await _started_transport(tmp_path)
        try:
            task = asyncio.create_task(t.send_control("set_model", model="muse-spark-1.2"))
            await host.answer("session/setModel", {"commandId": "x", "status": "accepted"})
            await asyncio.wait_for(task, timeout=5)
            assert host.sent("session/setModel")[0]["params"]["model"] == {
                "modelId": "muse-spark-1.2"
            }
            assert t.model == "muse-spark-1.2"

            task = asyncio.create_task(t.send_control("set_permission_mode", mode="default"))
            await host.answer("session/setApprovalMode", {"commandId": "x", "status": "accepted"})
            await asyncio.wait_for(task, timeout=5)
            assert host.sent("session/setApprovalMode")[0]["params"]["mode"] == "onRequest"
            assert t.approval_mode == "onRequest"
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_session_unloaded_between_turns_is_resumed_transparently(self, tmp_path):
        t, host, _ = await _started_transport(tmp_path)
        try:
            await host.push(
                _notification(
                    "session/closed", {"sessionId": SID, "viewCursor": "v:12", "reason": "idle"}
                )
            )
            await _settle()
            send = asyncio.create_task(t.send_message("again"))
            await host.answer(
                "session/resume",
                {
                    "session": _session(),
                    "viewCursor": "v:13",
                    "history": {"mode": "none", "items": None, "snapshot": None},
                    "pendingRequests": [],
                },
            )
            cid = await host.accept_turn()
            await host.push(
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:14",
                        "turnId": cid,
                        "terminal": "completed",
                    },
                )
            )
            await asyncio.wait_for(send, timeout=5)
            assert host.sent("session/resume")[0]["params"]["sessionId"] == SID
        finally:
            await t.stop()


# ---------------------------------------------------------------------------
# Approvals and questions (server -> client requests)
# ---------------------------------------------------------------------------


def _approval_request(req_id: int) -> bytes:
    return _frame(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "approval/request",
            "params": {
                "sessionId": SID,
                "viewCursor": "v:7",
                "approvalId": "ap1",
                "turnId": TURN,
                "taskId": "t1",
                "itemId": "tc1",
                "toolCallId": "call_9f21",
                "toolName": "write_file",
                "rawArgs": '{"path":"Cargo.toml"}',
                "subject": {
                    "kind": "fileAccess",
                    "toolName": "write_file",
                    "path": "/home/me/src/proj/Cargo.toml",
                    "access": "write",
                },
                "currentRequirementId": {"approvalId": "ap1", "sourceIndex": 0},
                "availableChoices": [
                    {
                        "choiceId": "allow_once",
                        "decision": "approved",
                        "scope": "once",
                        "label": "Allow once",
                    },
                    {
                        "choiceId": "allow_session",
                        "decision": "approvedForSession",
                        "scope": "session",
                        "label": "Allow for session",
                    },
                    {
                        "choiceId": "deny",
                        "decision": "denied",
                        "scope": "once",
                        "label": "Deny",
                        "acceptsFeedback": True,
                    },
                ],
                "judgeEscalated": False,
                "protectedWrite": False,
            },
        }
    )


class TestApprovalsAndQuestions:
    @pytest.mark.asyncio
    async def test_skip_permissions_auto_approves_for_the_session(self, tmp_path):
        t, host, events = await _started_transport(tmp_path, skip_permissions=True)
        try:
            await host.push(_approval_request(41))
            await host.answer(
                "approval/decide",
                {"approvalId": "ap1", "commandId": "x", "status": "accepted", "terminal": True},
            )
            await _settle()
            # The request was ACKED under the server's id first (silence hangs the agent)...
            ack = next(w for w in host.writes if w.get("id") == 41 and "method" not in w)
            assert ack["result"] == {}
            # ...then decided with a server-minted choice, guarded by the requirement id.
            decide = host.sent("approval/decide")[0]["params"]
            assert decide["approvalId"] == "ap1"
            assert decide["choiceId"] == "allow_session"
            assert decide["requirementId"] == {"approvalId": "ap1", "sourceIndex": 0}
            assert not [e for e in events if e.get("type") == "control_request"]
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_permission_gate_surfaces_a_control_request_and_relays_the_answer(self, tmp_path):
        t, host, events = await _started_transport(tmp_path, skip_permissions=False)
        try:
            assert host.sent("session/start")[0]["params"]["approvalMode"] == "onRequest"
            await host.push(_approval_request(42))
            await _settle()
            req = next(e for e in events if e.get("type") == "control_request")
            assert req["subtype"] == "can_use_tool"
            assert req["tool"] == "Write"
            assert req["input"]["path"] == "Cargo.toml"
            assert req["muse"]["subject"]["path"] == "/home/me/src/proj/Cargo.toml"
            assert req["muse"]["subject"]["access"] == "write"
            assert not host.sent("approval/decide")

            task = asyncio.create_task(
                t.send_control_response(
                    req["request_id"], {"behavior": "deny", "message": "not that file"}
                )
            )
            await host.answer(
                "approval/decide",
                {"approvalId": "ap1", "commandId": "x", "status": "accepted", "terminal": True},
            )
            await asyncio.wait_for(task, timeout=5)
            decide = host.sent("approval/decide")[0]["params"]
            assert decide["choiceId"] == "deny"
            assert decide["feedback"] == "not that file"
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_user_input_request_becomes_an_ask_user_question_and_answers_round_trip(
        self, tmp_path
    ):
        t, host, events = await _started_transport(tmp_path)
        try:
            questions = [
                {
                    "id": "db_choice",
                    "header": "Database",
                    "question": "Choose the database for the new service",
                    "options": [
                        {"label": "Postgres", "description": "relational"},
                        {"label": "SQLite", "description": "embedded"},
                    ],
                    "selection": {"mode": "single"},
                }
            ]
            await host.push(
                _frame(
                    {
                        "jsonrpc": "2.0",
                        "id": 18,
                        "method": "userInput/request",
                        "params": {
                            "sessionId": SID,
                            "viewCursor": "v:503",
                            "userInputId": "ui1",
                            "turnId": TURN,
                            "itemId": "tc-ask",
                            "toolCallId": "call_ask",
                            "toolName": "request_user_input",
                            "questions": questions,
                        },
                    }
                )
            )
            await _settle()
            ack = next(w for w in host.writes if w.get("id") == 18 and "method" not in w)
            assert ack["result"] == {}
            ask = next(e for e in events if e.get("type") == "ask_user_question")
            assert ask["event_type"] == "ask_user_question"
            assert ask["request_id"] == "ui1"
            assert ask["tool_use_id"] == "tc-ask"
            assert ask["questions"][0]["header"] == "Database"
            assert ask["questions"][0]["options"][1] == {
                "label": "SQLite",
                "description": "embedded",
            }
            assert ask["questions"][0]["multiSelect"] is False

            task = asyncio.create_task(
                t.send_control(
                    "ask_user_answer", request_id="ui1", answers=[{"answer": "Postgres"}]
                )
            )
            await host.answer(
                "userInput/answer", {"commandId": "x", "status": "accepted", "userInputId": "ui1"}
            )
            await asyncio.wait_for(task, timeout=5)
            answer = host.sent("userInput/answer")[0]["params"]
            assert answer["userInputId"] == "ui1"
            assert answer["answers"] == [{"questionId": "db_choice", "selectedLabel": "Postgres"}]
            resolved = next(e for e in events if e.get("type") == "ask_user_resolved")
            assert resolved["request_id"] == "ui1" and resolved["decision"] == "answered"
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_chat_reply_while_a_question_is_pending_is_a_clarification(self, tmp_path):
        t, host, events = await _started_transport(tmp_path)
        try:
            await host.push(
                _frame(
                    {
                        "jsonrpc": "2.0",
                        "id": 19,
                        "method": "userInput/request",
                        "params": {
                            "sessionId": SID,
                            "viewCursor": "v:1",
                            "userInputId": "ui2",
                            "turnId": TURN,
                            "itemId": "i",
                            "toolCallId": "c",
                            "toolName": "request_user_input",
                            "questions": [
                                {
                                    "id": "q",
                                    "header": "H",
                                    "question": "Q?",
                                    "options": [{"label": "A"}],
                                    "selection": {"mode": "single"},
                                }
                            ],
                        },
                    }
                )
            )
            await _settle()
            task = asyncio.create_task(
                t.send_control("redirect", content="Actually use B, and explain why.", msg_id="m-9")
            )
            await host.answer(
                "userInput/clarify", {"commandId": "x", "status": "accepted", "userInputId": "ui2"}
            )
            await asyncio.wait_for(task, timeout=5)
            clarify = host.sent("userInput/clarify")[0]["params"]
            assert clarify["userInputId"] == "ui2"
            assert clarify["clarification"] == {
                "format": "text",
                "content": "Actually use B, and explain why.",
            }
            # No new turn was started for the reply, and the bubble still flipped active.
            assert not host.sent("turn/start")
            assert [e for e in events if e.get("type") == "user_consumed" and e["msg_id"] == "m-9"]
            assert [
                e
                for e in events
                if e.get("type") == "ask_user_resolved" and e["decision"] == "clarified"
            ]
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_unknown_server_request_is_refused_not_ignored(self, tmp_path):
        t, host, _ = await _started_transport(tmp_path)
        try:
            await host.push(
                _frame({"jsonrpc": "2.0", "id": 77, "method": "future/thing", "params": {}})
            )
            await _settle()
            refusal = next(w for w in host.writes if w.get("id") == 77 and "method" not in w)
            assert refusal["error"]["code"] == -32601
        finally:
            await t.stop()


# ---------------------------------------------------------------------------
# Gap recovery + reducer seam
# ---------------------------------------------------------------------------


class TestGapAndSeam:
    @pytest.mark.asyncio
    async def test_view_gap_is_spliced_from_view_page(self, tmp_path):
        t, host, events = await _started_transport(tmp_path)
        try:
            send = asyncio.create_task(t.send_message("x"))
            cid = await host.accept_turn()
            await host.push(
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                ),
                _notification("view/gap", {"sessionId": SID, "after": "v:4", "next": "v:9"}),
            )
            await host.answer(
                "view/page",
                {
                    "events": [
                        {
                            "method": "item/completed",
                            "params": {
                                "sessionId": SID,
                                "viewCursor": "v:6",
                                "item": {
                                    "itemId": "a1",
                                    "kind": "agentMessage",
                                    "turnId": cid,
                                    "revision": 2,
                                    "status": "completed",
                                    "text": "recovered ",
                                },
                            },
                        },
                        {
                            "method": "item/completed",
                            "params": {
                                "sessionId": SID,
                                "viewCursor": "v:7",
                                "item": {
                                    "itemId": "a2",
                                    "kind": "agentMessage",
                                    "turnId": cid,
                                    "revision": 2,
                                    "status": "completed",
                                    "text": "text",
                                },
                            },
                        },
                    ],
                    "nextCursor": None,
                },
            )
            # Delivery resumes live from `next` (the exclusive upper bound of the hole).
            await host.push(
                _notification(
                    "turn/completed",
                    {"sessionId": SID, "viewCursor": "v:9", "turnId": cid, "terminal": "completed"},
                )
            )
            await asyncio.wait_for(send, timeout=5)
            page = host.sent("view/page")[0]["params"]
            assert page["cursor"] == "v:4" and page["direction"] == "forward"
            text = "".join(
                e["delta"]["text"]
                for e in events
                if e.get("type") == "content_block_delta" and "text" in e["delta"]
            )
            assert text == "recovered text"
            # The event AT `next` arrived live and closed the turn exactly once.
            assert len([e for e in events if e.get("type") == "result"]) == 1
        finally:
            await t.stop()

    @pytest.mark.asyncio
    async def test_transport_output_reduces_to_paired_transcript_parts(self, tmp_path):
        """The reducer seam: what this transport emits folds into paired tool parts."""
        from dataclasses import dataclass

        from niuu.domain.transcript_reducer import reduce_frames

        @dataclass
        class _Frame:
            seq: int
            kind: str
            payload: dict
            request_id: str | None = None
            ts: str | None = "2026-09-02T11:00:00Z"
            session_id: str | None = "s1"

        t, host, events = await _started_transport(tmp_path)
        try:
            send = asyncio.create_task(t.send_message("two tools"))
            cid = await host.accept_turn()
            frames = [
                _notification(
                    "turn/started",
                    {"sessionId": SID, "viewCursor": "v:4", "turnId": cid, "commandId": cid},
                ),
            ]
            for i, (tool, args, out) in enumerate(
                (
                    ("write_file", '{"path":"a.py"}', "ok"),
                    ("shell", '{"command":"pytest"}', "3 passed"),
                )
            ):
                iid = f"tc{i}"
                frames.append(
                    _notification(
                        "item/started",
                        {
                            "sessionId": SID,
                            "viewCursor": f"v:{10 + i}",
                            "item": {
                                "itemId": iid,
                                "kind": "toolCall",
                                "turnId": cid,
                                "revision": 1,
                                "status": "inProgress",
                                "tool": tool,
                                "args": args,
                            },
                        },
                    )
                )
                frames.append(
                    _notification(
                        "item/completed",
                        {
                            "sessionId": SID,
                            "viewCursor": f"v:{20 + i}",
                            "item": {
                                "itemId": iid,
                                "kind": "toolCall",
                                "turnId": cid,
                                "revision": 2,
                                "status": "completed",
                                "tool": tool,
                                "args": args,
                                "visibleOutput": out,
                            },
                        },
                    )
                )
            frames.append(
                _notification(
                    "item/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:30",
                        "item": {
                            "itemId": "a1",
                            "kind": "agentMessage",
                            "turnId": cid,
                            "revision": 2,
                            "status": "completed",
                            "text": "done",
                        },
                    },
                )
            )
            frames.append(
                _notification(
                    "turn/completed",
                    {
                        "sessionId": SID,
                        "viewCursor": "v:31",
                        "turnId": cid,
                        "terminal": "completed",
                    },
                )
            )
            await host.push(*frames)
            await asyncio.wait_for(send, timeout=5)

            reduced = reduce_frames(
                [
                    _Frame(seq=i + 1, kind=e.get("type", "unknown"), payload=e)
                    for i, e in enumerate(events)
                ]
            )
            parts = [p for turn in reduced.turns for p in (turn.get("parts") or [])]
            uses = [p for p in parts if p.get("type") == "tool_use"]
            results = [p for p in parts if p.get("type") == "tool_result"]
            assert {u["name"] for u in uses} == {"Write", "Bash"}
            assert {r["tool_use_id"] for r in results} == {u["id"] for u in uses}
            assert all(u.get("started_at") and u.get(TOOL_ENDED_AT) for u in uses)
        finally:
            await t.stop()


class TestProtocolError:
    def test_error_carries_typed_kind_and_reason(self):
        exc = MuseProtocolError(
            {
                "code": -32030,
                "message": "turn/cancel rejected: missing_run",
                "data": {"kind": "commandRejected", "reason": "missing_run"},
            }
        )
        assert exc.code == -32030 and exc.kind == "commandRejected" and exc.reason == "missing_run"
        assert "commandRejected" in str(exc)


def test_muse_rejects_system_instructions_it_cannot_apply(tmp_path):
    with pytest.raises(ValueError, match="cannot apply system_prompt"):
        MuseMSPTransport(str(tmp_path), system_prompt="project briefing")

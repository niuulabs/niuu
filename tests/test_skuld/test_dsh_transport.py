"""Tests for DshJsonRpcTransport — DeepSeek Harness via SDK JSON-RPC stdio.

Mirrors the depth and style of the Grok/Codex/OpenCode transport tests: event
mapping parity (UI/Ravn/broker), tool normalization, protocol plumbing against
a faked runtime process, interrupt semantics, resolver error paths, and
capabilities.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.config import SkuldSettings
from skuld.transports import DshJsonRpcTransport
from skuld.transports.dsh import (
    _map_dsh_tool,
    resolve_dsh_cordis_config,
    resolve_dsh_launch_args,
)


def _session_event(session_id: str, event: dict) -> dict:
    return {"sessionId": session_id, "event": event}


@pytest.fixture
def transport(tmp_path):
    return DshJsonRpcTransport(str(tmp_path), model="deepseek-v4-flash", session_id="sess-1")


@pytest.fixture
def events(transport):
    collected: list[dict] = []

    async def collect(event: dict) -> None:
        collected.append(event)

    transport.on_event(collect)
    return collected


class TestConstructionAndCapabilities:
    def test_init_defaults(self, tmp_path):
        t = DshJsonRpcTransport(str(tmp_path))
        assert t.workspace_dir == str(tmp_path)
        assert t.last_result is None
        assert t.is_alive is False
        assert t.is_turn_active is False
        assert t.session_id.startswith("session-")

    def test_dsh_session_id_is_deterministic_per_skuld_session(self, tmp_path):
        a = DshJsonRpcTransport(str(tmp_path), session_id="sess-1")
        b = DshJsonRpcTransport(str(tmp_path), session_id="sess-1")
        c = DshJsonRpcTransport(str(tmp_path), session_id="sess-2")
        assert a.session_id == b.session_id
        assert a.session_id != c.session_id

    def test_capabilities(self, tmp_path):
        caps = DshJsonRpcTransport(str(tmp_path)).capabilities
        assert caps.send_message is True
        assert caps.cli_websocket is False
        assert caps.session_resume is False
        assert caps.interrupt is False

    def test_cli_type_resolves_transport_adapter(self):
        settings = SkuldSettings(cli_type="dsh")
        assert settings.transport_adapter == "skuld.transports.dsh.DshJsonRpcTransport"


class TestToolMap:
    def test_tool_map_parity(self):
        # Overlapping tools must normalize identically to Codex/Claude for UI + Ravn.
        assert _map_dsh_tool("bash") == "Bash"
        assert _map_dsh_tool("read_file") == "Read"
        assert _map_dsh_tool("write_file") == "Write"
        assert _map_dsh_tool("edit_file") == "Edit"
        assert _map_dsh_tool("todo") == "Todo"
        assert _map_dsh_tool("subagent") == "Subagent"
        # Unknowns pass through.
        assert _map_dsh_tool("unknown_foo") == "unknown_foo"


class TestResolvers:
    def test_explicit_runtime_bin_wins(self):
        assert resolve_dsh_launch_args("/custom/dsh-agent") == ["/custom/dsh-agent"]

    def test_missing_runtime_package_is_fatal_with_remedy(self):
        with patch.dict("sys.modules", {"deepseek_harness_runtime": None}):
            with pytest.raises(RuntimeError, match="deepseek-harness-runtime-bin"):
                resolve_dsh_launch_args("")

    def test_explicit_cordis_config_must_exist(self, tmp_path):
        with pytest.raises(RuntimeError, match="does not exist"):
            resolve_dsh_cordis_config(str(tmp_path / "missing.yml"))

    def test_explicit_cordis_config_wins(self, tmp_path):
        path = tmp_path / "cordis.yml"
        path.write_text("- id: sdk-jsonrpc-server\n")
        assert resolve_dsh_cordis_config(str(path)) == str(path)

    def test_missing_cordis_package_is_fatal_with_remedy(self):
        with patch.dict("sys.modules", {"deepseek_harness_runtime": None}):
            with pytest.raises(RuntimeError, match="dsh.cordis_config"):
                resolve_dsh_cordis_config("")


class TestEventMapping:
    @pytest.mark.asyncio
    async def test_text_delta_chunk_maps_to_text_delta(self, transport, events):
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {
                    "type": "assistant/chunk",
                    "data": {"chunk": {"type": "text-delta", "index": 0, "text": "hello"}},
                },
            )
        )
        assert events == [
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}}
        ]

    @pytest.mark.asyncio
    async def test_reasoning_delta_maps_to_thinking_delta(self, transport, events):
        # Reasoning must map to thinking_delta (separate reasoning block),
        # never an inline text_delta.
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {
                    "type": "assistant/chunk",
                    "data": {"chunk": {"type": "reasoning-delta", "index": 0, "text": "hmm"}},
                },
            )
        )
        assert events == [
            {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hmm"}}
        ]

    @pytest.mark.asyncio
    async def test_empty_delta_is_filtered(self, transport, events):
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {
                    "type": "assistant/chunk",
                    "data": {"chunk": {"type": "text-delta", "index": 0, "text": ""}},
                },
            )
        )
        assert events == []

    @pytest.mark.asyncio
    async def test_usage_chunk_is_captured_for_result(self, transport, events):
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {
                    "type": "assistant/chunk",
                    "data": {
                        "chunk": {
                            "type": "usage",
                            "usage": {
                                "inputTokens": 120,
                                "outputTokens": 30,
                                "cacheReadTokens": 5,
                            },
                        }
                    },
                },
            )
        )
        assert events == []
        result = transport._make_result("end_turn")
        usage = result["modelUsage"]["deepseek-v4-flash"]
        assert usage["inputTokens"] == 120
        assert usage["outputTokens"] == 30
        assert usage["cacheReadInputTokens"] == 5

    @pytest.mark.asyncio
    async def test_assistant_message_emits_committed_text(self, transport, events):
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {
                    "type": "assistant/message",
                    "data": {
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "done"}],
                            "usage": {"inputTokens": 10, "outputTokens": 2},
                        }
                    },
                },
            )
        )
        assert events == [{"type": "assistant", "message": {"content": "done"}, "content": "done"}]
        usage = transport._make_result("end_turn")["modelUsage"]["deepseek-v4-flash"]
        assert usage["inputTokens"] == 10

    @pytest.mark.asyncio
    async def test_content_less_assistant_message_emits_nothing(self, transport, events):
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {"type": "assistant/message", "data": {"message": {"content": []}}},
            )
        )
        assert events == []

    @pytest.mark.asyncio
    async def test_tool_call_maps_to_tool_use(self, transport, events):
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {
                    "type": "tool/call",
                    "data": {
                        "callId": "call_1",
                        "name": "bash",
                        "arguments": json.dumps({"command": "echo hi"}),
                    },
                },
            )
        )
        assert events == [
            {
                "type": "assistant",
                "message": {
                    "model": "deepseek-v4-flash",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_1",
                            "name": "Bash",
                            "input": {"command": "echo hi"},
                        }
                    ],
                },
            }
        ]

    @pytest.mark.asyncio
    async def test_tool_call_with_malformed_arguments(self, transport, events):
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {
                    "type": "tool/call",
                    "data": {"callId": "call_2", "name": "bash", "arguments": "not-json"},
                },
            )
        )
        block = events[0]["message"]["content"][0]
        assert block["input"] == {"raw": "not-json"}

    @pytest.mark.asyncio
    async def test_tool_result_maps_to_user_tool_result(self, transport, events):
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {
                    "type": "tool/result",
                    "data": {
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool-result",
                                    "toolCallId": "call_1",
                                    "isError": False,
                                    "content": [{"type": "text", "text": "hi\n"}],
                                }
                            ],
                        }
                    },
                },
            )
        )
        assert events == [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": "hi\n",
                            "is_error": False,
                        }
                    ]
                },
            }
        ]

    @pytest.mark.asyncio
    async def test_turn_end_completed_emits_result(self, transport, events):
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "completed"}}},
            )
        )
        assert transport.last_result is not None
        assert transport.last_result["stop_reason"] == "end_turn"
        assert events[-1]["type"] == "result"

    @pytest.mark.asyncio
    async def test_turn_end_error_emits_error_and_result(self, transport, events):
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {
                    "type": "turn/end",
                    "data": {
                        "turn": 1,
                        "reason": {"kind": "error", "error": {"message": "boom"}},
                    },
                },
            )
        )
        assert events[0] == {"type": "error", "content": "boom"}
        assert transport.last_result["stop_reason"] == "error"

    @pytest.mark.asyncio
    async def test_turn_end_max_tokens_maps_stop_reason(self, transport, events):
        await transport._handle_session_event(
            _session_event(
                transport.session_id,
                {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "max-tokens"}}},
            )
        )
        assert transport.last_result["stop_reason"] == "max_tokens"

    @pytest.mark.asyncio
    async def test_descendant_session_events_are_not_forwarded(self, transport, events):
        # Subagent sessions stream over the same connection; the root
        # transcript must not interleave their chunks.
        await transport._handle_session_event(
            _session_event(
                "session-someotherchild",
                {
                    "type": "assistant/chunk",
                    "data": {"chunk": {"type": "text-delta", "index": 0, "text": "child"}},
                },
            )
        )
        assert events == []

    @pytest.mark.asyncio
    async def test_idle_status_only_for_own_session(self, transport):
        await transport._handle_status({"sessionId": "session-other", "status": "idle"})
        assert not transport._idle_event.is_set()
        await transport._handle_status({"sessionId": transport.session_id, "status": "idle"})
        assert transport._idle_event.is_set()


class TestProtocolPlumbing:
    @pytest.mark.asyncio
    async def test_response_resolves_pending_request(self, transport):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        transport._pending[7] = future
        await transport._handle_frame(json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"ok": 1}}))
        assert future.result() == {"ok": 1}

    @pytest.mark.asyncio
    async def test_error_response_raises_on_pending_request(self, transport):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        transport._pending[8] = future
        await transport._handle_frame(
            json.dumps({"jsonrpc": "2.0", "id": 8, "error": {"code": -32603, "message": "nope"}})
        )
        with pytest.raises(RuntimeError, match="nope"):
            future.result()

    @pytest.mark.asyncio
    async def test_non_json_stdout_line_is_ignored(self, transport, events):
        await transport._handle_frame("plain text noise")
        assert events == []

    @pytest.mark.asyncio
    async def test_send_message_without_process_is_fatal(self, transport):
        with pytest.raises(RuntimeError, match="not running"):
            await transport.send_message("hi")

    @pytest.mark.asyncio
    async def test_interrupt_raises(self, transport):
        with pytest.raises(RuntimeError, match="no cancel method"):
            await transport.interrupt()

    @pytest.mark.asyncio
    async def test_stop_without_process_is_noop(self, transport):
        await transport.stop()
        assert transport.is_alive is False

    @pytest.mark.asyncio
    async def test_send_message_full_turn(self, transport, events):
        """Drive a whole prompt turn against a faked runtime process."""
        process = MagicMock()
        process.returncode = None
        process.stdin.write = MagicMock()
        process.stdin.drain = AsyncMock()
        transport._process = process

        async def feed_turn() -> None:
            # The prompt request is id 1 (first request this transport sends).
            await transport._handle_frame(
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"messageId": "m-1"}})
            )
            for event in (
                {
                    "type": "assistant/chunk",
                    "data": {"chunk": {"type": "text-delta", "index": 0, "text": "hi"}},
                },
                {
                    "type": "assistant/message",
                    "data": {"message": {"content": [{"type": "text", "text": "hi"}]}},
                },
                {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "completed"}}},
            ):
                await transport._handle_session_event(_session_event(transport.session_id, event))
            await transport._handle_status({"sessionId": transport.session_id, "status": "idle"})

        feeder = asyncio.ensure_future(feed_turn())
        await transport.send_message("say hi")
        await feeder

        written = process.stdin.write.call_args[0][0].decode()
        frame = json.loads(written)
        assert frame["method"] == "session/prompt"
        assert frame["params"]["sessionId"] == transport.session_id
        assert frame["params"]["contentBlocks"] == [{"type": "text", "text": "say hi"}]

        types = [event["type"] for event in events]
        assert types == ["content_block_delta", "assistant", "result"]
        assert transport.last_result["stop_reason"] == "end_turn"
        assert transport.is_turn_active is False

    @pytest.mark.asyncio
    async def test_send_message_synthesizes_result_when_idle_without_turn_end(
        self, transport, events
    ):
        process = MagicMock()
        process.returncode = None
        process.stdin.write = MagicMock()
        process.stdin.drain = AsyncMock()
        transport._process = process

        async def feed() -> None:
            await transport._handle_frame(
                json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"messageId": "m-1"}})
            )
            await transport._handle_status({"sessionId": transport.session_id, "status": "idle"})

        feeder = asyncio.ensure_future(feed())
        await transport.send_message("noop")
        await feeder

        assert transport.last_result is not None
        assert transport.last_result["type"] == "result"
        assert events[-1]["type"] == "result"

    @pytest.mark.asyncio
    async def test_reader_death_fails_pending_and_unblocks_turn(self, transport):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        transport._pending[3] = future
        transport._fail_pending(RuntimeError("dsh runtime stdout closed"))
        with pytest.raises(RuntimeError, match="stdout closed"):
            future.result()
        assert transport._pending == {}

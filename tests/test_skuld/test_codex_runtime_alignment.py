"""Offline runtime-alignment regressions; native proof lives in opt-in tests."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from skuld.transports.codex_ws import CodexWebSocketTransport


@pytest.fixture(autouse=True)
def no_native_processes(monkeypatch):
    for method in ("create_subprocess_exec", "create_subprocess_shell"):
        monkeypatch.setattr(
            asyncio,
            method,
            AsyncMock(side_effect=AssertionError("Offline test launched a process")),
        )


@pytest.fixture
def transport(tmp_path):
    t = CodexWebSocketTransport(str(tmp_path), model="gpt-6-astra", reasoning_effort="xhigh")
    t._thread_id = "thread"
    t._current_turn_id = "turn"
    t._emit = AsyncMock()
    return t


def events(t, kind):
    return [call.args[0] for call in t._emit.await_args_list if call.args[0].get("type") == kind]


async def test_parallel_tools_have_independent_stream_indexes(transport):
    t = transport
    await t._handle_item_started({"type": "commandExecution", "id": "a", "command": "echo A"})
    await t._handle_item_started({"type": "agentMessage", "id": "prose", "text": ""})
    await t._handle_item_started({"type": "commandExecution", "id": "b", "command": "echo B"})
    starts = {e["content_block"].get("id"): e["index"] for e in events(t, "content_block_start")}
    for identity in ("a", "b"):
        await t._handle_item_completed(
            {
                "type": "commandExecution",
                "id": identity,
                "status": "completed",
                "exitCode": 0,
                "aggregatedOutput": identity,
            }
        )
    stops = events(t, "content_block_stop")
    assert all("index" in event for event in stops)
    assert starts["a"] in [event["index"] for event in stops]
    assert starts["b"] in [event["index"] for event in stops]
    assert starts["prose"] not in [event["index"] for event in stops]
    inputs = [
        e for e in events(t, "content_block_delta") if e["delta"]["type"] == "input_json_delta"
    ]
    assert [e["index"] for e in inputs] == [starts["a"], starts["b"]]


async def test_repeated_tool_start_updates_input_without_duplicate_stream(transport):
    t = transport
    item = {"type": "commandExecution", "id": "a", "command": "echo A"}
    await t._handle_item_started(item)
    await t._handle_item_started({**item, "command": "echo authoritative"})
    assert len(events(t, "content_block_start")) == 1
    assert len(events(t, "content_block_delta")) == 1
    assert (
        events(t, "assistant")[-1]["message"]["content"][0]["input"]["command"]
        == "echo authoritative"
    )


@pytest.mark.parametrize(
    "kind, details, expected",
    [
        ("commandExecution", {"status": "completed", "exitCode": None}, False),
        ("commandExecution", {"status": "declined", "exitCode": None}, True),
        ("commandExecution", {"status": "failed", "exitCode": None}, True),
        ("fileChange", {"status": "failed"}, True),
        ("fileChange", {"status": "declined"}, True),
        ("mcpToolCall", {"status": "failed", "error": {"message": "Native MCP failed"}}, True),
    ],
)
async def test_tool_result_reports_native_status(transport, kind, details, expected):
    await transport._handle_item_completed({"type": kind, "id": "tool", **details})
    result = events(transport, "user")[-1]["message"]["content"][0]
    assert bool(result.get("is_error")) is expected
    if kind == "mcpToolCall":
        assert "Native MCP failed" in result["content"]


async def test_retry_notice_does_not_poison_successful_native_turn(transport):
    t = transport
    await t._handle_server_message(
        {
            "method": "error",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "willRetry": True,
                "error": {"message": "Reconnecting"},
            },
        }
    )
    assert t._turn_error is None
    assert not events(t, "error")
    assert events(t, "system")[-1]["subtype"] == "runtime_notice"
    await t._handle_server_message(
        {
            "method": "turn/completed",
            "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}},
        }
    )
    assert events(t, "result")[-1]["is_error"] is False


async def test_failed_turn_completion_is_identity_scoped_and_idempotent(transport):
    t = transport
    t._turn_error = "failed"
    data = {
        "method": "turn/completed",
        "params": {"threadId": "thread", "turn": {"id": "turn", "status": "failed"}},
    }
    await t._handle_server_message(data)
    await t._handle_server_message(data)
    assert len(events(t, "result")) == 1
    assert events(t, "result")[0]["turn_id"] == "turn"


async def test_zero_last_turn_tokens_do_not_use_cumulative_totals(transport):
    await transport._handle_server_message(
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "tokenUsage": {
                    "total": {"inputTokens": 500, "outputTokens": 50, "cachedInputTokens": 100},
                    "last": {"inputTokens": 0, "outputTokens": 0, "cachedInputTokens": 0},
                },
            },
        }
    )
    usage = transport._last_usage["gpt-6-astra"]
    assert usage["inputTokens"] == usage["outputTokens"] == usage["cacheReadInputTokens"] == 0


async def test_inflight_usage_keeps_model_when_next_turn_model_changes(transport):
    t = transport
    await t._handle_server_message(
        {"method": "turn/started", "params": {"threadId": "thread", "turn": {"id": "turn"}}}
    )
    t._model = "gpt-5.6-sol"
    await t._handle_server_message(
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "tokenUsage": {"last": {"inputTokens": 9}},
            },
        }
    )
    assert set(t._last_usage) == {"gpt-6-astra"}


async def test_native_reroute_changes_actual_turn_not_requested_model(transport):
    t = transport
    await t._handle_server_message(
        {
            "method": "model/rerouted",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "fromModel": "gpt-6-astra",
                "toModel": "gpt-5.6-sol",
                "reason": "highRiskCyberActivity",
            },
        }
    )
    await t._handle_server_message(
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread",
                "turnId": "turn",
                "tokenUsage": {"last": {"inputTokens": 9}},
            },
        }
    )
    assert t._model == "gpt-6-astra"
    assert set(t._last_usage) == {"gpt-5.6-sol"}
    assert events(t, "system")[-1]["subtype"] == "runtime_notice"

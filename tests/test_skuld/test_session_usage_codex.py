"""Native Codex usage snapshots: offline transport inputs, never a provider call."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from skuld.transports.codex import CodexSubprocessTransport
from skuld.transports.codex_ws import CodexWebSocketTransport


@pytest.fixture(autouse=True)
def no_provider_processes(monkeypatch):
    for name in ("create_subprocess_exec", "create_subprocess_shell"):
        monkeypatch.setattr(
            asyncio, name, AsyncMock(side_effect=AssertionError("Unexpected provider process"))
        )


def _events(transport, kind):
    return [
        call.args[0] for call in transport._emit.await_args_list if call.args[0]["type"] == kind
    ]


def _ws(tmp_path):
    transport = CodexWebSocketTransport(str(tmp_path), model="gpt-6-astra")
    transport._thread_id = "thread"
    transport._current_turn_id = "turn"
    transport._emit = AsyncMock()
    return transport


def _usage(last=None, total=None, window=None, **params):
    return {
        "method": "thread/tokenUsage/updated",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "tokenUsage": {"last": last, "total": total, "modelContextWindow": window},
            **params,
        },
    }


async def test_reviewed_corpus_keeps_each_response_and_cumulative_snapshot(tmp_path):
    corpus = Path(__file__).parents[1] / "fixtures/forge-corpus/codex-agents-da06fa93af.frames.json"
    frames = json.loads(corpus.read_text())
    samples = [
        frame["payload"]["payload"]
        for frame in frames
        if frame["payload"].get("method") == "thread/tokenUsage/updated"
    ]
    transport = _ws(tmp_path)
    transport._thread_id = samples[0]["threadId"]
    transport._current_turn_id = samples[0]["turnId"]
    parent_samples = [row for row in samples if row["threadId"] == transport._thread_id]
    for sample in parent_samples:
        await transport._handle_server_message(
            {"method": "thread/tokenUsage/updated", "params": sample}
        )
    usage = _events(transport, "session_usage")
    assert len(usage) == 3  # Three responses inside the SAME native turn.
    assert [row["last"]["input"] + row["last"]["cache_read"] for row in usage] == [
        12011,
        12149,
        12291,
    ]
    assert [row["total"]["input"] + row["total"]["cache_read"] for row in usage] == [
        12011,
        24160,
        36451,
    ]
    assert {row["window"] for row in usage} == {258400}
    assert {row["model"] for row in usage} == {"gpt-6-astra"}
    assert {row["metadata"]["source"] for row in usage} == {"codex_ws"}
    assert usage[0]["last"] == {"input": 4203, "cache_read": 7808, "cache_write": 0, "output": 102}
    result = transport._last_usage["gpt-6-astra"]
    assert sum(result.values()) == parent_samples[-1]["tokenUsage"]["last"]["totalTokens"]


async def test_duplicate_native_usage_preserves_same_snapshot_for_durable_dedupe(tmp_path):
    transport = _ws(tmp_path)
    event = _usage({"inputTokens": 100, "cachedInputTokens": 20, "outputTokens": 3})
    await transport._handle_server_message(event)
    await transport._handle_server_message(event)
    frames = _events(transport, "session_usage")
    assert len(frames) == 2
    assert frames[0] == frames[1]


async def test_unknown_last_is_not_cumulative_context_and_zero_last_is_real(tmp_path):
    transport = _ws(tmp_path)
    total = {"inputTokens": 20000, "cachedInputTokens": 10000, "outputTokens": 20}
    await transport._handle_server_message(_usage(total=total))
    first = _events(transport, "session_usage")[-1]
    assert first["last"] is None
    assert first["window"] is None
    assert first["total"]["input"] == 10000
    await transport._handle_server_message(
        _usage(last={"inputTokens": 0, "cachedInputTokens": 0, "outputTokens": 0}, total=total)
    )
    assert _events(transport, "session_usage")[-1]["last"] == {
        "input": 0,
        "cache_read": 0,
        "cache_write": 0,
        "output": 0,
    }
    assert transport._last_usage["gpt-6-astra"]["inputTokens"] == 0


@pytest.mark.parametrize(
    "last,window",
    [
        ({}, 0),
        ({"inputTokens": True}, True),
        ({"inputTokens": -1}, -1),
        ({"inputTokens": 2, "cachedInputTokens": 3}, "258400"),
        ({"outputTokens": 3}, None),
        ({"inputTokens": 100}, None),
        ({"inputTokens": 100, "outputTokens": 3}, None),
    ],
)
async def test_invalid_usage_and_windows_remain_unknown(tmp_path, last, window):
    transport = _ws(tmp_path)
    await transport._handle_server_message(_usage(last=last, window=window))
    frame = _events(transport, "session_usage")[-1]
    assert frame["last"] is frame["total"] is frame["window"] is None


async def test_subagent_and_stale_turn_usage_never_update_parent(tmp_path):
    transport = _ws(tmp_path)
    await transport._handle_server_message(_usage({"inputTokens": 10}, threadId="child"))
    await transport._handle_server_message(_usage({"inputTokens": 10}, turnId="old-turn"))
    assert not _events(transport, "session_usage")
    assert transport._last_usage is None


async def test_compaction_native_identity_survives_item_and_turn_echo(tmp_path):
    transport = _ws(tmp_path)
    item = {"id": "compact-item", "type": "contextCompaction"}
    await transport._handle_server_message(
        {
            "method": "item/completed",
            "params": {"threadId": "thread", "turnId": "turn", "item": item},
        }
    )
    completion = {
        "method": "turn/completed",
        "params": {
            "threadId": "thread",
            "turn": {"id": "turn", "items": [item], "status": "completed"},
        },
    }
    await transport._handle_server_message(completion)
    await transport._handle_server_message(completion)
    boundaries = [
        row for row in _events(transport, "system") if row.get("subtype") == "compact_boundary"
    ]
    assert len(boundaries) == 2  # Item and turn are echoes of ONE durable identity.
    assert {row["compaction_id"] for row in boundaries} == {"compact-item"}
    assert {row["thread_id"] for row in boundaries} == {"thread"}
    assert not _events(transport, "result")


async def test_compaction_turn_without_items_does_not_invent_second_boundary(tmp_path):
    transport = _ws(tmp_path)
    transport._context_compaction_turn_id = "turn"
    await transport._handle_item_completed({"id": "compact-item", "type": "contextCompaction"})
    await transport._handle_server_message(
        {
            "method": "turn/completed",
            "params": {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}},
        }
    )
    assert (
        len(
            [
                row
                for row in _events(transport, "system")
                if row.get("subtype") == "compact_boundary"
            ]
        )
        == 1
    )


@pytest.mark.parametrize("status", ["failed", "interrupted"])
async def test_unsuccessful_compaction_is_not_a_completed_boundary(tmp_path, status):
    transport = _ws(tmp_path)
    item = {"id": "compact-item", "type": "contextCompaction", "status": status}
    await transport._handle_item_completed(item)
    await transport._handle_server_message(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread",
                "turn": {"id": "turn", "items": [item], "status": status},
            },
        }
    )
    assert not [
        row for row in _events(transport, "system") if row.get("subtype") == "compact_boundary"
    ]


async def test_raw_token_count_preserves_native_snapshots_without_aggregate_double_count(tmp_path):
    transport = CodexSubprocessTransport(str(tmp_path), model="gpt-6-astra")
    transport._emit = AsyncMock()
    await transport._handle_codex_event({"type": "session_meta", "payload": {"id": "thread"}})
    await transport._handle_codex_event({"type": "turn_context", "payload": {"turn_id": "turn"}})
    usage = {
        "input_tokens": 12011,
        "cached_input_tokens": 7808,
        "output_tokens": 102,
        "total_tokens": 12113,
    }
    await transport._handle_codex_event(
        {
            "type": "event_msg",
            "timestamp": "2026-09-07T16:55:47.901233Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": usage,
                    "total_token_usage": usage,
                    "model_context_window": 258400,
                },
            },
        }
    )
    await transport._handle_codex_event({"type": "turn.completed", "usage": usage})
    frames = _events(transport, "session_usage")
    assert len(frames) == 1
    frame = frames[0]
    assert frame["thread_id"] == "thread" and frame["turn_id"] == "turn"
    assert (
        frame["last"]
        == frame["total"]
        == {"input": 4203, "cache_read": 7808, "cache_write": 0, "output": 102}
    )
    assert frame["window"] == 258400
    assert frame["timestamp"] == "2026-09-07T16:55:47.901233Z"
    assert frame["metadata"]["source"] == "codex_token_count"
    assert sum(_events(transport, "result")[-1]["modelUsage"]["gpt-6-astra"].values()) == 12113


@pytest.mark.parametrize("turn_id", [None, "native-turn"])
async def test_exec_aggregate_never_pretends_to_be_last_response_context(tmp_path, turn_id):
    transport = CodexSubprocessTransport(str(tmp_path), model="gpt-6-astra")
    transport._emit = AsyncMock()
    await transport._handle_codex_event({"type": "thread.started", "thread_id": "thread"})
    await transport._handle_codex_event(
        {
            "type": "turn.completed",
            "turn_id": turn_id,
            "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 5},
        }
    )
    frames = _events(transport, "session_usage")
    if turn_id is None:
        assert not frames  # No stable identity: cannot safely contribute a delta.
        return
    assert len(frames) == 1
    assert frames[0]["last"] is frames[0]["total"] is frames[0]["window"] is None
    assert frames[0]["delta"] == {"input": 60, "cache_read": 40, "cache_write": 0, "output": 5}
    assert frames[0]["response_id"] == turn_id


async def test_partial_exec_usage_does_not_claim_complete_delta(tmp_path):
    transport = CodexSubprocessTransport(str(tmp_path), model="gpt-6-astra")
    transport._emit = AsyncMock()
    await transport._handle_codex_event({"type": "thread.started", "thread_id": "thread"})
    await transport._handle_codex_event(
        {"type": "turn.completed", "turn_id": "native-turn", "usage": {"output_tokens": 3}}
    )
    assert not _events(transport, "session_usage")
    assert _events(transport, "result")[-1]["modelUsage"]["gpt-6-astra"]["outputTokens"] == 3

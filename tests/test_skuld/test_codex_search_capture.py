"""Native webSearch completion must enrich its placeholder without losing results.

Shapes match the installed generated app-server schema and the .1 live catalog
finding: native Extension/web.search completion becomes ThreadItem/webSearch
with action and opaque results, while item/started initially has query="".
"""

import importlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from niuu.domain.transcript_reducer import TurnAccumulator, apply_assistant_blocks, reduce_frames
from skuld.transports.codex_ws import CodexWebSocketTransport
from tests.test_skuld.test_broker_review_regressions import _broker


@pytest.mark.parametrize(
    "action",
    [
        {"type": "search", "query": "site:docs.python.org json.loads", "queries": None},
        {"type": "search", "queries": ["json.loads", "JSON decoder"]},
        {"type": "openPage", "url": "https://docs.python.org/3/library/json.html"},
        {
            "type": "findInPage",
            "url": "https://docs.python.org/3/library/json.html",
            "pattern": "loads",
        },
    ],
)
async def test_completed_search_enriches_one_live_and_replayed_tool_pair(
    tmp_path, monkeypatch, action
):
    b = _broker(tmp_path)
    t = CodexWebSocketTransport(str(tmp_path), model="gpt-6-astra")
    t._thread_id = "native-thread"
    t.on_event(b._handle_cli_event)
    module = importlib.import_module("skuld.broker")
    monkeypatch.setattr(module, "broker", b)
    await t._handle_server_message(
        {
            "method": "turn/started",
            "params": {"threadId": "native-thread", "turn": {"id": "native-turn"}},
        }
    )
    await t._handle_server_message(
        {
            "method": "item/started",
            "params": {
                "threadId": "native-thread",
                "turnId": "native-turn",
                "item": {"type": "webSearch", "id": "search", "query": "", "action": None},
            },
        }
    )
    initial = next(p for p in b._serialize_in_progress_turn()["parts"] if p["type"] == "tool_use")
    original_start = initial["started_at"]
    results = [
        {
            "type": "text_result",
            "ref_id": "native-reference",
            "url": "https://docs.python.org/3/library/json.html",
            "snippet": "Synthetic fixture result",
            "future_field": {"preserve": [1, {"opaque": True}]},
        }
    ]
    complete = {
        "type": "webSearch",
        "id": "search",
        "query": "",
        "action": action,
        "results": results,
    }
    await t._handle_server_message(
        {
            "method": "item/completed",
            "params": {"threadId": "native-thread", "turnId": "native-turn", "item": complete},
        }
    )
    current = b._serialize_in_progress_turn()
    calls = [p for p in current["parts"] if p["type"] == "tool_use"]
    outputs = [p for p in current["parts"] if p["type"] == "tool_result"]
    assert len(calls) == len(outputs) == 1
    assert calls[0]["input"]["action"] == action
    assert calls[0]["started_at"] == original_start
    assert json.loads(outputs[0]["content"]) == {"query": "", "action": action, "results": results}
    lazy = await module.get_tool_result("search")
    assert lazy["input"] == calls[0]["input"]
    assert lazy["content"] == outputs[0]["content"]
    await t._handle_server_message(
        {
            "method": "turn/completed",
            "params": {
                "threadId": "native-thread",
                "turn": {"id": "native-turn", "status": "completed"},
            },
        }
    )
    # Fold the exact raw durable rows, including their observation timestamps;
    # conversation.turn is the live fold's output and cannot prove replay parity.
    frames = [
        SimpleNamespace(session_id=b.session_id, **entry)
        for entry in b._event_log_buffer
        if entry["kind"] != "conversation.turn"
    ]
    rebuilt = reduce_frames(frames).turns
    assert len(rebuilt) == 1
    assert rebuilt[0]["parts"] == b._conversation_turns[0].parts
    assert len([p for p in rebuilt[0]["parts"] if p["type"] == "tool_use"]) == 1


def test_tool_input_refresh_preserves_original_attribution_and_timing():
    acc = TurnAccumulator()
    start = datetime(2026, 9, 8, tzinfo=UTC)
    apply_assistant_blocks(
        acc,
        [
            {
                "type": "tool_use",
                "id": "tool",
                "name": "WebSearch",
                "input": {"query": ""},
                "agent_id": "worker",
                "parent_tool_use_id": "delegate",
            }
        ],
        ts=start,
    )
    apply_assistant_blocks(
        acc,
        [{"type": "tool_use", "id": "tool", "name": "WebSearch", "input": {"query": "json.loads"}}],
        ts=start + timedelta(seconds=4),
    )
    assert len(acc.parts) == 1
    assert acc.parts[0] == {
        "type": "tool_use",
        "id": "tool",
        "name": "WebSearch",
        "input": {"query": "json.loads"},
        "agent_id": "worker",
        "parent_tool_use_id": "delegate",
        "started_at": start.isoformat(),
    }


@pytest.mark.parametrize(
    "item,expected",
    [
        ({"query": "legacy query", "output": "legacy search result"}, "legacy search result"),
        ({"query": "no result available"}, '{"query": "no result available"}'),
        ({"query": "no hits", "results": []}, '{"query": "no hits", "results": []}'),
    ],
)
async def test_missing_search_results_are_not_invented(tmp_path, item, expected):
    t = CodexWebSocketTransport(str(tmp_path))
    events = []

    async def collect(event):
        events.append(event)

    t.on_event(collect)
    await t._handle_item_completed({"id": "search", "type": "webSearch", **item})
    result = next(frame for frame in events if frame["type"] == "user")
    assert result["message"]["content"][0]["content"] == expected


@pytest.mark.parametrize("error_key", ["isError", "is_error"])
async def test_search_error_preserves_native_result_and_error_flag(tmp_path, error_key):
    t = CodexWebSocketTransport(str(tmp_path))
    events = []

    async def collect(event):
        events.append(event)

    t.on_event(collect)
    await t._handle_item_started({"id": "search", "type": "webSearch", "query": ""})
    await t._handle_item_completed(
        {
            "id": "search",
            "type": "webSearch",
            "query": "native top-level query",
            "action": {"type": "other", "future_field": "retained"},
            "results": [{"error": "native search failed"}],
            error_key: True,
        }
    )
    call = [frame for frame in events if frame["type"] == "assistant"][-1]
    assert call["message"]["content"][0]["input"] == {
        "query": "native top-level query",
        "action": {"type": "other", "future_field": "retained"},
    }
    result = next(frame for frame in events if frame["type"] == "user")
    block = result["message"]["content"][0]
    assert block["is_error"] is True
    assert json.loads(block["content"])["results"] == [{"error": "native search failed"}]
    assert (
        sum(
            frame["type"] == "content_block_start" and frame["content_block"]["type"] == "tool_use"
            for frame in events
        )
        == 1
    )

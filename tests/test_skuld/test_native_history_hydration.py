"""Native imports remain visible after broker restart, reconnect, and new turns."""

import asyncio
import importlib
import json
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import WebSocketDisconnect

from niuu.ports.cli.transport import TransportCapabilities
from skuld.broker import Broker, ConversationTurn
from skuld.config import SkuldSettings
from skuld.history_hydration import merge_history_turns

_SID = "native-recovered-session"
_TS = "2026-09-08T07:43:04+00:00"


def _row(seq, kind, payload):
    return {"session_id": _SID, "seq": seq, "kind": kind, "payload": payload, "ts": _TS}


def _history():
    return [
        _row(7, "user", {"type": "user", "uuid": "native-user-1", "content": "Review Forge"}),
        _row(
            8,
            "assistant",
            {"type": "assistant", "content": [{"type": "text", "text": "Original work"}]},
        ),
        _row(9, "result", {"type": "result", "result": "", "stop_reason": "end_turn"}),
    ]


def _broker(tmp_path, **settings):
    broker = Broker(
        SkuldSettings(
            session={
                "id": _SID,
                "workspace_dir": str(tmp_path),
                "resume_session_id": "native-thread",
            },
            volundr_api_url="http://forge.test",
            transport="subprocess",
            **settings,
        )
    )
    broker._transport = MagicMock()
    broker._transport.is_alive = True
    broker._transport.capabilities = TransportCapabilities()
    broker._report_activity_state = AsyncMock()
    broker._report_usage = AsyncMock()
    broker._report_timeline_event = AsyncMock()
    broker._on_result_publish_mesh = AsyncMock()
    broker._report_session_start = AsyncMock()
    return broker


def _client(rows=None, *, head=10, pages=None, fail_after=None, delay=0):
    rows = _history() if rows is None else rows
    seen = []

    async def handle(request):
        assert request.headers["authorization"] == "Bearer test-history-token"
        if delay:
            await asyncio.sleep(delay)
        if request.url.path.endswith("/head"):
            return httpx.Response(200, json={"latest_seq": head})
        after = int(request.url.params["after"])
        seen.append(after)
        assert request.url.params["show_internal"] == "true"
        if fail_after is not None and after >= fail_after:
            return httpx.Response(503)
        page = (
            pages.get(after, [])
            if pages is not None
            else [row for row in rows if row["seq"] > after]
        )
        return httpx.Response(200, json=page)

    return httpx.AsyncClient(
        base_url="http://forge.test",
        headers={"Authorization": "Bearer test-history-token"},
        transport=httpx.MockTransport(handle),
    ), seen


async def test_empty_cache_hydrates_native_prefix_without_reemitting_events(tmp_path):
    broker = _broker(tmp_path)
    client, seen = _client()
    async with client:
        broker._http_client = client
        await broker._hydrate_conversation_history()

    assert [turn.content for turn in broker._conversation_turns] == [
        "Review Forge",
        "Original work",
    ]
    assert all(turn.created_at == _TS for turn in broker._conversation_turns)
    assert broker._event_log_buffer == []
    cached = json.loads(broker._conversation_history_path().read_text())
    assert cached["turns"] == [asdict(turn) for turn in broker._conversation_turns]
    assert seen == [0, 9]  # the filtered import marker at head is not visible


async def test_filtered_empty_and_short_pages_do_not_end_hydration(tmp_path):
    broker = _broker(tmp_path, history_hydration_page_size=2)
    rows = _history()
    pages = {0: [], 2: [], 4: [], 6: [rows[0]], 7: [rows[1]], 8: [rows[2]], 9: []}
    client, seen = _client(pages=pages)
    async with client:
        broker._http_client = client
        await broker._hydrate_conversation_history()

    assert seen == [0, 2, 4, 6, 7, 8, 9]
    assert len(broker._conversation_turns) == 2


async def test_frozen_head_excludes_new_frames_after_read_started(tmp_path):
    broker = _broker(tmp_path)
    client, _ = _client(rows=[*_history(), _row(12, "user", {"type": "user", "content": "Later"})])
    async with client:
        broker._http_client = client
        await broker._hydrate_conversation_history()
    assert [turn.content for turn in broker._conversation_turns] == [
        "Review Forge",
        "Original work",
    ]


async def test_hydration_preserves_newer_local_tail_and_participant_fields(tmp_path):
    broker = _broker(tmp_path)
    client, _ = _client()
    async with client:
        broker._http_client = client
        await broker._hydrate_conversation_history()
        broker._conversation_turns[1].participant_id = "astra"
        broker._conversation_turns[1].metadata["local_annotation"] = "keep"
        local_tail = ConversationTurn(id="unsaved-user", role="user", content="Continue the work")
        broker._conversation_turns.append(local_tail)
        await broker._hydrate_conversation_history()

    assert [turn.content for turn in broker._conversation_turns] == [
        "Review Forge",
        "Original work",
        "Continue the work",
    ]
    assert broker._conversation_turns[1].participant_id == "astra"
    assert broker._conversation_turns[1].metadata["local_annotation"] == "keep"
    assert broker._conversation_turns[-1] == local_tail
    restarted = _broker(tmp_path)
    restarted._load_conversation_history()
    assert restarted._conversation_turns == broker._conversation_turns


@pytest.mark.parametrize(
    "failure", ["network", "order", "foreign", "gap", "frames", "bytes", "timeout", "malformed"]
)
async def test_incomplete_hydration_preserves_existing_cache(tmp_path, failure, caplog):
    settings = {}
    options = {}
    rows = _history()
    if failure == "network":
        options["fail_after"] = 9
    elif failure == "order":
        options["rows"] = [rows[1], rows[0]]
    elif failure == "foreign":
        options["rows"] = [{**rows[0], "session_id": "another-session"}]
    elif failure == "gap":
        options["rows"] = [_row(7, "log_gap", {"type": "log_gap"})]
    elif failure == "frames":
        settings["history_hydration_max_frames"] = 1
    elif failure == "bytes":
        settings["history_hydration_max_bytes"] = 40
    elif failure == "timeout":
        settings["history_hydration_timeout_seconds"] = 0.001
        options["delay"] = 0.05
    elif failure == "malformed":
        options["pages"] = {0: {"unexpected": "object"}}
    broker = _broker(tmp_path, **settings)
    original = ConversationTurn(id="cached-turn", role="assistant", content="Keep this work")
    broker._conversation_turns = [original]
    broker._save_conversation_history()
    saved_bytes = broker._conversation_history_path().read_bytes()
    client, _ = _client(**options)
    async with client:
        broker._http_client = client
        await broker._hydrate_conversation_history()

    assert broker._conversation_turns == [original]
    assert broker._conversation_history_path().read_bytes() == saved_bytes
    assert broker._event_log_buffer == []
    assert "preserving local cache" in caplog.text


async def test_unconfigured_resume_does_not_fetch_history(tmp_path):
    broker = _broker(tmp_path)
    broker._settings.session.resume_session_id = ""
    broker._get_http_client = AsyncMock()
    await broker._hydrate_conversation_history()
    broker._get_http_client.assert_not_awaited()


async def test_hydrated_prefix_survives_live_append_rest_and_websocket_snapshot(
    tmp_path, monkeypatch
):
    broker = _broker(tmp_path)
    client, _ = _client()
    async with client:
        broker._http_client = client
        await broker._hydrate_conversation_history()
    broker._event_log_seq = 10
    await broker._handle_cli_event(
        {"type": "user", "uuid": "new-user", "message": {"content": "Continue"}}
    )
    await broker._handle_cli_event(
        {"type": "result", "result": "New work", "stop_reason": "end_turn"}
    )

    module = importlib.import_module("skuld.broker")
    monkeypatch.setattr(module, "broker", broker)
    rest = await module.get_conversation_history()
    expected = ["Review Forge", "Original work", "Continue", "New work"]
    assert [turn["content"] for turn in rest["turns"]] == expected
    assert rest["projection_revision"] == "text-items-1:0"

    websocket = AsyncMock()
    websocket.headers = {}
    websocket.query_params = {}
    websocket.receive_json.side_effect = WebSocketDisconnect()
    await broker.handle_websocket(websocket)
    snapshots = [
        call.args[0]
        for call in websocket.send_json.call_args_list
        if call.args[0].get("type") == "conversation_history"
    ]
    assert len(snapshots) == 1
    assert [turn["content"] for turn in snapshots[0]["turns"]] == expected
    assert snapshots[0]["head_seq"] > 10
    assert snapshots[0]["projection_revision"] == rest["projection_revision"]
    assert all(row["seq"] > 10 for row in broker._event_log_buffer)


async def test_in_progress_snapshot_does_not_mutate_after_text_or_tool_refresh(tmp_path):
    broker = _broker(tmp_path)
    await broker._handle_cli_event(
        {
            "type": "content_block_start",
            "item_id": "message",
            "index": 0,
            "content_block": {"type": "text", "id": "message"},
        }
    )
    await broker._handle_cli_event(
        {
            "type": "content_block_delta",
            "item_id": "message",
            "delta": {"type": "text_delta", "text": "Before"},
        }
    )
    await broker._handle_cli_event(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "tool", "name": "exec", "input": {}},
                ]
            },
        }
    )
    snapshot = broker._serialize_in_progress_turn()
    before = json.dumps(snapshot, sort_keys=True)
    await broker._handle_cli_event(
        {
            "type": "content_block_delta",
            "item_id": "message",
            "delta": {"type": "text_delta", "text": " after"},
        }
    )
    await broker._handle_cli_event(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "tool", "name": "exec", "input": {"cmd": "true"}},
                ]
            },
        }
    )
    assert json.dumps(snapshot, sort_keys=True) == before
    current = broker._serialize_in_progress_turn()
    assert current["parts"][0]["text"] == "Before after"
    assert current["parts"][1]["input"] == {"cmd": "true"}


async def test_startup_hydrates_before_creating_or_warming_transport(tmp_path, monkeypatch):
    module = importlib.import_module("skuld.broker")
    broker = _broker(tmp_path)
    expected = ["Review Forge", "Original work"]

    def create_transport():
        assert [turn.content for turn in broker._conversation_turns] == expected
        return broker._transport

    async def warm():
        assert [turn.content for turn in broker._conversation_turns] == expected

    monkeypatch.setattr(broker, "_create_transport", create_transport)
    monkeypatch.setattr(broker, "_auto_start_transport", AsyncMock(side_effect=warm))
    for name in ("_ensure_session_trace_started", "_init_telegram_channel", "_init_event_log"):
        monkeypatch.setattr(broker, name, AsyncMock())
    manager = MagicMock()
    manager.init = AsyncMock()
    monkeypatch.setattr(module, "ServiceManager", MagicMock(return_value=manager))
    client, _ = _client()
    async with client:
        broker._http_client = client
        await broker.startup()
        await asyncio.sleep(0)
    broker._auto_start_transport.assert_awaited_once()


def _turn(identity, content="work"):
    return {"id": identity, "role": "assistant", "content": content, "parts": [], "metadata": {}}


def test_merge_inserts_missing_durable_prefix_and_preserves_richer_local_turn():
    durable = [_turn("imported"), _turn("shared")]
    local = [_turn("shared", "work continued"), _turn("unsaved")]
    merged = merge_history_turns(durable, local)
    assert [turn["id"] for turn in merged] == ["imported", "shared", "unsaved"]
    assert merged[1]["content"] == "work continued"


@pytest.mark.parametrize(
    ("durable", "local"),
    [
        ([_turn("a")], [_turn("other")]),
        ([_turn("a"), _turn("b")], [_turn("b"), _turn("a")]),
        ([_turn("a"), _turn("b")], [_turn("a"), _turn("other"), _turn("b")]),
        ([_turn("a"), _turn("b")], [_turn("a"), _turn("other")]),
        ([_turn("a")], [_turn("a", "conflicting text")]),
    ],
)
def test_ambiguous_merge_is_rejected_without_mutating_either_history(durable, local):
    before = json.dumps([durable, local])
    with pytest.raises(ValueError):
        merge_history_turns(durable, local)
    assert json.dumps([durable, local]) == before

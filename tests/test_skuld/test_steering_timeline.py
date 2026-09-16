"""Same-turn input chronology from real broker → REST and raw/seed replay.

Native RPC and database are hermetic fixtures. No provider or live session is used.
"""

import copy
import json
from dataclasses import asdict
from datetime import datetime
from unittest.mock import AsyncMock

from starlette.websockets import WebSocket

from niuu.domain.conversation_timeline import TIMELINE_KEY, project_timeline
from niuu.domain.text_projection import projection_revision
from niuu.domain.transcript_reducer import reduce_frames
from skuld import broker_api
from tests.test_skuld.test_broker_review_regressions import _broker, _finish_deliveries, _Ledger
from tests.test_skuld.test_codex_ws_transport import _make_transport
from volundr.domain.models import SessionLogEntry
from volundr.domain.services.transcript_rebuild import rebuild_turns


def _text(identity, text, *, complete=True):
    return {
        "type": "assistant",
        "thread_id": "thread",
        "turn_id": "native-turn",
        "message": {
            "content": [
                {
                    "type": "text",
                    "id": identity,
                    "text": text,
                    "complete": complete,
                    "thread_id": "thread",
                    "turn_id": "native-turn",
                    "phase": "commentary",
                }
            ]
        },
    }


def _entries(broker, *, seeds):
    return [
        SessionLogEntry(
            session_id=broker.session_id,
            seq=e["seq"],
            kind=e["kind"],
            payload=e["payload"],
            request_id=e.get("request_id"),
            ts=datetime.fromisoformat(e["ts"]),
        )
        for e in broker._event_log_buffer
        if seeds or e["kind"] != "conversation.turn"
    ]


async def _snapshot(broker, *, recent=False):
    sent = []
    socket = WebSocket(
        {"type": "websocket", "headers": [], "query_string": b"history=recent" if recent else b""},
        AsyncMock(
            side_effect=[
                {"type": "websocket.connect"},
                {"type": "websocket.disconnect", "code": 1000},
            ]
        ),
        AsyncMock(side_effect=sent.append),
    )
    await broker.handle_websocket(socket)
    frames = [json.loads(x["text"]) for x in sent if x.get("type") == "websocket.send"]
    return next(x for x in frames if x.get("type") == "conversation_history")


async def test_two_live_steers_preserve_chronology_item_identity_and_poll_completion(
    tmp_path, monkeypatch
):
    b = _broker(tmp_path)
    t = _make_transport(tmp_path)
    t._alive = True
    t.start = AsyncMock(side_effect=AssertionError("No provider startup in this test"))
    t._thread_id = "thread"
    t._current_turn_id = "native-turn"
    t._send_rpc = AsyncMock(return_value={"turnId": "native-turn"})
    t.on_event(b._handle_cli_event)
    b._transport = t
    b._start_trace_span = AsyncMock(return_value=None)
    b._finish_trace_span = AsyncMock()
    b._finish_pending_assistant_tool_trace_spans = AsyncMock()
    b._channels.broadcast = AsyncMock()
    ledger = _Ledger()
    async with ledger.client() as client:
        b._http_client = client
        await b._handle_cli_event(_text("before", "BEFORE", complete=False))
        await b._handle_cli_event(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "held-tool",
                            "name": "Bash",
                            "input": {"command": "sleep"},
                        },
                    ]
                },
            }
        )
        first_pending = b._serialize_in_progress_turn()
        assert first_pending == b._serialize_in_progress_turn()
        await b._dispatch_browser_message({"content": "French", "request_id": "steer-1"})
        await _finish_deliveries()
        first = project_timeline(
            [asdict(x) for x in b._conversation_turns] + [b._serialize_in_progress_turn()],
            b.session_id,
        )
        assert [x["role"] for x in first] == ["assistant", "user"]
        stable_prefix_id = first[0]["id"]
        # The same native item finishes AFTER input; update its original slot,
        # never replay the already rendered prefix as a second item.
        await b._handle_cli_event(_text("before", "BEFORE completed"))
        await b._handle_cli_event(
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "held-tool", "content": "done"},
                    ]
                },
            }
        )
        await b._handle_cli_event(_text("between", "BETWEEN"))
        await b._dispatch_browser_message({"content": "English", "request_id": "steer-2"})
        await _finish_deliveries()
        await b._handle_cli_event(_text("after", "AFTER"))
        pending = [asdict(x) for x in b._conversation_turns] + [b._serialize_in_progress_turn()]
        live = project_timeline(pending, b.session_id)
        assert [x["role"] for x in live] == ["assistant", "user", "assistant", "user", "assistant"]
        assert [x["content"] for x in live] == [
            "BEFORE completed",
            "French",
            "BETWEEN",
            "English",
            "AFTER",
        ]
        assert live[0]["id"] == stable_prefix_id
        assert len([p for x in live for p in x["parts"] if p.get("id") == "before"]) == 1
        call = next(p for p in live[0]["parts"] if p.get("id") == "held-tool")
        result = next(p for p in live[0]["parts"] if p.get("tool_use_id") == "held-tool")
        assert call["ended_at"] == result["ended_at"]
        assert call["duration_ms"] >= 0 and result["content"] == "done"
        assert project_timeline(live, b.session_id) == live
        assert project_timeline(pending, b.session_id) == live
        assert [x["in_progress"] for x in live if "in_progress" in x] == [True]
        b._settings.conversation_recent_max_turns = 3
        ws_live = await _snapshot(b)
        assert ws_live["turns"] == live
        recent = await _snapshot(b, recent=True)
        assert recent["turns"] == live[-3:]
        assert recent["total_turns"] == 5 and recent["window_offset"] == 2
        assert recent["projection_revision"] == projection_revision(live)
        await b._handle_cli_event(
            {
                "type": "result",
                "turn_id": "native-turn",
                "result": "AFTER",
                "modelUsage": {"model": {"inputTokens": 12}},
                "stop_reason": "end_turn",
            }
        )
        final = project_timeline([asdict(x) for x in b._conversation_turns], b.session_id)
        assert [x["id"] for x in final] == [x["id"] for x in live]
        assert [x["content"] for x in final] == [x["content"] for x in live]
        assert sum("usage" in x["metadata"] for x in final) == 1
        assert t._send_rpc.await_count == 2
        assert all(c.args[0] == "turn/steer" for c in t._send_rpc.await_args_list)
        assert t._current_turn_id == "native-turn"
        for user in (x for x in final if x["role"] == "user"):
            echo = next(
                e["payload"]
                for e in b._event_log_buffer
                if e["kind"] == "user_confirmed" and e["payload"]["id"] == user["id"]
            )
            raw = next(
                e
                for e in b._event_log_buffer
                if e["kind"] == "user" and e["payload"].get("uuid") == user["id"]
            )
            assert user["created_at"] == echo["created_at"] == raw["ts"]
            assert (
                user["metadata"][TIMELINE_KEY] == echo[TIMELINE_KEY] == raw["payload"][TIMELINE_KEY]
            )
            assert user["metadata"]["steering_accepted_at"] >= user["created_at"]

        # Compare IDs, dates, content, complete parts and metadata, not counts alone.
        def comparable(turns):
            return [
                {k: x[k] for k in ("id", "role", "content", "parts", "created_at", "metadata")}
                for x in turns
            ]

        assert comparable(reduce_frames(_entries(b, seeds=False)).turns) == comparable(final)
        assert comparable(rebuild_turns(_entries(b, seeds=True)).turns) == comparable(final)
        saved = json.loads(b._conversation_history_path().read_text())
        assert comparable(saved["turns"]) == comparable(final)
        monkeypatch.setattr(broker_api, "_broker_getter", lambda: b)
        response = await broker_api.get_conversation_history()
        assert comparable(response["turns"]) == comparable(final)
        assert response["projection_revision"].endswith(";timeline-1")
        assert projection_revision(live) == projection_revision(final)
        assert (
            copy.deepcopy(response["turns"])
            == (await broker_api.get_conversation_history())["turns"]
        )

        restored = _broker(tmp_path)
        restored._load_conversation_history()
        assert comparable([asdict(x) for x in restored._conversation_turns]) == comparable(final)
        ws_final = await _snapshot(restored)
        assert comparable(ws_final["turns"]) == comparable(final)
        assert ws_final["projection_revision"] == response["projection_revision"]
        t.start.assert_not_awaited()


async def test_steering_seed_cannot_hide_unflushed_prefix_after_previous_completed_turn(tmp_path):
    b = _broker(tmp_path)
    b._transport.capabilities = _make_transport(tmp_path).capabilities
    await b._handle_cli_event(_text("previous", "PREVIOUS"))
    await b._handle_cli_event({"type": "result", "result": "PREVIOUS"})
    await b._handle_cli_event(_text("prefix", "PREFIX"))
    ledger = _Ledger()
    async with ledger.client() as client:
        b._http_client = client
        await b._dispatch_browser_message({"content": "MID TURN", "request_id": "middle"})
        await _finish_deliveries()
    await b._handle_cli_event(_text("suffix", "SUFFIX"))
    rebuilt = rebuild_turns(_entries(b, seeds=True))
    assert [x["content"] for x in rebuilt.turns] == ["PREVIOUS", "PREFIX", "MID TURN", "SUFFIX"]
    assert rebuilt.partial
    assert len({x["id"] for x in rebuilt.turns}) == 4
    live = project_timeline(
        [asdict(x) for x in b._conversation_turns] + [b._serialize_in_progress_turn()], b.session_id
    )
    assert [x["id"] for x in live] == [x["id"] for x in rebuilt.turns]
    assert [x["parts"] for x in live] == [x["parts"] for x in rebuilt.turns]


async def test_native_timed_user_echo_does_not_close_existing_item(tmp_path):
    b = _broker(tmp_path)
    b._transport.capabilities = _make_transport(tmp_path).capabilities
    await b._handle_cli_event(_text("open", "PREFIX", complete=False))
    await b._handle_cli_event(
        {"type": "user", "uuid": "native-input", "message": {"content": "INPUT"}}
    )
    assert [x.role for x in b._conversation_turns] == ["user"]
    await b._handle_cli_event(_text("open", "PREFIX COMPLETED"))
    await b._handle_cli_event(_text("next", "AFTER"))
    await b._handle_cli_event({"type": "result", "result": "AFTER"})
    projected = project_timeline([asdict(x) for x in b._conversation_turns], b.session_id)
    assert [x["content"] for x in projected] == ["PREFIX COMPLETED", "INPUT", "AFTER"]
    rebuilt = reduce_frames(_entries(b, seeds=False)).turns
    assert [x["id"] for x in rebuilt] == [x["id"] for x in projected]
    assert [x["parts"] for x in rebuilt] == [x["parts"] for x in projected]
    assert [x["created_at"] for x in rebuilt] == [x["created_at"] for x in projected]

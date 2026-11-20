"""A trailing tmux repaint never changes a turn's id (rebuild == live).

The live broker never folds pane repaints (terminal_frame) into a span's seq and time; a
rebuild did, so a human message that closed a span ending in repaints gave the rebuilt
turn another id than the live one, and apps showed a jumped or duplicated turn after a
rebuild. Repaints now move a span's seq and time only while it has nothing streamed,
when they are the span (its last-resort scrape). Every repaint is still logged.
"""

from __future__ import annotations

import random
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from niuu.domain.transcript_reducer import assistant_turn_id, reduce_frames
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.conversation_models import turn_record
from skuld.transports import TransportCapabilities

_SID = "repaint-turn-id-session"


class _Frame:
    def __init__(self, entry: dict) -> None:
        self.session_id = _SID
        self.seq = int(entry["seq"])
        self.kind = entry["kind"]
        self.payload = entry["payload"]
        self.request_id = entry.get("request_id")
        self.ts = datetime.fromisoformat(entry["ts"])


def _broker(tmp_path) -> Broker:
    broker = Broker(
        SkuldSettings(
            session={"id": _SID, "workspace_dir": str(tmp_path)},
            transport="subprocess",
            volundr_api_url="http://forge.test",
            event_log_enabled=True,
        )
    )
    broker._transport = MagicMock()
    broker._transport.is_alive = True
    broker._transport.capabilities = TransportCapabilities()
    broker._report_activity_state = AsyncMock()
    broker._report_timeline_event = AsyncMock()
    broker._start_trace_span = AsyncMock(return_value=None)
    broker._finish_trace_span = AsyncMock()
    broker._save_conversation_history = MagicMock()
    broker._channels.broadcast = AsyncMock()
    return broker


def _text(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _tool(tool_id: str) -> dict:
    block = {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {}}
    return {"type": "assistant", "message": {"role": "assistant", "content": [block]}}


def _pane(pane: str, screen: str) -> dict:
    rows = [f"{pane}: {screen}"]
    return {"type": "terminal_frame", "pane_id": pane, "rows": rows, "text": rows[0]}


def _user(text: str) -> dict:
    return {"type": "user", "uuid": f"u-{text}", "message": {"role": "user", "content": text}}


_RESULT = {"type": "result", "result": "", "stop_reason": "end_turn"}


def _delta(kind: str, text: str) -> dict:
    field = "thinking" if kind == "thinking_delta" else "text"
    return {"type": "content_block_delta", "delta": {"type": kind, field: text}}


def _tool_result(tool_id: str) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": "out"}
    return {"type": "user", "message": {"role": "user", "content": [block]}}


async def _play(broker: Broker, events: list) -> None:
    for event in events:
        if event == "interrupt":
            await broker._interrupt_open_turn("broker_shutdown")
        else:
            await broker._handle_cli_event(dict(event))


def _logged(broker: Broker) -> list[_Frame]:
    return [_Frame(e) for e in broker._event_log_buffer]


def _shape(turns: list[dict]) -> list[tuple]:
    return [
        (
            t["id"],
            t["role"],
            t["content"],
            [p.get("type") for p in t["parts"]],
            t["metadata"].get("status"),
            t["created_at"],
        )
        for t in turns
    ]


async def test_a_human_message_after_trailing_repaints_keeps_the_turn_id(tmp_path):
    broker = _broker(tmp_path)
    await _play(broker, [_text("Working"), _pane("%0", "1"), _pane("%0", "2"), _user("steer")])
    text_seq = next(e["seq"] for e in broker._event_log_buffer if e["kind"] == "assistant")

    live = [turn_record(t) for t in broker._conversation_turns]
    rebuilt = reduce_frames(_logged(broker)).turns

    assert live[0]["id"] == assistant_turn_id(_SID, text_seq)
    assert _shape(rebuilt) == _shape(live)


async def test_a_span_of_only_repaints_still_takes_their_seq(tmp_path):
    """The scrape-only span (nothing streamed) is its repaints: its id and time are theirs."""
    broker = _broker(tmp_path)
    await _play(broker, [_pane("%0", "1"), _pane("%0", "2"), _user("next")])
    last_repaint = max(e["seq"] for e in broker._event_log_buffer if e["kind"] == "terminal_frame")

    rebuilt = reduce_frames(_logged(broker), scrape=lambda rows: "\n".join(rows)).turns

    assert rebuilt[0]["id"] == assistant_turn_id(_SID, last_repaint)
    assert rebuilt[0]["content"] == "%0: 2"
    assert len({t["id"] for t in rebuilt}) == len(rebuilt)


def _random_session(rng: random.Random) -> list:
    events: list = []
    tools = 0
    for _ in range(rng.randint(5, 40)):
        pick = rng.random()
        if pick < 0.45:
            events.append(_pane(rng.choice(["%0", "%1"]), str(rng.randint(0, 9))))
        elif pick < 0.55:
            events.append(_text(f"text {len(events)}"))
        elif pick < 0.6:
            events.append(_delta("text_delta", f"delta {len(events)} "))
        elif pick < 0.64:
            events.append(_delta("thinking_delta", f"thought {len(events)} "))
        elif pick < 0.72:
            tools += 1
            events.append(_tool(f"t{tools}"))
        elif pick < 0.78 and tools:
            events.append(_tool_result(f"t{tools}"))
        elif pick < 0.88:
            events.append(_RESULT)
        elif pick < 0.95:
            events.append(_user(f"message {len(events)}"))
        else:
            events.append("interrupt")  # LEX-1982: a broker stop mid-turn
    # End on a turn end: an open span at the end of a log is an interrupted turn to a
    # rebuild but still in progress to the live broker.
    events.append(_RESULT)
    return events


async def test_rebuild_equals_live_with_repaints_messages_and_interrupts(tmp_path):
    rng = random.Random(1984)
    for case in range(300):
        events = _random_session(rng)
        broker = _broker(tmp_path / str(case))
        await _play(broker, events)

        live = [turn_record(t) for t in broker._conversation_turns]
        rebuilt = reduce_frames(_logged(broker)).turns

        assert _shape(rebuilt) == _shape(live), (case, events)

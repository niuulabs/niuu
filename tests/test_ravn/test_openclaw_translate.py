"""Translator tests, driven by the REAL captured Ravn turns.

Every fixture here came off a live gateway (see fixtures/README.md), so these
tests assert against bytes Ravn actually produced rather than against the shape
someone believed it produced. The tool path in particular was pure inference
until the corpus existed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ravn.adapters.channels.openclaw_translate import TurnTranslator

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> list[dict]:
    """Read a captured turn, dropping the __meta__ header line."""
    out = []
    for line in (FIXTURES / name).read_text().splitlines():
        if not line.strip():
            continue
        frame = json.loads(line)
        if "__meta__" in frame:
            continue
        out.append(frame)
    return out


def run_turn(frames: list[dict], *, session_key: str = "agent:travis:main") -> tuple:
    tr = TurnTranslator(session_key=session_key, run_id="run-1")
    events: list[dict] = []
    for frame in frames:
        events.extend(tr.ingest(frame))
    events.extend(tr.finish())
    return tr, events


def text_of(event: dict) -> str:
    return "".join(b.get("text", "") for b in event["message"]["blocks"] if b.get("type") == "text")


class TestDeltaInversion:
    """The bug this module exists to make impossible."""

    def test_plain_turn_never_doubles_the_answer(self) -> None:
        frames = load("ravn_turn_plain.jsonl")
        # Sanity: the fixture really is incremental + a repeating terminal.
        assert [f["payload"].get("text") for f in frames] == ["p", "ong", "pong"]

        _, events = run_turn(frames)
        assert text_of(events[-1]) == "pong", "naive concatenation would give 'pongpong'"

    def test_every_delta_is_a_cumulative_snapshot(self) -> None:
        """ChatScreenModel REPLACES with message.blocks, so each delta is a total."""
        _, events = run_turn(load("ravn_turn_plain.jsonl"))
        deltas = [e for e in events if e["state"] == "delta"]
        assert [text_of(e) for e in deltas] == ["p", "pong"]

    def test_final_text_is_authoritative(self) -> None:
        """The terminal response REPLACES the buffer; it does not append."""
        tr = TurnTranslator(session_key="k", run_id="r")
        tr.ingest({"type": "thought", "payload": {"text": "partial"}})
        events = tr.ingest({"type": "response", "payload": {"text": "the whole answer"}})
        assert text_of(events[0]) == "the whole answer"

    def test_terminal_state_is_final(self) -> None:
        _, events = run_turn(load("ravn_turn_plain.jsonl"))
        assert events[-1]["state"] == "final"
        assert sum(1 for e in events if e["state"] == "final") == 1


class TestToolTurns:
    def test_tool_blocks_are_emitted_with_ids(self) -> None:
        _, events = run_turn(load("ravn_turn_tools.jsonl"))
        blocks = events[-1]["message"]["blocks"]
        uses = [b for b in blocks if b["type"] == "tool_use"]
        results = [b for b in blocks if b["type"] == "tool_result"]
        assert uses and results
        # Ravn carries no call id, so we mint one and the result back-references it.
        assert results[0]["tool_use_id"] == uses[0]["id"]
        assert uses[0]["name"] == "read_file"

    def test_a_turn_may_open_with_a_tool(self) -> None:
        """Captured: the tools fixture's first frame is tool_start, not text."""
        frames = load("ravn_turn_tools.jsonl")
        assert frames[0]["type"] == "tool_start"
        _, events = run_turn(frames)
        assert events[-1]["message"]["blocks"][0]["type"] == "tool_use"
        assert text_of(events[-1]) == "The first heading line is `# niuu` on line 1."

    def test_edit_turn_carries_the_diff_preview(self) -> None:
        _, events = run_turn(load("ravn_turn_edit.jsonl"))
        blocks = events[-1]["message"]["blocks"]
        edits = [b for b in blocks if b.get("name") == "edit_file" and b["type"] == "tool_use"]
        assert edits, "expected an edit_file tool_use"
        assert "-beta" in edits[0]["diff"] and "+BETA" in edits[0]["diff"]

    def test_diff_absent_on_read_only_tools(self) -> None:
        _, events = run_turn(load("ravn_turn_tools.jsonl"))
        for block in events[-1]["message"]["blocks"]:
            if block["type"] == "tool_use" and block["name"] == "read_file":
                assert "diff" not in block

    def test_error_results_are_preserved(self) -> None:
        """A failed tool must reach the client flagged, not silently dropped."""
        tr = TurnTranslator(session_key="k", run_id="r")
        tr.ingest({"type": "tool_start", "payload": {"tool_name": "read_file", "input": {}}})
        events = tr.ingest(
            {
                "type": "tool_result",
                "payload": {"tool_name": "read_file", "result": "nope", "is_error": True},
            }
        )
        result = [b for b in events[0]["message"]["blocks"] if b["type"] == "tool_result"][0]
        assert result["is_error"] is True
        assert result["content"] == "nope"

    def test_pairing_prefers_the_most_recent_unmatched_start(self) -> None:
        tr = TurnTranslator(session_key="k", run_id="r")
        tr.ingest({"type": "tool_start", "payload": {"tool_name": "bash", "input": {}}})
        tr.ingest({"type": "tool_start", "payload": {"tool_name": "bash", "input": {}}})
        tr.ingest({"type": "tool_result", "payload": {"tool_name": "bash", "result": "a"}})
        events = tr.ingest({"type": "tool_result", "payload": {"tool_name": "bash", "result": "b"}})
        blocks = events[0]["message"]["blocks"]
        uses = [b["id"] for b in blocks if b["type"] == "tool_use"]
        results = [b["tool_use_id"] for b in blocks if b["type"] == "tool_result"]
        assert sorted(results) == sorted(uses), "each start matched exactly once"

    def test_unpaired_result_is_kept_without_a_backreference(self) -> None:
        tr = TurnTranslator(session_key="k", run_id="r")
        events = tr.ingest(
            {"type": "tool_result", "payload": {"tool_name": "ghost", "result": "orphan"}}
        )
        block = [b for b in events[0]["message"]["blocks"] if b["type"] == "tool_result"][0]
        assert "tool_use_id" not in block
        assert block["content"] == "orphan"


class TestThinking:
    def test_thinking_lands_in_its_own_block(self) -> None:
        frames = load("ravn_turn_thinking_synthetic.jsonl")
        _, events = run_turn(frames)
        blocks = events[-1]["message"]["blocks"]
        thinking = [b for b in blocks if b["type"] == "thinking"]
        assert thinking, "thinking must not be merged into the answer text"
        assert "0.05" in thinking[0]["text"]

    def test_thinking_never_contaminates_the_answer(self) -> None:
        """The client drops thinking blocks; the answer must stand alone."""
        _, events = run_turn(load("ravn_turn_thinking_synthetic.jsonl"))
        assert text_of(events[-1]) == "The ball costs 0.05."


class TestSynthesizedTerminals:
    """Ravn emits no error events and no end-of-turn frame at all."""

    def test_stream_closing_without_a_response_is_an_error(self) -> None:
        tr = TurnTranslator(session_key="k", run_id="r")
        tr.ingest({"type": "thought", "payload": {"text": "half an ans"}})
        events = tr.finish(reason="upstream dropped")
        assert len(events) == 1
        assert events[0]["state"] == "error"
        assert "incomplete" in events[0]["error"]["message"]

    def test_a_truncated_turn_never_looks_complete(self) -> None:
        tr = TurnTranslator(session_key="k", run_id="r")
        tr.ingest({"type": "thought", "payload": {"text": "partial"}})
        states = [e["state"] for e in tr.finish()]
        assert "final" not in states

    def test_finish_after_a_clean_final_is_a_no_op(self) -> None:
        tr = TurnTranslator(session_key="k", run_id="r")
        tr.ingest({"type": "response", "payload": {"text": "done"}})
        assert tr.finish() == []

    def test_ravn_error_frames_translate_to_error_state(self) -> None:
        tr = TurnTranslator(session_key="k", run_id="r")
        events = tr.ingest({"type": "error", "payload": {"message": "boom", "failure_kind": "llm"}})
        assert events[0]["state"] == "error"
        assert events[0]["error"]["message"] == "boom"
        assert events[0]["error"]["kind"] == "llm"

    def test_abort_is_distinct_from_error(self) -> None:
        tr = TurnTranslator(session_key="k", run_id="r")
        tr.ingest({"type": "thought", "payload": {"text": "x"}})
        assert [e["state"] for e in tr.aborted()] == ["aborted"]


class TestEnvelope:
    def test_every_event_carries_the_session_key_and_run_id(self) -> None:
        """handleChatEvent drops any payload without a sessionKey."""
        _, events = run_turn(load("ravn_turn_tools.jsonl"), session_key="agent:travis:main")
        assert events
        for event in events:
            assert event["sessionKey"] == "agent:travis:main"
            assert event["runId"] == "run-1"
            assert event["message"]["role"] == "assistant"

    def test_blocks_are_snapshots_not_shared_references(self) -> None:
        """A later mutation must not retroactively change an emitted event."""
        tr = TurnTranslator(session_key="k", run_id="r")
        first = tr.ingest({"type": "thought", "payload": {"text": "one"}})[0]
        tr.ingest({"type": "thought", "payload": {"text": " two"}})
        assert text_of(first) == "one"


class TestUnknownFrames:
    def test_unknown_event_types_are_preserved(self) -> None:
        """Only 5 of 12 RavnEventType members reach this path today."""
        tr = TurnTranslator(session_key="k", run_id="r")
        events = tr.ingest({"type": "task_stuck", "payload": {"why": "waiting"}})
        block = events[0]["message"]["blocks"][0]
        assert block["type"] == "ravn_unknown"
        assert block["ravnType"] == "task_stuck"
        assert block["payload"] == {"why": "waiting"}

    def test_empty_frames_are_ignored(self) -> None:
        tr = TurnTranslator(session_key="k", run_id="r")
        assert tr.ingest({}) == []
        assert tr.ingest({"type": "thought", "payload": {"text": ""}}) == []


@pytest.mark.parametrize(
    "fixture",
    ["ravn_turn_plain.jsonl", "ravn_turn_tools.jsonl", "ravn_turn_edit.jsonl"],
)
def test_every_captured_turn_terminates_exactly_once(fixture: str) -> None:
    _, events = run_turn(load(fixture))
    terminals = [e for e in events if e["state"] in {"final", "error", "aborted"}]
    assert len(terminals) == 1, f"{fixture} produced {len(terminals)} terminal events"

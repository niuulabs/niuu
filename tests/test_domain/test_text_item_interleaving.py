"""Native text-item chronology must survive shared reduction, not only raw replay."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from niuu.domain.transcript_reducer import reduce_frames

SESSION = UUID("7472b2ce-1078-42dc-9392-4b669292a461")


def fold(*payloads):
    frames = [
        SimpleNamespace(
            session_id=SESSION,
            seq=index,
            kind=payload["type"],
            payload=payload,
            request_id=None,
            ts=datetime(2026, 9, 8, tzinfo=UTC),
        )
        for index, payload in enumerate(payloads, 1)
    ]
    return reduce_frames(frames).turns


def start(identifier, phase="commentary", index=0):
    return {
        "type": "content_block_start",
        "item_id": identifier,
        "turn_id": "turn-1",
        "thread_id": "thread-1",
        "index": index,
        "content_block": {"type": "text", "id": identifier, "phase": phase, "complete": False},
    }


def delta(identifier, text):
    return {
        "type": "content_block_delta",
        "item_id": identifier,
        "turn_id": "turn-1",
        "thread_id": "thread-1",
        "delta": {"type": "text_delta", "text": text},
    }


def completed(identifier, text, phase="commentary"):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "id": identifier,
                    "text": text,
                    "phase": phase,
                    "turn_id": "turn-1",
                    "thread_id": "thread-1",
                    "complete": True,
                }
            ],
        },
    }


TOOL = {
    "type": "assistant",
    "message": {
        "content": [
            {"type": "tool_use", "id": "call-1", "name": "exec", "input": {"cmd": "true"}},
        ]
    },
}
TOOL_RESULT = {
    "type": "user",
    "message": {
        "content": [
            {"type": "tool_result", "tool_use_id": "call-1", "content": "ok"},
        ]
    },
}
DONE = {"type": "result", "stop_reason": "end_turn"}


def test_commentary_tool_commentary_final_are_ordered_items():
    [turn] = fold(
        start("a"),
        delta("a", "Checking."),
        completed("a", "Checking."),
        TOOL,
        TOOL_RESULT,
        start("b", index=1),
        delta("b", "Found it."),
        completed("b", "Found it."),
        completed("c", "**Done.**\n\n- café 東京", "final_answer"),
        DONE,
    )
    assert [(p["type"], p.get("id", p.get("tool_use_id"))) for p in turn["parts"]] == [
        ("text", "a"),
        ("tool_use", "call-1"),
        ("tool_result", "call-1"),
        ("text", "b"),
        ("text", "c"),
    ]
    text = [p for p in turn["parts"] if p["type"] == "text"]
    assert [p["phase"] for p in text] == ["commentary", "commentary", "final_answer"]
    assert all(p["complete"] for p in text)
    assert all(p["turn_id"] == "turn-1" and p["thread_id"] == "thread-1" for p in text)
    assert turn["content"] == "Checking.\n\nFound it.\n\n**Done.**\n\n- café 東京"


def test_completed_text_reconciles_partial_delta_and_ignores_late_delta():
    full = "**Checking** café 東京\n\n```text\nα\n\nβ\n```"
    [turn] = fold(
        start("a"),
        delta("a", full[:12]),
        completed("a", full),
        completed("a", full),
        delta("a", "duplicate late fragment"),
        DONE,
    )
    assert turn["content"] == full
    assert len(turn["parts"]) == 1
    assert turn["parts"][0]["text"] == full


def test_arbitrary_chunks_preserve_exact_bytes_and_distinct_adjacent_items():
    text = "# Status\n\n- **café** 東京\n- second\n"
    [turn] = fold(
        start("a"),
        *(delta("a", character) for character in text),
        completed("a", text),
        start("b", "final_answer", 1),
        delta("b", "Done."),
        completed("b", "Done.", "final_answer"),
        DONE,
    )
    assert [p["text"] for p in turn["parts"]] == [text, "Done."]
    assert [p["id"] for p in turn["parts"]] == ["a", "b"]


def test_legacy_text_starts_preserve_boundaries_without_claiming_native_ids():
    legacy_start = {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}}
    legacy_stop = {"type": "content_block_stop"}

    def legacy_delta(text):
        return {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}

    [turn] = fold(
        legacy_start,
        legacy_delta("First."),
        legacy_stop,
        TOOL,
        TOOL_RESULT,
        legacy_start,
        legacy_delta("Second."),
        legacy_stop,
        DONE,
    )
    parts = turn["parts"]
    assert [p["type"] for p in parts] == ["text", "tool_use", "tool_result", "text"]
    assert parts[0]["id"] != parts[-1]["id"]
    assert parts[0]["id_source"] == parts[-1]["id_source"] == "synthetic"
    assert "phase" not in parts[0] and "phase" not in parts[-1]
    assert turn["content"] == "First.\n\nSecond."


def test_text_start_with_no_content_does_not_create_empty_turn():
    assert fold(start("a"), {"type": "content_block_stop", "item_id": "a"}, DONE) == []


def test_result_only_answer_enters_ordered_parts_after_tools():
    [turn] = fold(TOOL, TOOL_RESULT, {"type": "result", "result": "Done.\n\n- verified"})
    assert turn["parts"][-1] == {"type": "text", "text": "Done.\n\n- verified"}
    assert turn["content"] == "Done.\n\n- verified"

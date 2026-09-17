"""AgentUsageTracker — incremental per-subagent token accounting from the JSONL transcript.

The tracker tails Claude's append-only subagent transcript from a stored byte offset. The
tests pin the three behaviours that make that safe on a live, growing file: streaming
re-emissions of one message must not be double-counted, an append between polls must be
picked up without re-reading old bytes, and a malformed or half-written trailing line must
never poison the total.
"""

from __future__ import annotations

import json

from skuld.agent_usage import AgentUsageTracker, tokens_in_usage, workflow_id_from_path


def _usage_row(message_id: str, *, inp: int = 0, out: int = 0, read: int = 0, create: int = 0):
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": create,
            },
        },
    }


def _write(path, rows) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _append(path, rows) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def test_tokens_sums_the_four_components(tmp_path) -> None:
    """input + output + cache_read + cache_creation — the event_mapper convention."""
    transcript = tmp_path / "agent-a1.jsonl"
    _write(
        transcript,
        [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            _usage_row("msg_1", inp=2, out=100, read=9779, create=10211),
        ],
    )
    tracker = AgentUsageTracker()
    tracker.register("a1", transcript)
    assert tracker.tokens_for("a1") == 2 + 100 + 9779 + 10211


def test_streaming_reemissions_of_one_message_are_not_double_counted(tmp_path) -> None:
    """Real transcripts write the SAME assistant message several times as it streams, each
    row carrying the usage so far. Summing every row roughly doubles the true total, so
    rows are folded by message.id (largest total per id wins)."""
    transcript = tmp_path / "agent-a1.jsonl"
    _write(
        transcript,
        [
            _usage_row("msg_1", inp=2, out=1, read=9779, create=10211),
            _usage_row("msg_1", inp=2, out=236, read=9779, create=10211),
            _usage_row("msg_2", inp=4, out=50, read=100, create=0),
        ],
    )
    tracker = AgentUsageTracker()
    tracker.register("a1", transcript)
    # msg_1 counted ONCE at its largest (2+236+9779+10211), plus msg_2 (4+50+100).
    assert tracker.tokens_for("a1") == (2 + 236 + 9779 + 10211) + (4 + 50 + 100)


def test_incremental_append_between_polls(tmp_path) -> None:
    """A second poll adds only the newly appended rows — old bytes are never re-read."""
    transcript = tmp_path / "agent-a1.jsonl"
    _write(transcript, [_usage_row("msg_1", out=10)])
    tracker = AgentUsageTracker()
    tracker.register("a1", transcript)
    assert tracker.tokens_for("a1") == 10

    _append(transcript, [_usage_row("msg_2", out=25), _usage_row("msg_3", out=5)])
    assert tracker.tokens_for("a1") == 40
    # Polling again with no new bytes is stable (no re-counting).
    assert tracker.tokens_for("a1") == 40


def test_partial_trailing_line_is_deferred_then_counted(tmp_path) -> None:
    """A half-written final line (writer mid-append) is skipped, then counted once complete."""
    transcript = tmp_path / "agent-a1.jsonl"
    _write(transcript, [_usage_row("msg_1", out=10)])
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "assistant", "message": {"id": "msg_2", "usa')

    tracker = AgentUsageTracker()
    tracker.register("a1", transcript)
    assert tracker.tokens_for("a1") == 10  # the torn line is NOT parsed

    # The writer finishes that line; the next poll picks it up in full.
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write('ge": {"output_tokens": 7}}}\n')
    assert tracker.tokens_for("a1") == 17


def test_malformed_line_is_tolerated(tmp_path) -> None:
    """A corrupt row in the middle of the file doesn't stop accounting."""
    transcript = tmp_path / "agent-a1.jsonl"
    transcript.write_text(
        json.dumps(_usage_row("msg_1", out=10)) + "\n"
        "{not json at all\n"
        "\n" + json.dumps(_usage_row("msg_2", out=5)) + "\n",
        encoding="utf-8",
    )
    tracker = AgentUsageTracker()
    tracker.register("a1", transcript)
    assert tracker.tokens_for("a1") == 15


def test_unknown_or_missing_transcript_reports_none_not_zero(tmp_path) -> None:
    """None (→ no tokens_used key) is the honest answer; 0 would be a lie."""
    tracker = AgentUsageTracker()
    assert tracker.tokens_for("never-registered") is None

    missing = tmp_path / "agent-ghost.jsonl"
    tracker.register("ghost", missing)
    assert tracker.tokens_for("ghost") is None

    # File exists but has no usage rows yet (subagent just booted).
    empty = tmp_path / "agent-empty.jsonl"
    _write(empty, [{"type": "user", "message": {"role": "user", "content": "go"}}])
    tracker.register("empty", empty)
    assert tracker.tokens_for("empty") is None

    # …and once the first usage row lands, the same agent starts reporting.
    _append(empty, [_usage_row("msg_1", out=3)])
    assert tracker.tokens_for("empty") == 3


def test_file_rotation_restarts_the_tail(tmp_path) -> None:
    """If the file shrinks (rewritten), the tail restarts rather than reading past EOF."""
    transcript = tmp_path / "agent-a1.jsonl"
    _write(transcript, [_usage_row("msg_1", out=10), _usage_row("msg_2", out=10)])
    tracker = AgentUsageTracker()
    tracker.register("a1", transcript)
    assert tracker.tokens_for("a1") == 20

    _write(transcript, [_usage_row("msg_9", out=4)])
    assert tracker.tokens_for("a1") == 24  # 20 already committed + the re-read 4


def test_workflow_id_derivation() -> None:
    """Workflow subagents live under …/subagents/workflows/wf_<id>/agent-<id>.jsonl."""
    wf = "/home/u/.claude/projects/-p/sess/subagents/workflows/wf_14a94390-bcd/agent-a1.jsonl"
    assert workflow_id_from_path(wf) == "wf_14a94390-bcd"
    assert (
        workflow_id_from_path("/home/u/.claude/projects/-p/sess/subagents/agent-a1.jsonl") is None
    )
    assert workflow_id_from_path("") is None
    assert workflow_id_from_path(None) is None


def test_workflow_for_registered_agent(tmp_path) -> None:
    wf_dir = tmp_path / "subagents" / "workflows" / "wf_abc123"
    wf_dir.mkdir(parents=True)
    transcript = wf_dir / "agent-a1.jsonl"
    _write(transcript, [_usage_row("msg_1", out=8)])

    plain_dir = tmp_path / "subagents"
    plain = plain_dir / "agent-a2.jsonl"
    _write(plain, [_usage_row("msg_1", out=8)])

    tracker = AgentUsageTracker()
    tracker.register("a1", transcript)
    tracker.register("a2", plain)
    assert tracker.workflow_for("a1") == "wf_abc123"
    assert tracker.workflow_for("a2") is None
    assert tracker.workflow_for("nope") is None


def test_release_and_registry_bound(tmp_path) -> None:
    """release() frees an agent's tail state; the registry itself is bounded."""
    transcript = tmp_path / "agent-a1.jsonl"
    _write(transcript, [_usage_row("msg_1", out=10)])
    tracker = AgentUsageTracker(max_agents=2)
    tracker.register("a1", transcript)
    assert tracker.tokens_for("a1") == 10
    tracker.release("a1")
    assert tracker.tokens_for("a1") is None

    tracker.register("x", transcript)
    tracker.register("y", transcript)
    tracker.register("z", transcript)  # evicts the oldest ("x")
    assert tracker.transcript_path_for("x") is None
    assert tracker.transcript_path_for("z") == str(transcript)


def test_tokens_in_usage_ignores_non_int_and_negative() -> None:
    assert tokens_in_usage({"input_tokens": 5, "output_tokens": "nope"}) == 5
    assert tokens_in_usage({"input_tokens": -3, "output_tokens": 4}) == 4
    assert tokens_in_usage({"input_tokens": True, "output_tokens": 4}) == 4
    assert tokens_in_usage(None) == 0

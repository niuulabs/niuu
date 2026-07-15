"""Boundary test for SRD FR-10: the chronicle is a DERIVED, non-authoritative
UI aggregate and the CANONICAL transcript is INDEPENDENT of it.

This pins the architect decision: ``session_event_log`` is the single source of
truth for the transcript. The canonical transcript (durable log -> reduce) must
NEVER depend on the chronicle/timeline. We assert that by reducing real
``session_event_log``-shaped frames with NO chronicle input whatsoever, and by
showing that an absent/empty chronicle does not change or reduce the result.

Hermetic: no real Claude CLI, no Docker, no HTTP, no chronicle_watcher running.
We operate on duck-typed log frames and the pure reducer directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from niuu.domain.transcript_reducer import reduce_frames

SESSION_ID = "11111111-1111-4111-8111-111111111111"


@dataclass
class LogFrame:
    """A duck-typed stand-in for ``volundr.domain.models.SessionLogEntry``.

    Structurally satisfies the reducer's ``Frame`` protocol (``seq``, ``kind``,
    ``payload``, ``request_id``) plus the optional ``session_id`` / ``ts`` the
    reducer reads via ``getattr``. Nothing here references the chronicle.
    """

    seq: int
    kind: str
    payload: dict
    request_id: str | None = None
    session_id: str = SESSION_ID
    ts: datetime = field(default_factory=lambda: datetime(2026, 6, 27, 12, 0, 0, tzinfo=UTC))


def _conversation_frames() -> list[LogFrame]:
    """A complete user->assistant->result exchange, as it lands in the log."""
    return [
        LogFrame(seq=1, kind="user", payload={"uuid": "u-1", "content": "hello there"}),
        LogFrame(
            seq=2,
            kind="assistant",
            payload={"message": {"content": [{"type": "text", "text": "hi, working on it"}]}},
        ),
        LogFrame(
            seq=3,
            kind="result",
            payload={
                "modelUsage": {"claude-3": {"costUSD": 0.0021}},
                "stop_reason": "end_turn",
            },
        ),
    ]


def test_transcript_rebuilds_from_event_log_with_no_chronicle_input():
    """The reducer takes ONLY log frames and produces the full transcript.

    There is no chronicle/timeline argument to ``reduce_frames`` at all — the
    canonical fold cannot, by construction, depend on the chronicle.
    """
    result = reduce_frames(_conversation_frames())

    roles = [t["role"] for t in result.turns]
    assert roles == ["user", "assistant"]

    user_turn, assistant_turn = result.turns
    assert user_turn["content"] == "hello there"
    assert "working on it" in assistant_turn["content"]
    # Metadata is lifted from the log's ``result`` frame, not from any chronicle.
    assert assistant_turn["metadata"]["model"] == "claude-3"
    assert assistant_turn["metadata"]["cost"] == 0.0021


def test_empty_chronicle_does_not_reduce_the_transcript():
    """A non-Claude (or simply un-watched) session has an EMPTY chronicle, yet
    the transcript folded from the event log is fully present and unchanged.

    We model "empty chronicle" by passing nothing chronicle-derived: the same
    frames reduce identically whether or not a chronicle ever existed.
    """
    frames = _conversation_frames()

    with_no_chronicle = reduce_frames(frames)

    # Re-running the SAME canonical inputs yields the SAME transcript — the
    # fold is a pure function of the log, so an empty chronicle cannot shrink it.
    rerun = reduce_frames(_conversation_frames())

    assert len(with_no_chronicle.turns) == 2
    assert [t["role"] for t in with_no_chronicle.turns] == ["user", "assistant"]
    assert [t["content"] for t in with_no_chronicle.turns] == [t["content"] for t in rerun.turns]


def test_non_claude_transport_transcript_is_full_despite_empty_chronicle():
    """For Codex/Grok/OpenCode the chronicle is legitimately empty (EventMapper
    keys on Anthropic JSONL). The event-log transcript must still be complete:
    here a tmux/terminal-style assistant turn folds purely from log frames.
    """
    frames = [
        LogFrame(seq=1, kind="user", payload={"uuid": "u-9", "content": "run the build"}),
        LogFrame(
            seq=2,
            kind="content_block_delta",
            payload={"delta": {"type": "text_delta", "text": "build "}},
        ),
        LogFrame(
            seq=3,
            kind="content_block_delta",
            payload={"delta": {"type": "text_delta", "text": "succeeded"}},
        ),
        LogFrame(seq=4, kind="result", payload={"usage": {}}),
    ]

    result = reduce_frames(frames)

    assert [t["role"] for t in result.turns] == ["user", "assistant"]
    assert result.turns[1]["content"] == "build succeeded"


def test_reducer_signature_takes_no_chronicle_parameter():
    """Guard rail: the canonical fold's contract has NO chronicle/timeline input.

    If someone tries to thread the chronicle into the reducer (making it a second
    source of truth), this fails and forces a conversation.
    """
    import inspect

    params = set(inspect.signature(reduce_frames).parameters)
    forbidden = {"chronicle", "timeline", "chronicle_events", "timeline_events"}
    assert not (params & forbidden), f"reducer must not accept chronicle inputs: {params}"

"""LEX-1825: a hook that fires just after its turn ended must not duplicate the saved reply.

The evidence: a late MessageDisplay event 29 ms after turn 1's result refilled the pending
buffer the flush had just cleared, so the stale text was served as a live turn and saved
again at the front of turn 2. The tmux transport stamps its frames and results with the
agent's own turn id; a frame of the turn the last result closed is dropped by the
transport, the live broker and the log rebuild alike. Matching is by id, never by text.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from niuu.domain.transcript_rebuild import rebuild_turns
from skuld.transports.tmux_interactive import TmuxInteractiveTransport
from tests.test_domain.test_transcript_reducer_parity import (
    SID,
    _broker,
    _drive,
    _live_turns,
    _rebuilt_turns,
)
from volundr.domain.models import SessionLogEntry


def display(text: str, *, message_id: str = "msg-1", turn_id: str | None = "turn-1") -> dict:
    metadata: dict = {
        "source": "claude_hook",
        "hook_event_name": "MessageDisplay",
        "claude_message_id": message_id,
    }
    if turn_id is not None:
        metadata["claude_turn_id"] = turn_id
    return {
        "type": "assistant",
        "message": {"model": "interactive", "content": [{"type": "text", "text": text}]},
        "metadata": metadata,
    }


def result(text: str, turn_id: str | None = "turn-1") -> dict:
    metadata = {"source": "claude_hook", **({"claude_turn_id": turn_id} if turn_id else {})}
    return {
        "type": "result",
        "result": text,
        "stop_reason": "end_turn",
        "modelUsage": {},
        "metadata": metadata,
    }


def user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def replies(turns: list[dict]) -> list[str]:
    return [turn["content"] for turn in turns if turn["role"] == "assistant"]


async def test_a_late_frame_of_the_closed_turn_is_dropped_never_logged_and_rebuilt_alike(
    tmp_path,
):
    broker = _broker(tmp_path)

    await _drive(
        broker,
        [
            user("are you there?"),
            display("ready"),
            result("ready"),
            display("ready", message_id="msg-1b"),  # the late hook: same turn, any id or text
            user("now do the next thing"),
            display("done", message_id="msg-2", turn_id="turn-2"),
            result("done", turn_id="turn-2"),
        ],
    )

    live = _live_turns(broker)
    assert replies(live) == ["ready", "done"]  # two replies, not three turns
    assert live[1]["metadata"]["message_ids"] == ["msg-1"]
    assert live[3]["metadata"]["message_ids"] == ["msg-2"]
    logged = [e for e in broker._event_log_buffer if e["kind"] == "assistant"]
    assert len(logged) == 2  # the late frame never reached the durable log (or clients)
    assert _rebuilt_turns(broker) == live  # INV-4: the rebuild agrees
    assert broker._serialize_in_progress_turn() is None


async def test_a_new_turn_repeating_the_last_reply_is_kept(tmp_path):
    broker = _broker(tmp_path)

    await _drive(
        broker,
        [
            user("say ready"),
            display("ready"),
            result("ready"),
            # A new agent turn without a Forge user turn (typed into the terminal, or a
            # background completion): a new turn id, so the same text is a genuine reply.
            display("ready", message_id="msg-2", turn_id="turn-2"),
            result("ready", turn_id="turn-2"),
        ],
    )

    assert replies(_live_turns(broker)) == ["ready", "ready"]
    assert _rebuilt_turns(broker) == _live_turns(broker)


async def test_frames_without_a_turn_id_are_never_dropped(tmp_path):
    broker = _broker(tmp_path)

    await _drive(
        broker,
        [
            user("go"),
            display("ok", turn_id=None),
            result("ok", turn_id=None),
            display("ok", message_id="msg-2", turn_id=None),
            result("ok", turn_id=None),
        ],
    )

    assert replies(_live_turns(broker)) == ["ok", "ok"]


def entry(seq: int, kind: str, payload: dict) -> SessionLogEntry:
    return SessionLogEntry(
        session_id=SID, seq=seq, kind=kind, payload=payload, ts=datetime.now(UTC)
    )


def test_a_rebuild_of_raw_frames_drops_a_late_frame_of_the_closed_turn():
    rows = [
        entry(1, "user", user("are you there?")),
        entry(2, "assistant", display("ready")),
        entry(3, "result", result("ready")),
        entry(4, "assistant", display("ready", message_id="msg-1b")),
        entry(5, "user", user("next")),
        entry(6, "assistant", display("done", message_id="msg-2", turn_id="turn-2")),
        entry(7, "result", result("done", turn_id="turn-2")),
    ]

    assert replies(rebuild_turns(rows).turns) == ["ready", "done"]


def transport(**state) -> SimpleNamespace:
    defaults = {
        "_hook_turn_id": "",
        "_finished_hook_turn_id": "turn-1",
        "_turn_active": False,
        "_last_result": {"result": "Working on it.\nready"},
        "_normalize_prompt": TmuxInteractiveTransport._normalize_prompt,
    }
    return SimpleNamespace(**{**defaults, **state})


def test_the_transport_drops_a_late_flush_by_turn_id_and_keeps_new_turns():
    late = TmuxInteractiveTransport._is_late_flush
    assert late(transport(), "turn-1", "anything at all")
    assert not late(transport(), "turn-2", "ready")  # a new turn, even with the same text
    # Without both ids, only the old exact echo of the last result counts, and only idle.
    assert late(transport(_finished_hook_turn_id=""), "turn-9", "Working on it. ready")
    assert not late(transport(_finished_hook_turn_id=""), "", "ready")
    assert not late(
        transport(_finished_hook_turn_id="", _turn_active=True), "", "Working on it. ready"
    )


def test_a_late_flush_is_caught_while_the_next_turn_runs():
    late = TmuxInteractiveTransport._is_late_flush
    running = transport(_turn_active=True, _hook_turn_id="turn-2")
    assert late(running, "turn-1", "ready")  # turn 1's flush, landing in turn 2
    assert not late(running, "turn-2", "ready")


def test_only_the_agents_own_stop_ends_its_turn():
    closing = TmuxInteractiveTransport._closing_metadata
    state = transport(_hook_turn_id="turn-7", _finished_hook_turn_id="turn-6")

    # Skuld's own close (turn cap, idle timeout): the agent keeps working in turn-7, so its
    # later output must still flow. No turn named, nothing remembered.
    assert closing(state, {"source": "tmux_interactive"}, agent_ended=False) == {
        "source": "tmux_interactive"
    }
    assert (state._hook_turn_id, state._finished_hook_turn_id) == ("turn-7", "turn-6")
    assert not TmuxInteractiveTransport._is_late_flush(state, "turn-7", "still working")

    # The agent's Stop: the turn is named and remembered.
    assert closing(state, {"source": "claude_hook"}, agent_ended=True) == {
        "source": "claude_hook",
        "claude_turn_id": "turn-7",
    }
    assert (state._hook_turn_id, state._finished_hook_turn_id) == ("", "turn-7")


def test_only_stop_hooks_count_as_the_agent_ending_its_turn():
    from skuld.transports.tmux_interactive import AGENT_ENDED_REASONS

    assert AGENT_ENDED_REASONS == {"stop", "stop_failure"}

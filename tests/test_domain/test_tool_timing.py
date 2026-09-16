"""D1 per-tool timing — the pure reducer transitions that stamp a tool's wall clock.

Level-0 in the client used to say "ran in 3m 12s" by DERIVING the span from turn boundaries:
start = the preceding user turn, end = this assistant turn. That over-counts (the span includes
model thinking and prose) and, worse, a ``text -> tools -> text -> tools`` turn reported the
SAME whole-run span on EVERY burst inside it. D1 puts the real per-tool wall clock on the wire.

The contract these tests pin:

  * ``tool_use``    part gains ``started_at``, ``ended_at``, ``duration_ms``
  * ``tool_result`` part gains ``ended_at``
  * every key is ADDITIVE and OMITTED ENTIRELY when the frame carries no timestamp, so a
    pre-D1 transcript / an old broker / a client that never learned the keys is untouched.

Live-vs-durable parity for the same stamps is pinned separately, through the real broker, in
``test_transcript_reducer_parity.py`` (the INV-4 file).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from niuu.domain.transcript_reducer import (
    TOOL_DURATION_MS,
    TOOL_ENDED_AT,
    TOOL_STARTED_AT,
    TurnAccumulator,
    apply_assistant_blocks,
    apply_tool_result_blocks,
)

T0 = datetime(2026, 8, 3, 10, 0, 0, tzinfo=UTC)
TIMING_KEYS = (TOOL_STARTED_AT, TOOL_ENDED_AT, TOOL_DURATION_MS)


def _tool_use(uid: str, **extra) -> dict:
    return {"type": "tool_use", "id": uid, "name": "Bash", "input": {"command": "ls"}, **extra}


def _tool_result(uid: str, **extra) -> dict:
    return {"type": "tool_result", "tool_use_id": uid, "content": "ok", **extra}


def _part(acc: TurnAccumulator, ptype: str, uid: str) -> dict:
    key = "id" if ptype == "tool_use" else "tool_use_id"
    return next(p for p in acc.parts if p.get("type") == ptype and p.get(key) == uid)


class TestNormalPair:
    """A tool_use followed by its tool_result — the everyday case."""

    def test_pair_carries_start_end_and_duration(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")], ts=T0)
        call = _part(acc, "tool_use", "t1")
        # While the tool is RUNNING the call has a start and nothing else — the client can
        # tick an honest elapsed timer without ever being told a duration that is not known yet.
        assert call[TOOL_STARTED_AT] == T0.isoformat()
        assert TOOL_ENDED_AT not in call
        assert TOOL_DURATION_MS not in call

        apply_tool_result_blocks(acc, [_tool_result("t1")], ts=T0 + timedelta(seconds=192))
        assert call[TOOL_STARTED_AT] == T0.isoformat()
        assert call[TOOL_ENDED_AT] == (T0 + timedelta(seconds=192)).isoformat()
        assert call[TOOL_DURATION_MS] == 192_000  # the real "ran in 3m 12s"

    def test_result_part_carries_its_own_end_stamp(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")], ts=T0)
        apply_tool_result_blocks(acc, [_tool_result("t1")], ts=T0 + timedelta(milliseconds=250))
        result = _part(acc, "tool_result", "t1")
        assert result[TOOL_ENDED_AT] == (T0 + timedelta(milliseconds=250)).isoformat()
        # the result part keeps its historical shape otherwise
        assert result["content"] == "ok" and result["is_error"] is False

    def test_sub_millisecond_tool_reports_zero_not_missing(self):
        """A tool that returned instantly is 0 ms — a REAL measurement, not an absent one."""
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("fast")], ts=T0)
        apply_tool_result_blocks(acc, [_tool_result("fast")], ts=T0)
        call = _part(acc, "tool_use", "fast")
        assert call[TOOL_DURATION_MS] == 0

    def test_each_call_in_a_burst_gets_its_own_span(self):
        """The whole point: two tools in one turn must NOT share the turn's span."""
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("slow"), _tool_use("quick")], ts=T0)
        apply_tool_result_blocks(acc, [_tool_result("quick")], ts=T0 + timedelta(seconds=1))
        apply_tool_result_blocks(acc, [_tool_result("slow")], ts=T0 + timedelta(seconds=60))
        assert _part(acc, "tool_use", "quick")[TOOL_DURATION_MS] == 1_000
        assert _part(acc, "tool_use", "slow")[TOOL_DURATION_MS] == 60_000


class TestAbsentStampCompatibility:
    """No timestamp ⇒ byte-identical to the pre-D1 part dicts. This is the whole safety story."""

    def test_no_ts_produces_the_exact_pre_d1_tool_use_part(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")])
        assert acc.parts == [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}
        ]

    def test_no_ts_produces_the_exact_pre_d1_tool_result_part(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")])
        apply_tool_result_blocks(acc, [_tool_result("t1")])
        assert acc.parts[1] == {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "ok",
            "is_error": False,
        }
        # and the call was NOT touched by the close (no half-stamped part)
        assert not any(k in acc.parts[0] for k in TIMING_KEYS)

    def test_empty_string_ts_is_treated_as_absent_not_stamped_blank(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")], ts="")
        assert TOOL_STARTED_AT not in acc.parts[0]

    def test_result_without_ts_never_closes_a_started_call(self):
        """A half-timed stream must leave the call OPEN rather than invent an end."""
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")], ts=T0)
        apply_tool_result_blocks(acc, [_tool_result("t1")])
        call = _part(acc, "tool_use", "t1")
        assert call[TOOL_STARTED_AT] == T0.isoformat()
        assert TOOL_ENDED_AT not in call and TOOL_DURATION_MS not in call

    def test_call_without_start_still_gets_an_end_but_no_fabricated_duration(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")])
        apply_tool_result_blocks(acc, [_tool_result("t1")], ts=T0)
        call = _part(acc, "tool_use", "t1")
        assert call[TOOL_ENDED_AT] == T0.isoformat()
        assert TOOL_DURATION_MS not in call  # no start ⇒ no duration, never a guess

    def test_text_and_reasoning_parts_are_never_stamped(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(
            acc,
            [{"type": "text", "text": "hi"}, {"type": "thinking", "thinking": "hmm"}],
            ts=T0,
        )
        assert acc.parts == [
            {"type": "text", "text": "hi"},
            {"type": "reasoning", "text": "hmm"},
        ]


class TestLateAndOutOfOrderResults:
    """Results do not have to arrive in call order, or even in the same turn."""

    def test_out_of_order_results_close_their_own_calls(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("a"), _tool_use("b")], ts=T0)
        # b returns first, a second — the reverse of the call order
        apply_tool_result_blocks(acc, [_tool_result("b")], ts=T0 + timedelta(seconds=2))
        apply_tool_result_blocks(acc, [_tool_result("a")], ts=T0 + timedelta(seconds=5))
        assert _part(acc, "tool_use", "b")[TOOL_DURATION_MS] == 2_000
        assert _part(acc, "tool_use", "a")[TOOL_DURATION_MS] == 5_000

    def test_orphan_result_stamps_itself_and_backfills_nothing(self):
        """A result whose call lives in an ALREADY-FLUSHED turn (crash / turn boundary)."""
        acc = TurnAccumulator()
        apply_tool_result_blocks(acc, [_tool_result("gone")], ts=T0)
        assert acc.parts == [
            {
                "type": "tool_result",
                "tool_use_id": "gone",
                "content": "ok",
                "is_error": False,
                TOOL_ENDED_AT: T0.isoformat(),
            }
        ]

    def test_duplicate_result_does_not_restamp_the_call(self):
        """A replayed / re-delivered result must not stretch the recorded duration."""
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")], ts=T0)
        apply_tool_result_blocks(acc, [_tool_result("t1")], ts=T0 + timedelta(seconds=3))
        apply_tool_result_blocks(acc, [_tool_result("t1")], ts=T0 + timedelta(seconds=90))
        call = _part(acc, "tool_use", "t1")
        assert call[TOOL_DURATION_MS] == 3_000
        assert call[TOOL_ENDED_AT] == (T0 + timedelta(seconds=3)).isoformat()

    def test_legacy_repeated_id_closes_the_most_recent_open_call(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("dup")], ts=T0)
        # Older snapshots could already contain repeated IDs. New assistant
        # frames upsert by ID, but result timing must still safely handle that
        # historical shape by closing its most recent open call.
        acc.parts.append(
            {**_tool_use("dup"), TOOL_STARTED_AT: (T0 + timedelta(seconds=10)).isoformat()}
        )
        apply_tool_result_blocks(acc, [_tool_result("dup")], ts=T0 + timedelta(seconds=12))
        calls = [p for p in acc.parts if p.get("type") == "tool_use"]
        assert TOOL_ENDED_AT not in calls[0]  # the older call stays open
        assert calls[1][TOOL_DURATION_MS] == 2_000

    def test_same_id_input_refresh_keeps_original_completed_duration(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")], ts=T0)
        apply_tool_result_blocks(acc, [_tool_result("t1")], ts=T0 + timedelta(seconds=3))
        updated = {**_tool_use("t1"), "input": {"command": "actual completed arguments"}}
        apply_assistant_blocks(acc, [updated], ts=T0 + timedelta(seconds=90))
        calls = [part for part in acc.parts if part["type"] == "tool_use"]
        assert len(calls) == 1
        assert calls[0]["input"] == updated["input"]
        assert calls[0][TOOL_STARTED_AT] == T0.isoformat()
        assert calls[0][TOOL_ENDED_AT] == (T0 + timedelta(seconds=3)).isoformat()
        assert calls[0][TOOL_DURATION_MS] == 3_000


class TestClockAndFormatEdges:
    """Timing must degrade to "no claim" rather than to a wrong claim."""

    def test_backwards_clock_clamps_to_zero(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")], ts=T0)
        apply_tool_result_blocks(acc, [_tool_result("t1")], ts=T0 - timedelta(seconds=5))
        assert _part(acc, "tool_use", "t1")[TOOL_DURATION_MS] == 0

    def test_naive_and_aware_stamps_yield_no_duration(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")], ts=T0.replace(tzinfo=None))
        apply_tool_result_blocks(acc, [_tool_result("t1")], ts=T0 + timedelta(seconds=4))
        call = _part(acc, "tool_use", "t1")
        assert call[TOOL_STARTED_AT] and call[TOOL_ENDED_AT]
        assert TOOL_DURATION_MS not in call  # not subtractable — say nothing

    def test_unparseable_start_yields_no_duration(self):
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")], ts="not-a-timestamp")
        apply_tool_result_blocks(acc, [_tool_result("t1")], ts=T0)
        call = _part(acc, "tool_use", "t1")
        assert call[TOOL_STARTED_AT] == "not-a-timestamp"
        assert TOOL_DURATION_MS not in call

    def test_iso_string_ts_is_accepted_verbatim(self):
        """The batch path hands a datetime; a transport may hand an ISO string. Both work."""
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")], ts=T0.isoformat())
        apply_tool_result_blocks(
            acc, [_tool_result("t1")], ts=(T0 + timedelta(seconds=7)).isoformat()
        )
        assert _part(acc, "tool_use", "t1")[TOOL_DURATION_MS] == 7_000


class TestTransportStampPreference:
    """A transport with hook-exact boundaries can pre-stamp the raw block; it wins."""

    def test_block_started_at_beats_the_frame_ts(self):
        hook_start = T0 - timedelta(seconds=30)
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1", started_at=hook_start.isoformat())], ts=T0)
        assert _part(acc, "tool_use", "t1")[TOOL_STARTED_AT] == hook_start.isoformat()

    def test_block_ended_at_beats_the_frame_ts_on_both_parts(self):
        hook_end = T0 + timedelta(seconds=45)
        acc = TurnAccumulator()
        apply_assistant_blocks(acc, [_tool_use("t1")], ts=T0)
        apply_tool_result_blocks(
            acc,
            [_tool_result("t1", ended_at=hook_end.isoformat())],
            ts=T0 + timedelta(seconds=99),
        )
        assert _part(acc, "tool_result", "t1")[TOOL_ENDED_AT] == hook_end.isoformat()
        assert _part(acc, "tool_use", "t1")[TOOL_DURATION_MS] == 45_000

"""Unit tests for ``drive_replay`` with an injected (fake) clock.

These prove the driver's contract without any real sleeping: the recorded
sleeps land in a list, emission order is preserved, ``prev_ts`` advances across
dropped/hidden frames so visible spacing is the recorded wall gap, and a
``CancelledError`` from the injected sleep stops the loop cleanly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from volundr.domain.models import SessionLogEntry
from volundr.replay.pacing import PacingConfig, drive_replay

_SID = uuid4()
_BASE = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)


def _entry(seq: int, *, offset: float, kind: str = "assistant") -> SessionLogEntry:
    return SessionLogEntry(
        session_id=_SID,
        seq=seq,
        kind=kind,
        payload={"type": kind, "n": seq},
        ts=_BASE + timedelta(seconds=offset),
        role=None,
        request_id="req-1",
    )


async def _aiter(entries):
    for e in entries:
        yield e


async def test_emits_in_seq_order_and_records_paced_sleeps():
    entries = [
        _entry(1, offset=0),
        _entry(2, offset=3),  # +3s
        _entry(3, offset=4.5),  # +1.5s
    ]
    sleeps: list[float] = []
    emitted: list[int] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    async def emit(e: SessionLogEntry) -> bool:
        emitted.append(e.seq)
        return True

    cfg = PacingConfig(speed=1.0, max_gap_seconds=10.0)
    count = await drive_replay(_aiter(entries), cfg=cfg, emit=emit, sleep=fake_sleep)

    assert emitted == [1, 2, 3]
    assert count == 3
    # First frame: no sleep. Then +3s, +1.5s.
    assert sleeps == [3.0, 1.5]


async def test_speed_compresses_recorded_sleeps():
    entries = [_entry(1, offset=0), _entry(2, offset=10)]
    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    async def emit(e: SessionLogEntry) -> bool:
        return True

    cfg = PacingConfig(speed=4.0, max_gap_seconds=100.0)
    await drive_replay(_aiter(entries), cfg=cfg, emit=emit, sleep=fake_sleep)
    assert sleeps == [2.5]  # 10s / 4


async def test_prev_ts_advances_across_a_hidden_frame():
    # seq 2 is hidden (emit returns False); the gap from 1->3 must still be the
    # SUM of the two real gaps, so visible spacing follows the recorded timeline.
    entries = [
        _entry(1, offset=0),
        _entry(2, offset=2),  # hidden
        _entry(3, offset=5),
    ]
    sleeps: list[float] = []
    emitted: list[int] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    async def emit(e: SessionLogEntry) -> bool:
        if e.seq == 2:
            return False  # dropped/hidden
        emitted.append(e.seq)
        return True

    cfg = PacingConfig(speed=1.0, max_gap_seconds=100.0)
    count = await drive_replay(_aiter(entries), cfg=cfg, emit=emit, sleep=fake_sleep)

    # Only visible frames counted, but ALL gaps consumed wall-time.
    assert emitted == [1, 3]
    assert count == 2
    # sleep before seq2 (+2s from seq1), sleep before seq3 (+3s from seq2).
    assert sleeps == [2.0, 3.0]


async def test_cancellation_from_sleep_stops_loop_with_no_further_emit():
    entries = [_entry(1, offset=0), _entry(2, offset=3), _entry(3, offset=6)]
    emitted: list[int] = []

    async def cancel_on_second_sleep(d: float) -> None:
        # First emit (seq1) happens with no sleep; the first sleep precedes seq2.
        raise asyncio.CancelledError

    async def emit(e: SessionLogEntry) -> bool:
        emitted.append(e.seq)
        return True

    cfg = PacingConfig(speed=1.0, max_gap_seconds=100.0)
    with pytest.raises(asyncio.CancelledError):
        await drive_replay(_aiter(entries), cfg=cfg, emit=emit, sleep=cancel_on_second_sleep)

    # seq1 emitted (no sleep before it); cancellation hit before seq2 emit.
    assert emitted == [1]


async def test_null_ts_frame_does_not_collapse_the_surrounding_gap():
    # A mid-stream frame with ts=None must NOT advance prev_ts, so the genuine
    # gap across it (seq1 @0s -> seq3 @10s) is preserved rather than zeroed.
    # Regression: prev_ts used to advance unconditionally, dropping the far gap.
    e_null = SessionLogEntry(
        session_id=_SID,
        seq=2,
        kind="assistant",
        payload={"type": "assistant", "n": 2},
        ts=None,
        role=None,
        request_id="r",
    )
    entries = [_entry(1, offset=0), e_null, _entry(3, offset=10)]
    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    async def emit(e: SessionLogEntry) -> bool:
        return True

    cfg = PacingConfig(speed=1.0, max_gap_seconds=100.0)
    await drive_replay(_aiter(entries), cfg=cfg, emit=emit, sleep=fake_sleep)

    # seq1: first frame, no sleep. seq2: ts=None -> delay 0, no sleep recorded,
    # prev_ts stays @0s. seq3: full 10s gap from seq1 survives.
    assert sleeps == [10.0]


async def test_default_sleep_is_real_asyncio_sleep_but_zero_delays_are_skipped():
    # With max_gap=0 every delay is 0, so the default asyncio.sleep is never
    # awaited with a positive value — the driver returns promptly.
    entries = [_entry(1, offset=0), _entry(2, offset=1000)]
    emitted: list[int] = []

    async def emit(e: SessionLogEntry) -> bool:
        emitted.append(e.seq)
        return True

    cfg = PacingConfig(speed=1.0, max_gap_seconds=0.0)
    count = await drive_replay(_aiter(entries), cfg=cfg, emit=emit)
    assert emitted == [1, 2]
    assert count == 2

"""Unit tests for the PURE pacing function ``frame_delay``.

No I/O, no clock, no DB — just arithmetic on ts deltas. The load-bearing
property is clamp-BEFORE-divide: a long idle gap is capped by ``max_gap_seconds``
*before* the ``speed`` division, so a high speed never re-inflates a capped gap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from volundr.replay.pacing import PacingConfig, frame_delay

_BASE = datetime(2026, 6, 18, 9, 0, 0, tzinfo=UTC)


def _at(seconds: float) -> datetime:
    return _BASE + timedelta(seconds=seconds)


def test_first_frame_has_zero_delay():
    # prev_ts is None => nothing to pace against.
    assert frame_delay(None, _at(0), PacingConfig()) == 0.0


def test_missing_cur_ts_has_zero_delay():
    assert frame_delay(_at(0), None, PacingConfig()) == 0.0


def test_both_none_has_zero_delay():
    assert frame_delay(None, None, PacingConfig()) == 0.0


def test_equal_ts_has_zero_delay():
    assert frame_delay(_at(5), _at(5), PacingConfig()) == 0.0


def test_out_of_order_ts_has_zero_delay():
    # Negative raw gap: seq is the true order, never sleep backwards.
    assert frame_delay(_at(10), _at(4), PacingConfig()) == 0.0


def test_simple_gap_at_speed_one():
    cfg = PacingConfig(speed=1.0, max_gap_seconds=10.0)
    assert frame_delay(_at(0), _at(5), cfg) == 5.0


def test_speed_compresses_gap():
    cfg = PacingConfig(speed=5.0, max_gap_seconds=10.0)
    assert frame_delay(_at(0), _at(5), cfg) == 1.0


def test_slow_motion_speed_below_one_expands_gap():
    # speed=0.5 => twice as slow; gap still clamped first, then divided.
    cfg = PacingConfig(speed=0.5, max_gap_seconds=10.0)
    assert frame_delay(_at(0), _at(4), cfg) == 8.0


def test_huge_gap_is_clamped_to_max_gap():
    # 30-minute idle, capped to max_gap=2.0 at speed 1.
    cfg = PacingConfig(speed=1.0, max_gap_seconds=2.0)
    assert frame_delay(_at(0), _at(1800), cfg) == 2.0


def test_clamp_before_divide_not_after():
    # 30-min gap, max_gap=2, speed=10. Clamp-before-divide => 2/10 = 0.2,
    # NOT (1800/10 capped at 2). This is the decisive assertion.
    cfg = PacingConfig(speed=10.0, max_gap_seconds=2.0)
    assert frame_delay(_at(0), _at(1800), cfg) == 0.2


def test_zero_speed_is_floored_to_one_no_div_by_zero():
    cfg = PacingConfig(speed=0.0, max_gap_seconds=10.0)
    assert frame_delay(_at(0), _at(5), cfg) == 5.0


def test_negative_speed_is_floored_to_one():
    cfg = PacingConfig(speed=-3.0, max_gap_seconds=10.0)
    assert frame_delay(_at(0), _at(5), cfg) == 5.0


def test_zero_max_gap_yields_zero_delay():
    # max_gap=0 collapses all spacing (used by tests for instant streaming).
    cfg = PacingConfig(speed=1.0, max_gap_seconds=0.0)
    assert frame_delay(_at(0), _at(5), cfg) == 0.0

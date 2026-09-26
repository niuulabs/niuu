"""Unit tests for mimir.live_activity.LiveActivityRecorder."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mimir.live_activity import LiveActivityRecorder


def _clock(moment: list[datetime]):
    """Return a zero-arg clock reading (and steppable) from *moment*."""

    def _now() -> datetime:
        return moment[0]

    return _now


def test_records_are_returned_newest_first() -> None:
    moment = [datetime(2026, 1, 1, tzinfo=UTC)]
    recorder = LiveActivityRecorder(buffer_size=10, window_seconds=3600, clock=_clock(moment))

    recorder.record(kind="read", mount="local", path="a.md", actor="alice")
    moment[0] += timedelta(seconds=1)
    recorder.record(kind="write", mount="local", path="b.md", actor="bob")
    moment[0] += timedelta(seconds=1)
    recorder.record(kind="read", mount="shared", path="c.md", actor=None)

    events = recorder.list_since(None)
    assert [e.path for e in events] == ["c.md", "b.md", "a.md"]
    assert events[0].actor is None
    assert events[1].actor == "bob"
    assert events[0].kind == "read"
    assert events[1].kind == "write"
    assert events[2].mount == "local"


def test_since_excludes_events_at_or_before_the_given_timestamp() -> None:
    moment = [datetime(2026, 1, 1, tzinfo=UTC)]
    recorder = LiveActivityRecorder(buffer_size=10, window_seconds=3600, clock=_clock(moment))

    recorder.record(kind="read", mount="local", path="a.md", actor="alice")
    cutoff = moment[0]
    moment[0] += timedelta(seconds=1)
    recorder.record(kind="read", mount="local", path="b.md", actor="alice")

    events = recorder.list_since(cutoff)
    assert [e.path for e in events] == ["b.md"]

    # since exactly equal to an event's own timestamp excludes that event
    # (strictly newer only).
    events_at_second = recorder.list_since(moment[0])
    assert events_at_second == []


def test_since_naive_datetime_is_treated_as_utc() -> None:
    moment = [datetime(2026, 1, 1, tzinfo=UTC)]
    recorder = LiveActivityRecorder(buffer_size=10, window_seconds=3600, clock=_clock(moment))
    recorder.record(kind="read", mount="local", path="a.md", actor=None)
    moment[0] += timedelta(seconds=1)
    recorder.record(kind="read", mount="local", path="b.md", actor=None)

    naive_cutoff = datetime(2026, 1, 1, 0, 0, 0)  # no tzinfo
    events = recorder.list_since(naive_cutoff)
    assert [e.path for e in events] == ["b.md"]


def test_window_expiry_hides_stale_events_even_without_since() -> None:
    moment = [datetime(2026, 1, 1, tzinfo=UTC)]
    recorder = LiveActivityRecorder(buffer_size=10, window_seconds=60, clock=_clock(moment))

    recorder.record(kind="read", mount="local", path="old.md", actor=None)
    moment[0] += timedelta(seconds=61)
    recorder.record(kind="read", mount="local", path="fresh.md", actor=None)

    events = recorder.list_since(None)
    assert [e.path for e in events] == ["fresh.md"]


def test_maxlen_evicts_oldest_events() -> None:
    moment = [datetime(2026, 1, 1, tzinfo=UTC)]
    recorder = LiveActivityRecorder(buffer_size=2, window_seconds=3600, clock=_clock(moment))

    recorder.record(kind="read", mount="local", path="a.md", actor=None)
    moment[0] += timedelta(seconds=1)
    recorder.record(kind="read", mount="local", path="b.md", actor=None)
    moment[0] += timedelta(seconds=1)
    recorder.record(kind="read", mount="local", path="c.md", actor=None)

    events = recorder.list_since(None)
    assert [e.path for e in events] == ["c.md", "b.md"]


def test_each_event_gets_a_distinct_id() -> None:
    moment = [datetime(2026, 1, 1, tzinfo=UTC)]
    recorder = LiveActivityRecorder(buffer_size=10, window_seconds=3600, clock=_clock(moment))
    recorder.record(kind="read", mount="local", path="a.md", actor=None)
    recorder.record(kind="read", mount="local", path="a.md", actor=None)
    events = recorder.list_since(None)
    assert events[0].id != events[1].id


def test_default_clock_uses_real_time() -> None:
    recorder = LiveActivityRecorder(buffer_size=10, window_seconds=3600)
    before = datetime.now(UTC)
    recorder.record(kind="read", mount="local", path="a.md", actor=None)
    after = datetime.now(UTC)
    (event,) = recorder.list_since(None)
    assert before <= event.timestamp <= after

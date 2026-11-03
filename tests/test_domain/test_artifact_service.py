"""ArtifactService: ingest origin, backfill paging, the hold's edge cases, the notice."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

import pytest

from tests.support.artifacts import (
    CATALOG,
    POLICY,
    T0,
    FakeNotices,
    candidate,
    hook_row,
    service,
)
from volundr.adapters.outbound.memory_artifacts import InMemoryArtifactPublicationRepository
from volundr.domain.artifact_ports import EmitOutcome, LogCursor
from volundr.domain.artifacts import Origin
from volundr.domain.models import Session
from volundr.domain.services.artifacts import ArtifactService, _burst_title


def _row(session: Session, seq: int, at, key: str = "Key1"):
    url = f"https://claude.ai/artifact/{key}"
    return hook_row(
        str(session.id),
        seq,
        at,
        {
            "tool_name": "Artifact",
            "tool_use_id": f"toolu_{seq}",
            "tool_input": {},
            "tool_response": {"url": url, "version": f"{seq}-v", "title": "Plan"},
        },
    )


async def test_ingest_marks_live_or_backfill_by_lag():
    repository = InMemoryArtifactPublicationRepository()
    svc = service(repository)
    session = Session(name="s", owner_id="o")
    rows = [_row(session, 1, T0), _row(session, 2, T0 - timedelta(minutes=11))]
    created = await svc.project_log_entries(session, rows, seen_at=T0 + timedelta(seconds=5))
    assert {p.session_seq: p.origin for p in created} == {1: Origin.LIVE, 2: Origin.BACKFILL}
    # The same rows again: nothing new (the append is idempotent).
    assert await svc.project_log_entries(session, rows, seen_at=T0) == []


async def test_ingest_refuses_rows_of_another_session():
    svc = service(InMemoryArtifactPublicationRepository())
    other = Session(name="other")
    with pytest.raises(ValueError):
        await svc.project_log_entries(Session(name="s"), [_row(other, 1, T0)], seen_at=T0)


async def test_backfill_pages_through_sessions_and_resumes_from_a_cursor():
    repository = InMemoryArtifactPublicationRepository()
    sessions = [Session(name=f"s{i}") for i in range(3)]
    repository.add_log_rows(
        [_row(s, seq, T0 + timedelta(minutes=seq)) for s in sessions for seq in range(1, 5)]
    )
    svc = ArtifactService(
        repository,
        host="thor",
        policy=POLICY,
        catalog=CATALOG,
        notices=FakeNotices(),
        session_lookup=None,
        emit_batch_size=10,
        backfill_page_size=5,
    )
    first = await svc.backfill(max_pages=1)
    assert (first.scanned_rows, first.recorded) == (5, 5)
    rest = await svc.backfill(cursor=LogCursor(first.last_session_id, first.last_seq))
    assert rest.recorded == 7
    assert len(await repository.list_after(None, after=0, limit=100)) == 12
    one = await svc.backfill(session_id=sessions[0].id)
    assert (one.scanned_rows, one.recorded) == (4, 0)
    windowed = await svc.backfill(since=T0 + timedelta(minutes=2), until=T0 + timedelta(minutes=3))
    assert windowed.scanned_rows == 3


async def test_emit_due_without_notices_is_silent():
    repository = InMemoryArtifactPublicationRepository()
    svc = ArtifactService(
        repository,
        host="thor",
        policy=POLICY,
        catalog=CATALOG,
        notices=None,
        session_lookup=None,
        emit_batch_size=10,
        backfill_page_size=10,
    )
    await svc.record([(candidate("K", "s1", 1, T0), Origin.LIVE)])
    assert await svc.emit_due(T0 + timedelta(hours=1)) == []
    assert repository.bursts[0].pending


async def test_a_gone_seed_session_settles_without_a_notice():
    repository = InMemoryArtifactPublicationRepository()

    async def gone(_session_id):
        return None

    notices = FakeNotices()
    svc = ArtifactService(
        repository,
        host="thor",
        policy=POLICY,
        catalog=CATALOG,
        notices=notices,
        session_lookup=gone,
        emit_batch_size=10,
        backfill_page_size=10,
    )
    await svc.record([(candidate("K", "s1", 1, T0), Origin.LIVE)])
    [result] = await svc.emit_due(T0 + timedelta(minutes=5))
    assert result.outcome is EmitOutcome.SETTLED
    assert repository.bursts[0].suppress_reason == "session_gone"
    assert await svc.emit_due(T0 + timedelta(minutes=6)) == []
    assert not await repository.settle(repository.bursts[0].burst_id, reason="again")
    assert notices.broadcast == []


async def test_the_notice_names_the_latest_title_and_the_session():
    repository = InMemoryArtifactPublicationRepository()
    notices = FakeNotices()
    svc = service(repository, notices=notices)
    await svc.record(
        [
            (candidate("K", "s1", 1, T0, title="Draft"), Origin.LIVE),
            (candidate("K", "s1", 2, T0 + timedelta(seconds=30), title="Final plan"), Origin.LIVE),
        ]
    )
    [result] = await svc.emit_due(T0 + timedelta(seconds=120))
    notice = result.notification
    assert notice.title == "Final plan published"
    assert notice.body.startswith("session-")
    assert notice.kind.value == "milestone" and notice.severity.value == "info"
    assert notice.metadata["provider_key"] == "K"
    burst = await svc.get_burst(repository.bursts[0].burst_id)
    assert burst.notification_id == notice.id and burst.publications == 2
    assert [p.session_seq for p in await svc.burst_publications(None, burst.burst_id)] == [1, 2]
    assert [b.publications for _, b in await svc.list_bursts_after(None, after=0, limit=5)] == [2]


def test_burst_titles_are_bounded():
    assert _burst_title(None) == "Artifact published"
    assert _burst_title("  ") == "Artifact published"
    long = _burst_title("x" * 300)
    assert len(long) <= 200 and long.endswith("… published")


def test_constructor_validation():
    repository = InMemoryArtifactPublicationRepository()
    kwargs = dict(policy=POLICY, catalog=CATALOG, notices=None, session_lookup=None)
    with pytest.raises(ValueError):
        ArtifactService(repository, host="", emit_batch_size=1, backfill_page_size=1, **kwargs)
    with pytest.raises(ValueError):
        ArtifactService(repository, host="h", emit_batch_size=0, backfill_page_size=1, **kwargs)
    svc = ArtifactService(repository, host="h", emit_batch_size=1, backfill_page_size=1, **kwargs)
    assert svc.host == "h" and svc.policy is POLICY


async def test_record_nothing():
    assert await service(InMemoryArtifactPublicationRepository()).record([]) == []


async def test_emission_of_an_empty_burst_is_skipped():
    """Defensive: a burst row without members (never produced by record) is not announced."""
    repository = InMemoryArtifactPublicationRepository()
    svc = service(repository)
    await svc.record([(candidate("K", "s1", 1, T0), Origin.LIVE)])
    repository._state.members.clear()
    assert await svc.emit_due(T0 + timedelta(minutes=5)) == []


def test_session_ids_are_uuids():
    assert isinstance(uuid.UUID(str(Session(name="x").id)), uuid.UUID)


async def test_backfill_leaves_rows_younger_than_the_live_lag_to_ingest():
    repository = InMemoryArtifactPublicationRepository()
    session = Session(name="s")
    now = T0 + timedelta(hours=1)
    repository.add_log_rows(
        [
            _row(session, 1, now - timedelta(minutes=30)),
            _row(session, 2, now - timedelta(minutes=5)),
        ]
    )
    svc = ArtifactService(
        repository,
        host="thor",
        policy=POLICY,
        catalog=CATALOG,
        notices=None,
        session_lookup=None,
        emit_batch_size=10,
        backfill_page_size=10,
        clock=lambda: now,
    )
    assert (await svc.backfill(until=now + timedelta(days=1))).recorded == 1
    # Ingest then records the young row live, so its burst still announces itself.
    [live] = await svc.project_log_entries(
        session, [_row(session, 2, now - timedelta(minutes=5))], seen_at=now
    )
    assert live.origin is Origin.LIVE


async def test_a_failing_burst_does_not_stall_the_others_and_is_settled(caplog):
    repository = InMemoryArtifactPublicationRepository()
    notices = FakeNotices()

    async def lookup(session_id):
        if session_id == bad_session:
            raise RuntimeError("boom")
        return Session(id=session_id, name="ok", owner_id="o")

    svc = ArtifactService(
        repository,
        host="thor",
        policy=POLICY,
        catalog=CATALOG,
        notices=notices,
        session_lookup=lookup,
        emit_batch_size=10,
        backfill_page_size=10,
        max_emit_failures=2,
    )
    bad = candidate("Bad", "bad", 1, T0)
    bad_session = bad.session_id
    await svc.record([(bad, Origin.LIVE), (candidate("Good", "good", 1, T0), Origin.LIVE)])
    first = await svc.emit_due(T0 + timedelta(minutes=5))
    assert [r.outcome for r in first] == [EmitOutcome.EMITTED]
    with caplog.at_level(logging.ERROR):
        second = await svc.emit_due(T0 + timedelta(minutes=6))
    assert [r.outcome for r in second] == [EmitOutcome.SETTLED]
    # Hitting the cap is loud: an error naming the knob, not only the per-try traces.
    assert any(
        record.levelno == logging.ERROR and "artifacts.max_emit_failures" in record.getMessage()
        for record in caplog.records
    )
    assert {b.provider_key: b.suppress_reason for b in repository.bursts}["Bad"] == "emit_failed"
    assert len(notices.broadcast) == 1

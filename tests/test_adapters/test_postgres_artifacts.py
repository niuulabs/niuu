"""Artifact SQL adapter with a mocked asyncpg boundary (no database).

The real-SQL behaviour (B1-B9, the goldens) is in
``tests/integration/volundr/test_artifact_publications_pg.py``; this pins the branches."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from niuu.domain.notifications import NotificationKind, NotificationSeverity, NotificationSource
from tests.support.artifacts import POLICY, T0, candidate
from volundr.adapters.outbound.postgres_artifacts import (
    ARTIFACT_FEED_LOCK_SQL,
    DUE_BURSTS_SQL,
    EMITTED_SQL,
    INSERT_BURST_SQL,
    INSERT_MEMBER_SQL,
    INSERT_PUBLICATION_SQL,
    LOCK_BURST_SQL,
    SCAN_LOG_SQL,
    SETTLE_SQL,
    SUPPRESS_SQL,
    SUPPRESSOR_SQL,
    WIDEN_BURST_SQL,
    WINDOW_BURSTS_SQL,
    PostgresArtifactPublicationRepository,
    row_to_publication,
)
from volundr.adapters.outbound.postgres_notifications import (
    FEED_ORDER_LOCK_SQL,
    INSERT_NOTIFICATION_SQL,
)
from volundr.domain.artifact_ports import ArtifactStoreUnavailableError, EmitOutcome, LogCursor
from volundr.domain.artifacts import Origin, Provider, PublicationSource, publication_id
from volundr.domain.notifications import NotificationCandidate, NotificationScope

SID = uuid.UUID("11111111-2222-3333-4444-555555555555")
BID = uuid.UUID("99999999-2222-3333-4444-555555555555")


def _pool():
    pool, conn = MagicMock(), MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    for target in (pool, conn):
        target.execute = AsyncMock(return_value="UPDATE 1")
        target.executemany = AsyncMock()
        target.fetch = AsyncMock(return_value=[])
        target.fetchrow = AsyncMock(return_value=None)
        target.fetchval = AsyncMock(return_value=None)
    return pool, conn


def _pub_row(seq: int = 1, **overrides) -> dict:
    row = {
        "publication_id": publication_id("thor", SID, seq, 0),
        "seq": seq,
        "host": "thor",
        "session_id": SID,
        "session_seq": seq,
        "ordinal": 0,
        "source": "artifact_tool",
        "provider": "claude_ai",
        "provider_key": "K",
        "revision_id": "rev_x",
        "at": T0,
        "origin": "live",
        "url": "https://claude.ai/artifact/K",
        "title": "T",
        "tool_use_id": "toolu",
        "provider_version": "1-a",
        "provider_seq": 1,
        "artifact_type": "page",
        "type_url": None,
        "mime": None,
        "subagent": False,
        "version_label": None,
        "created_at": T0,
    }
    row.update(overrides)
    return row


def _burst_row(**overrides) -> dict:
    row = {
        "burst_id": BID,
        "rev": 3,
        "host": "thor",
        "provider_key": "K",
        "origin": "live",
        "started_at": T0,
        "last_at": T0,
        "publications": 1,
        "seed_session_id": SID,
        "seed_session_seq": 1,
        "due_at": T0 + timedelta(seconds=120),
        "notification_id": None,
        "suppressed_by": None,
        "suppress_reason": None,
        "emitted_at": None,
    }
    row.update(overrides)
    return row


def _notice_row(candidate: NotificationCandidate) -> dict:
    return {
        "id": candidate.id,
        "seq": 7,
        "dedupe_key": candidate.dedupe_key,
        "session_id": candidate.session_id,
        "session_seq": None,
        "session_name": "s",
        "owner_id": candidate.owner_id,
        "tenant_id": None,
        "project_id": None,
        "kind": "milestone",
        "severity": "info",
        "source": "system",
        "title": candidate.title,
        "body": "",
        "links": "[]",
        "engine": None,
        "model": None,
        "correlation_id": None,
        "metadata": "{}",
        "speak_text": None,
        "speak_priority": None,
        "created_at": T0,
    }


def _candidate() -> NotificationCandidate:
    return NotificationCandidate(
        dedupe_key=f"artifact-burst:{BID}",
        session_id=SID,
        owner_id="o",
        kind=NotificationKind.MILESTONE,
        severity=NotificationSeverity.INFO,
        source=NotificationSource.SYSTEM,
        title="T published",
    )


async def test_record_opens_a_burst_for_a_new_publication():
    pool, conn = _pool()
    conn.fetchrow.return_value = _pub_row()
    repo = PostgresArtifactPublicationRepository(pool)
    [created] = await repo.record(
        "thor", [(candidate("K", str(SID), 1, T0), Origin.LIVE)], policy=POLICY
    )
    sqls = [c.args[0] for c in conn.execute.await_args_list]
    assert sqls[0] == ARTIFACT_FEED_LOCK_SQL
    assert INSERT_BURST_SQL in sqls and INSERT_MEMBER_SQL in sqls
    assert conn.fetchrow.await_args.args[0] == INSERT_PUBLICATION_SQL
    assert conn.fetch.await_args.args[0] == WINDOW_BURSTS_SQL
    assert created.burst_id is not None and created.source is PublicationSource.ARTIFACT_TOOL


async def test_record_widens_an_existing_burst_and_skips_a_known_row():
    pool, conn = _pool()
    conn.fetchrow.side_effect = [None, _pub_row(2, session_seq=2)]  # seq 1 is known
    conn.fetch.return_value = [_burst_row()]
    repo = PostgresArtifactPublicationRepository(pool)
    items = [
        (candidate("K", str(SID), 2, T0 + timedelta(minutes=1)), Origin.LIVE),
        (candidate("K", str(SID), 1, T0), Origin.LIVE),
    ]
    created = await repo.record("thor", items, policy=POLICY)
    assert [p.burst_id for p in created] == [BID]
    widen = next(c for c in conn.execute.await_args_list if c.args[0] == WIDEN_BURST_SQL)
    assert widen.args[1:] == (BID, T0, T0 + timedelta(minutes=1), 2)


async def test_record_files_without_a_burst():
    pool, conn = _pool()
    conn.fetchrow.return_value = _pub_row(source="present_file", provider="forge_file")
    repo = PostgresArtifactPublicationRepository(pool)
    file = candidate("pf_1", str(SID), 1, T0)
    file = type(file)(
        **{
            **file.__dict__,
            "source": PublicationSource.PRESENT_FILE,
            "provider": Provider.FORGE_FILE,
        }
    )
    [created] = await repo.record("thor", [(file, Origin.LIVE)], policy=POLICY)
    assert created.burst_id is None
    conn.fetch.assert_not_awaited()


async def test_record_nothing_and_connection_errors():
    pool, conn = _pool()
    repo = PostgresArtifactPublicationRepository(pool)
    assert await repo.record("thor", [], policy=POLICY) == []
    conn.execute.side_effect = asyncpg.PostgresConnectionError("down")
    with pytest.raises(ArtifactStoreUnavailableError):
        await repo.record("thor", [(candidate("K", "s", 1, T0), Origin.LIVE)], policy=POLICY)


@pytest.mark.parametrize(
    "scope,needle,args",
    [
        (None, "TRUE", (0, 10)),
        (
            NotificationScope(user_id="u", tenant_id="t", is_admin=False),
            "s.owner_id = $2",
            (0, "u", 10),
        ),
        (
            NotificationScope(user_id="a", tenant_id="t", is_admin=True),
            "COALESCE(s.tenant_id",
            (0, "a", "t", 10),
        ),
    ],
)
async def test_list_after_scopes_by_session_owner(scope, needle, args):
    pool, _ = _pool()
    pool.fetch.return_value = [{**_pub_row(), "burst_id": BID}]
    repo = PostgresArtifactPublicationRepository(pool)
    [row] = await repo.list_after(scope, after=0, limit=10)
    sql = pool.fetch.await_args.args[0]
    assert needle in sql and pool.fetch.await_args.args[1:] == args
    assert row.burst_id == BID


async def test_burst_reads():
    pool, _ = _pool()
    repo = PostgresArtifactPublicationRepository(pool)
    pool.fetch.return_value = [_burst_row()]
    [(rev, burst)] = await repo.list_bursts_after(None, after=0, limit=5)
    assert (rev, burst.burst_id, burst.origin) == (3, BID, Origin.LIVE)
    assert await repo.get_burst(BID) is None
    pool.fetchrow.return_value = _burst_row()
    assert (await repo.get_burst(BID)).pending
    [due] = await repo.due_bursts(T0, limit=4)
    assert pool.fetch.await_args.args == (DUE_BURSTS_SQL, T0, 4)
    pool.fetch.return_value = [{**_pub_row(), "burst_id": BID}]
    [member] = await repo.burst_publications(BID, NotificationScope("u", "t", False))
    assert member.burst_id == BID and pool.fetch.await_args.args[1:] == (BID, "u")


async def test_emit_settled_when_missing_or_not_pending():
    pool, conn = _pool()
    repo = PostgresArtifactPublicationRepository(pool)
    result = await repo.emit(BID, _candidate(), now=T0, policy=POLICY)
    assert result.outcome is EmitOutcome.SETTLED
    conn.fetchrow.return_value = _burst_row(notification_id=uuid.uuid4())
    assert (
        await repo.emit(BID, _candidate(), now=T0, policy=POLICY)
    ).outcome is EmitOutcome.SETTLED
    assert conn.fetchrow.await_args.args == (LOCK_BURST_SQL, BID)


async def test_emit_suppressed_by_an_agent_notice():
    pool, conn = _pool()
    conn.fetchrow.return_value = _burst_row()
    suppressor = uuid.uuid4()
    conn.fetchval.return_value = suppressor
    repo = PostgresArtifactPublicationRepository(pool)
    now = T0 + timedelta(seconds=121)
    result = await repo.emit(BID, _candidate(), now=now, policy=POLICY)
    assert (result.outcome, result.suppressed_by) == (EmitOutcome.SUPPRESSED, suppressor)
    assert conn.fetchval.await_args.args == (SUPPRESSOR_SQL, "K", T0 - POLICY.window, now)
    assert conn.execute.await_args.args == (SUPPRESS_SQL, BID, suppressor, "agent_notice")


async def test_emit_inserts_the_notice_and_its_deliveries():
    pool, conn = _pool()
    cand = _candidate()
    conn.fetchrow.side_effect = [_burst_row(), _notice_row(cand)]
    conn.fetch.return_value = []  # no rules
    repo = PostgresArtifactPublicationRepository(pool)
    result = await repo.emit(BID, cand, now=T0, policy=POLICY)
    assert result.outcome is EmitOutcome.EMITTED and result.notification.id == cand.id
    assert conn.fetchrow.await_args_list[1].args[0] == INSERT_NOTIFICATION_SQL
    assert conn.execute.await_args.args == (EMITTED_SQL, BID, cand.id, T0)


async def test_emit_settles_onto_an_existing_notice():
    pool, conn = _pool()
    cand = _candidate()
    conn.fetchrow.side_effect = [_burst_row(), None]
    repo = PostgresArtifactPublicationRepository(pool)
    result = await repo.emit(BID, cand, now=T0, policy=POLICY)
    assert result.outcome is EmitOutcome.SETTLED
    assert conn.execute.await_args.args == (EMITTED_SQL, BID, cand.id, T0)


async def test_settle_takes_the_artifact_lock_first():
    pool, conn = _pool()
    repo = PostgresArtifactPublicationRepository(pool)
    assert await repo.settle(BID, reason="session_gone")
    assert [c.args for c in conn.execute.await_args_list] == [
        (ARTIFACT_FEED_LOCK_SQL,),
        (SETTLE_SQL, BID, "session_gone"),
    ]
    conn.execute.return_value = "UPDATE 0"
    assert not await repo.settle(BID, reason="session_gone")


async def test_emit_locks_in_order_before_the_suppression_recheck():
    pool, conn = _pool()
    conn.fetchrow.return_value = _burst_row()
    conn.fetchval.return_value = uuid.uuid4()
    repo = PostgresArtifactPublicationRepository(pool)
    await repo.emit(BID, _candidate(), now=T0, policy=POLICY)
    calls = [c.args[0] for c in conn.execute.await_args_list]
    assert calls[:2] == [ARTIFACT_FEED_LOCK_SQL, FEED_ORDER_LOCK_SQL]


async def test_deadlocks_are_retryable_at_ingest():
    pool, conn = _pool()
    conn.execute.side_effect = asyncpg.exceptions.DeadlockDetectedError("dl")
    repo = PostgresArtifactPublicationRepository(pool)
    with pytest.raises(ArtifactStoreUnavailableError):
        await repo.record("thor", [(candidate("K", "s", 1, T0), Origin.LIVE)], policy=POLICY)


async def test_scan_log_decodes_payloads():
    pool, _ = _pool()
    ts = datetime(2026, 10, 1, tzinfo=UTC)
    base = {"session_id": SID, "kind": "claude_hook", "role": None, "request_id": None, "ts": ts}
    pool.fetch.return_value = [
        {**base, "seq": 1, "payload": json.dumps({"type": "claude_hook"})},
        {**base, "seq": 2, "payload": {"type": "claude_hook"}},
        {**base, "seq": 3, "payload": "[1]"},
    ]
    repo = PostgresArtifactPublicationRepository(pool)
    rows = await repo.scan_log(LogCursor(SID, 0), limit=3, since=ts, until=None, session_id=None)
    assert [r.payload for r in rows] == [{"type": "claude_hook"}, {"type": "claude_hook"}, {}]
    assert pool.fetch.await_args.args == (SCAN_LOG_SQL, SID, 0, None, ts, None, 3)


def test_row_to_publication_reads_a_joined_burst():
    assert row_to_publication({**_pub_row(), "burst_id": BID}).burst_id == BID
    assert row_to_publication(_pub_row()).burst_id is None
    assert row_to_publication(_pub_row(), BID).wire()["burst_id"] == str(BID)

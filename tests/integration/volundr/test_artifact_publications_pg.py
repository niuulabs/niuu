"""F1/F1n on real PostgreSQL: the same oracles as the in-memory store (B1-B9, the model's
B1-B6, the 33/25 golden, the 230-row capture golden), plus the SQL-only paths: the
internal backfill scan, feed order, scoping and the emission transaction."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest

from tests.integration.pool_wrapper import TransactionalPool
from tests.support.artifacts import (
    POLICY,
    T0,
    FakeNotices,
    _ts,
    agent_notice,
    burst_notices,
    candidate,
    load,
    manifest_log_rows,
    run_golden,
    run_model_case,
    run_platform_case,
    service,
)
from volundr.adapters.outbound.postgres_artifacts import PostgresArtifactPublicationRepository
from volundr.domain.artifact_ports import EmitOutcome
from volundr.domain.artifacts import Origin
from volundr.domain.notifications import NotificationScope

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

PLATFORM = load("platform-bursts.json")["cases"]
MODEL = load("model-bursts.json")["cases"]


class _Repo(PostgresArtifactPublicationRepository):
    async def any_bridges(self) -> bool:
        return bool(
            await self._pool.fetchval(
                "SELECT count(*) FROM forge_artifact_burst_publications WHERE bridged <> '{}'"
            )
        )


async def _insert_notice(pool, notice) -> None:
    await pool.execute(
        """INSERT INTO forge_notifications (id, dedupe_key, session_id, owner_id, kind, severity,
             source, title, links, correlation_id, created_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11)""",
        notice.id,
        notice.dedupe_key,
        notice.session_id,
        notice.owner_id,
        notice.kind.value,
        notice.severity.value,
        notice.source.value,
        notice.title,
        json.dumps([link.model_dump(mode="json") for link in notice.links]),
        notice.correlation_id,
        notice.created_at,
    )


async def _insert_log(pool, rows) -> None:
    await pool.executemany(
        "INSERT INTO session_event_log (session_id, seq, kind, payload, ts) "
        "VALUES ($1, $2, $3, $4::jsonb, $5)",
        [(r.session_id, r.seq, r.kind, json.dumps(r.payload), r.ts) for r in rows],
    )


@pytest.fixture
def harness(txn_pool: TransactionalPool):
    async def make():
        return _Repo(txn_pool)

    async def restart(_repository):
        return _Repo(txn_pool)

    async def add_notice(_repository, notice):
        await _insert_notice(txn_pool, notice)

    return make, restart, add_notice


@pytest.mark.parametrize("case", PLATFORM, ids=[c["id"] for c in PLATFORM])
async def test_platform_burst_cases_pg(case, harness):
    make, restart, add_notice = harness
    assert await run_platform_case(case, make, restart, add_notice) == []


@pytest.mark.parametrize("case", MODEL, ids=[c["id"] for c in MODEL])
async def test_model_burst_cases_pg(case, harness):
    make, _, add_notice = harness
    assert await run_model_case(case, make, add_notice) == []


async def test_slice0_golden_pg(harness):
    make, _, add_notice = harness
    assert await run_golden(make, add_notice) == (33, 25)


@pytest.mark.parametrize("host,expected", [("thor", 122), ("mini-m6", 108)])
async def test_capture_backfill_pg(host, expected, txn_pool):
    manifest = load("capture-manifest-v1.json")
    await _insert_log(txn_pool, manifest_log_rows(manifest, host))
    repository = _Repo(txn_pool)
    notices = FakeNotices()
    svc = service(repository, host, notices)
    since, until = _ts(manifest["window"]["start"]), _ts(manifest["window"]["end"])

    report = await svc.backfill(since=since, until=until)

    rows = await repository.list_after(None, after=0, limit=10_000)
    assert report.recorded == expected == len(rows)
    want = {
        (p["session_id"], p["seq"], p["tool_use_id"], p["provider_version"], p["provider_seq"])
        for p in manifest["publications"]
        if p["host"] == host
    }
    got = {
        (str(r.session_id), r.session_seq, r.tool_use_id, r.provider_version, r.provider_seq)
        for r in rows
    }
    assert got == want
    assert [r.seq for r in rows] == sorted(r.seq for r in rows)
    assert (await svc.backfill(since=since, until=until)).recorded == 0
    assert await svc.emit_due(T0 + timedelta(days=3650)) == []
    assert await burst_notices(repository) == 0


async def test_emission_writes_notice_and_burst_in_one_transaction(txn_pool):
    repository = _Repo(txn_pool)
    notices = FakeNotices()
    svc = service(repository, notices=notices)
    [pub] = await svc.record([(candidate("K", "s1", 1, T0), Origin.LIVE)])
    assert await svc.emit_due(T0 + timedelta(seconds=119)) == []
    [result] = await svc.emit_due(T0 + timedelta(seconds=120))
    assert result.outcome is EmitOutcome.EMITTED
    burst = await repository.get_burst(pub.burst_id)
    stored = await txn_pool.fetchrow(
        "SELECT * FROM forge_notifications WHERE id = $1", burst.notification_id
    )
    assert stored["dedupe_key"] == f"artifact-burst:{pub.burst_id}"
    assert stored["correlation_id"] == f"art-burst:{pub.burst_id}"
    assert stored["source"] == "system"
    assert notices.broadcast[0].id == burst.notification_id
    # A second worker (or a restart) finds it settled.
    again = await repository.emit(
        pub.burst_id, _dummy_candidate(pub.burst_id), now=T0, policy=POLICY
    )
    assert again.outcome is EmitOutcome.SETTLED


def _dummy_candidate(burst_id):
    from niuu.domain.notifications import NotificationKind, NotificationSeverity, NotificationSource
    from volundr.domain.notifications import NotificationCandidate

    return NotificationCandidate(
        dedupe_key=f"artifact-burst:{burst_id}",
        owner_id="owner",
        kind=NotificationKind.MILESTONE,
        severity=NotificationSeverity.INFO,
        source=NotificationSource.SYSTEM,
        title="t",
    )


async def test_suppression_by_agent_notice_pg(txn_pool):
    repository = _Repo(txn_pool)
    svc = service(repository)
    [pub] = await svc.record([(candidate("Kx1", "s1", 1, T0), Origin.LIVE)])
    late = agent_notice("Kx1", T0 + timedelta(minutes=45), seq=1)  # outside the window
    other = agent_notice("Kx10", T0 + timedelta(seconds=10), seq=2)  # a different key
    system = agent_notice(
        "Kx1", T0 + timedelta(seconds=11), source="system", correlation_id="art-burst:other", seq=3
    )
    early = agent_notice("Kx1", T0 + timedelta(seconds=20), seq=4)
    for notice in (late, other, system, early):
        await _insert_notice(txn_pool, notice)
    [result] = await svc.emit_due(T0 + timedelta(seconds=121))
    assert result.outcome is EmitOutcome.SUPPRESSED
    assert result.suppressed_by == early.id
    burst = await repository.get_burst(pub.burst_id)
    assert (burst.suppressed_by, burst.suppress_reason) == (early.id, "agent_notice")


async def test_reads_are_owner_scoped_and_burst_feed_follows_changes(txn_pool):
    owner = f"owner-{uuid.uuid4().hex[:6]}"
    await txn_pool.execute(
        "INSERT INTO users (id, email) VALUES ($1, $2) ON CONFLICT DO NOTHING", owner, f"{owner}@x"
    )
    session_id = uuid.uuid4()
    await txn_pool.execute(
        "INSERT INTO sessions (id, name, model, owner_id) VALUES ($1, 'mine', 'm', $2)",
        session_id,
        owner,
    )
    repository = _Repo(txn_pool)
    svc = service(repository)
    await svc.record([(candidate("Own", str(session_id), 1, T0), Origin.LIVE)])
    await svc.record([(candidate("Theirs", "someone-else", 1, T0), Origin.LIVE)])
    mine = NotificationScope(user_id=owner, tenant_id="", is_admin=False)
    visible = await repository.list_after(mine, after=0, limit=100)
    assert [p.provider_key for p in visible] == ["Own"]
    revs = await repository.list_bursts_after(None, after=0, limit=100)
    before = max(rev for rev, _ in revs)
    await svc.record(
        [(candidate("Own", str(session_id), 2, T0 + timedelta(minutes=5)), Origin.LIVE)]
    )
    changed = await repository.list_bursts_after(mine, after=before, limit=100)
    assert [(b.provider_key, b.publications) for _, b in changed] == [("Own", 2)]

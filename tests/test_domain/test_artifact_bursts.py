"""F1n against the control plane's oracles, on the in-memory store (same rule as Postgres).

* platform B1-B9 (``platform/api/fixtures/bursts.json``);
* the model's B1-B6 (``model/acceptance/cases/bursts.json``);
* the Slice 0 golden: Thor's manifest gives 33 bursts and 25 notices;
* the capture golden: the backfill reproduces the manifest's 230 rows and 26 keys exactly,
  a second replay inserts 0, and backfilled bursts never notify.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.support.artifacts import (
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
from volundr.adapters.outbound.memory_artifacts import InMemoryArtifactPublicationRepository
from volundr.domain.artifact_ports import EmitOutcome
from volundr.domain.artifacts import Origin

PLATFORM = load("platform-bursts.json")["cases"]
MODEL = load("model-bursts.json")["cases"]


async def _make():
    return InMemoryArtifactPublicationRepository()


async def _restart(repository):
    return repository.restarted()


async def _add_notice(repository, notice):
    repository.add_notice(notice)


@pytest.mark.parametrize("case", PLATFORM, ids=[c["id"] for c in PLATFORM])
async def test_platform_burst_cases(case):
    assert await run_platform_case(case, _make, _restart, _add_notice) == []


@pytest.mark.parametrize("case", MODEL, ids=[c["id"] for c in MODEL])
async def test_model_burst_cases(case):
    assert await run_model_case(case, _make, _add_notice) == []


async def test_slice0_golden_33_bursts_25_notices():
    golden = load("platform-bursts.json")["golden"]["expect"]
    assert await run_golden(_make, _add_notice) == (golden["bursts"], golden["notices"])


async def test_golden_without_the_hold_would_notify_every_burst():
    """The measured offsets are what the 120 s hold suppresses: none land before publish."""
    replay = load("slice0-burst-replay.json")
    assert replay["bursts"] == 33
    assert replay["human_notice_before_first_publish"] == 0
    assert replay["human_notice_within_120s_after"] == 8


@pytest.mark.parametrize("host,expected", [("thor", 122), ("mini-m6", 108)])
async def test_capture_backfill_reproduces_the_manifest(host, expected):
    manifest = load("capture-manifest-v1.json")
    repository = InMemoryArtifactPublicationRepository()
    repository.add_log_rows(manifest_log_rows(manifest, host))
    notices = FakeNotices()
    svc = service(repository, host, notices)
    window = manifest["window"]
    since, until = _ts(window["start"]), _ts(window["end"])

    report = await svc.backfill(since=since, until=until)

    rows = await repository.list_after(None, after=0, limit=10_000)
    assert report.recorded == expected == len(rows)
    want = {
        (
            p["session_id"],
            p["seq"],
            p["tool_use_id"],
            p["provider_key"],
            p["provider_version"],
            p["provider_seq"],
            p["subagent"],
        )
        for p in manifest["publications"]
        if p["host"] == host
    }
    got = {
        (
            str(r.session_id),
            r.session_seq,
            r.tool_use_id,
            r.provider_key,
            r.provider_version,
            r.provider_seq,
            r.subagent,
        )
        for r in rows
    }
    assert got == want
    assert all(r.origin is Origin.BACKFILL and r.host == host for r in rows)
    # A second full replay inserts nothing.
    assert (await svc.backfill(since=since, until=until)).recorded == 0
    # Backfilled bursts are silent: no notice, however late the clock.
    assert await svc.emit_due(T0 + timedelta(days=3650)) == []
    assert await burst_notices(repository) == 0
    assert notices.broadcast == []


async def test_capture_manifest_has_26_keys_across_both_hosts():
    manifest = load("capture-manifest-v1.json")
    keys: set[str] = set()
    for host in ("thor", "mini-m6"):
        repository = InMemoryArtifactPublicationRepository()
        repository.add_log_rows(manifest_log_rows(manifest, host))
        svc = service(repository, host)
        window = manifest["window"]
        await svc.backfill(since=_ts(window["start"]), until=_ts(window["end"]))
        keys |= {r.provider_key for r in await repository.list_after(None, after=0, limit=10_000)}
    assert len(keys) == manifest["expect"]["distinct_provider_keys"] == 26


async def test_restart_during_hold_emits_once_and_broadcasts_once():
    repository = InMemoryArtifactPublicationRepository()
    notices = FakeNotices()
    svc = service(repository, notices=notices)
    await svc.record([(candidate("K", "s1", 1, T0), Origin.LIVE)])
    assert await svc.emit_due(T0 + timedelta(seconds=60)) == []
    restarted = repository.restarted()
    svc = service(restarted, notices=notices)
    first = await svc.emit_due(T0 + timedelta(seconds=121))
    again = await service(restarted.restarted(), notices=notices).emit_due(T0 + timedelta(hours=1))
    assert [r.outcome for r in first] == [EmitOutcome.EMITTED]
    assert again == []
    assert len(notices.broadcast) == 1
    notice = notices.broadcast[0]
    assert notice.title == "Artifact K published"
    assert notice.correlation_id.startswith("art-burst:")
    assert notice.dedupe_key.startswith("artifact-burst:")
    assert notice.links[0].url == "https://claude.ai/artifact/K"
    assert notice.links[0].kind.value == "artifact"


async def test_suppression_records_the_announcing_notice():
    repository = InMemoryArtifactPublicationRepository()
    svc = service(repository)
    await svc.record([(candidate("K", "s1", 1, T0), Origin.LIVE)])
    notice = agent_notice("K", T0 + timedelta(seconds=30), seq=1)
    repository.add_notice(notice)
    # Slice 0's own system notices never suppress.
    repository.add_notice(
        agent_notice(
            "K", T0 + timedelta(seconds=31), source="system", correlation_id="art-burst:x", seq=2
        )
    )
    [result] = await svc.emit_due(T0 + timedelta(seconds=120))
    assert result.outcome is EmitOutcome.SUPPRESSED
    assert result.suppressed_by == notice.id
    [burst] = repository.bursts
    assert burst.suppressed_by == notice.id
    assert burst.suppress_reason == "agent_notice"

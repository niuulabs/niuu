"""Retention: run and session layers are deleted after their retention (C7, decision 1)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from mimir.adapters.forge_lifetimes import ForgeLayerLifetimes
from mimir.adapters.gbrain_layers import source_id_of
from mimir.memory.gateway import MemoryGateway
from mimir.memory.retention import SWEEP_PATH, RetentionSweeper
from mimir.memory.systems import MemorySystems
from mimir.ports.layer_lifetimes import (
    LayerLifetimes,
    LifeState,
    Lifetime,
    Lifetimes,
    LifetimesUnavailableError,
)
from niuu.adapters.outbound.http_auth import NoAuthHeaderAdapter, StaticBearerTokenAuthAdapter
from niuu.cap_telemetry import degraded_counts, reset_cap_hits
from tests.test_mimir.unit.memory_fakes import PROJECT_ID, FakeGBrain, context, gbrain_backend

FORGE = "http://forge.test"
ENDED_URL = f"{FORGE}/api/v1/forge/memory/ended"
NOW = datetime(2026, 11, 30, tzinfo=UTC)
OLD = "5e551011-0000-4000-8000-0000000000a1"
RECENT = "5e551011-0000-4000-8000-0000000000a2"
ACTIVE = "5e551011-0000-4000-8000-0000000000a3"
GONE = "5e551011-0000-4000-8000-0000000000a4"
RUN = "a1b2c3d4-0000-4000-8000-000000000123"


@pytest.fixture(autouse=True)
def _reset_caps():
    reset_cap_hits()


class FakeLifetimes(LayerLifetimes):
    def __init__(self, sessions: dict[str, Lifetime], runs: dict[str, Lifetime]) -> None:
        self.sessions, self.runs = sessions, runs
        self.asked: list[tuple[list[str], list[str]]] = []
        self.fail = False

    async def ended(self, sessions: list[str], runs: list[str]) -> Lifetimes:
        self.asked.append((sessions, runs))
        if self.fail:
            raise LifetimesUnavailableError("Forge answered 503 on /api/v1/forge/memory/ended")
        return Lifetimes(
            sessions={s: self.sessions[s] for s in sessions}, runs={r: self.runs[r] for r in runs}
        )


@pytest.fixture
def brain():
    fake = FakeGBrain()
    with respx.mock(assert_all_called=False) as router:
        fake.mount(router)
        yield fake


@pytest.fixture
async def gateway(brain):
    systems = MemorySystems({"acme-brain": gbrain_backend()}, {"acme": "acme-brain"})
    yield MemoryGateway(systems, default_result_count=10)
    await systems.close()


async def _session_with_a_page(gateway, session_id: str, *, run: str | None = None) -> None:
    session = await gateway.open(replace(context(session_id), workflow_run_id=run))
    await gateway.write_page(
        session, "findings/x", compiled_truth="found", expected_revision=None, layer="session"
    )
    if run:
        await gateway.write_page(
            session, "plan/x", compiled_truth="plan", expected_revision=None, layer="run"
        )


def _ended(days_ago: float) -> Lifetime:
    return Lifetime(LifeState.ENDED, NOW - timedelta(days=days_ago))


def _sweeper(gateway, lifetimes, forgotten=None) -> RetentionSweeper:
    return RetentionSweeper(
        gateway.systems,
        lifetimes,
        retention_days=14,
        interval_seconds=60,
        forget=lambda system, ns: (forgotten if forgotten is not None else []).append(ns),
        clock=lambda: NOW,
    )


def _live(brain: FakeGBrain, prefix: str) -> list[str]:
    return [s for s, p in brain.pages.items() if not p["deleted"] and s.startswith(prefix)]


class TestSweep:
    async def test_expired_and_gone_layers_go_and_the_rest_stay(self, brain, gateway) -> None:
        for session_id in (OLD, RECENT, ACTIVE, GONE):
            await _session_with_a_page(gateway, session_id, run=RUN if session_id == OLD else None)
        lifetimes = FakeLifetimes(
            {
                OLD: _ended(15),
                RECENT: _ended(3),
                ACTIVE: Lifetime(LifeState.ACTIVE),
                GONE: Lifetime(LifeState.GONE),
            },
            {RUN: _ended(20)},
        )
        forgotten: list[str] = []
        reports = await _sweeper(gateway, lifetimes, forgotten).sweep()
        assert lifetimes.asked == [(sorted([OLD, RECENT, ACTIVE, GONE]), [RUN])]
        assert sorted(reports[0].deleted) == sorted(
            [f"session-{OLD}", f"session-{GONE}", f"run-{RUN}"]
        )
        assert sorted(reports[0].kept) == sorted([f"session:{RECENT}", f"session:{ACTIVE}"])
        assert _live(brain, f"session-{OLD}/") == []
        assert _live(brain, f"run-{RUN}/") == []
        assert _live(brain, f"session-{GONE}/") == []
        assert _live(brain, f"session-{RECENT}/")
        assert _live(brain, f"session-{ACTIVE}/")
        # long-lived layers are never swept
        assert source_id_of(f"project-{PROJECT_ID}") in brain.sources
        assert sorted(forgotten) == sorted(reports[0].deleted)

    async def test_a_layer_ended_exactly_at_retention_goes(self, brain, gateway) -> None:
        await _session_with_a_page(gateway, OLD)
        lifetimes = FakeLifetimes({OLD: _ended(14)}, {})
        reports = await _sweeper(gateway, lifetimes).sweep()
        assert reports[0].deleted == [f"session-{OLD}"]

    async def test_a_forge_failure_deletes_nothing_and_is_counted(self, brain, gateway) -> None:
        await _session_with_a_page(gateway, OLD)
        lifetimes = FakeLifetimes({OLD: _ended(30)}, {})
        lifetimes.fail = True
        sweeper = _sweeper(gateway, lifetimes)
        assert await sweeper.sweep() == []
        assert _live(brain, f"session-{OLD}/")
        assert degraded_counts()[SWEEP_PATH] == 1
        assert sweeper.failed_sweeps == 1

    async def test_a_system_that_cannot_be_listed_is_counted(self, brain, gateway) -> None:
        brain.fail_paths.add("/admin/api/sources")
        sweeper = _sweeper(gateway, FakeLifetimes({}, {}))
        assert await sweeper.sweep() == []
        assert degraded_counts()[SWEEP_PATH] == 1

    async def test_nothing_ephemeral_asks_forge_nothing(self, brain, gateway) -> None:
        await gateway.open(context())
        lifetimes = FakeLifetimes({}, {})
        await gateway.systems.items()[0][1].delete_namespace(f"session-{context().session_id}")
        reports = await _sweeper(gateway, lifetimes).sweep()
        assert lifetimes.asked == [] and reports[0].deleted == []

    async def test_the_loop_survives_a_failure_and_sweeps_again(self, brain, gateway) -> None:
        lifetimes = FakeLifetimes({}, {})
        sweeper = RetentionSweeper(
            gateway.systems,
            lifetimes,
            retention_days=14,
            interval_seconds=0.01,
            forget=lambda system, ns: None,
            clock=lambda: NOW,
        )
        calls = 0
        original = sweeper.sweep

        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")
            return await original()

        sweeper.sweep = flaky  # type: ignore[method-assign]
        task = asyncio.create_task(sweeper.run())
        while calls < 2:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert degraded_counts()[SWEEP_PATH] == 1

    def test_settings_are_checked(self, gateway) -> None:
        with pytest.raises(ValueError, match="retention_sweep_seconds"):
            RetentionSweeper(
                gateway.systems,
                FakeLifetimes({}, {}),
                retention_days=14,
                interval_seconds=0,
                forget=lambda s, n: None,
            )


class TestForgeLayerLifetimes:
    @respx.mock
    async def test_forge_is_asked_with_the_service_credential(self) -> None:
        route = respx.post(ENDED_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "sessions": {
                        OLD: {"state": "ended", "ended_at": "2026-11-01T10:00:00Z"},
                        GONE: {"state": "gone", "ended_at": None},
                    },
                    "runs": {RUN: {"state": "active", "ended_at": None}},
                },
            )
        )
        lifetimes = ForgeLayerLifetimes(
            FORGE, timeout_seconds=5, service_auth=StaticBearerTokenAuthAdapter(token="svc")
        )
        found = await lifetimes.ended([OLD, GONE], [RUN])
        await lifetimes.close()
        assert route.calls.last.request.headers["authorization"] == "Bearer svc"
        assert found.sessions[OLD] == Lifetime(
            LifeState.ENDED, datetime(2026, 11, 1, 10, tzinfo=UTC)
        )
        assert found.sessions[GONE].state is LifeState.GONE
        assert found.runs[RUN].state is LifeState.ACTIVE

    @pytest.mark.parametrize(
        ("response", "match"),
        [
            (httpx.Response(401), "service credential"),
            (httpx.Response(500), "500"),
            (httpx.Response(200, json={"sessions": {}, "runs": {}}), "does not name"),
            (
                httpx.Response(200, json={"sessions": {OLD: {"state": "ended"}}, "runs": {}}),
                "ended_at",
            ),
            (
                httpx.Response(200, json={"sessions": {OLD: {"state": "paused"}}, "runs": {}}),
                "paused",
            ),
        ],
    )
    @respx.mock
    async def test_an_unusable_answer_is_an_error(self, response, match) -> None:
        respx.post(ENDED_URL).mock(return_value=response)
        lifetimes = ForgeLayerLifetimes(
            FORGE, timeout_seconds=5, service_auth=NoAuthHeaderAdapter()
        )
        with pytest.raises(LifetimesUnavailableError, match=match):
            await lifetimes.ended([OLD], [])
        await lifetimes.close()

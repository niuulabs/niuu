"""Tests for the server-side instance health checker."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from niuu.domain.models import (
    InstanceHealthStatus,
    InstanceKind,
    InstanceVisibility,
    RegisteredInstance,
)
from niuu.domain.services.instance_health import InstanceHealthChecker
from niuu.ports.instance_probe import InstanceProbeResult


def _instance(
    instance_id: str = "instance-1",
    *,
    last_seen_at: datetime | None = None,
) -> RegisteredInstance:
    now = datetime.now(UTC)
    return RegisteredInstance(
        id=instance_id,
        kind=InstanceKind.VOLUNDR,
        slug=instance_id,
        name=f"Instance {instance_id}",
        base_url=f"https://{instance_id}.example.com",
        visibility=InstanceVisibility.SYSTEM,
        owner_id=None,
        tenant_id=None,
        enabled=True,
        is_default=False,
        config={},
        created_at=now,
        updated_at=now,
        last_seen_at=last_seen_at,
    )


class StubProbe:
    def __init__(self, result: InstanceProbeResult | dict[str, InstanceProbeResult]) -> None:
        self._result = result
        self.probed_ids: list[str] = []

    async def probe(self, instance: RegisteredInstance) -> InstanceProbeResult:
        self.probed_ids.append(instance.id)
        if isinstance(self._result, dict):
            return self._result[instance.id]
        return self._result


class FakeRepository:
    def __init__(self, instances: list[RegisteredInstance] | None = None) -> None:
        self._instances = list(instances or [])
        self.recorded: list[dict[str, object]] = []

    async def list_instances(self, kind=None):
        return list(self._instances)

    async def record_health(
        self, instance_id, *, health, last_seen_at, last_checked_at, last_error
    ):
        self.recorded.append(
            {
                "instance_id": instance_id,
                "health": health,
                "last_seen_at": last_seen_at,
                "last_checked_at": last_checked_at,
                "last_error": last_error,
            }
        )


@pytest.mark.asyncio
async def test_check_instance_records_ok_and_advances_last_seen_at() -> None:
    checked_at = datetime(2026, 1, 1, tzinfo=UTC)
    probe = StubProbe(InstanceProbeResult(ok=True, status_code=200, message="reachable"))
    repo = FakeRepository()
    checker = InstanceHealthChecker(
        repository=repo, probe=probe, interval_seconds=30.0, now=lambda: checked_at
    )

    result = await checker.check_instance(_instance())

    assert result.health == InstanceHealthStatus.OK
    assert result.last_seen_at == checked_at
    assert result.last_error is None
    assert repo.recorded == [
        {
            "instance_id": "instance-1",
            "health": InstanceHealthStatus.OK,
            "last_seen_at": checked_at,
            "last_checked_at": checked_at,
            "last_error": None,
        }
    ]


@pytest.mark.asyncio
async def test_check_instance_on_failure_records_unreachable_and_keeps_prior_last_seen_at() -> None:
    """A failed probe must not read as "never seen" — the prior last-seen
    timestamp answers "when did it last work", which a failed probe does not
    change (see .claude/rules/no-fallbacks.md)."""
    previously_seen = datetime(2025, 12, 31, tzinfo=UTC)
    checked_at = datetime(2026, 1, 1, tzinfo=UTC)
    probe = StubProbe(InstanceProbeResult(ok=False, status_code=None, message="refused"))
    repo = FakeRepository()
    checker = InstanceHealthChecker(
        repository=repo, probe=probe, interval_seconds=30.0, now=lambda: checked_at
    )

    result = await checker.check_instance(_instance(last_seen_at=previously_seen))

    assert result.health == InstanceHealthStatus.UNREACHABLE
    assert result.last_seen_at == previously_seen
    assert result.last_error == "refused"
    assert repo.recorded[0]["last_seen_at"] == previously_seen
    # last_checked_at always advances — it answers "when did we last look",
    # unlike last_seen_at which only advances on success.
    assert repo.recorded[0]["last_checked_at"] == checked_at


@pytest.mark.asyncio
async def test_check_instance_falls_back_to_a_generic_message_when_the_probe_gives_none() -> None:
    probe = StubProbe(InstanceProbeResult(ok=False, status_code=None, message=""))
    repo = FakeRepository()
    checker = InstanceHealthChecker(repository=repo, probe=probe, interval_seconds=30.0)

    result = await checker.check_instance(_instance())

    assert result.last_error == "Health probe failed"


@pytest.mark.asyncio
async def test_check_all_probes_every_registered_instance() -> None:
    instances = [_instance("a"), _instance("b")]
    probe = StubProbe(InstanceProbeResult(ok=True, status_code=200, message="ok"))
    repo = FakeRepository(instances)
    checker = InstanceHealthChecker(repository=repo, probe=probe, interval_seconds=30.0)

    results = await checker.check_all()

    assert sorted(probe.probed_ids) == ["a", "b"]
    assert len(results) == 2


@pytest.mark.asyncio
async def test_check_all_records_unreachable_for_a_crashing_probe_instead_of_leaving_it_stale() -> (
    None
):
    """InstanceProbePort.probe() is documented to never raise, but a
    misbehaving or third-party adapter (health.probe.adapter is a dynamic
    import) might anyway. The old behavior let check_instance's exception
    propagate out to check_all's return_exceptions=True gather, which logged
    and skipped it — record_health was never called, so a previously "ok"
    instance stayed "ok" in the database while actually unreachable. It must
    be recorded UNREACHABLE instead."""

    class FlakyProbe:
        def __init__(self) -> None:
            self.probed_ids: list[str] = []

        async def probe(self, instance: RegisteredInstance) -> InstanceProbeResult:
            self.probed_ids.append(instance.id)
            if instance.id == "broken":
                raise RuntimeError("boom")
            return InstanceProbeResult(ok=True, status_code=200, message="ok")

    instances = [_instance("broken"), _instance("fine")]
    repo = FakeRepository(instances)
    checker = InstanceHealthChecker(repository=repo, probe=FlakyProbe(), interval_seconds=30.0)

    results = await checker.check_all()

    assert len(results) == 2
    recorded = {entry["instance_id"]: entry for entry in repo.recorded}
    assert recorded["broken"]["health"] == InstanceHealthStatus.UNREACHABLE
    assert recorded["broken"]["last_error"] == "boom"
    assert recorded["fine"]["health"] == InstanceHealthStatus.OK


async def test_check_instance_records_unreachable_when_the_probe_port_itself_raises() -> None:
    class CrashingProbe:
        async def probe(self, instance: RegisteredInstance) -> InstanceProbeResult:
            raise ValueError("")  # blank message — must still produce a real reason

    repo = FakeRepository()
    checker = InstanceHealthChecker(repository=repo, probe=CrashingProbe(), interval_seconds=30.0)

    result = await checker.check_instance(_instance())

    assert result.health == InstanceHealthStatus.UNREACHABLE
    assert result.last_error == "ValueError"  # falls back to the class name, never blank


def test_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="interval_seconds"):
        InstanceHealthChecker(
            repository=FakeRepository(),
            probe=StubProbe(InstanceProbeResult(ok=True, status_code=200, message="ok")),
            interval_seconds=0,
        )


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_cancels_the_loop_cleanly() -> None:
    repo = FakeRepository()
    probe = StubProbe(InstanceProbeResult(ok=True, status_code=200, message="ok"))
    checker = InstanceHealthChecker(repository=repo, probe=probe, interval_seconds=30.0)

    checker.start()
    first_task = checker._task
    checker.start()  # idempotent — does not replace the running task

    assert checker._task is first_task
    assert not first_task.done()

    await checker.stop()

    assert checker._task is None
    assert first_task.cancelled() or first_task.done()

    # Stopping again is a no-op, not an error.
    await checker.stop()


@pytest.mark.asyncio
async def test_loop_sweeps_immediately_then_on_each_interval() -> None:
    """A fresh registration's health must not sit at "unknown" for a full
    interval after Guild restarts, so the loop sweeps once at start, then on
    every interval after that. The sleep function is injected rather than
    monkeypatching asyncio.sleep, so this only ever intercepts this
    checker's own waits."""
    instances = [_instance("a")]
    probe = StubProbe(InstanceProbeResult(ok=True, status_code=200, message="ok"))
    repo = FakeRepository(instances)

    sleep_calls: list[float] = []
    released = asyncio.Event()
    gate = asyncio.Event()

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        released.set()
        await gate.wait()
        gate.clear()

    checker = InstanceHealthChecker(
        repository=repo, probe=probe, interval_seconds=30.0, sleep=_fake_sleep
    )
    checker.start()

    await asyncio.wait_for(released.wait(), timeout=1.0)
    released.clear()
    assert probe.probed_ids.count("a") == 1
    assert sleep_calls == [30.0]

    gate.set()
    await asyncio.wait_for(released.wait(), timeout=1.0)
    assert probe.probed_ids.count("a") == 2
    assert sleep_calls == [30.0, 30.0]

    await checker.stop()


@pytest.mark.asyncio
async def test_loop_logs_and_continues_after_an_unexpected_sweep_crash() -> None:
    """check_all() itself should never raise in practice (check_instance
    already turns probe crashes into UNREACHABLE results), but if the
    repository's list_instances() call fails — e.g. a database blip — the
    loop must survive to try again next interval, not die silently."""
    repo = FakeRepository()

    async def _broken_list_instances(kind=None):
        raise RuntimeError("db unavailable")

    repo.list_instances = _broken_list_instances  # type: ignore[method-assign]
    probe = StubProbe(InstanceProbeResult(ok=True, status_code=200, message="ok"))

    sleep_calls: list[float] = []
    gate = asyncio.Event()

    async def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        await gate.wait()
        gate.clear()

    checker = InstanceHealthChecker(
        repository=repo, probe=probe, interval_seconds=30.0, sleep=_fake_sleep
    )
    checker.start()

    # The crashed first sweep still reaches the sleep call — the loop is
    # alive, not dead.
    await asyncio.wait_for(_wait_for(lambda: len(sleep_calls) >= 1), timeout=1.0)
    assert sleep_calls == [30.0]

    gate.set()
    await asyncio.wait_for(_wait_for(lambda: len(sleep_calls) >= 2), timeout=1.0)
    assert sleep_calls == [30.0, 30.0]

    await checker.stop()


async def _wait_for(condition, *, poll_interval: float = 0.01) -> None:
    while not condition():
        await asyncio.sleep(poll_interval)


@pytest.mark.asyncio
async def test_check_all_skips_disabled_instances() -> None:
    """A disabled instance is already excluded from environment selectors —
    checking it too would spend a probe on something nothing uses, and could
    misreport an intentionally-retired node as "unreachable"."""
    enabled = _instance("enabled-1")
    disabled = replace(_instance("disabled-1"), enabled=False)
    probe = StubProbe(InstanceProbeResult(ok=True, status_code=200, message="ok"))
    repo = FakeRepository([enabled, disabled])
    checker = InstanceHealthChecker(repository=repo, probe=probe, interval_seconds=30.0)

    results = await checker.check_all()

    assert probe.probed_ids == ["enabled-1"]
    assert len(results) == 1
    assert [entry["instance_id"] for entry in repo.recorded] == ["enabled-1"]

from __future__ import annotations

import asyncio

import pytest

from ting.adapters.workflow_execution_worker import WorkflowExecutionWorker


class TransientService:
    def __init__(self) -> None:
        self.launch_calls = 0
        self.reconcile_calls = 0
        self.recovered = asyncio.Event()
        self.calls: list[str] = []

    async def launch_ready(self) -> int:
        self.calls.append("launch")
        self.launch_calls += 1
        if self.launch_calls == 1:
            raise ConnectionError("database connection reset")
        self.recovered.set()
        return 0

    async def reconcile(self) -> dict[str, int]:
        self.calls.append("reconcile")
        self.reconcile_calls += 1
        return {"projected": 0, "executions": 0}


@pytest.mark.asyncio
async def test_worker_survives_transient_cycle_and_retries() -> None:
    service = TransientService()
    worker = WorkflowExecutionWorker(services=[service], interval_seconds=0.001)

    await worker.start()
    await asyncio.wait_for(service.recovered.wait(), timeout=1)
    await worker.stop()

    assert service.launch_calls >= 2
    assert service.reconcile_calls >= 2
    assert service.calls[:4] == ["reconcile", "launch", "reconcile", "launch"]
    assert worker.running is False


def test_worker_rejects_nonpositive_interval() -> None:
    with pytest.raises(ValueError, match="interval"):
        WorkflowExecutionWorker(services=[TransientService()], interval_seconds=0)


def test_worker_rejects_empty_service_list() -> None:
    with pytest.raises(ValueError, match="at least one service"):
        WorkflowExecutionWorker(services=[], interval_seconds=1)


class AlwaysFailingReconcileService:
    """reconcile() always raises; launch_ready() must still run every cycle."""

    def __init__(self) -> None:
        self.launch_calls = 0
        self.reconcile_calls = 0

    async def reconcile(self) -> dict[str, int]:
        self.reconcile_calls += 1
        raise RuntimeError("reconcile is poisoned")

    async def launch_ready(self) -> int:
        self.launch_calls += 1
        return 0


class RecordingWaitService:
    def __init__(self) -> None:
        self.calls = 0

    async def reconcile(self) -> int:
        self.calls += 1
        return 0


@pytest.mark.asyncio
async def test_worker_runs_every_phase_even_when_an_earlier_phase_raises() -> None:
    service = AlwaysFailingReconcileService()
    wait_service = RecordingWaitService()
    worker = WorkflowExecutionWorker(
        services=[service],
        interval_seconds=0.001,
        wait_service=wait_service,
    )

    await worker.start()
    for _ in range(200):
        if service.launch_calls >= 2 and wait_service.calls >= 2:
            break
        await asyncio.sleep(0.005)
    await worker.stop()

    assert service.reconcile_calls >= 2
    assert service.launch_calls >= 2
    assert wait_service.calls >= 2


@pytest.mark.asyncio
async def test_worker_reconciles_every_configured_service_each_cycle() -> None:
    """A deployment with both the generic pack and the delivery specialization

    enabled supplies one service per pack; the worker must drive each one's
    own reconcile/launch_ready every cycle rather than picking just one, so
    a purely generic execution and a delivery execution are both serviced
    by the composition root's own repository-scoped service in one worker.
    """
    generic_service = TransientService()
    generic_service.launch_calls = 1  # skip the induced-failure branch
    delivery_service = TransientService()
    delivery_service.launch_calls = 1
    worker = WorkflowExecutionWorker(
        services=[generic_service, delivery_service],
        interval_seconds=0.001,
    )

    await worker.start()
    for _ in range(200):
        if generic_service.reconcile_calls >= 2 and delivery_service.reconcile_calls >= 2:
            break
        await asyncio.sleep(0.005)
    await worker.stop()

    assert generic_service.reconcile_calls >= 2
    assert delivery_service.reconcile_calls >= 2
    assert generic_service.launch_calls >= 2
    assert delivery_service.launch_calls >= 2

from __future__ import annotations

import asyncio

import pytest

from ting.adapters.developer_execution_worker import DeveloperExecutionWorker


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
    worker = DeveloperExecutionWorker(service=service, interval_seconds=0.001)

    await worker.start()
    await asyncio.wait_for(service.recovered.wait(), timeout=1)
    await worker.stop()

    assert service.launch_calls >= 2
    assert service.reconcile_calls >= 2
    assert service.calls[:4] == ["reconcile", "launch", "reconcile", "launch"]
    assert worker.running is False


def test_worker_rejects_nonpositive_interval() -> None:
    with pytest.raises(ValueError, match="interval"):
        DeveloperExecutionWorker(service=TransientService(), interval_seconds=0)

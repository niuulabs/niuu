"""Tests for the resident Environment signal runtime."""

from __future__ import annotations

import pytest

from ravn.config import EnvironmentConfig, Settings, SignalSourceConfig
from ravn.domain.models import AgentTask
from ravn.environment_signal_runtime import (
    EnvironmentSignalRuntime,
    build_runtime_environment,
)
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent


def _settings() -> Settings:
    return Settings(
        environment=EnvironmentConfig(
            id="host-jozef",
            name="Jozef Host",
            type="host",
            flocks=["host-valkyries"],
            signal_poll_interval_seconds=0.01,
            signal_sources=[
                SignalSourceConfig(
                    id="host-events",
                    name="Host Events",
                    kind="host",
                    adapter="ravn.adapters.environment_signals.HostSignalAdapter",
                    kwargs={
                        "raw_items": [
                            {
                                "id": "disk-full",
                                "host_id": "jozef-host",
                                "category": "disk",
                                "severity": "critical",
                                "message": "Disk usage crossed 95%",
                                "observed_at": "2026-06-03T12:14:00Z",
                            }
                        ]
                    },
                )
            ],
        )
    )


def test_build_runtime_environment_reuses_configured_flocks_and_sources() -> None:
    environment = build_runtime_environment(_settings())

    assert environment.id == "host-jozef"
    assert environment.topology.type_id == "host"
    assert environment.flock_ids == ["host-valkyries"]
    assert [source.id for source in environment.signal_sources] == ["host-events"]


@pytest.mark.asyncio
async def test_runtime_publishes_and_enqueues_deduped_signal_tasks() -> None:
    bus = InProcessBus()
    received: list[SleipnirEvent] = []
    enqueued: list[AgentTask] = []
    await bus.subscribe(["signal.*"], lambda event: _record(received, event))
    runtime = EnvironmentSignalRuntime(
        settings=_settings(),
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
    )

    first_count = await runtime.collect_once()
    await bus.flush()
    second_count = await runtime.collect_once()
    await bus.flush()

    assert first_count == 1
    assert second_count == 0
    assert [event.event_type for event in received] == ["signal.host.event"]
    assert received[0].payload["environment_id"] == "host-jozef"
    assert len(enqueued) == 1
    assert enqueued[0].triggered_by == "signal:signal.host.event"
    assert enqueued[0].root_correlation_id == received[0].correlation_id
    assert "Use existing tools and memory" in enqueued[0].initiative_context


async def _record(events: list[SleipnirEvent], event: SleipnirEvent) -> None:
    events.append(event)


async def _enqueue(tasks: list[AgentTask], task: AgentTask) -> None:
    tasks.append(task)

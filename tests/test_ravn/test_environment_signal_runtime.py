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
    settings = Settings(
        environment=EnvironmentConfig(
            id="host-jozef",
            name="Jozef Host",
            type="host",
            resident_name="Sigrun",
            resident_personality=(
                "Quietly skeptical and evidence-first; escalate only with crisp context."
            ),
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
    settings.mesh.own_peer_id = "valkyrie-host-jozef"
    return settings


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
    telemetry: list[SleipnirEvent] = []
    enqueued: list[AgentTask] = []
    await bus.subscribe(["signal.*"], lambda event: _record(received, event))
    await bus.subscribe(["valkyrie.signal_poll.completed"], lambda event: _record(telemetry, event))
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
    assert [event.event_type for event in telemetry] == [
        "valkyrie.signal_poll.completed",
        "valkyrie.signal_poll.completed",
    ]
    assert telemetry[0].payload["environment_id"] == "host-jozef"
    assert telemetry[0].payload["valkyrie_id"] == "valkyrie-host-jozef"
    assert telemetry[0].payload["source_id"] == "host-events"
    assert telemetry[0].payload["collected_count"] == 1
    assert telemetry[0].payload["new_count"] == 1
    assert telemetry[0].payload["duplicate_count"] == 0
    assert telemetry[0].payload["published_count"] == 1
    assert telemetry[0].payload["enqueued_task_count"] == 1
    assert telemetry[0].payload["resident_learning_checked_count"] == 0
    assert telemetry[0].payload["resident_learning_used_count"] == 0
    assert telemetry[0].payload["drive_loop_enabled"] is True
    assert telemetry[0].payload["severity_counts"] == {"critical": 1}
    assert telemetry[0].payload["nats_subject"] == (
        "ravn.environment.valkyrie.signal_poll.completed"
    )
    assert telemetry[1].payload["collected_count"] == 1
    assert telemetry[1].payload["new_count"] == 0
    assert telemetry[1].payload["duplicate_count"] == 1
    assert telemetry[1].payload["published_count"] == 0
    assert telemetry[1].payload["enqueued_task_count"] == 0
    assert len(enqueued) == 1
    assert enqueued[0].triggered_by == "signal:signal.host.event"
    assert enqueued[0].root_correlation_id == received[0].correlation_id
    assert "Resident Valkyrie: Sigrun" in enqueued[0].initiative_context
    assert "Resident peer id: valkyrie-host-jozef" in enqueued[0].initiative_context
    assert "Quietly skeptical and evidence-first" in enqueued[0].initiative_context
    assert "Use existing tools, memory" in enqueued[0].initiative_context
    assert "resident skills/runbooks" in enqueued[0].initiative_context
    assert "skill_list and skill_run" in enqueued[0].initiative_context
    assert "call build_tool with a manifest" in enqueued[0].initiative_context
    assert "use the newly registered tool" in enqueued[0].initiative_context
    assert "note the review item" in enqueued[0].initiative_context
    assert "Schema compliance is mandatory" in enqueued[0].initiative_context
    assert "environment_id: host-jozef" in enqueued[0].initiative_context
    assert "valkyrie_id: valkyrie-host-jozef" in enqueued[0].initiative_context
    assert "correlation_ids:" in enqueued[0].initiative_context


@pytest.mark.asyncio
async def test_runtime_runs_resident_learning_before_enqueueing_signal_task() -> None:
    bus = InProcessBus()
    telemetry: list[SleipnirEvent] = []
    enqueued: list[AgentTask] = []
    processed: list[SleipnirEvent] = []
    await bus.subscribe(["valkyrie.signal_poll.completed"], lambda event: _record(telemetry, event))

    async def _resident_process(event: SleipnirEvent) -> dict:
        processed.append(event)
        return {
            "usedAdoptedLearning": True,
            "decision": "inspect_with_adopted_learning",
            "skillName": "valkyrie-inspect-host-host-disk-pressure",
            "capabilityName": "inspect.host.host.disk-pressure",
        }

    runtime = EnvironmentSignalRuntime(
        settings=_settings(),
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
        resident_signal_processor=_resident_process,
    )

    count = await runtime.collect_once()
    await bus.flush()

    assert count == 1
    assert len(processed) == 1
    assert processed[0].event_type == "signal.host.event"
    assert len(enqueued) == 1
    assert "Resident learning matched this signal before task enqueue" in (
        enqueued[0].initiative_context
    )
    assert "skill: valkyrie-inspect-host-host-disk-pressure" in (enqueued[0].initiative_context)
    assert telemetry[0].payload["resident_learning_checked_count"] == 1
    assert telemetry[0].payload["resident_learning_used_count"] == 1


@pytest.mark.asyncio
async def test_runtime_start_publishes_configuration_telemetry() -> None:
    bus = InProcessBus()
    telemetry: list[SleipnirEvent] = []
    await bus.subscribe(["valkyrie.runtime.started"], lambda event: _record(telemetry, event))
    runtime = EnvironmentSignalRuntime(
        settings=_settings(),
        publisher=bus,
        enqueue=None,
    )

    await runtime.start()
    await bus.flush()
    await runtime.stop()

    assert len(telemetry) == 1
    payload = telemetry[0].payload
    assert payload["environment_id"] == "host-jozef"
    assert payload["valkyrie_id"] == "valkyrie-host-jozef"
    assert payload["valkyrie_name"] == "Sigrun"
    assert payload["resident_personality"] == (
        "Quietly skeptical and evidence-first; escalate only with crisp context."
    )
    assert payload["source_count"] == 1
    assert payload["sources"] == [{"id": "host-events", "signal_type": "host"}]
    assert payload["poll_interval_seconds"] == 0.01
    assert payload["signal_task_severities"] == ["warning", "critical"]
    assert payload["drive_loop_enabled"] is False
    assert payload["initiative_enabled"] is False
    assert payload["llm_model"] == _settings().effective_model()
    assert payload["reflection_model"] == _settings().effective_memory_reflection_model()
    assert payload["nats_subject"] == "ravn.environment.valkyrie.runtime.started"


async def _record(events: list[SleipnirEvent], event: SleipnirEvent) -> None:
    events.append(event)


async def _enqueue(tasks: list[AgentTask], task: AgentTask) -> None:
    tasks.append(task)

"""Tests for the resident Environment signal runtime."""

from __future__ import annotations

import pytest

from ravn.cli.commands import _build_environment_signal_runtime
from ravn.config import (
    CapabilitySourceConfig,
    EnvironmentConfig,
    Settings,
    SignalSourceConfig,
)
from ravn.domain.capability_catalog import WorkflowCapability
from ravn.domain.models import AgentTask
from ravn.environment_signal_runtime import (
    EnvironmentSignalRuntime,
    build_runtime_environment,
)
from ravn.ports.capability import (
    WorkflowCapabilityPort,
    WorkflowLaunchRequest,
    WorkflowLaunchResult,
)
from ravn.resident_inbox import LocalResidentInbox
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
                    kind="generic",
                    adapter="ravn.adapters.environment_signals.GenericSignalAdapter",
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


class FakeWorkflowCapabilitySource(WorkflowCapabilityPort):
    launched: list[WorkflowLaunchRequest] = []
    list_calls = 0

    async def list_workflows(self) -> list[WorkflowCapability]:
        self.__class__.list_calls += 1
        return [
            WorkflowCapability(
                workflow_id="wf-incident",
                name="Incident Investigation",
                version="1.0.0",
                tags=["incident", "host"],
            )
        ]

    async def launch_workflow(self, request: WorkflowLaunchRequest) -> WorkflowLaunchResult:
        self.launched.append(request)
        return WorkflowLaunchResult(
            workflow_id=request.workflow_id,
            workflow_name="Incident Investigation",
            session_id="session-from-policy",
            session_name=request.session_name,
            status="running",
            slug="session-from-policy",
            cluster_name="ymir",
            owner_id="owner-1",
            tenant_id="tenant-1",
            workload_subject="system:serviceaccount:nats:valkyrie-host-jozef",
            workload_name="valkyrie-host-jozef",
        )


class WakefulnessProbe:
    def __init__(self) -> None:
        self.activity_count = 0

    def notify_activity(self) -> None:
        self.activity_count += 1


def test_build_runtime_environment_reuses_configured_flocks_and_sources() -> None:
    environment = build_runtime_environment(_settings())

    assert environment.id == "host-jozef"
    assert environment.topology.type_id == "host"
    assert environment.flock_ids == ["host-valkyries"]
    assert [source.id for source in environment.signal_sources] == ["host-events"]


def test_build_runtime_environment_does_not_interpret_environment_type() -> None:
    settings = Settings(
        environment={
            "id": "cell-7",
            "type": "vendor.example/cnc-v9",
            "topology": {
                "node_id": "machine:cell-7",
                "type_id": "printer",
                "parent_id": "room:workshop",
                "realm_id": "factory",
                "zone": "west",
            },
        }
    )

    environment = build_runtime_environment(settings)

    assert environment.type == "vendor.example/cnc-v9"
    assert environment.topology.model_dump() == {
        "node_id": "machine:cell-7",
        "type_id": "printer",
        "parent_id": "room:workshop",
        "realm_id": "factory",
        "cluster_id": "",
        "host_id": "",
        "zone": "west",
    }


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
    assert [event.event_type for event in received] == ["signal.received"]
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
    assert enqueued[0].triggered_by == "signal:signal.received"
    assert enqueued[0].root_correlation_id == received[0].correlation_id
    # The prompt is structured markdown: a heading, sections, fenced payload +
    # outcome schema. Assert the durable contract, not exact prose.
    context = enqueued[0].initiative_context
    assert context.startswith("# Environment signal")
    assert "not a predetermined conclusion or action" in context
    assert "**Valkyrie:** Sigrun" in context
    assert "**Peer id:** `valkyrie-host-jozef`" in context
    assert "Quietly skeptical and evidence-first" in context
    assert "`capability_list`" in context
    assert "`build_tool`" in context
    assert "options, not a prescribed route" in context
    assert "decision: watch" not in context
    assert "operational_state: watching" not in context
    assert "## Required outcome" in context
    assert "```json" in context  # the signal payload is a fenced code block
    assert "---outcome---" in context and "---end---" in context  # response contract
    assert "environment_id: host-jozef" not in context
    assert "valkyrie_id: valkyrie-host-jozef" not in context
    assert "correlation_ids:" not in context


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
    assert processed[0].event_type == "signal.received"
    assert len(enqueued) == 1
    context = enqueued[0].initiative_context
    assert "## Capability lookup hints" in context
    assert "cheap catalog hint, not a prior judgment" in context
    assert '"skillName": "valkyrie-inspect-host-host-disk-pressure"' in context
    assert telemetry[0].payload["resident_learning_checked_count"] == 1
    assert telemetry[0].payload["resident_learning_used_count"] == 1


@pytest.mark.asyncio
async def test_durable_home_holds_routine_signals_but_keeps_urgent_wakes() -> None:
    settings = _settings()
    settings.environment.signal_sources[0].kwargs["raw_items"][0]["severity"] = "info"
    bus = InProcessBus()
    enqueued: list[AgentTask] = []

    async def persisted(_event: SleipnirEvent) -> dict:
        return {"residentAutonomySignalRef": "resident/inbox/signals/info.md"}

    routine = EnvironmentSignalRuntime(
        settings=settings,
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
        resident_signal_processor=persisted,
        durable_home_enabled=True,
    )
    await routine.collect_once()
    assert enqueued == []
    assert routine._untriaged == []

    urgent_settings = _settings()
    urgent = EnvironmentSignalRuntime(
        settings=urgent_settings,
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
        resident_signal_processor=persisted,
        durable_home_enabled=True,
    )
    await urgent.collect_once()
    assert len(enqueued) == 1
    assert enqueued[0].resident_inbox_refs == ["resident/inbox/signals/info.md"]


@pytest.mark.asyncio
async def test_daemon_environment_signal_is_recorded_into_resident_inbox(
    tmp_path,
) -> None:
    settings = _settings()
    settings.state_dir = str(tmp_path / "state")
    settings.resident_inbox.environment_signals_enabled = True
    bus = InProcessBus()
    runtime = _build_environment_signal_runtime(
        settings,
        publisher=bus,
        owns_publisher=False,
    )

    assert runtime is not None
    count = await runtime.collect_once()
    source = LocalResidentInbox(tmp_path / "state" / "resident-inbox")
    rows = await source.list_signals(status="", limit=5)

    assert count == 1
    assert len(rows) == 1
    _path, signal = rows[0]
    assert signal.source == "host-events"
    assert signal.kind == "signal.received"
    assert "Disk usage crossed 95%" in signal.summary


@pytest.mark.asyncio
async def test_daemon_environment_signal_recording_notifies_wakefulness(
    tmp_path,
) -> None:
    settings = _settings()
    settings.state_dir = str(tmp_path / "state")
    settings.resident_inbox.environment_signals_enabled = True
    bus = InProcessBus()
    wakefulness = WakefulnessProbe()
    runtime = _build_environment_signal_runtime(
        settings,
        publisher=bus,
        resident_wakefulness=wakefulness,
        owns_publisher=False,
    )

    assert runtime is not None
    await runtime.collect_once()

    assert wakefulness.activity_count == 1


@pytest.mark.asyncio
async def test_runtime_exposes_workflow_sources_as_tools_without_policy_routing() -> None:
    bus = InProcessBus()
    telemetry: list[SleipnirEvent] = []
    enqueued: list[AgentTask] = []
    FakeWorkflowCapabilitySource.launched = []
    FakeWorkflowCapabilitySource.list_calls = 0
    settings = _settings()
    settings.environment.capability_sources = [
        CapabilitySourceConfig(
            adapter=("tests.test_ravn.test_environment_signal_runtime.FakeWorkflowCapabilitySource")
        )
    ]
    await bus.subscribe(["valkyrie.signal_poll.completed"], lambda event: _record(telemetry, event))

    runtime = EnvironmentSignalRuntime(
        settings=settings,
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
    )

    count = await runtime.collect_once()
    await bus.flush()

    assert count == 1
    assert len(enqueued) == 1
    assert FakeWorkflowCapabilitySource.launched == []
    assert FakeWorkflowCapabilitySource.list_calls == 0
    assert "## Remote workflows" in enqueued[0].initiative_context
    assert "`workflow_list`" in enqueued[0].initiative_context
    assert "`workflow_launch`" in enqueued[0].initiative_context
    assert "hidden signal policy" in enqueued[0].initiative_context
    assert telemetry[0].payload["workflow_capability_source_count"] == 1
    assert telemetry[0].payload["enqueued_task_count"] == 1


@pytest.mark.asyncio
async def test_resident_learning_failure_is_published_and_recovered() -> None:
    bus = InProcessBus()
    failures: list[SleipnirEvent] = []
    enqueued: list[AgentTask] = []
    await bus.subscribe(
        ["valkyrie.resident_learning.failed"], lambda event: _record(failures, event)
    )

    async def _boom(_event: SleipnirEvent) -> dict:
        raise RuntimeError("resident exploded")

    runtime = EnvironmentSignalRuntime(
        settings=_settings(),
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
        resident_signal_processor=_boom,
    )
    count = await runtime.collect_once()
    await bus.flush()

    assert count == 1
    assert failures and failures[0].payload["error_type"] == "RuntimeError"
    # The signal still escalates; the prompt records the failed pre-check.
    assert enqueued and "resident_learning_failed" in enqueued[0].initiative_context


@pytest.mark.asyncio
async def test_capability_lookup_miss_is_only_a_hint_not_a_build_mandate() -> None:
    bus = InProcessBus()
    enqueued: list[AgentTask] = []

    async def _resident_process(event: SleipnirEvent) -> dict:
        return {
            "usedAdoptedLearning": False,
            "decision": "capability_lookup_miss",
            "capabilityName": "inspect.host.host.disk-pressure",
            "skillName": "",
        }

    runtime = EnvironmentSignalRuntime(
        settings=_settings(),
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
        resident_signal_processor=_resident_process,
    )
    await runtime.collect_once()
    await bus.flush()

    assert len(enqueued) == 1
    context = enqueued[0].initiative_context
    assert "## Capability lookup hints" in context
    assert '"capabilityName": "inspect.host.host.disk-pressure"' in context
    assert "not a prior judgment" in context
    assert "## Required before you finish" not in context
    assert "Only if no suitable capability exists" not in context


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
    assert payload["sources"] == [{"id": "host-events", "signal_type": "generic"}]
    assert payload["poll_interval_seconds"] == 0.01
    assert payload["signal_task_severities"] == ["warning", "critical"]
    assert payload["drive_loop_enabled"] is False
    assert payload["initiative_enabled"] is False
    assert payload["llm_model"] == _settings().effective_model()
    assert payload["reflection_model"] == _settings().effective_memory_reflection_model()
    assert payload["nats_subject"] == "ravn.environment.valkyrie.runtime.started"


@pytest.mark.asyncio
async def test_charter_travels_in_runtime_telemetry_and_task_context() -> None:
    bus = InProcessBus()
    telemetry: list[SleipnirEvent] = []
    enqueued: list[AgentTask] = []
    await bus.subscribe(["valkyrie.runtime.started"], lambda event: _record(telemetry, event))
    settings = _settings()
    settings.environment.charter = (
        "Keep the host healthy and quiet; escalate only real risk to Jozef."
    )
    runtime = EnvironmentSignalRuntime(
        settings=settings,
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
    )

    await runtime.start()
    await bus.flush()
    await runtime.stop()
    await runtime.collect_once()

    assert telemetry[0].payload["charter"] == (
        "Keep the host healthy and quiet; escalate only real risk to Jozef."
    )
    assert enqueued
    assert "**Charter:** Keep the host healthy and quiet" in enqueued[0].initiative_context


@pytest.mark.asyncio
async def test_below_threshold_signals_accumulate_for_idle_triage() -> None:
    bus = InProcessBus()
    enqueued: list[AgentTask] = []
    settings = _settings()
    # The only configured signal is critical; raise the bar so nothing enqueues.
    settings.environment.signal_task_severities = ["never"]
    runtime = EnvironmentSignalRuntime(
        settings=settings,
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
    )

    count = await runtime.collect_once()
    await bus.flush()

    assert count == 1
    assert enqueued == []
    task = runtime._triage_task(runtime._untriaged)
    assert task.triggered_by == "signal:idle_triage"
    assert task.title == "Signal window: 1 observation(s)"
    context = task.initiative_context
    assert "# Signals since your last look" in context
    assert "without a predetermined interpretation" in context
    assert "signal_task_severities" not in context
    assert "**critical**: 1" in context
    assert "## Observed signals" in context
    assert "(critical, host-events)" in context
    assert "---outcome---" in context and "---end---" in context
    assert "decision: watch" not in context
    assert "operational_state: watching" not in context
    assert "decision: <ignore | watch | investigate" in context
    assert "selected_next_action: <one concrete next step, or none>" in context
    assert "continuation: <ask_operator | sleep | stop>" in context
    assert "next_action_timing: <external_event | scheduled_time" in context


def test_idle_triage_prompt_is_bounded_regardless_of_batch_size() -> None:
    """NIU-1118: a huge signal batch must render a bounded triage prompt."""
    settings = _settings()
    settings.environment.idle_triage_sample_signals = 4
    settings.environment.idle_triage_sample_summary_max_chars = 40
    settings.environment.idle_triage_max_signal_refs = 6
    runtime = EnvironmentSignalRuntime(settings=settings, publisher=InProcessBus())
    long_summary = "very long signal summary " * 50
    batch = [
        {
            "signal_ref": f"sig-{index}",
            "signal_type": "generic",
            "severity": "info",
            "source_id": "host-events",
            "summary": long_summary,
        }
        for index in range(40)
    ]

    task = runtime._triage_task(batch)
    context = task.initiative_context

    assert task.title == "Signal window: 40 observation(s)"
    # Sample lines are capped and each summary is truncated, never verbatim.
    assert long_summary not in context
    assert context.count("(info, host-events)") == 4
    assert "…and 36 more signal(s) not shown" in context
    # The outcome template lists a bounded number of refs.
    assert "  - sig-5" in context
    assert "  - sig-6" not in context
    # The severity breakdown still summarizes the whole batch.
    assert "**info**: 40" in context


@pytest.mark.asyncio
async def test_idle_triage_loop_enqueues_and_resets_batch() -> None:
    import asyncio

    bus = InProcessBus()
    enqueued: list[AgentTask] = []
    settings = _settings()
    settings.environment.signal_task_severities = ["never"]
    settings.environment.idle_triage_interval_seconds = 0.01
    settings.environment.idle_triage_max_signals = 5
    runtime = EnvironmentSignalRuntime(
        settings=settings,
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
    )

    await runtime.collect_once()
    await runtime.start()
    for _ in range(200):
        if enqueued:
            break
        await asyncio.sleep(0.01)
    await runtime.stop()

    triage_tasks = [task for task in enqueued if task.triggered_by == "signal:idle_triage"]
    assert triage_tasks, "idle triage loop never enqueued a task"
    assert runtime._untriaged == []


def test_untriaged_buffer_is_capped() -> None:
    settings = _settings()
    settings.environment.idle_triage_max_signals = 3
    runtime = EnvironmentSignalRuntime(settings=settings, publisher=InProcessBus())
    for index in range(6):
        runtime._untriaged.append(
            {
                "signal_ref": f"sig-{index}",
                "signal_type": "generic",
                "severity": "info",
                "source_id": "host-events",
                "summary": f"signal {index}",
            }
        )
    event = SleipnirEvent(
        event_type="signal.received",
        source="test",
        payload={},
        summary="s",
        urgency=0.1,
        domain="infrastructure",
        timestamp=SleipnirEvent.now(),
    )

    class _Sig:
        provider_event_id = "p"
        dedupe_key = "d"
        signal_type = "host"
        severity = "info"
        source_id = "host-events"

    runtime._remember_untriaged(_Sig(), event)  # type: ignore[arg-type]

    assert len(runtime._untriaged) == 3


async def _record(events: list[SleipnirEvent], event: SleipnirEvent) -> None:
    events.append(event)


async def _enqueue(tasks: list[AgentTask], task: AgentTask) -> None:
    tasks.append(task)

"""Tests for the resident Environment signal runtime."""

from __future__ import annotations

import pytest

from ravn.adapters.capabilities import FileWorkflowSubmissionStore
from ravn.config import (
    CapabilityPolicyConfig,
    CapabilitySourceConfig,
    CapabilitySubmissionStoreConfig,
    EnvironmentConfig,
    Settings,
    SignalSourceConfig,
    WorkflowSelectorConfig,
)
from ravn.domain.capability_resolution import WorkflowCapability
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


class FakeWorkflowCapabilitySource(WorkflowCapabilityPort):
    launched: list[WorkflowLaunchRequest] = []

    async def list_workflows(self) -> list[WorkflowCapability]:
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
    # The prompt is structured markdown: a heading, sections, fenced payload +
    # outcome schema. Assert the durable contract, not exact prose.
    context = enqueued[0].initiative_context
    assert context.startswith("# Signal investigation")
    assert "**Valkyrie:** Sigrun" in context
    assert "**Peer id:** `valkyrie-host-jozef`" in context
    assert "Quietly skeptical and evidence-first" in context
    assert "skill_list" in context and "skill_run" in context
    assert "`build_tool`" in context
    assert "use the newly registered tool" in context
    assert "## Required outcome" in context
    assert "```json" in context  # the signal payload is a fenced code block
    assert "---outcome---" in context and "---end---" in context  # response contract
    assert "environment_id: host-jozef" in context
    assert "valkyrie_id: valkyrie-host-jozef" in context
    assert "correlation_ids:" in context


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
    context = enqueued[0].initiative_context
    assert "## Resident learning" in context
    assert "Use the adopted skill context before proposing new tooling" in context
    assert "**Skill:** `valkyrie-inspect-host-host-disk-pressure`" in context
    assert telemetry[0].payload["resident_learning_checked_count"] == 1
    assert telemetry[0].payload["resident_learning_used_count"] == 1


@pytest.mark.asyncio
async def test_runtime_keeps_workflow_policy_local_without_remote_trigger_decision(
    tmp_path,
) -> None:
    bus = InProcessBus()
    telemetry: list[SleipnirEvent] = []
    enqueued: list[AgentTask] = []
    FakeWorkflowCapabilitySource.launched = []
    settings = _settings()
    settings.environment.capability_sources = [
        CapabilitySourceConfig(
            adapter=(
                "tests.test_ravn.test_environment_signal_runtime."
                "FakeWorkflowCapabilitySource"
            )
        )
    ]
    settings.environment.capability_submission_store = CapabilitySubmissionStoreConfig(
        kwargs={"path": str(tmp_path / "capability_submissions.json")}
    )
    settings.environment.capability_policies = [
        CapabilityPolicyConfig(
            name="host-critical-to-incident-workflow",
            signal_types=["signal.host.event"],
            severities=["critical"],
            remote_workflows=WorkflowSelectorConfig(tags=["incident"]),
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
    assert "## Capability policy" in enqueued[0].initiative_context
    assert "not allowed to launch remote work" in enqueued[0].initiative_context
    assert telemetry[0].payload["capability_policy_checked_count"] == 1
    assert telemetry[0].payload["capability_remote_launch_count"] == 0
    assert telemetry[0].payload["enqueued_task_count"] == 1


@pytest.mark.asyncio
async def test_runtime_launches_configured_workflow_after_remote_trigger_decision(
    tmp_path,
) -> None:
    bus = InProcessBus()
    telemetry: list[SleipnirEvent] = []
    enqueued: list[AgentTask] = []
    FakeWorkflowCapabilitySource.launched = []
    settings = _settings()
    settings.environment.capability_sources = [
        CapabilitySourceConfig(
            adapter=(
                "tests.test_ravn.test_environment_signal_runtime."
                "FakeWorkflowCapabilitySource"
            )
        )
    ]
    journal_path = tmp_path / "capability_submissions.json"
    settings.environment.capability_submission_store = CapabilitySubmissionStoreConfig(
        kwargs={"path": str(journal_path)}
    )
    settings.environment.capability_policies = [
        CapabilityPolicyConfig(
            name="host-critical-to-incident-workflow",
            signal_types=["signal.host.event"],
            severities=["critical"],
            remote_trigger_decisions=["needs_remote_research"],
            remote_workflows=WorkflowSelectorConfig(tags=["incident"]),
        )
    ]
    await bus.subscribe(["valkyrie.signal_poll.completed"], lambda event: _record(telemetry, event))

    async def _resident_process(event: SleipnirEvent) -> dict:
        return {
            "usedAdoptedLearning": False,
            "decision": "needs_remote_research",
            "capabilityName": "inspect.host.disk",
            "skillName": "",
        }

    runtime = EnvironmentSignalRuntime(
        settings=settings,
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
        resident_signal_processor=_resident_process,
    )

    count = await runtime.collect_once()
    await bus.flush()

    assert count == 1
    assert enqueued == []
    assert len(FakeWorkflowCapabilitySource.launched) == 1
    launch = FakeWorkflowCapabilitySource.launched[0]
    assert launch.workflow_id == "wf-incident"
    assert "## Capability policy" in launch.prompt
    assert "host-critical-to-incident-workflow" in launch.prompt
    assert launch.provenance["policy"] == "host-critical-to-incident-workflow"
    assert launch.provenance["decision"] == "invoke_workflow"
    assert launch.provenance["workflow_id"] == "wf-incident"
    assert launch.provenance["tenant_id"] == "host-jozef"
    assert launch.provenance["source_id"] == "host-events"
    assert launch.provenance["source_event_id"]
    assert telemetry[0].payload["capability_policy_checked_count"] == 1
    assert telemetry[0].payload["capability_remote_launch_count"] == 1
    launch_summary = telemetry[0].payload["capability_remote_launches"][0]
    assert launch_summary["status"] == "launched"
    assert launch_summary["submissionId"]
    assert launch_summary["sessionId"] == "session-from-policy"
    assert launch_summary["workflowId"] == "wf-incident"
    assert launch_summary["policyName"] == "host-critical-to-incident-workflow"
    assert launch_summary["capabilityName"]
    assert launch_summary["ownerId"] == "owner-1"
    assert launch_summary["tenantId"] == "tenant-1"
    assert launch_summary["workloadSubject"] == "system:serviceaccount:nats:valkyrie-host-jozef"
    assert telemetry[0].payload["enqueued_task_count"] == 0
    records = await FileWorkflowSubmissionStore(journal_path).list_submissions()
    assert len(records) == 1
    assert records[0].status == "launched"
    assert records[0].session_id == "session-from-policy"
    assert records[0].owner_id == "owner-1"
    assert records[0].tenant_id == "tenant-1"
    assert records[0].workload_subject == "system:serviceaccount:nats:valkyrie-host-jozef"
    assert records[0].source_id == "host-events"
    assert records[0].source_event_id
    assert records[0].provenance["policy"] == "host-critical-to-incident-workflow"
    assert records[0].provenance["owner_id"] == "owner-1"
    assert records[0].provenance["session_id"] == "session-from-policy"

    restarted = EnvironmentSignalRuntime(
        settings=settings,
        publisher=bus,
        enqueue=lambda task: _enqueue(enqueued, task),
    )
    await restarted.collect_once()
    await bus.flush()

    assert len(FakeWorkflowCapabilitySource.launched) == 1
    assert len(await FileWorkflowSubmissionStore(journal_path).list_submissions()) == 1


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
async def test_defer_to_investigation_appends_the_build_mandate() -> None:
    bus = InProcessBus()
    enqueued: list[AgentTask] = []

    async def _resident_process(event: SleipnirEvent) -> dict:
        return {
            "usedAdoptedLearning": False,
            "decision": "defer_to_investigation_with_build_tool",
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
    # The capability-gap mandate is the prompt's closing directive.
    assert "## Resident learning" in context
    assert "## Required before you finish" in context
    assert "`inspect.host.host.disk-pressure`" in context
    assert "Do not finish without calling" in context
    # It lands after the outcome schema so the model weights it.
    assert context.index("## Required before you finish") > context.index("---end---")


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

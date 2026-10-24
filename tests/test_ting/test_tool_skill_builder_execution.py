"""Execution coverage for Tool & Skill Builder publisher outcomes."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from a2a.types import TaskState

from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.config import Settings as RavnSettings
from ravn.domain.events import RavnEventType
from ravn.workflow_runtime import _workflow_allowed_outcome_topics
from skuld.broker import Broker
from skuld.config import SkuldSettings
from tests.test_ravn.conftest import _make_agent_task, _make_drive_loop
from ting.api.a2a import campaign_to_task
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.activity_subscriber import _workflow_gate_from_help_needed
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.system_workflows import load_system_workflows

_MIMIR_IDENTITY_DIAGNOSIS = (
    "Publication is blocked by missing Mímir workload identity exchange configuration."
)


def _tool_skill_builder_graph() -> dict:
    return next(
        workflow.graph
        for workflow in load_system_workflows()
        if workflow.name == "Tool & Skill Builder"
    )


def _publisher_drive_loop(graph: dict) -> tuple[object, AsyncMock]:
    drive_loop = _make_drive_loop()
    drive_loop._settings = RavnSettings.model_validate({"workflow": {"graph": graph}})
    mesh = AsyncMock()
    drive_loop._mesh = mesh
    drive_loop._skuld_channel = AsyncMock()
    drive_loop._source_id = "capability-publisher"
    drive_loop._persona_config = FilesystemPersonaAdapter().load("capability-publisher")
    drive_loop.set_workflow_allowed_outcomes_resolver(
        lambda task, _persona: _workflow_allowed_outcome_topics(
            drive_loop._settings,
            node_id=task.workflow_node_id,
        )
    )
    return drive_loop, mesh


def _publisher_task(task_id: str) -> object:
    task = _make_agent_task(task_id)
    task.session_id = "session-niu-1147"
    task.root_correlation_id = "root-niu-1147"
    task.workflow_node_id = "capability-publish"
    return task


def _workflow_broker(graph: dict) -> Broker:
    settings = SkuldSettings.model_validate(
        {
            "session": {"id": "session-niu-1147", "workspace_dir": "/tmp"},
            "workflow": {"graph": graph},
        }
    )
    broker = Broker(settings=settings)
    broker._mesh_adapter = MagicMock(peer_id="skuld-peer")
    broker._room_bridge = MagicMock()
    broker._room_bridge.participants = {
        "flock-capability-publisher": MagicMock(persona="capability-publisher")
    }
    broker._room_bridge.register_mesh_peer = AsyncMock()
    broker._room_bridge.handle_collaboration_frame = AsyncMock()
    broker._emit_pipeline_event = AsyncMock()
    broker._emit_workflow_gate_help_needed_sleipnir_event = AsyncMock()
    broker._report_activity_state = AsyncMock()
    return broker


def _campaign(graph: dict) -> WorkflowCampaign:
    now = datetime.now(UTC)
    return WorkflowCampaign(
        id=uuid4(),
        slug="build-niu-1147",
        name="build-niu-1147",
        owner_id="user-1",
        workflow_id=uuid4(),
        workflow_version="1.0.4",
        workflow_name="Tool & Skill Builder",
        workflow_snapshot={"graph": graph},
        session_id="session-niu-1147",
        session_name="build-niu-1147",
        status=WorkflowCampaignStatus.RUNNING,
        active_stage_id="capability-publish",
        stage_state=[],
        metadata={"surface": "a2a"},
        created_at=now,
        updated_at=now,
        last_activity_at=now,
    )


@pytest.mark.asyncio
async def test_tool_skill_builder_publisher_block_reaches_a2a_help_gate() -> None:
    """A real bundled publisher outcome blocks at the A2A-visible help gate."""
    graph = _tool_skill_builder_graph()
    drive_loop, mesh = _publisher_drive_loop(graph)

    accepted = await drive_loop._emit_mesh_outcome_event(
        _publisher_task("publisher-blocked"),
        f"""---outcome---
verdict: blocked
summary: {_MIMIR_IDENTITY_DIAGNOSIS}
artifact_path:
---end---
""",
        success=True,
        channel=drive_loop._skuld_channel,
    )

    assert accepted is True
    published = {call.kwargs["topic"]: call.args[0] for call in mesh.publish.await_args_list}
    assert set(published) == {"capability.publish.completed", "capability.blocked"}
    blocked_event = published["capability.blocked"]
    assert blocked_event.payload["fields"]["summary"] == _MIMIR_IDENTITY_DIAGNOSIS
    assert blocked_event.payload["fields"]["artifact_path"] == ""
    assert all(
        event.type != RavnEventType.ERROR
        for event in (call.args[0] for call in drive_loop._skuld_channel.emit.await_args_list)
    )

    broker = _workflow_broker(graph)
    await broker._observe_room_peer_event(
        "flock-capability-publisher",
        "outcome",
        {
            "root_correlation_id": "root-niu-1147",
            "metadata": {"event_type": "capability.blocked", "task_id": "publisher-blocked"},
            "data": blocked_event.payload,
        },
    )

    gates = broker.list_workflow_gates()
    assert len(gates) == 1
    assert gates[0]["node_id"] == "capability-blocked"
    assert gates[0]["pending_behavior"] == "help_needed"
    assert gates[0]["summary"] == _MIMIR_IDENTITY_DIAGNOSIS
    assert broker._emit_pipeline_event.await_count == 1
    assert broker._emit_pipeline_event.await_args.args[0] == "workflow.gate.pending"

    help_needed = broker._report_activity_state.await_args.kwargs["extra_metadata"]["help_needed"]
    assert help_needed["summary"] == _MIMIR_IDENTITY_DIAGNOSIS
    assert help_needed["context"]["gate_node_id"] == "capability-blocked"

    campaign = _campaign(graph)
    repository = AsyncMock()
    repository.get_active_campaign_by_session.return_value = campaign
    repository.save_campaign.side_effect = lambda saved: saved
    projector = WorkflowCampaignProjector(
        repo=repository,
        volundr_factory=SimpleNamespace(),
        event_bus=AsyncMock(),
    )

    assert await projector.record_help_needed(
        help_needed,
        campaign.owner_id,
        session_id=campaign.session_id,
        gate=_workflow_gate_from_help_needed(help_needed),
    )
    blocked_campaign = repository.save_campaign.await_args.args[0]
    assert blocked_campaign.status == WorkflowCampaignStatus.BLOCKED
    task = campaign_to_task(blocked_campaign)
    assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert task.artifacts == []
    assert "failure_error" not in blocked_campaign.metadata


@pytest.mark.asyncio
async def test_tool_skill_builder_publisher_success_still_completes() -> None:
    """The ready publisher outcome still reaches the bundled terminal node."""
    graph = _tool_skill_builder_graph()
    drive_loop, mesh = _publisher_drive_loop(graph)

    accepted = await drive_loop._emit_mesh_outcome_event(
        _publisher_task("publisher-ready"),
        """---outcome---
verdict: published
summary: Published the learned capability artifact.
artifact_path: capabilities/build-niu-1147/learned_tool.json
---end---
""",
        success=True,
        channel=drive_loop._skuld_channel,
    )

    assert accepted is True
    published = {call.kwargs["topic"]: call.args[0] for call in mesh.publish.await_args_list}
    assert set(published) == {"capability.publish.completed", "capability.ready"}

    broker = _workflow_broker(graph)
    await broker._observe_room_peer_event(
        "flock-capability-publisher",
        "outcome",
        {
            "root_correlation_id": "root-niu-1147",
            "metadata": {"event_type": "capability.ready", "task_id": "publisher-ready"},
            "data": published["capability.ready"].payload,
        },
    )

    assert broker.list_workflow_gates() == []
    terminal_events = [
        call.args[1]
        for call in broker._emit_pipeline_event.await_args_list
        if call.args[0] == "outcome"
    ]
    assert len(terminal_events) == 1
    assert terminal_events[0]["event_type"] == "capability.ready"
    assert terminal_events[0]["fields"]["checks"] == [
        {
            "persona": "capability-publisher",
            "event_type": "capability.ready",
            "verdict": "published",
            "summary": "Published the learned capability artifact.",
        }
    ]

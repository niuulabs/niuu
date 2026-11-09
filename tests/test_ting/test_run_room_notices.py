"""A run's steps, gates and end, posted as notices in the run's room, each once."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.run_room_notices import RunRoomNotifier, run_notices
from ting.domain.workflow_steps import (
    RunState,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
)
from ting.ports.volundr import VolundrSession

from .fake_forge import FakeForge, FakeForgeFactory

NOW = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


def _campaign() -> WorkflowCampaign:
    nodes = [
        {"id": "code", "kind": "stage", "label": "Write the code"},
        {"id": "review", "kind": "stage", "label": "Review it"},
        {"id": "approve", "kind": "gate", "label": "Approve the merge", "mode": "human_approval"},
    ]
    return WorkflowCampaign(
        id=uuid4(),
        slug="crm-1",
        name="run",
        owner_id="owner",
        workflow_id=uuid4(),
        workflow_version="1.0.0",
        workflow_name="Code, Review and Merge",
        workflow_snapshot={"graph": {"nodes": nodes}},
        session_id="",
        session_name="",
        status=WorkflowCampaignStatus.RUNNING,
        active_stage_id=None,
        stage_state=[],
        metadata={},
        created_at=NOW,
        updated_at=NOW,
        connection_id="cluster-1",
    )


def _member(run_id: UUID, node: str, member: str, at: datetime, **changes) -> WorkflowStep:
    values = {
        "id": uuid4(),
        "run_id": run_id,
        "node_id": node,
        "member": member,
        "kind": StepKind.STAGE_MEMBER,
        "attempt": 1,
        "generation": 0,
        "state": StepState.ACTIVE,
        "created_at": at,
        "activated_at": at,
    }
    values.update(changes)
    return WorkflowStep(**values)


def _gate(run_id: UUID, state: StepState, at: datetime, **changes) -> WorkflowStep:
    return WorkflowStep(
        id=uuid4(),
        run_id=run_id,
        node_id="approve",
        member=None,
        kind=StepKind.GATE,
        attempt=1,
        generation=0,
        state=state,
        created_at=at,
        waiting_on=WaitingOn(WaitingOnKind.PERSON, "gate:approve", at)
        if state is StepState.WAITING
        else None,
        **changes,
    )


def _with_room(forge: FakeForge, campaign: WorkflowCampaign) -> str:
    forge.sessions["room-1"] = VolundrSession(
        id="room-1",
        name="wf-crm-1",
        status="running",
        tracker_issue_id=f"workflow:{campaign.slug}",
        project_id="project-1",
    )
    return "room-1"


def test_a_run_reads_as_its_steps_gates_and_end_in_order() -> None:
    campaign = _campaign()
    code = _member(
        campaign.id, "code", "coder", NOW, state=StepState.DONE, verdict="pass", note="Tests pass."
    )
    review = _member(campaign.id, "review", "reviewer", NOW + timedelta(minutes=5))
    gate = _gate(campaign.id, StepState.WAITING, NOW + timedelta(minutes=9))
    state = RunState(campaign.id, steps=(gate, review, code))

    texts = [n.text for n in run_notices(campaign, state, WorkflowCampaignStatus.BLOCKED)]

    assert texts == [
        "coder started Write the code.",
        "coder closed Write the code: pass. Tests pass.",
        "reviewer started Review it.",
        "Approve the merge is waiting for a decision.",
    ]


def test_a_decided_gate_a_rework_and_the_runs_end_are_told() -> None:
    campaign = _campaign()
    rework = _member(campaign.id, "code", "coder", NOW, attempt=2, generation=1)
    gate = _gate(campaign.id, StepState.DONE, NOW + timedelta(minutes=1), verdict="approve")
    state = RunState(campaign.id, steps=(rework, gate))

    texts = [n.text for n in run_notices(campaign, state, WorkflowCampaignStatus.COMPLETED)]

    assert texts[0] == "coder started Write the code (attempt 2), rework 1."
    assert texts[1:3] == [
        "Approve the merge is waiting for a decision.",
        "Approve the merge approved.",
    ]
    assert texts[-1] == "The run completed."


async def test_notices_go_to_the_runs_room_once_each() -> None:
    campaign = _campaign()
    forge = FakeForge()
    room = _with_room(forge, campaign)
    notifier = RunRoomNotifier(volundr_factory=FakeForgeFactory(forge))
    step = _member(campaign.id, "code", "coder", NOW)
    state = RunState(campaign.id, steps=(step,))

    await notifier.sync(campaign, state, WorkflowCampaignStatus.RUNNING)
    await notifier.sync(campaign, state, WorkflowCampaignStatus.RUNNING)
    closed = replace(step, state=StepState.DONE, verdict="pass")
    await notifier.sync(
        campaign, RunState(campaign.id, steps=(closed,)), WorkflowCampaignStatus.RUNNING
    )

    assert forge.room_notices == [
        (room, "coder started Write the code.", f"{step.id}:started"),
        (room, "coder closed Write the code: pass.", f"{step.id}:closed"),
    ]


async def test_a_notice_forge_refused_is_posted_again_on_the_next_change() -> None:
    campaign = _campaign()
    forge = FakeForge()
    _with_room(forge, campaign)
    notifier = RunRoomNotifier(volundr_factory=FakeForgeFactory(forge))
    state = RunState(campaign.id, steps=(_member(campaign.id, "code", "coder", NOW),))
    forge.fail_notice = RuntimeError("room not up")

    await notifier.sync(campaign, state, WorkflowCampaignStatus.RUNNING)
    forge.fail_notice = None
    await notifier.sync(campaign, state, WorkflowCampaignStatus.RUNNING)

    assert [text for _, text, _ in forge.room_notices] == ["coder started Write the code."]


async def test_a_run_without_a_room_posts_nothing_and_keeps_its_notices() -> None:
    campaign = _campaign()
    forge = FakeForge()
    notifier = RunRoomNotifier(volundr_factory=FakeForgeFactory(forge))
    state = RunState(campaign.id, steps=(_member(campaign.id, "code", "coder", NOW),))

    await notifier.sync(campaign, state, WorkflowCampaignStatus.RUNNING)
    assert forge.room_notices == []

    # The room comes up with the run's first member; the notice is posted then.
    _with_room(forge, campaign)
    await notifier.sync(campaign, state, WorkflowCampaignStatus.RUNNING)
    assert len(forge.room_notices) == 1

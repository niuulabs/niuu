"""A run's steps, gates and end, posted as notices in the run's room, each once."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

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


def _offer(run_id: UUID, node: str, at: datetime, **changes) -> WorkflowStep:
    offer_id = uuid4()
    values = {
        "id": offer_id,
        "run_id": run_id,
        "node_id": node,
        "member": None,
        "kind": StepKind.OFFER,
        "attempt": 1,
        "generation": 0,
        "state": StepState.ACTIVE,
        "created_at": at,
        "activated_at": at,
        "offer_id": str(offer_id),
    }
    values.update(changes)
    return WorkflowStep(**values)


def test_a_run_reads_as_its_gates_and_end_in_order_and_its_steps_are_not_told() -> None:
    campaign = _campaign()
    offer = _offer(campaign.id, "code", NOW, state=StepState.DONE)
    code = _member(
        campaign.id, "code", "coder", NOW, state=StepState.DONE, verdict="pass", note="Tests pass."
    )
    review = _member(campaign.id, "review", "reviewer", NOW + timedelta(minutes=5))
    gate = _gate(campaign.id, StepState.WAITING, NOW + timedelta(minutes=9))
    state = RunState(campaign.id, steps=(gate, review, code, offer))

    texts = [n.text for n in run_notices(campaign, state, WorkflowCampaignStatus.BLOCKED)]

    # The mesh events show the offer, the pickups and the closes; Ting tells only the gate.
    assert texts == ["Approve the merge is waiting for a decision."]


def test_steps_alone_raise_no_notice() -> None:
    campaign = _campaign()
    steps = (
        _offer(campaign.id, "code", NOW),
        _member(campaign.id, "code", "coder", NOW, attempt=2, generation=1),
    )

    assert (
        run_notices(campaign, RunState(campaign.id, steps=steps), WorkflowCampaignStatus.RUNNING)
        == []
    )


def test_a_decided_gate_and_the_runs_end_are_told() -> None:
    campaign = _campaign()
    rework = _member(campaign.id, "code", "coder", NOW, attempt=2, generation=1)
    gate = _gate(campaign.id, StepState.DONE, NOW + timedelta(minutes=1), verdict="approve")
    state = RunState(campaign.id, steps=(rework, gate))

    texts = [n.text for n in run_notices(campaign, state, WorkflowCampaignStatus.COMPLETED)]

    assert texts == [
        "Approve the merge is waiting for a decision.",
        "Approve the merge approved.",
        "The run completed.",
    ]


@pytest.mark.parametrize(
    "status, text",
    [
        (WorkflowCampaignStatus.FAILED, "The run failed."),
        (WorkflowCampaignStatus.CANCELED, "The run was canceled."),
    ],
)
def test_a_run_that_failed_or_was_canceled_says_so(
    status: WorkflowCampaignStatus, text: str
) -> None:
    campaign = _campaign()

    notices = run_notices(campaign, RunState(campaign.id, steps=()), status)

    assert [notice.text for notice in notices] == [text]


async def test_notices_go_to_the_runs_room_once_each() -> None:
    campaign = _campaign()
    forge = FakeForge()
    room = _with_room(forge, campaign)
    notifier = RunRoomNotifier(volundr_factory=FakeForgeFactory(forge))
    gate = _gate(campaign.id, StepState.WAITING, NOW)
    state = RunState(campaign.id, steps=(gate,))

    await notifier.sync(campaign, state, WorkflowCampaignStatus.BLOCKED)
    await notifier.sync(campaign, state, WorkflowCampaignStatus.BLOCKED)
    decided = replace(gate, state=StepState.DONE, verdict="approve", waiting_on=None)
    await notifier.sync(
        campaign, RunState(campaign.id, steps=(decided,)), WorkflowCampaignStatus.RUNNING
    )

    assert forge.room_notices == [
        (room, "Approve the merge is waiting for a decision.", f"{gate.id}:open"),
        (room, "Approve the merge approved.", f"{gate.id}:decided"),
    ]


async def test_a_notice_forge_refused_is_posted_again_on_the_next_change() -> None:
    campaign = _campaign()
    forge = FakeForge()
    _with_room(forge, campaign)
    notifier = RunRoomNotifier(volundr_factory=FakeForgeFactory(forge))
    state = RunState(campaign.id, steps=(_gate(campaign.id, StepState.WAITING, NOW),))
    forge.fail_notice = RuntimeError("room not up")

    await notifier.sync(campaign, state, WorkflowCampaignStatus.BLOCKED)
    forge.fail_notice = None
    await notifier.sync(campaign, state, WorkflowCampaignStatus.BLOCKED)

    assert [text for _, text, _ in forge.room_notices] == [
        "Approve the merge is waiting for a decision."
    ]


async def test_a_run_without_a_room_posts_nothing_and_keeps_its_notices() -> None:
    campaign = _campaign()
    forge = FakeForge()
    notifier = RunRoomNotifier(volundr_factory=FakeForgeFactory(forge))
    state = RunState(campaign.id, steps=(_gate(campaign.id, StepState.WAITING, NOW),))

    await notifier.sync(campaign, state, WorkflowCampaignStatus.BLOCKED)
    assert forge.room_notices == []

    # The room comes up with the run's unit; the notice is posted then.
    _with_room(forge, campaign)
    await notifier.sync(campaign, state, WorkflowCampaignStatus.BLOCKED)
    assert len(forge.room_notices) == 1

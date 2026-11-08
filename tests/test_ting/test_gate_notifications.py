"""The gate notifier against a fake Forge: who is told, where, and how a gate settles."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.gate_notifications import GateNotifier, gate_correlation_id
from ting.domain.workflow_steps import (
    RunState,
    StepInput,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
)

from .fake_forge import FakeForge, FakeForgeFactory

NOW = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


def _campaign(gate: dict | None = None) -> WorkflowCampaign:
    node = {"id": "approve", "kind": "gate", "label": "Approve", "mode": "human_approval"}
    node.update(gate or {})
    return WorkflowCampaign(
        id=uuid4(),
        slug="run-1",
        name="run",
        owner_id="owner",
        workflow_id=uuid4(),
        workflow_version="1.0.0",
        workflow_name="W",
        workflow_snapshot={"graph": {"nodes": [node]}},
        session_id="",
        session_name="",
        status=WorkflowCampaignStatus.BLOCKED,
        active_stage_id=None,
        stage_state=[],
        metadata={},
        created_at=NOW,
        updated_at=NOW,
        connection_id="cluster-1",
    )


def _member(run_id: UUID, node_id: str, member: str, session_id: str) -> WorkflowStep:
    return WorkflowStep(
        id=uuid4(),
        run_id=run_id,
        node_id=node_id,
        member=member,
        kind=StepKind.STAGE_MEMBER,
        attempt=1,
        generation=0,
        state=StepState.DONE,
        created_at=NOW,
        session_id=session_id,
        verdict="pass",
        closed_at=NOW,
    )


def _gate(run_id: UUID, state: StepState, inputs=(), **changes) -> WorkflowStep:
    return WorkflowStep(
        id=uuid4(),
        run_id=run_id,
        node_id="approve",
        member=None,
        kind=StepKind.GATE,
        attempt=1,
        generation=0,
        state=state,
        created_at=NOW,
        inputs=tuple(inputs),
        waiting_on=WaitingOn(WaitingOnKind.PERSON, "gate:approve", NOW)
        if state is StepState.WAITING
        else None,
        **changes,
    )


def _notifier(forge: FakeForge, origin: str = "https://niuu.example") -> GateNotifier:
    return GateNotifier(volundr_factory=FakeForgeFactory(forge), public_origin=origin)


async def test_the_gate_is_recorded_on_the_session_whose_outcome_opened_it() -> None:
    forge = FakeForge()
    coder = forge.add_session("coder", "t")
    reviewer = forge.add_session("reviewer", "t")
    campaign = _campaign()
    steps = (
        _member(campaign.id, "review", "reviewer", reviewer),
        _member(campaign.id, "implement", "coder", coder),  # newer, but did not open it
    )
    gate = _gate(
        campaign.id,
        StepState.WAITING,
        inputs=[StepInput(1, "review.passed", "e4", "review", "reviewer")],
    )

    await _notifier(forge).sync(campaign, RunState(campaign.id, steps=(*steps, gate)))

    [(key, (session_id, _))] = forge.notifications.items()
    assert key == gate_correlation_id(campaign.id, gate.id)
    assert session_id == reviewer


async def test_without_a_public_origin_the_review_link_is_relative() -> None:
    forge = FakeForge()
    session = forge.add_session("coder", "t")
    campaign = _campaign()
    state = RunState(
        campaign.id,
        steps=(
            _member(campaign.id, "implement", "coder", session),
            _gate(campaign.id, StepState.WAITING),
        ),
    )

    await _notifier(forge, origin="").sync(campaign, state)

    [(_, (_, note))] = forge.notifications.items()
    assert note["links"][0]["url"] == "/ting/review/run/run-1"


async def test_a_gate_with_no_session_in_its_run_is_reported_as_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    degraded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "ting.domain.services.gate_notifications.record_degraded",
        lambda path, message: degraded.append((path, message)),
    )
    forge = FakeForge()
    campaign = _campaign()

    await _notifier(forge).sync(
        campaign, RunState(campaign.id, steps=(_gate(campaign.id, StepState.WAITING),))
    )

    assert forge.notifications == {}
    [(path, message)] = degraded
    assert path == "ting.workflow_gate_notification"
    assert "/ting/review/run/run-1" in message


async def test_a_silent_gate_is_not_announced() -> None:
    forge = FakeForge()
    session = forge.add_session("coder", "t")
    campaign = _campaign({"pendingBehavior": "silent"})
    state = RunState(
        campaign.id,
        steps=(
            _member(campaign.id, "implement", "coder", session),
            _gate(campaign.id, StepState.WAITING),
        ),
    )

    await _notifier(forge).sync(campaign, state)

    assert forge.notification_calls == 0


@pytest.mark.parametrize(
    ("state", "verdict", "resolution"),
    [
        (StepState.DONE, "request_changes", "Changes requested."),
        (StepState.CANCELED, None, "The run ended with the gate open (canceled)."),
    ],
)
async def test_a_gate_that_left_waiting_settles_its_notification_with_what_happened(
    state: StepState, verdict: str | None, resolution: str
) -> None:
    forge = FakeForge()
    session = forge.add_session("coder", "t")
    campaign = _campaign()
    run_state = RunState(
        campaign.id,
        steps=(
            _member(campaign.id, "implement", "coder", session),
            _gate(campaign.id, state, verdict=verdict, closed_at=NOW),
        ),
    )

    await _notifier(forge).sync(campaign, run_state)

    [(_, (_, note))] = forge.notifications.items()
    assert forge.resolved == {note["id"]: resolution}


async def test_a_long_summary_is_cut_to_forges_limit_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cut: list[str] = []
    monkeypatch.setattr(
        "ting.domain.services.gate_notifications.record_truncation",
        lambda knob, **_: cut.append(knob),
    )
    forge = FakeForge()
    session = forge.add_session("coder", "t")
    campaign = _campaign({"instructions": "x" * 5000})
    state = RunState(
        campaign.id,
        steps=(
            _member(campaign.id, "implement", "coder", session),
            _gate(campaign.id, StepState.WAITING),
        ),
    )

    await _notifier(forge).sync(campaign, state)

    [(_, (_, note))] = forge.notifications.items()
    assert len(note["body"]) == 4000 and note["body"].endswith("…")
    assert cut == ["forge notification body limit (4000 characters)"]


async def test_two_syncs_of_the_same_gate_at_once_raise_it_once() -> None:
    """Each activity stream that carries the session delivers the close, and each copy
    projects the run: the gate's notification is still raised once."""
    import asyncio

    forge = FakeForge()
    session = forge.add_session("coder", "t")
    campaign = _campaign()
    state = RunState(
        campaign.id,
        steps=(
            _member(campaign.id, "implement", "coder", session),
            _gate(campaign.id, StepState.WAITING),
        ),
    )
    notifier = _notifier(forge)
    submit = forge.submit_session_notification

    async def slow_submit(*args, **kwargs):
        await asyncio.sleep(0)  # Forge is a network call: the other sync runs meanwhile
        return await submit(*args, **kwargs)

    forge.submit_session_notification = slow_submit  # type: ignore[method-assign]

    await asyncio.gather(notifier.sync(campaign, state), notifier.sync(campaign, state))

    assert forge.notification_calls == 1

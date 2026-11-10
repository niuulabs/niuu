"""The gate notifier against a fake Forge: who is told, where, and how a gate settles."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.gate_notifications import (
    GateNotifier,
    gate_correlation_id,
    paused_correlation_id,
)
from ting.domain.workflow_steps import (
    FlagKind,
    RunState,
    StepFlag,
    StepInput,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
)

from .fake_forge import FakeForge, FakeForgeFactory
from .step_support import Recorder

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


async def test_a_run_with_its_own_session_has_its_gate_recorded_there() -> None:
    forge = FakeForge()
    reviewer = forge.add_session("reviewer", "t")
    campaign = _campaign()
    root = forge.add_session("wf-run-1", f"workflow:{campaign.slug}")
    forge.sessions[root] = replace(forge.sessions[root], project_id="project-1")
    gate = _gate(
        campaign.id,
        StepState.WAITING,
        inputs=[StepInput(1, "review.passed", "e4", "review", "reviewer")],
    )
    steps = (_member(campaign.id, "review", "reviewer", reviewer), gate)

    await _notifier(forge).sync(campaign, RunState(campaign.id, steps=steps))

    [(session_id, _)] = forge.notifications.values()
    assert session_id == root


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


async def test_a_failed_run_tells_its_owner_why_on_the_failed_members_session() -> None:
    forge = FakeForge()
    coder = forge.add_session("coder", "t")
    auditor = forge.add_session("security-auditor", "t")
    campaign = _campaign()
    reason = (
        "delivery to 'security-auditor' failed: session s is failed, so it cannot take the "
        "step: PersonaProjectionError: Persona 'security-auditor' cannot run on codex"
    )
    failed = WorkflowStep(
        id=uuid4(),
        run_id=campaign.id,
        node_id="checks",
        member="security-auditor",
        kind=StepKind.STAGE_MEMBER,
        attempt=1,
        generation=0,
        state=StepState.FAILED,
        created_at=NOW,
        session_id=auditor,
        note=reason,
        closed_at=NOW,
    )
    state = RunState(campaign.id, steps=(failed, _member(campaign.id, "code", "coder", coder)))
    notifier = _notifier(forge)

    for _ in range(2):
        await notifier.sync_failure(campaign, state, WorkflowCampaignStatus.FAILED)

    [(key, (session_id, draft))] = forge.notifications.items()
    assert key == f"workflow-run-failed:{campaign.id}"
    assert session_id == auditor
    assert (draft["kind"], draft["severity"]) == ("error", "critical")
    assert draft["title"] == "run: the run failed"
    assert draft["body"] == f"security-auditor failed checks: {reason}"
    assert draft["links"][0]["url"] == "https://niuu.example/ting/review/run/run-1"


async def test_a_run_that_did_not_fail_raises_no_failure_notification() -> None:
    forge = FakeForge()
    campaign = _campaign()
    state = RunState(campaign.id, steps=(_member(campaign.id, "code", "coder", "s-1"),))

    for status in (WorkflowCampaignStatus.RUNNING, WorkflowCampaignStatus.COMPLETED):
        await _notifier(forge).sync_failure(campaign, state, status)

    assert forge.notifications == {}


async def test_a_failure_notification_forge_refused_is_raised_on_the_next_change() -> None:
    forge = FakeForge()
    coder = forge.add_session("coder", "t")
    campaign = _campaign()
    state = RunState(campaign.id, steps=(_member(campaign.id, "code", "coder", coder),))
    notifier = _notifier(forge)
    forge.fail_notify = RuntimeError("feed down")

    await notifier.sync_failure(campaign, state, WorkflowCampaignStatus.FAILED)
    forge.fail_notify = None
    await notifier.sync_failure(campaign, state, WorkflowCampaignStatus.FAILED)

    assert list(forge.notifications) == [f"workflow-run-failed:{campaign.id}"]


async def test_a_run_that_failed_before_a_member_took_a_step_is_told_on_its_root() -> None:
    forge = FakeForge()
    campaign = _campaign()
    root = forge.add_session("wf-run-1", f"workflow:{campaign.slug}")
    forge.sessions[root] = replace(forge.sessions[root], project_id="project-1")
    offered = WorkflowStep(
        id=uuid4(),
        run_id=campaign.id,
        node_id="code",
        member=None,
        kind=StepKind.STAGE_MEMBER,
        attempt=1,
        generation=0,
        state=StepState.FAILED,
        created_at=NOW,
        note="member 'coder' cannot take the offer of 'code': session s is failed",
        closed_at=NOW,
    )

    await _notifier(forge).sync_failure(
        campaign, RunState(campaign.id, steps=(offered,)), WorkflowCampaignStatus.FAILED
    )

    [(session_id, draft)] = forge.notifications.values()
    assert session_id == root
    assert "cannot take the offer" in draft["body"]


# --- a run whose sessions are stopped ---------------------------------------------------


def _offer(run_id: UUID, state: StepState = StepState.ACTIVE) -> WorkflowStep:
    offer_id = uuid4()
    return WorkflowStep(
        id=offer_id,
        run_id=run_id,
        node_id="code-merge",
        member=None,
        kind=StepKind.OFFER,
        attempt=1,
        generation=0,
        state=state,
        created_at=NOW,
        offer_id=str(offer_id),
    )


def _stopped_flag(step: WorkflowStep, *, resolved: bool = False) -> StepFlag:
    return StepFlag(
        run_id=step.run_id,
        activation_id=step.id,
        kind=FlagKind.UNIT_STOPPED,
        since=NOW,
        raised_at=NOW,
        detail="the run's root session is stopped",
        resolved_at=NOW if resolved else None,
    )


def _with_root(forge: FakeForge, campaign: WorkflowCampaign) -> str:
    root = forge.add_session("wf-run-1", f"workflow:{campaign.slug}")
    forge.sessions[root] = replace(forge.sessions[root], project_id="project-1")
    return root


async def test_a_run_whose_sessions_are_stopped_asks_its_owner_to_resume_it_on_its_root() -> None:
    forge = FakeForge()
    campaign = _campaign()
    root = _with_root(forge, campaign)
    offer = _offer(campaign.id)
    state = RunState(campaign.id, steps=(offer,), flags=(_stopped_flag(offer),))
    notifier = _notifier(forge)

    await notifier.sync_paused(campaign, state)
    await notifier.sync_paused(campaign, state)

    [(key, (session_id, draft))] = forge.notifications.items()
    assert key == paused_correlation_id(campaign.id, _stopped_flag(offer))
    assert session_id == root
    assert draft["kind"] == "attention"
    assert "sessions are stopped" in draft["title"]
    assert "code-merge" in draft["body"] and "Resume this session" in draft["body"]
    assert forge.resolved == {}
    assert forge.notification_calls == 1


@pytest.mark.parametrize(
    ("resolved", "offer_state", "resolution"),
    [
        (True, StepState.ACTIVE, "The run's sessions are running again."),
        (False, StepState.FAILED, "The step ended (failed)."),
    ],
)
async def test_the_paused_notice_is_resolved_when_the_run_is_up_again_or_the_step_ended(
    resolved: bool, offer_state: StepState, resolution: str
) -> None:
    forge = FakeForge()
    campaign = _campaign()
    _with_root(forge, campaign)
    offer = _offer(campaign.id)
    notifier = _notifier(forge)
    await notifier.sync_paused(
        campaign, RunState(campaign.id, steps=(offer,), flags=(_stopped_flag(offer),))
    )

    ended = replace(offer, state=offer_state)
    await notifier.sync_paused(
        campaign,
        RunState(campaign.id, steps=(ended,), flags=(_stopped_flag(offer, resolved=resolved),)),
    )

    [(_, (_, note))] = forge.notifications.items()
    assert forge.resolved == {note["id"]: resolution}


async def test_a_paused_notice_forge_refused_is_counted_and_raised_on_the_next_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counters = Recorder()
    monkeypatch.setattr(
        "ting.domain.services.gate_notifications.get_observability", lambda: counters
    )
    forge = FakeForge()
    campaign = _campaign()
    _with_root(forge, campaign)
    offer = _offer(campaign.id)
    state = RunState(campaign.id, steps=(offer,), flags=(_stopped_flag(offer),))
    notifier = _notifier(forge)
    forge.fail_notify = RuntimeError("feed down")

    await notifier.sync_paused(campaign, state)
    forge.fail_notify = None
    await notifier.sync_paused(campaign, state)

    assert counters.counted("ting_workflow_paused_notification_failures_total", phase="paused") == 1
    assert list(forge.notifications) == [paused_correlation_id(campaign.id, _stopped_flag(offer))]


async def test_a_paused_run_without_a_root_in_forge_is_loud(
    caplog: pytest.LogCaptureFixture,
) -> None:
    forge = FakeForge()
    campaign = _campaign()
    offer = _offer(campaign.id)
    state = RunState(campaign.id, steps=(offer,), flags=(_stopped_flag(offer),))

    with caplog.at_level(logging.ERROR):
        await _notifier(forge).sync_paused(campaign, state)

    assert forge.notifications == {}
    assert any("lists no root session" in str(r.exc_info) for r in caplog.records if r.exc_info)


async def test_other_flags_raise_no_paused_notice() -> None:
    forge = FakeForge()
    campaign = _campaign()
    _with_root(forge, campaign)
    offer = _offer(campaign.id)
    flag = replace(_stopped_flag(offer), kind=FlagKind.UNCLAIMED)

    await _notifier(forge).sync_paused(
        campaign, RunState(campaign.id, steps=(offer,), flags=(flag,))
    )

    assert forge.notifications == {} and forge.notification_calls == 0


async def test_each_time_the_run_is_stopped_gets_its_own_paused_notice() -> None:
    forge = FakeForge()
    campaign = _campaign()
    _with_root(forge, campaign)
    offer = _offer(campaign.id)
    first = _stopped_flag(offer)
    notifier = _notifier(forge)
    await notifier.sync_paused(campaign, RunState(campaign.id, steps=(offer,), flags=(first,)))
    resolved = replace(first, resolved_at=NOW, resolution="cleared")
    await notifier.sync_paused(campaign, RunState(campaign.id, steps=(offer,), flags=(resolved,)))

    again = replace(first, raised_at=NOW.replace(hour=10))
    await notifier.sync_paused(campaign, RunState(campaign.id, steps=(offer,), flags=(again,)))

    assert list(forge.notifications) == [
        paused_correlation_id(campaign.id, first),
        paused_correlation_id(campaign.id, again),
    ]
    assert list(forge.resolved) == [
        forge.notifications[paused_correlation_id(campaign.id, first)][1]["id"]
    ]

"""Which open steps are stuck, judged from the ledger and what their sessions reported."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ting.domain.step_stuck import StuckSettings, detect_stuck
from ting.domain.workflow_steps import (
    EVENT_STEP_UNCLOSED,
    FlagKind,
    RunEvent,
    RunState,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
)
from ting.ports.workflow_steps import SessionLiveness, StepObservation

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
GRACE = 120.0
SETTINGS = StuckSettings(start_grace_seconds=GRACE)
RUN_ID = uuid4()


def step(**changes) -> WorkflowStep:
    values = {
        "id": uuid4(),
        "run_id": RUN_ID,
        "node_id": "make",
        "member": "alpha",
        "kind": StepKind.STAGE_MEMBER,
        "attempt": 1,
        "generation": 0,
        "state": StepState.ACTIVE,
        "created_at": NOW - timedelta(seconds=30),
        "session_id": "ses-1",
        "activated_at": NOW - timedelta(seconds=30),
        "deadline_at": NOW + timedelta(hours=1),
    }
    values.update(changes)
    return WorkflowStep(**values)


def kinds(state: RunState, observations=None, now: datetime = NOW) -> list[str]:
    found = detect_stuck(state, observations or {}, now=now, settings=SETTINGS)
    return sorted(condition.kind.value for condition in found)


def running(reported: dict | None = None) -> StepObservation:
    return StepObservation(SessionLiveness.RUNNING, reported)


def test_the_grace_must_be_positive() -> None:
    with pytest.raises(ValueError, match="start_grace_seconds"):
        StuckSettings(start_grace_seconds=0)


def test_a_healthy_step_is_not_stuck() -> None:
    open_step = step()

    assert kinds(RunState(RUN_ID, (open_step,)), {open_step.id: running({"state": "active"})}) == []


def test_a_step_is_overdue_only_past_its_deadline() -> None:
    late = step(deadline_at=NOW - timedelta(seconds=1))
    on_time = step(deadline_at=NOW)

    [found] = detect_stuck(RunState(RUN_ID, (late,)), {}, now=NOW, settings=SETTINGS)

    assert found.kind is FlagKind.OVERDUE and found.since == late.deadline_at
    assert kinds(RunState(RUN_ID, (on_time,))) == []


def test_a_waiting_step_and_a_gate_can_be_overdue_but_only_open_ones() -> None:
    since = NOW - timedelta(hours=2)
    waiting = step(
        state=StepState.WAITING,
        waiting_on=WaitingOn(WaitingOnKind.PERSON, "alice", since),
        deadline_at=NOW - timedelta(minutes=1),
    )
    gate = step(
        kind=StepKind.GATE,
        member=None,
        session_id=None,
        state=StepState.WAITING,
        waiting_on=WaitingOn(WaitingOnKind.PERSON, "gate", since),
        deadline_at=NOW - timedelta(minutes=1),
    )
    done = step(state=StepState.DONE, deadline_at=NOW - timedelta(minutes=1))

    assert kinds(RunState(RUN_ID, (waiting, gate, done))) == ["overdue", "overdue"]


def test_a_gate_is_never_judged_by_a_session() -> None:
    gate = step(
        kind=StepKind.GATE,
        member=None,
        session_id=None,
        activated_at=NOW - timedelta(days=1),
        state=StepState.WAITING,
        waiting_on=WaitingOn(WaitingOnKind.PERSON, "gate", NOW),
        deadline_at=None,
    )

    assert kinds(RunState(RUN_ID, (gate,))) == []


def test_a_step_with_no_session_is_not_started_after_the_grace() -> None:
    fresh = step(session_id=None, activated_at=NOW - timedelta(seconds=GRACE))
    stale = step(session_id=None, activated_at=NOW - timedelta(seconds=GRACE + 1))

    assert kinds(RunState(RUN_ID, (fresh,))) == []
    [found] = detect_stuck(RunState(RUN_ID, (stale,)), {}, now=NOW, settings=SETTINGS)
    assert found.kind is FlagKind.NOT_STARTED and found.since == stale.activated_at


def test_a_waiting_step_without_a_session_is_not_called_not_started() -> None:
    waiting = step(
        session_id=None,
        state=StepState.WAITING,
        waiting_on=WaitingOn(WaitingOnKind.EVENT, "x", NOW),
        activated_at=NOW - timedelta(days=1),
    )

    assert kinds(RunState(RUN_ID, (waiting,))) == []


def test_a_running_session_that_does_not_know_the_step_is_undelivered_after_the_grace() -> None:
    fresh = step(activated_at=NOW - timedelta(seconds=GRACE))
    stale = step(activated_at=NOW - timedelta(seconds=GRACE + 1))

    assert kinds(RunState(RUN_ID, (fresh,)), {fresh.id: running()}) == []
    assert kinds(RunState(RUN_ID, (stale,)), {stale.id: running()}) == ["undelivered"]
    assert kinds(RunState(RUN_ID, (stale,)), {stale.id: running({"state": "active"})}) == []


def test_a_step_nobody_could_ask_about_is_judged_only_on_what_needs_no_question() -> None:
    stale = step(activated_at=NOW - timedelta(days=1), deadline_at=NOW - timedelta(hours=1))

    assert kinds(RunState(RUN_ID, (stale,))) == ["overdue"]


@pytest.mark.parametrize(
    "liveness", [SessionLiveness.STOPPED, SessionLiveness.FAILED, SessionLiveness.GONE]
)
def test_a_step_whose_session_ended_is_session_gone(liveness: SessionLiveness) -> None:
    open_step = step()

    [found] = detect_stuck(
        RunState(RUN_ID, (open_step,)),
        {open_step.id: StepObservation(liveness)},
        now=NOW,
        settings=SETTINGS,
    )

    assert found.kind is FlagKind.SESSION_GONE and liveness.value in found.detail


def test_a_session_that_is_still_coming_up_is_neither_gone_nor_undelivered() -> None:
    old = step(activated_at=NOW - timedelta(days=1))

    assert (
        kinds(RunState(RUN_ID, (old,)), {old.id: StepObservation(SessionLiveness.STARTING)}) == []
    )


def test_a_turn_that_ended_with_the_step_open_is_unclosed_while_it_is_active() -> None:
    open_step = step()
    ended = NOW - timedelta(seconds=5)
    event = RunEvent(
        run_id=RUN_ID,
        seq=1,
        event_type=EVENT_STEP_UNCLOSED,
        generation=0,
        created_at=ended,
        source_activation_id=open_step.id,
    )
    other = RunEvent(
        run_id=RUN_ID,
        seq=2,
        event_type=EVENT_STEP_UNCLOSED,
        generation=0,
        created_at=NOW,
        source_activation_id=uuid4(),
    )

    [found] = detect_stuck(
        RunState(RUN_ID, (open_step,), (event, other)),
        {open_step.id: running({"state": "active"})},
        now=NOW,
        settings=SETTINGS,
    )

    assert found.kind is FlagKind.UNCLOSED and found.since == ended
    parked = step(
        id=open_step.id,
        state=StepState.WAITING,
        waiting_on=WaitingOn(WaitingOnKind.EVENT, "x", NOW),
    )
    assert kinds(RunState(RUN_ID, (parked,), (event,))) == []


def test_one_step_can_be_stuck_in_several_ways() -> None:
    open_step = step(deadline_at=NOW - timedelta(minutes=1), activated_at=NOW - timedelta(hours=1))

    found = kinds(RunState(RUN_ID, (open_step,)), {open_step.id: running()})

    assert found == ["overdue", "undelivered"]

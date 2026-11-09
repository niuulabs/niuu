"""Which open steps and offers are stuck, judged from the ledger and what their sessions say."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ting.domain.step_stuck import StuckSettings, detect_stuck
from ting.domain.workflow_steps import (
    EVENT_OFFER_PUBLISHED,
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
from ting.ports.workflow_steps import SessionLiveness

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
GRACE = 120.0
SETTINGS = StuckSettings(pickup_grace_seconds=GRACE)
RUN_ID = uuid4()


def step(**changes) -> WorkflowStep:
    """A member's pickup that is working, with time left."""
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


def offer(**changes) -> WorkflowStep:
    """An offer to a node's members, waiting to be taken up."""
    offer_id = changes.pop("id", uuid4())
    values = {
        "id": offer_id,
        "member": None,
        "kind": StepKind.OFFER,
        "session_id": None,
        "offer_id": str(offer_id),
    }
    values.update(changes)
    return step(**values)


def published(item: WorkflowStep, *ago: float) -> list[RunEvent]:
    """The events of the offer being published, ``ago`` seconds before NOW each."""
    return [
        RunEvent(
            run_id=RUN_ID,
            seq=index + 1,
            event_type=EVENT_OFFER_PUBLISHED,
            generation=0,
            created_at=NOW - timedelta(seconds=seconds),
            payload={"offer_id": item.offer_id, "publication": index + 1},
            source_activation_id=item.id,
        )
        for index, seconds in enumerate(ago)
    ]


def kinds(state: RunState, liveness=None, now: datetime = NOW) -> list[str]:
    found = detect_stuck(state, liveness or {}, now=now, settings=SETTINGS)
    return sorted(condition.kind.value for condition in found)


def test_the_grace_must_be_positive() -> None:
    with pytest.raises(ValueError, match="pickup_grace_seconds"):
        StuckSettings(pickup_grace_seconds=0)


def test_a_healthy_step_is_not_stuck() -> None:
    open_step = step()

    assert kinds(RunState(RUN_ID, (open_step,)), {open_step.id: SessionLiveness.RUNNING}) == []


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

    assert kinds(RunState(RUN_ID, (gate,)), {gate.id: SessionLiveness.GONE}) == []


# --- offers: overdue and unclaimed -----------------------------------------------------


def test_an_offer_past_its_deadline_is_overdue_published_or_not() -> None:
    waiting = offer(deadline_at=NOW - timedelta(seconds=1))
    sent = offer(deadline_at=NOW - timedelta(seconds=1))

    state = RunState(RUN_ID, (waiting, sent), tuple(published(sent, GRACE / 2)))

    found = detect_stuck(state, {}, now=NOW, settings=SETTINGS)
    assert sorted((c.kind.value, c.step.id == waiting.id) for c in found) == [
        ("overdue", False),
        ("overdue", True),
    ]
    assert all(c.step.kind is StepKind.OFFER for c in found)


def test_an_offer_that_is_not_published_yet_is_never_unclaimed() -> None:
    unsent = offer(created_at=NOW - timedelta(days=1), activated_at=NOW - timedelta(days=1))

    assert kinds(RunState(RUN_ID, (unsent,))) == []


def test_a_published_offer_is_unclaimed_only_after_the_grace_since_its_last_publication() -> None:
    sent = offer()

    def judged(*ago: float) -> list[str]:
        return kinds(RunState(RUN_ID, (sent,), tuple(published(sent, *ago))))

    assert judged(GRACE) == []
    assert judged(GRACE + 1) == ["unclaimed"]
    # published again since: the grace restarts from the last publication
    assert judged(GRACE * 5, GRACE - 1) == []
    assert judged(GRACE * 5, GRACE + 1) == ["unclaimed"]


def test_an_unclaimed_offer_says_how_often_it_went_out_and_is_unclaimed_since_the_first() -> None:
    sent = offer()
    events = published(sent, 900, 500, 200)

    [found] = detect_stuck(RunState(RUN_ID, (sent,), tuple(events)), {}, now=NOW, settings=SETTINGS)

    assert found.kind is FlagKind.UNCLAIMED and found.step.id == sent.id
    assert found.since == events[0].created_at
    assert "offered 3 time(s)" in found.detail
    assert "no member took it up or passed it" in found.detail
    assert "120s" in found.detail


def test_another_offers_publication_does_not_make_this_one_unclaimed() -> None:
    mine, theirs = offer(), offer()

    state = RunState(RUN_ID, (mine, theirs), tuple(published(theirs, GRACE * 3)))

    found = detect_stuck(state, {}, now=NOW, settings=SETTINGS)
    assert [(c.kind, c.step.id) for c in found] == [(FlagKind.UNCLAIMED, theirs.id)]


@pytest.mark.parametrize("state", [StepState.DONE, StepState.FAILED, StepState.PENDING])
def test_an_offer_that_is_taken_failed_or_queued_is_not_unclaimed(state: StepState) -> None:
    item = offer(state=state)

    assert kinds(RunState(RUN_ID, (item,), tuple(published(item, GRACE * 3)))) == []


def test_an_offer_is_never_judged_by_a_session_or_called_unclosed() -> None:
    item = offer()
    unclosed = RunEvent(
        run_id=RUN_ID,
        seq=1,
        event_type=EVENT_STEP_UNCLOSED,
        generation=0,
        created_at=NOW,
        source_activation_id=item.id,
    )

    assert kinds(RunState(RUN_ID, (item,), (unclosed,)), {item.id: SessionLiveness.GONE}) == []


# --- pickups: session gone and unclosed -------------------------------------------------


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
        {open_step.id: liveness},
        now=NOW,
        settings=SETTINGS,
    )

    assert found.kind is FlagKind.SESSION_GONE and liveness.value in found.detail
    assert open_step.session_id in found.detail and found.since == NOW


def test_a_waiting_step_whose_session_ended_is_session_gone_too() -> None:
    parked = step(state=StepState.WAITING, waiting_on=WaitingOn(WaitingOnKind.EVENT, "x", NOW))

    assert kinds(RunState(RUN_ID, (parked,)), {parked.id: SessionLiveness.STOPPED}) == [
        "session_gone"
    ]


@pytest.mark.parametrize("liveness", [SessionLiveness.STARTING, SessionLiveness.RUNNING])
def test_a_session_that_is_up_or_coming_up_is_not_gone(liveness: SessionLiveness) -> None:
    old = step(activated_at=NOW - timedelta(days=1))

    assert kinds(RunState(RUN_ID, (old,)), {old.id: liveness}) == []


def test_a_step_that_is_not_open_is_not_judged_by_its_session() -> None:
    closed = step(state=StepState.DONE)

    assert kinds(RunState(RUN_ID, (closed,)), {closed.id: SessionLiveness.GONE}) == []


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
        {open_step.id: SessionLiveness.RUNNING},
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

    found = kinds(RunState(RUN_ID, (open_step,)), {open_step.id: SessionLiveness.FAILED})

    assert found == ["overdue", "session_gone"]

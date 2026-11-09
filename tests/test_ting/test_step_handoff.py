"""The step engine over the mesh: offers, pickups, joins over pickups and the reports.

A stage is offered once, on the topic its delivery names; every member whose persona takes the
topic picks it up (or passes), and each pickup is a step of its own. These tests play the
members the way their Skuld does (``Run`` reports them listening and answers each offer) and
pin what the engine records.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from tests.test_ting.graph_builders import edge, end, persona, stage, trigger
from tests.test_ting.step_support import (
    STEP_DEADLINE_SECONDS,
    Recorder,
    Run,
    custom_run,
)
from ting.domain.step_engine import listening_members, offer_publications, unit_sessions
from ting.domain.step_stuck import StuckSettings, detect_stuck
from ting.domain.workflow_steps import (
    EVENT_MEMBER_LISTENING,
    EVENT_OFFER_PASSED,
    EVENT_OFFER_PICKED_UP,
    EVENT_STEP_FAILED,
    Ending,
    FlagKind,
    HandoffRefusedError,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
)
from ting.ports.workflow_steps import SessionLiveness


def member(alias: str, **extra: object) -> dict[str, object]:
    return {"personaId": alias, **extra}


GRACE = StuckSettings(pickup_grace_seconds=300.0)


def two_reviewers(*, end_join: str = "any") -> Run:
    """A coder, then two reviewers that take the same topic, then the end."""
    return custom_run(
        [
            trigger("go"),
            stage("code", [member("coder")]),
            stage("review", [member("r1"), member("r2")], execution="parallel"),
            end(joinMode=end_join),
        ],
        [
            edge("e1", "go", "code", "go"),
            edge("e2", "code", "review", "code.ready"),
            edge("e3", "review", "done", "review.ok"),
        ],
        {
            "coder": persona({"ok": "code.ready"}, ["go"]),
            "r1": persona({"ok": "review.ok"}, ["code.ready"]),
            "r2": persona({"ok": "review.ok"}, ["code.ready"]),
        },
    )


def review_loop() -> Run:
    """A coder, one reviewer, and a rework edge back to the coder."""
    return custom_run(
        [
            trigger("go"),
            stage("code", [member("coder")]),
            stage("review", [member("r1")]),
            end(),
        ],
        [
            edge("e1", "go", "code", "go"),
            edge("e2", "code", "review", "code.ready"),
            edge("e3", "review", "done", "review.ok"),
            edge("e4", "review", "code", "review.changes", "go", rework=True),
        ],
        {
            "coder": persona({"ok": "code.ready"}, ["go"]),
            "r1": persona({"ok": "review.ok", "changes": "review.changes"}, ["code.ready"]),
        },
    )


# --- launch: the whole unit starts, and an offer waits for its members ---------------


async def test_launch_starts_the_unit_with_every_member_and_offers_the_first_step() -> None:
    run = two_reviewers()
    run.auto = False
    await run.launch()

    assert run.sessions.launches == [(run.run_id, ("coder", "r1", "r2"))]
    unit = run.sessions.units[run.run_id]
    assert unit_sessions(run.state) == unit.members
    offer = run.offer_rows("code")[0]
    assert (offer.kind, offer.state, offer.member) == (StepKind.OFFER, StepState.ACTIVE, None)
    assert offer.offer_id == str(offer.id)
    assert offer.deadline_at == offer.activated_at + timedelta(seconds=STEP_DEADLINE_SECONDS)
    assert run.sessions.offers == []  # nobody listens yet


async def test_an_offer_is_published_once_every_member_that_takes_its_topic_listens() -> None:
    run = two_reviewers()
    run.auto = False
    await run.launch()
    await run.listen("r1")
    assert run.sessions.offers == []

    await run.listen("coder")

    (offer,) = run.offers
    assert offer["kind"] == "step.offered"
    assert offer["topic"] == "go"
    assert offer["node_id"] == "code"
    assert [c["persona"]["alias"] for c in offer["contracts"]] == ["coder"]
    assert run.sessions.offers[0]["attestation"] == {
        "signed_by": "service:ting",
        "offer_id": offer["offer_id"],
    }
    assert listening_members(run.state) == {
        "r1": "session-r1",
        "coder": "session-coder",
    }
    assert offer_publications(run.state)[offer["offer_id"]]


async def test_every_member_of_the_unit_reports_listening_after_launch() -> None:
    run = two_reviewers()
    await run.launch()

    assert listening_members(run.state) == {
        "coder": "session-coder",
        "r1": "session-r1",
        "r2": "session-r2",
    }


async def test_an_offer_whose_publication_fails_stays_outstanding_and_goes_out_later() -> None:
    run = two_reviewers()
    run.sessions.fail_publish = 1
    run.auto = False
    await run.launch()
    await run.listen("coder")
    assert run.sessions.offers == []
    offer = run.offer_rows("code")[0]
    assert offer.state is StepState.ACTIVE

    assert await run.engine.publish_ready_offers(run.run_id) == 1

    assert len(run.offers) == 1


# --- fan-out: each pickup is a step -----------------------------------------------


async def test_one_offer_fans_out_to_two_consumers_and_each_pickup_is_its_own_step() -> None:
    run = two_reviewers()
    await run.launch()
    await run.close(run.current("code", "coder"), "ok")

    review_offers = [offer for offer in run.offers if offer["node_id"] == "review"]
    assert len(review_offers) == 1
    assert sorted(run.addressed(review_offers[0])) == ["r1", "r2"]
    first = run.current("review", "r1")
    second = run.current("review", "r2")
    assert first.id != second.id
    assert (first.session_id, second.session_id) == ("session-r1", "session-r2")
    assert first.offer_id == second.offer_id == review_offers[0]["offer_id"]
    assert first.picked_up_at is not None
    assert first.deadline_at == run.offer_rows("review")[0].deadline_at
    assert run.offer_rows("review")[0].state is StepState.DONE
    assert [e.event_type for e in run.state.events].count(EVENT_OFFER_PICKED_UP) == 3


async def test_each_pickup_closes_on_its_own_and_the_run_ends_when_both_have() -> None:
    run = two_reviewers()
    await run.launch()
    await run.close(run.current("code", "coder"), "ok")

    await run.close(run.current("review", "r1"), "ok")
    assert run.finished_with() == "engine.run.completed"  # an `any` end takes the first
    assert run.sessions.stopped == [run.run_id]


# --- passes ----------------------------------------------------------------------------


async def test_a_member_that_passes_is_not_a_step_and_the_other_carries_the_work() -> None:
    run = two_reviewers()
    run.passers.add("r2")
    await run.launch()
    await run.close(run.current("code", "coder"), "ok")

    assert [step.state for step in run.steps("review")] == [StepState.ACTIVE]
    assert run.steps("review")[0].member == "r1"
    passes = [e for e in run.state.events if e.event_type == EVENT_OFFER_PASSED]
    assert [(e.payload["member"], e.payload["reason"]) for e in passes] == [("r2", "filters")]


async def test_a_pass_reported_twice_is_recorded_once() -> None:
    run = two_reviewers()
    run.passers.add("r2")
    await run.launch()
    await run.close(run.current("code", "coder"), "ok")
    offer = next(offer for offer in run.offers if offer["node_id"] == "review")

    await run.decline(offer, "r2")

    passes = [e for e in run.state.events if e.event_type == EVENT_OFFER_PASSED]
    assert len(passes) == 1


async def test_an_offer_every_member_passed_fails_its_path_and_names_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = two_reviewers()
    run.passers.update({"r1", "r2"})
    await run.launch()
    await run.close(run.current("code", "coder"), "ok")

    offer_row = run.offer_rows("review")[0]
    assert offer_row.state is StepState.FAILED
    assert "every member offered 'review' passed it" in offer_row.note
    assert "r1: filters" in offer_row.note
    assert run.finished_with() == "engine.run.failed"
    assert "review" in run.failure_reason()
    assert len(run.offers) == 2  # the pass is an answer: not published again
    assert any(e.event_type == EVENT_STEP_FAILED for e in run.state.events)


# --- joins count pickups ---------------------------------------------------------------


async def test_an_all_join_waits_for_the_members_that_picked_the_work_up() -> None:
    run = two_reviewers(end_join="all")
    await run.launch()
    await run.close(run.current("code", "coder"), "ok")

    await run.close(run.current("review", "r1"), "ok")
    assert run.finished_with() is None

    await run.close(run.current("review", "r2"), "ok")
    assert run.finished_with() == "engine.run.completed"


async def test_an_all_join_does_not_wait_for_a_member_that_passed() -> None:
    run = two_reviewers(end_join="all")
    run.passers.add("r2")
    await run.launch()
    await run.close(run.current("code", "coder"), "ok")

    await run.close(run.current("review", "r1"), "ok")

    assert run.finished_with() == "engine.run.completed"


# --- rework ----------------------------------------------------------------------------


async def test_rework_offers_the_stage_again_in_a_new_generation_and_refuses_the_old_offer() -> (
    None
):
    run = review_loop()
    await run.launch()
    await run.close(run.current("code", "coder"), "ok")
    first_review = run.offer_rows("review")[0]
    await run.close(run.current("review", "r1"), "changes")

    code_offers = run.offer_rows("code")
    assert [(o.attempt, o.generation) for o in code_offers] == [(1, 0), (2, 1)]
    second_code = run.current("code", "coder")
    assert (second_code.attempt, second_code.generation) == (2, 1)
    assert second_code.offer_id == str(code_offers[1].id)
    assert [offer["attempt"] for offer in run.offers if offer["node_id"] == "code"] == [1, 2]

    # the first round's review offer no longer stands: a late pickup is refused
    unit = run.sessions.units[run.run_id]
    with pytest.raises(HandoffRefusedError, match="rework replaced it"):
        await run.engine.note_picked_up(
            run_id=run.run_id,
            session_id=unit.members["r1"],
            member="r1",
            offer_id=first_review.id,
            activation_id=uuid4(),
            attempt=first_review.attempt,
        )


# --- reports delivered again --------------------------------------------------------


async def test_a_pickup_reported_again_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(
        "ting.domain.services.workflow_step_engine.get_observability", lambda: recorder
    )
    run = two_reviewers()
    await run.launch()
    offer = run.offers[0]
    before = run.state

    await run.pickup(offer, "coder")

    after = run.state
    assert after.steps == before.steps
    assert len(after.events) == len(before.events)
    assert recorder.counted("ting_workflow_step_reports_repeated_total", report="picked_up") == 1


async def test_listening_reported_again_is_the_same_start(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(
        "ting.domain.services.workflow_step_engine.get_observability", lambda: recorder
    )
    run = two_reviewers()
    await run.launch()

    await run.listen("coder", "start-1")
    await run.listen("coder", "start-2")

    heard = [
        e.payload["start_id"]
        for e in run.state.events
        if e.event_type == EVENT_MEMBER_LISTENING and e.payload["member"] == "coder"
    ]
    assert heard == ["start-1", "start-2"]
    assert recorder.counted("ting_workflow_step_reports_repeated_total", report="listening") == 1


async def test_a_close_reported_again_is_accepted_and_changes_nothing() -> None:
    run = two_reviewers()
    await run.launch()
    code = run.current("code", "coder")
    await run.close(code, "ok")
    events = len(run.state.events)

    await run.engine.record_close(code.id, code.session_id, code.attempt, Ending.with_verdict("ok"))

    assert len(run.state.events) == events


# --- reports that do not fit the run ----------------------------------------------------


async def test_a_pickup_from_a_session_that_is_not_the_members_is_refused() -> None:
    run = two_reviewers()
    run.auto = False
    await run.launch()
    await run.listen("coder")
    offer = run.offers[0]

    with pytest.raises(HandoffRefusedError, match="member's session is session-coder"):
        await run.engine.note_picked_up(
            run_id=run.run_id,
            session_id="some-other-session",
            member="coder",
            offer_id=UUID(offer["offer_id"]),
            activation_id=run.activation_id(offer, "coder"),
            attempt=offer["attempt"],
        )

    assert run.steps("code") == []


async def test_a_pickup_by_a_member_the_offer_is_not_addressed_to_is_refused() -> None:
    run = two_reviewers()
    run.auto = False
    await run.launch()
    await run.listen("coder")
    offer = run.offers[0]

    with pytest.raises(HandoffRefusedError, match="addressed to the members that take 'go'"):
        await run.pickup(offer, "r1")


async def test_a_pickup_of_the_wrong_attempt_is_refused() -> None:
    run = two_reviewers()
    run.auto = False
    await run.launch()
    await run.listen("coder")
    offer = run.offers[0]
    unit = run.sessions.units[run.run_id]

    with pytest.raises(HandoffRefusedError, match="took up attempt 7"):
        await run.engine.note_picked_up(
            run_id=run.run_id,
            session_id=unit.members["coder"],
            member="coder",
            offer_id=UUID(offer["offer_id"]),
            activation_id=uuid4(),
            attempt=7,
        )


async def test_a_pickup_of_an_offer_the_run_never_made_is_refused() -> None:
    run = two_reviewers()
    await run.launch()
    unit = run.sessions.units[run.run_id]

    with pytest.raises(HandoffRefusedError, match="made no offer"):
        await run.engine.note_picked_up(
            run_id=run.run_id,
            session_id=unit.members["coder"],
            member="coder",
            offer_id=uuid4(),
            activation_id=uuid4(),
            attempt=1,
        )


# --- a member holds its offers back ------------------------------------------------------


async def test_an_offer_waits_while_a_member_it_is_addressed_to_still_holds_a_step() -> None:
    run = custom_run(
        [
            trigger("go"),
            stage("a", [member("w")]),
            stage("b", [member("w")]),
            end(),
        ],
        [
            edge("e1", "go", "a", "go"),
            edge("e2", "a", "b", "a.done"),
            edge("e3", "b", "done", "b.done"),
        ],
        {
            "w": persona({"ok": "a.done", "fin": "b.done"}, ["go", "a.done"]),
        },
    )
    await run.launch()
    first = run.current("a", "w")
    # the member parks its step: it still holds it, so a second offer to it cannot go out
    parked = Ending.waiting(
        WaitingOn(WaitingOnKind.PERSON, "alice", datetime(2026, 11, 8, 9, 30, tzinfo=UTC))
    )
    await run.engine.record_close(first.id, first.session_id, first.attempt, parked)
    await run.pump()
    assert [offer["node_id"] for offer in run.offers] == ["a"]

    await run.engine.record_close(
        first.id, first.session_id, first.attempt, Ending.with_verdict("ok")
    )
    await run.pump()

    assert [offer["node_id"] for offer in run.offers] == ["a", "b"]


# --- nobody picks it up ---------------------------------------------------------------------


async def test_an_offer_nobody_took_up_is_unclaimed_after_the_grace_and_published_again() -> None:
    run = two_reviewers()
    run.passers.clear()
    run.auto = False
    await run.launch()
    await run.listen("coder")
    offer = run.offers[0]
    published = offer_publications(run.state)[offer["offer_id"]]
    assert len(published) == 1
    row = run.offer_rows("code")[0]
    quiet = published[0] + timedelta(seconds=GRACE.pickup_grace_seconds + 1)

    conditions = detect_stuck(run.state, {}, now=quiet, settings=GRACE)

    (unclaimed,) = [c for c in conditions if c.kind is FlagKind.UNCLAIMED]
    assert unclaimed.step.id == row.id
    assert "no member took it up" in unclaimed.detail
    assert await run.engine.republish_offer(run.run_id, row.id)
    assert len(run.sessions.offers) == 2
    assert run.sessions.offers[1]["offer"] == run.sessions.offers[0]["offer"]
    assert len(offer_publications(run.state)[offer["offer_id"]]) == 2


async def test_an_offer_within_the_grace_or_not_yet_published_is_not_unclaimed() -> None:
    run = two_reviewers()
    run.auto = False
    await run.launch()
    later = run.state.events[-1].created_at + timedelta(seconds=GRACE.pickup_grace_seconds * 10)
    assert not [
        c
        for c in detect_stuck(run.state, {}, now=later, settings=GRACE)
        if c.kind is FlagKind.UNCLAIMED
    ]

    await run.listen("coder")
    published = offer_publications(run.state)[run.offers[0]["offer_id"]][0]
    soon = published + timedelta(seconds=GRACE.pickup_grace_seconds - 1)

    assert not [
        c
        for c in detect_stuck(run.state, {}, now=soon, settings=GRACE)
        if c.kind is FlagKind.UNCLAIMED
    ]


async def test_an_offer_past_its_deadline_is_overdue_and_failing_it_names_the_knob() -> None:
    run = two_reviewers()
    run.auto = False
    await run.launch()
    row = run.offer_rows("code")[0]
    late = row.deadline_at + timedelta(seconds=1)

    conditions = detect_stuck(run.state, {}, now=late, settings=GRACE)

    assert [c.kind for c in conditions] == [FlagKind.OVERDUE]
    assert conditions[0].step.kind is StepKind.OFFER
    assert await run.engine.fail_step(
        run.run_id, row.id, "nobody took it up; raise workflow_steps.step_deadline_seconds"
    )
    assert run.offer_rows("code")[0].state is StepState.FAILED
    assert run.finished_with() == "engine.run.failed"
    assert "step_deadline_seconds" in run.failure_reason()


async def test_a_pickup_whose_session_is_gone_is_stuck() -> None:
    run = two_reviewers()
    await run.launch()
    code = run.current("code", "coder")

    conditions = detect_stuck(
        run.state, {code.id: SessionLiveness.FAILED}, now=code.activated_at, settings=GRACE
    )

    assert [(c.kind, c.step.id) for c in conditions] == [(FlagKind.SESSION_GONE, code.id)]

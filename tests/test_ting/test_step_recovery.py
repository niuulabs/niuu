"""Operator recovery of workflow steps: retry, reassign and abort, and what they refuse.

A recovery replaces a member's step by a new offer of the same node (the next attempt, on the
same inputs); the offer goes to every member that takes the node's work and the member
picks it up again. ``reassign`` restarts the member's harness first, so the new offer waits
until the member reports itself listening again.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from ting.domain.models import WorkflowCampaignStatus
from ting.domain.step_engine import listening_members
from ting.domain.step_recovery import check_recoverable, check_run_open
from ting.domain.step_stuck import Condition
from ting.domain.workflow_steps import (
    EVENT_MEMBER_RESTARTED,
    EVENT_RECOVERY,
    EVENT_RUN_CANCELED,
    ActorKind,
    Ending,
    FlagKind,
    GateActor,
    RecoveryAction,
    RecoveryRefusedError,
    RunEvent,
    RunState,
    RunStateError,
    StepFencingError,
    StepKind,
    StepState,
)
from ting.ports.workflow_steps import SessionLiveness

from .step_support import Recorder, bundled, make_run
from .stuck_support import STEP_DEADLINE_SECONDS, StuckHarness

ENGINE = "ting.domain.services.workflow_step_engine"
PERSON = GateActor(ActorKind.PERSON, "alice")
CATCH_UP = 1  # seconds past a deadline


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    counters = Recorder()
    monkeypatch.setattr(f"{ENGINE}.get_observability", lambda: counters)
    return counters


@pytest.fixture
async def run() -> StuckHarness:
    """Both members took the work up and hold a step each."""
    harness = StuckHarness()
    await harness.launch()
    return harness


@pytest.fixture
async def solo() -> StuckHarness:
    """Only alpha took the work up (bravo passed), so a new offer is free to go out."""
    harness = StuckHarness()
    harness.run.passers.add("bravo")
    await harness.launch()
    return harness


@pytest.fixture
async def offered() -> StuckHarness:
    """The offer is published and nobody took it up."""
    harness = StuckHarness()
    harness.run.auto = False
    await harness.launch()
    for member in ("alpha", "bravo"):
        await harness.run.listen(member)
    return harness


async def agent_fails(run: StuckHarness, member: str, reason: str = "could not build") -> None:
    """The member's session closes its step as failed."""
    step = run.step(member)
    await run.runs.record_step_closed(
        run_id=run.campaign.id,
        activation_id=step.id,
        session_id=step.session_id,
        attempt=step.attempt,
        report={"kind": "failed", "failure_reason": reason},
    )


async def go_stuck(run: StuckHarness) -> None:
    """Pass the deadline and sweep, so every open step is flagged overdue."""
    run.advance(STEP_DEADLINE_SECONDS + CATCH_UP)
    await run.sweep()


def recoveries(run: StuckHarness) -> list[dict]:
    return [event.payload for event in run.events(EVENT_RECOVERY)]


# --- retry --------------------------------------------------------------------


async def test_a_failed_step_is_retried_as_a_new_offer_of_the_same_node_and_inputs(
    run: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")
    run.advance(30)

    with caplog.at_level(logging.WARNING):
        recovered = await run.runs.retry_step(
            run.campaign, old.id, by=PERSON, reason="the flaky network is back"
        )

    offer = recovered.step
    assert offer.kind is StepKind.OFFER and offer.member is None
    assert (offer.node_id, offer.attempt) == ("make", 2)
    assert offer.generation == old.generation
    assert offer.inputs == old.inputs
    assert offer.offer_id == str(offer.id)
    assert offer.state is StepState.PENDING  # bravo, which it is also addressed to, is working
    assert run.step("alpha", attempt=1).state is StepState.FAILED
    assert run.sessions.replaced == []  # a healthy session is not restarted
    # Who, when and why, in the run's log.
    [recovery] = recoveries(run)
    assert recovery["action"] == "retry"
    assert recovery["by"] == {"kind": "person", "id": "alice"}
    assert recovery["reason"] == "the flaky network is back"
    assert recovery["activation_id"] == str(old.id)
    assert recovery["new_activation_id"] == str(offer.id) and recovery["new_attempt"] == 2
    assert run.events(EVENT_RECOVERY)[0].created_at == run.clock.now
    assert recorder.counted("ting_workflow_step_recoveries_total", action="retry") == 1
    assert any(
        "retry by alice" in record.getMessage() and record.levelno == logging.WARNING
        for record in caplog.records
    )


async def test_a_late_close_from_the_replaced_attempt_is_fenced_out(run: StuckHarness) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")
    await run.runs.retry_step(run.campaign, old.id, by=PERSON, reason="try again")
    await agent_fails(run, "bravo")  # frees bravo, so the new offer goes out
    await run.play()
    new = run.step("alpha", attempt=2)
    assert new.state is StepState.ACTIVE and new.session_id == old.session_id

    with pytest.raises(StepFencingError, match="failed"):
        await run.engine.record_close(
            old.id, old.session_id, old.attempt, Ending.with_verdict("ok")
        )

    assert run.step("alpha", attempt=2).state is StepState.ACTIVE
    # The new attempt closes normally under its own id.
    closed = await run.engine.record_close(new.id, new.session_id, 2, Ending.with_verdict("ok"))
    assert closed.finished


async def test_a_retried_offer_waits_behind_a_member_that_is_still_working_then_goes_out(
    run: StuckHarness,
) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")

    recovered = await run.runs.retry_step(run.campaign, old.id, by=PERSON, reason="again")

    # The offer is addressed to bravo as well, and bravo still holds its step.
    assert recovered.step.state is StepState.PENDING
    assert len(run.published()) == 1
    assert run.state.step(recovered.step.id) is not None

    await agent_fails(run, "bravo")
    await run.play()

    assert run.state.step(recovered.step.id).state is StepState.DONE  # type: ignore[union-attr]
    assert [o["attempt"] for o in run.published()] == [1, 2]
    assert run.step("alpha", attempt=2).state is StepState.ACTIVE
    assert run.step("bravo", attempt=2).state is StepState.ACTIVE
    assert not run.state.finished


async def test_a_stuck_step_its_session_still_holds_open_cannot_be_retried_there(
    run: StuckHarness,
) -> None:
    await go_stuck(run)
    alpha = run.step("alpha")
    attempts = len(run.state.steps)

    with pytest.raises(RecoveryRefusedError, match="reassign the step to restart the member"):
        await run.runs.retry_step(run.campaign, alpha.id, by=PERSON, reason="it is slow")

    # Nothing changed: the old attempt is open and no new one exists.
    assert run.step("alpha").state is StepState.ACTIVE
    assert len(run.state.steps) == attempts
    assert not recoveries(run)
    assert run.sessions.replaced == []


async def test_a_stuck_step_on_a_failed_session_is_retried_by_restarting_the_member(
    solo: StuckHarness,
) -> None:
    await go_stuck(solo)
    old = solo.step("alpha")
    solo.sessions.liveness[old.session_id] = SessionLiveness.FAILED

    recovered = await solo.runs.retry_step(solo.campaign, old.id, by=PERSON, reason="session died")

    assert solo.sessions.replaced == [(solo.campaign.id, "alpha", old.session_id)]
    assert solo.step("alpha", attempt=1).state is StepState.CANCELED
    # The restarted harness is not listening yet, so the offer waits for it.
    [restarted] = solo.events(EVENT_MEMBER_RESTARTED)
    assert restarted.payload == {"member": "alpha", "session_id": old.session_id}
    assert "alpha" not in listening_members(solo.state)
    assert recovered.step.state is StepState.ACTIVE
    assert len(solo.published()) == 1

    await solo.run.listen("alpha", "start-2")
    await solo.play()

    assert [o["attempt"] for o in solo.published()] == [1, 2]
    assert solo.step("alpha", attempt=2).state is StepState.ACTIVE


async def test_a_failed_step_on_a_failed_session_restarts_the_member_too(
    run: StuckHarness,
) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")
    run.sessions.liveness[old.session_id] = SessionLiveness.FAILED

    await run.runs.retry_step(run.campaign, old.id, by=PERSON, reason="session died")

    assert run.sessions.replaced == [(run.campaign.id, "alpha", old.session_id)]
    assert run.session_of("bravo") not in {replaced for _, _, replaced in run.sessions.replaced}


async def test_a_retry_that_cannot_restart_the_member_is_refused_and_creates_no_offer(
    run: StuckHarness,
) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")
    run.sessions.liveness[old.session_id] = SessionLiveness.FAILED
    run.sessions.fail_replace = True
    offers = len(run.offers())

    with pytest.raises(RuntimeError, match="cannot restart the member"):
        await run.runs.retry_step(run.campaign, old.id, by=PERSON, reason="session died")

    assert len(run.offers()) == offers
    assert not recoveries(run)


@pytest.mark.parametrize("action", ["retry", "reassign"])
async def test_a_recovery_needs_a_reason(run: StuckHarness, action: str) -> None:
    await agent_fails(run, "alpha")
    call = getattr(run.engine, f"{action}_step")

    with pytest.raises(RecoveryRefusedError, match="with a reason"):
        await call(run.campaign.id, run.step("alpha").id, by=PERSON, reason="  ")


# --- reassign ---------------------------------------------------------------------


async def test_a_stuck_step_is_reassigned_by_restarting_the_member_and_offering_again(
    solo: StuckHarness, recorder: Recorder
) -> None:
    await go_stuck(solo)
    old = solo.step("alpha")

    recovered = await solo.runs.reassign_step(
        solo.campaign, old.id, by=PERSON, reason="the session is wedged"
    )

    offer = recovered.step
    assert offer.kind is StepKind.OFFER and offer.attempt == 2
    assert offer.generation == old.generation and offer.inputs == old.inputs
    assert solo.step("alpha", attempt=1).state is StepState.CANCELED
    # The member's harness was restarted in place: same session id, not listening until it says so.
    assert solo.sessions.replaced == [(solo.campaign.id, "alpha", old.session_id)]
    assert [e.payload["member"] for e in solo.events(EVENT_MEMBER_RESTARTED)] == ["alpha"]
    assert "alpha" not in listening_members(solo.state)
    assert offer.state is StepState.ACTIVE
    assert len(solo.published()) == 1  # held back until the member listens

    await solo.run.listen("alpha", "start-2")
    await solo.play()

    again = solo.step("alpha", attempt=2)
    assert again.state is StepState.ACTIVE and again.session_id == old.session_id
    [recovery] = recoveries(solo)
    assert recovery["action"] == "reassign" and recovery["by"]["id"] == "alice"
    assert recovery["reason"] == "the session is wedged"
    assert recovery["new_activation_id"] == str(offer.id)
    assert recorder.counted("ting_workflow_step_recoveries_total", action="reassign") == 1
    # The stuck flag of the replaced attempt leaves the campaign.
    await solo.sweep()
    assert solo.flags() == []


async def test_a_reassigned_offer_waits_for_the_other_member_and_its_flags_stay(
    run: StuckHarness,
) -> None:
    await go_stuck(run)
    old = run.step("alpha")

    recovered = await run.runs.reassign_step(run.campaign, old.id, by=PERSON, reason="wedged")

    assert recovered.step.state is StepState.PENDING  # bravo still holds its step
    assert run.session_of("bravo") not in {replaced for _, _, replaced in run.sessions.replaced}
    await run.sweep()
    assert {f.activation_id for f in run.flags()} == {run.step("bravo").id}


async def test_a_late_close_from_the_old_attempt_is_fenced_out_after_a_reassign(
    solo: StuckHarness,
) -> None:
    await go_stuck(solo)
    old = solo.step("alpha")
    await solo.runs.reassign_step(solo.campaign, old.id, by=PERSON, reason="wedged")

    with pytest.raises(StepFencingError, match="canceled"):
        await solo.runs.record_step_closed(
            run_id=solo.campaign.id,
            activation_id=old.id,
            session_id=old.session_id,
            attempt=old.attempt,
            report={"kind": "verdict", "verdict": "ok"},
        )

    assert not solo.state.finished


async def test_a_failed_step_can_be_reassigned_too(run: StuckHarness) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")

    recovered = await run.runs.reassign_step(run.campaign, old.id, by=PERSON, reason="restart it")

    assert run.step("alpha", attempt=1).state is StepState.FAILED
    assert recovered.step.attempt == 2
    assert run.sessions.replaced == [(run.campaign.id, "alpha", old.session_id)]


async def test_a_reassign_that_cannot_restart_the_member_changes_no_step(
    run: StuckHarness,
) -> None:
    await go_stuck(run)
    old = run.step("alpha")
    run.sessions.fail_replace = True
    steps = list(run.state.steps)

    with pytest.raises(RuntimeError, match="cannot restart the member"):
        await run.runs.reassign_step(run.campaign, old.id, by=PERSON, reason="wedged")

    assert list(run.state.steps) == steps
    assert not recoveries(run)


async def test_asking_twice_for_a_reassign_does_not_restart_the_member_twice(
    solo: StuckHarness,
) -> None:
    await go_stuck(solo)
    old = solo.step("alpha")
    await solo.runs.reassign_step(solo.campaign, old.id, by=PERSON, reason="wedged")

    with pytest.raises(RecoveryRefusedError, match="canceled"):
        await solo.runs.reassign_step(solo.campaign, old.id, by=PERSON, reason="wedged")

    assert len(solo.sessions.replaced) == 1
    assert len(solo.offers()) == 2


async def test_two_members_keep_their_own_sessions_through_a_reassign(
    run: StuckHarness,
) -> None:
    await go_stuck(run)
    bravo = run.step("bravo")
    await run.runs.reassign_step(run.campaign, run.step("alpha").id, by=PERSON, reason="wedged")

    assert run.step("bravo").id == bravo.id
    assert run.step("bravo").session_id == bravo.session_id
    assert [member for _, member, _ in run.sessions.replaced] == ["alpha"]
    assert "bravo" in listening_members(run.state)


# --- what is refused --------------------------------------------------------------


async def test_a_step_that_is_open_and_not_flagged_is_refused(run: StuckHarness) -> None:
    alpha = run.step("alpha")

    with pytest.raises(RecoveryRefusedError, match="not flagged stuck") as refused:
        await run.runs.retry_step(run.campaign, alpha.id, by=PERSON, reason="impatient")

    assert refused.value.action is RecoveryAction.RETRY
    assert "make/alpha" in refused.value.reason and "attempt 1" in refused.value.reason
    assert run.sessions.replaced == []


async def test_a_step_that_is_done_or_unknown_is_refused(run: StuckHarness) -> None:
    alpha = run.step("alpha")
    run.with_state(alpha, StepState.DONE, verdict="ok")

    with pytest.raises(RecoveryRefusedError, match="is done, so there is nothing to recover"):
        await run.runs.retry_step(run.campaign, alpha.id, by=PERSON, reason="again")
    with pytest.raises(RecoveryRefusedError, match="has no step") as unknown:
        await run.runs.retry_step(run.campaign, uuid4(), by=PERSON, reason="again")

    assert unknown.value.missing is True


async def test_a_step_of_another_run_is_refused_as_unknown(run: StuckHarness) -> None:
    other = StuckHarness()
    await other.launch()
    foreign = other.step("alpha")

    with pytest.raises(RecoveryRefusedError, match="has no step") as refused:
        await run.engine.retry_step(run.campaign.id, foreign.id, by=PERSON, reason="again")

    assert refused.value.missing is True


async def test_an_offer_is_refused_because_no_session_runs_it(offered: StuckHarness) -> None:
    offer = offered.offer()

    with pytest.raises(RecoveryRefusedError, match="which no session runs"):
        await offered.runs.retry_step(offered.campaign, offer.id, by=PERSON, reason="again")
    with pytest.raises(RecoveryRefusedError, match="which no session runs"):
        await offered.runs.reassign_step(offered.campaign, offer.id, by=PERSON, reason="again")

    assert not recoveries(offered)


async def test_a_failed_step_with_a_newer_attempt_is_refused(run: StuckHarness) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")
    await run.runs.retry_step(run.campaign, old.id, by=PERSON, reason="again")
    await agent_fails(run, "bravo")  # frees bravo, so the new offer goes out
    await run.play()
    assert run.step("alpha").attempt == 2  # the member took the new offer up

    with pytest.raises(RecoveryRefusedError, match="already has a newer attempt"):
        await run.runs.retry_step(run.campaign, old.id, by=PERSON, reason="again")


async def test_a_gate_is_refused() -> None:
    ticket = make_run(bundled("Ticket Work Flow"))
    await ticket.launch()
    await ticket.close(ticket.current("ticket-work"), "done", {"summary": "implemented"})
    gate = next(step for step in ticket.state.steps if step.kind is StepKind.GATE)

    with pytest.raises(RecoveryRefusedError, match="is a gate, which no session runs"):
        check_recoverable(RecoveryAction.RETRY, ticket.state, ticket.run_id, gate.id)


async def test_a_step_of_an_older_generation_is_refused(run: StuckHarness) -> None:
    alpha = run.step("alpha")
    state = run.state
    reworked = RunState(
        run_id=state.run_id,
        steps=state.steps,
        events=(
            *state.events,
            _event(state, "engine.rework.taken", generation=state.generation + 1),
        ),
        flags=state.flags,
    )

    with pytest.raises(RecoveryRefusedError, match="rework replaced it"):
        check_recoverable(RecoveryAction.REASSIGN, reworked, state.run_id, alpha.id)


def _event(state: RunState, event_type: str, *, generation: int):
    return RunEvent(
        run_id=state.run_id,
        seq=state.next_seq,
        event_type=event_type,
        generation=generation,
        created_at=state.events[-1].created_at,
    )


async def test_a_queued_step_is_refused(run: StuckHarness) -> None:
    alpha = run.step("alpha")
    run.with_state(alpha, StepState.PENDING, session_id=None, deadline_at=None, activated_at=None)

    with pytest.raises(RecoveryRefusedError, match="queued behind a running step"):
        await run.runs.retry_step(run.campaign, alpha.id, by=PERSON, reason="again")


async def test_a_finished_run_is_not_revived(run: StuckHarness) -> None:
    await agent_fails(run, "alpha")
    await agent_fails(run, "bravo")
    assert run.state.finished and run.current.status is WorkflowCampaignStatus.FAILED

    for action in ("retry", "reassign"):
        with pytest.raises(RecoveryRefusedError, match="already finished"):
            await getattr(run.runs, f"{action}_step")(
                run.campaign, run.step("alpha").id, by=PERSON, reason="again"
            )

    assert run.sessions.replaced == []


def test_a_run_that_was_never_launched_is_refused() -> None:
    with pytest.raises(RecoveryRefusedError, match="never launched"):
        check_run_open(RecoveryAction.RETRY, RunState(run_id=uuid4()), uuid4())


# --- abort ------------------------------------------------------------------------


async def test_abort_cancels_the_run_with_who_when_and_why(run: StuckHarness) -> None:
    run.advance(60)

    await run.runs.cancel(run.campaign, "wrong ticket", by=PERSON)

    assert run.state.finished
    assert run.current.status is WorkflowCampaignStatus.CANCELED
    assert run.current.metadata["run_canceled"] is True
    assert [run.step(m).state for m in ("alpha", "bravo")] == [StepState.CANCELED] * 2
    [recovery] = recoveries(run)
    assert recovery == {
        "action": "abort",
        "by": {"kind": "person", "id": "alice"},
        "reason": "wrong ticket",
    }
    abort_event = run.events(EVENT_RECOVERY)[0]
    assert abort_event.created_at == run.clock.now
    [canceled] = run.events(EVENT_RUN_CANCELED)
    assert canceled.payload["reason"] == "wrong ticket"
    assert run.sessions.stopped == [run.campaign.id]  # the run's sessions are stopped


async def test_abort_cancels_an_offer_nobody_took_up(offered: StuckHarness) -> None:
    await offered.runs.cancel(offered.campaign, "wrong ticket", by=PERSON)

    assert offered.offer().state is StepState.CANCELED
    assert offered.state.finished


async def test_abort_needs_a_reason_and_a_run_that_is_still_going(run: StuckHarness) -> None:
    with pytest.raises(RunStateError, match="with a reason"):
        await run.runs.cancel(run.campaign, " ", by=PERSON)
    await run.runs.cancel(run.campaign, "done with it", by=PERSON)

    with pytest.raises(RunStateError, match="already finished"):
        await run.runs.cancel(run.campaign, "again", by=PERSON)


async def test_an_abort_without_an_actor_records_no_recovery(run: StuckHarness) -> None:
    await run.runs.cancel(run.campaign, "stale campaign sweep")

    assert recoveries(run) == []
    assert run.state.finished


# --- the engine's flag and automatic-action operations ----------------------------


async def test_raising_the_same_flag_twice_changes_nothing_the_second_time(
    run: StuckHarness,
) -> None:
    alpha = run.step("alpha")
    condition = Condition(alpha, FlagKind.UNCLOSED, "a turn ended", run.clock.now)

    first = await run.engine.raise_flags(run.campaign.id, [condition])
    second = await run.engine.raise_flags(run.campaign.id, [condition])

    assert [f.kind for f in first] == [FlagKind.UNCLOSED] and second == []
    assert len(run.state.flags) == 1


async def test_a_closed_step_is_not_flagged(run: StuckHarness) -> None:
    await agent_fails(run, "alpha")

    raised = await run.engine.raise_flags(
        run.campaign.id,
        [Condition(run.step("alpha"), FlagKind.OVERDUE, "late", run.clock.now)],
    )

    assert raised == []


async def test_an_offer_can_be_flagged(offered: StuckHarness) -> None:
    offer = offered.offer()

    raised = await offered.engine.raise_flags(
        offered.campaign.id,
        [Condition(offer, FlagKind.UNCLAIMED, "nobody took it up", offered.clock.now)],
    )

    assert [(f.activation_id, f.kind) for f in raised] == [(offer.id, FlagKind.UNCLAIMED)]


async def test_the_automatic_actions_leave_a_step_that_moved_on_alone(run: StuckHarness) -> None:
    await agent_fails(run, "alpha")
    alpha = run.step("alpha")
    offer = run.offer()  # taken up: no longer outstanding
    published = len(run.sessions.offers)

    assert await run.engine.fail_step(run.campaign.id, alpha.id, "late") is False
    assert await run.engine.fail_step(run.campaign.id, uuid4(), "late") is False
    assert await run.engine.fail_step(run.campaign.id, offer.id, "late") is False
    assert await run.engine.republish_offer(run.campaign.id, offer.id) is False
    assert await run.engine.republish_offer(run.campaign.id, uuid4()) is False
    assert await run.engine.republish_offer(run.campaign.id, alpha.id) is False
    assert not recoveries(run)
    assert len(run.sessions.offers) == published


async def test_failing_an_offer_ends_it_like_any_failed_step(offered: StuckHarness) -> None:
    offer = offered.offer()

    assert await offered.engine.fail_step(offered.campaign.id, offer.id, "no one is home")

    assert offered.offer().state is StepState.FAILED
    assert offered.offer().note == "no one is home"
    assert offered.state.finished
    [recovery] = recoveries(offered)
    assert recovery["action"] == "fail_step" and recovery["activation_id"] == str(offer.id)


async def test_flags_resolve_with_how_they_ended(run: StuckHarness) -> None:
    run.advance(STEP_DEADLINE_SECONDS + CATCH_UP)
    await run.sweep()
    alpha = run.step("alpha")

    resolved = await run.engine.resolve_flags(run.campaign.id, set())

    assert resolved == 2
    flag = run.state.flag(alpha.id, FlagKind.OVERDUE)
    assert flag is not None and flag.resolution == "condition cleared"
    assert flag.resolved_at == run.clock.now

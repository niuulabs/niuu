"""Operator recovery of workflow steps: retry, reassign and abort, and what they refuse."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from ting.domain.models import WorkflowCampaignStatus
from ting.domain.step_recovery import check_recoverable, check_run_open
from ting.domain.step_stuck import Condition
from ting.domain.workflow_steps import (
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
from ting.ports.workflow_steps import PersonaRef

from .step_support import Recorder, bundled, make_run
from .stuck_support import STEP_DEADLINE_SECONDS, StuckHarness

ENGINE = "ting.domain.services.workflow_step_engine"
PERSON = GateActor(ActorKind.PERSON, "alice")
PERSONA_REF = PersonaRef(alias="alpha", id="alpha", revision="r1", digest="sha256:" + "0" * 64)
CATCH_UP = 1  # seconds past a deadline


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    counters = Recorder()
    monkeypatch.setattr(f"{ENGINE}.get_observability", lambda: counters)
    return counters


@pytest.fixture
async def run() -> StuckHarness:
    harness = StuckHarness()
    await harness.launch()
    return harness


async def agent_fails(run: StuckHarness, member: str, reason: str = "could not build") -> None:
    """The member's session closes its step as failed, and says so when asked."""
    step = run.step(member)
    await run.runs.record_step_closed(
        run_id=run.campaign.id,
        activation_id=step.id,
        session_id=step.session_id,
        attempt=step.attempt,
        report={"kind": "failed", "failure_reason": reason},
    )
    run.forge.step_states[(step.session_id, str(step.id))] = {
        "state": "closed",
        "attempt": step.attempt,
        "ending": {"kind": "failed", "failure_reason": reason},
    }


async def go_stuck(run: StuckHarness) -> None:
    """Pass the deadline and sweep, so every open step is flagged overdue."""
    run.advance(STEP_DEADLINE_SECONDS + CATCH_UP)
    await run.sweep()


def recoveries(run: StuckHarness) -> list[dict]:
    return [event.payload for event in run.events(EVENT_RECOVERY)]


# --- retry --------------------------------------------------------------------


async def test_a_failed_step_is_retried_as_a_new_attempt_on_the_members_session(
    run: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")
    run.advance(30)

    with caplog.at_level(logging.WARNING):
        recovered = await run.runs.retry_step(
            run.campaign, old.id, by=PERSON, reason="the flaky network is back"
        )

    new = recovered.step
    assert new.id != old.id
    assert (new.node_id, new.member, new.attempt) == ("make", "alpha", 2)
    assert new.generation == old.generation
    assert new.state is StepState.ACTIVE
    assert new.inputs == old.inputs
    assert new.session_id == old.session_id  # the kept-alive session is reused
    assert run.step("alpha", attempt=1).state is StepState.FAILED
    [delivered] = [b for _, b in run.forge.deliveries if b["activation_id"] == str(new.id)]
    assert delivered["attempt"] == 2 and delivered["member"] == "alpha"
    assert len(run.forge.sessions) == 2
    # Who, when and why, in the run's log.
    [recovery] = recoveries(run)
    assert recovery["action"] == "retry"
    assert recovery["by"] == {"kind": "person", "id": "alice"}
    assert recovery["reason"] == "the flaky network is back"
    assert recovery["activation_id"] == str(old.id)
    assert recovery["new_activation_id"] == str(new.id) and recovery["new_attempt"] == 2
    assert run.events(EVENT_RECOVERY)[0].created_at == run.clock.now
    assert recorder.counted("ting_workflow_step_recoveries_total", action="retry") == 1
    assert any(
        "retry by alice" in record.getMessage() and record.levelno == logging.WARNING
        for record in caplog.records
    )


async def test_a_late_close_from_the_replaced_attempt_is_fenced_out(run: StuckHarness) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")
    recovered = await run.runs.retry_step(run.campaign, old.id, by=PERSON, reason="try again")

    with pytest.raises(StepFencingError, match="failed"):
        await run.engine.record_close(
            old.id, old.session_id, old.attempt, Ending.with_verdict("ok")
        )

    assert run.step("alpha", attempt=2).state is StepState.ACTIVE
    # The new attempt closes normally under its own id.
    closed = await run.engine.record_close(
        recovered.step.id, recovered.step.session_id, 2, Ending.with_verdict("ok")
    )
    assert closed.finished


async def test_a_stuck_step_nobody_delivered_is_canceled_and_retried_on_the_same_session(
    run: StuckHarness,
) -> None:
    alpha = run.step("alpha")
    run.forge.forgotten.add((alpha.session_id, str(alpha.id)))
    run.forge.fail_deliver = ConnectionError("forge is down")
    run.advance(200)
    await run.sweep()
    assert "undelivered" in run.kinds()
    run.forge.fail_deliver = None

    recovered = await run.runs.retry_step(
        run.campaign, alpha.id, by=PERSON, reason="the delivery never landed"
    )

    assert run.step("alpha", attempt=1).state is StepState.CANCELED
    assert recovered.step.attempt == 2 and recovered.step.state is StepState.ACTIVE
    assert recovered.step.session_id == alpha.session_id
    with pytest.raises(StepFencingError, match="canceled"):
        await run.engine.record_close(
            alpha.id, alpha.session_id, alpha.attempt, Ending.with_verdict("ok")
        )


async def test_a_step_its_session_still_holds_open_cannot_be_retried_there(
    run: StuckHarness,
) -> None:
    await go_stuck(run)
    alpha = run.step("alpha")
    attempts = len(run.state.steps)

    with pytest.raises(RecoveryRefusedError, match="reassign the step to a fresh session"):
        await run.runs.retry_step(run.campaign, alpha.id, by=PERSON, reason="it is slow")

    # Nothing changed: the old attempt is open and no new one exists.
    assert run.step("alpha").state is StepState.ACTIVE
    assert len(run.state.steps) == attempts
    assert not recoveries(run)


async def test_a_step_its_session_closed_but_ting_missed_is_not_retried_over_the_close(
    run: StuckHarness,
) -> None:
    await go_stuck(run)
    alpha = run.step("alpha")
    run.forge.step_states[(alpha.session_id, str(alpha.id))] = {
        "state": "closed",
        "attempt": 1,
        "ending": {"kind": "verdict", "verdict": "ok"},
    }

    with pytest.raises(RecoveryRefusedError, match="already closed"):
        await run.runs.retry_step(run.campaign, alpha.id, by=PERSON, reason="stuck")


async def test_a_retry_on_a_failed_session_gets_a_fresh_one_and_stops_nothing_else(
    run: StuckHarness,
) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")
    run.forge.set_status(old.session_id, "failed")

    recovered = await run.runs.retry_step(run.campaign, old.id, by=PERSON, reason="session died")

    new = recovered.step
    assert new.session_id != old.session_id
    assert run.session(new.session_id).name.endswith("-alpha-r2")
    assert (
        run.session(new.session_id).tracker_issue_id == run.session(old.session_id).tracker_issue_id
    )
    assert run.session_of("bravo") not in run.forge.stopped
    assert [r.name for r in run.forge.create_requests][-1].endswith("-alpha-r2")
    # The member's later activations find the replacement, not the failed session.
    later = await run.sessions.ensure_session(run.campaign.id, "alpha", PERSONA_REF)
    assert later == new.session_id


async def test_a_retry_that_cannot_start_reports_the_failed_attempt(run: StuckHarness) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")
    run.forge.fail_deliver = ConnectionError("forge is down")

    recovered = await run.runs.retry_step(run.campaign, old.id, by=PERSON, reason="try again")

    assert recovered.step.state is StepState.FAILED
    assert "delivery to 'alpha' failed" in recovered.step.note


@pytest.mark.parametrize("action", ["retry", "reassign"])
async def test_a_recovery_needs_a_reason(run: StuckHarness, action: str) -> None:
    await agent_fails(run, "alpha")
    call = getattr(run.engine, f"{action}_step")

    with pytest.raises(RecoveryRefusedError, match="with a reason"):
        await call(run.campaign.id, run.step("alpha").id, by=PERSON, reason="  ")


# --- reassign ---------------------------------------------------------------------


async def test_a_stuck_step_moves_to_a_fresh_session_and_the_old_one_is_stopped(
    run: StuckHarness, recorder: Recorder
) -> None:
    await go_stuck(run)
    old = run.step("alpha")
    sessions_before = len(run.forge.sessions)

    recovered = await run.runs.reassign_step(
        run.campaign, old.id, by=PERSON, reason="the session is wedged"
    )

    new = recovered.step
    assert run.step("alpha", attempt=1).state is StepState.CANCELED
    assert new.attempt == 2 and new.state is StepState.ACTIVE and new.generation == old.generation
    assert new.session_id != old.session_id
    assert len(run.forge.sessions) == sessions_before + 1
    assert run.session(new.session_id).name.endswith("-alpha-r2")
    assert old.session_id in run.forge.stopped
    assert run.session_of("bravo") not in run.forge.stopped
    [(delivered_to, body)] = [d for d in run.forge.deliveries if d[1]["attempt"] == 2]
    assert delivered_to == new.session_id and body["activation_id"] == str(new.id)
    [recovery] = recoveries(run)
    assert recovery["action"] == "reassign" and recovery["by"]["id"] == "alice"
    assert recovery["reason"] == "the session is wedged"
    assert recorder.counted("ting_workflow_step_recoveries_total", action="reassign") == 1
    # The stuck flags of the replaced attempt leave the campaign; bravo's stay.
    await run.sweep()
    assert {f.activation_id for f in run.flags()} == {run.step("bravo").id}


async def test_a_late_close_from_the_old_session_is_fenced_out_after_a_reassign(
    run: StuckHarness,
) -> None:
    await go_stuck(run)
    old = run.step("alpha")
    await run.runs.reassign_step(run.campaign, old.id, by=PERSON, reason="wedged")

    with pytest.raises(StepFencingError, match="canceled"):
        await run.runs.record_step_closed(
            run_id=run.campaign.id,
            activation_id=old.id,
            session_id=old.session_id,
            attempt=old.attempt,
            report={"kind": "verdict", "verdict": "ok"},
        )

    assert run.step("alpha", attempt=2).state is StepState.ACTIVE
    assert not run.state.finished


async def test_a_failed_step_can_be_reassigned_too(run: StuckHarness) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")

    recovered = await run.runs.reassign_step(run.campaign, old.id, by=PERSON, reason="new session")

    assert run.step("alpha", attempt=1).state is StepState.FAILED
    assert recovered.step.session_id != old.session_id
    assert old.session_id in run.forge.stopped


async def test_a_reassign_that_cannot_stop_the_old_session_changes_nothing(
    run: StuckHarness,
) -> None:
    await go_stuck(run)
    old = run.step("alpha")
    run.forge.fail_stop.add(old.session_id)
    steps = list(run.state.steps)

    with pytest.raises(Exception, match="could not be stopped"):
        await run.runs.reassign_step(run.campaign, old.id, by=PERSON, reason="wedged")

    assert list(run.state.steps) == steps
    assert not recoveries(run)


async def test_asking_twice_for_a_reassign_does_not_make_a_second_session(
    run: StuckHarness,
) -> None:
    await go_stuck(run)
    old = run.step("alpha")
    first = await run.runs.reassign_step(run.campaign, old.id, by=PERSON, reason="wedged")
    sessions = len(run.forge.sessions)

    with pytest.raises(RecoveryRefusedError, match="canceled"):
        await run.runs.reassign_step(run.campaign, old.id, by=PERSON, reason="wedged")

    assert len(run.forge.sessions) == sessions
    assert run.step("alpha").id == first.step.id


async def test_two_members_keep_their_own_sessions_through_a_reassign(
    run: StuckHarness,
) -> None:
    await go_stuck(run)
    bravo = run.step("bravo")
    await run.runs.reassign_step(run.campaign, run.step("alpha").id, by=PERSON, reason="wedged")

    assert run.step("bravo").id == bravo.id
    assert run.step("bravo").session_id == bravo.session_id
    assert bravo.session_id not in run.forge.stopped


# --- what is refused --------------------------------------------------------------


async def test_a_step_that_is_open_and_not_flagged_is_refused(run: StuckHarness) -> None:
    alpha = run.step("alpha")

    with pytest.raises(RecoveryRefusedError, match="not flagged stuck") as refused:
        await run.runs.retry_step(run.campaign, alpha.id, by=PERSON, reason="impatient")

    assert refused.value.action is RecoveryAction.RETRY
    assert "make/alpha" in refused.value.reason and "attempt 1" in refused.value.reason


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


async def test_a_failed_step_with_a_newer_attempt_is_refused(run: StuckHarness) -> None:
    await agent_fails(run, "alpha")
    old = run.step("alpha")
    await run.runs.retry_step(run.campaign, old.id, by=PERSON, reason="again")

    with pytest.raises(RecoveryRefusedError, match="already has a newer attempt"):
        await run.runs.retry_step(run.campaign, old.id, by=PERSON, reason="again")


async def test_a_gate_or_wait_is_refused() -> None:
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


def test_a_run_that_was_never_launched_is_refused() -> None:
    with pytest.raises(RecoveryRefusedError, match="never launched"):
        check_run_open(RecoveryAction.RETRY, RunState(run_id=uuid4()), uuid4())


# --- abort ------------------------------------------------------------------------


async def test_abort_cancels_the_run_with_who_when_and_why(run: StuckHarness) -> None:
    run.advance(60)

    await run.runs.cancel(run.campaign, "wrong ticket", by=PERSON)

    assert run.state.finished
    assert run.current.status is WorkflowCampaignStatus.FAILED
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
    assert run.forge.stopped  # the run's sessions are stopped


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


# --- the engine's flag operations -------------------------------------------------


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


async def test_the_automatic_actions_leave_a_step_that_moved_on_alone(run: StuckHarness) -> None:
    await agent_fails(run, "alpha")
    alpha = run.step("alpha")

    assert await run.engine.resume_start(run.campaign.id, alpha.id, "late") is False
    assert await run.engine.fail_step(run.campaign.id, alpha.id, "late") is False
    assert await run.engine.resume_start(run.campaign.id, uuid4(), "late") is False
    assert not recoveries(run)


async def test_flags_resolve_with_how_they_ended(run: StuckHarness) -> None:
    run.advance(STEP_DEADLINE_SECONDS + CATCH_UP)
    await run.sweep()
    alpha = run.step("alpha")

    resolved = await run.engine.resolve_flags(run.campaign.id, set())

    assert resolved == 2
    flag = run.state.flag(alpha.id, FlagKind.OVERDUE)
    assert flag is not None and flag.resolution == "condition cleared"
    assert flag.resolved_at == run.clock.now

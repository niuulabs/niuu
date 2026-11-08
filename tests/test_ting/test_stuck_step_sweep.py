"""The stuck-step sweep: every flag kind, raised once, the safe actions, and the read side."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from ting.domain.campaign_run_projection import STUCK_STEPS_KEY
from ting.domain.models import WorkflowCampaignStatus
from ting.domain.services.workflow_step_watch import StuckStepSweeper
from ting.domain.workflow_steps import (
    EVENT_RECOVERY,
    EVENT_STEP_FLAGGED,
    EVENT_STEP_UNCLOSED,
    Ending,
    FlagKind,
    StepFencingError,
    StepState,
    WaitingOn,
    WaitingOnKind,
)
from ting.ports.workflow_steps import SessionLiveness

from .step_support import Recorder
from .stuck_support import (
    OWNER,
    SLUG,
    START_GRACE_SECONDS,
    STEP_DEADLINE_SECONDS,
    StuckHarness,
)

WATCH = "ting.domain.services.workflow_step_watch"
ENGINE = "ting.domain.services.workflow_step_engine"


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    counters = Recorder()
    for module in (WATCH, ENGINE):
        monkeypatch.setattr(f"{module}.get_observability", lambda: counters)
    return counters


@pytest.fixture
async def run() -> StuckHarness:
    harness = StuckHarness()
    await harness.launch()
    return harness


def stuck_entries(harness: StuckHarness) -> list[dict]:
    return harness.current.metadata.get(STUCK_STEPS_KEY, [])


# --- a healthy run -----------------------------------------------------------------


async def test_a_healthy_run_is_not_flagged(run: StuckHarness, recorder: Recorder) -> None:
    run.advance(START_GRACE_SECONDS - 1)

    flagged = await run.sweep()

    assert run.flags(live=False) == []
    assert not any(flagged.values())
    assert run.notifications() == []
    assert STUCK_STEPS_KEY not in run.current.metadata
    assert recorder.total("ting_workflow_step_flags_raised_total") == 0
    assert recorder.gauge_value("ting_workflow_steps_flagged", kind="overdue") == 0


async def test_a_run_that_has_not_been_launched_or_has_finished_is_left_alone(
    run: StuckHarness,
) -> None:
    unlaunched = replace(run.campaign, id=uuid4(), slug="not-launched")
    await run.campaigns.save_campaign(unlaunched)
    alpha = run.step("alpha")
    await run.engine.record_close(alpha.id, alpha.session_id, alpha.attempt, _verdict("ok"))
    assert run.state.finished
    run.advance(STEP_DEADLINE_SECONDS * 10)

    flagged = await run.sweep()

    assert run.flags(live=False) == []
    assert not any(flagged.values())


def _verdict(verdict: str):
    return Ending.with_verdict(verdict)


# --- overdue --------------------------------------------------------------------


async def test_a_step_past_its_deadline_is_flagged_overdue_and_only_flagged(
    run: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    alpha = run.step("alpha")
    deliveries = len(run.forge.deliveries)
    run.advance(STEP_DEADLINE_SECONDS + 1)

    with caplog.at_level(logging.ERROR):
        flagged = await run.sweep()

    assert flagged[FlagKind.OVERDUE] == 2
    overdue = run.state.flag(alpha.id, FlagKind.OVERDUE)
    assert overdue is not None and overdue.since == alpha.deadline_at
    assert "past its deadline" in overdue.detail
    # Only flagged: the step is still active, nothing was restarted, redelivered or failed.
    assert run.step("alpha").state is StepState.ACTIVE
    assert len(run.forge.deliveries) == deliveries
    assert not run.events(EVENT_RECOVERY)
    assert recorder.counted("ting_workflow_step_flags_raised_total", kind="overdue") == 2
    assert recorder.gauge_value("ting_workflow_steps_flagged", kind="overdue") == 2
    assert any("is stuck (overdue)" in record.message for record in caplog.records)


async def test_a_flag_is_raised_once_per_step_and_kind_however_many_sweeps(
    run: StuckHarness, recorder: Recorder
) -> None:
    run.advance(STEP_DEADLINE_SECONDS + 1)
    await run.sweep()
    notified = len(run.notifications())

    run.advance(600)
    await run.sweep()
    await run.sweep()

    assert len(run.flags(live=False)) == 2
    assert len(run.events(EVENT_STEP_FLAGGED)) == 2
    assert recorder.counted("ting_workflow_step_flags_raised_total", kind="overdue") == 2
    assert len(run.notifications()) == notified == 2


async def test_the_owner_is_told_which_run_step_member_flag_and_since_when(
    run: StuckHarness,
) -> None:
    alpha = run.step("alpha")
    run.advance(STEP_DEADLINE_SECONDS + 1)

    await run.sweep()

    event = next(e for e in run.notifications() if e.data["member"] == "alpha")
    assert event.owner_id == OWNER
    assert event.data["run_slug"] == SLUG
    assert event.data["run_name"] == "add-endpoint"
    assert event.data["node_id"] == "make"
    assert event.data["kind"] == "overdue"
    assert event.data["since"] == alpha.deadline_at.isoformat()
    assert event.data["activation_id"] == str(alpha.id)
    assert event.data["ui_path"] == f"/ting/review/run/{SLUG}"
    flag = run.state.flag(alpha.id, FlagKind.OVERDUE)
    assert flag is not None and flag.notified_at == run.clock.now


async def test_a_notification_that_could_not_be_sent_is_sent_on_the_next_sweep(
    run: StuckHarness, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    run.advance(STEP_DEADLINE_SECONDS + 1)
    emit = run.bus.emit

    async def broken(event):
        raise ConnectionError("the bus is down")

    monkeypatch.setattr(run.bus, "emit", broken)
    await run.sweep()

    assert run.notifications() == []
    assert recorder.counted("ting_workflow_step_sweep_errors_total", stage="run") == 1
    assert all(flag.notified_at is None for flag in run.flags())

    monkeypatch.setattr(run.bus, "emit", emit)
    await run.sweep()

    assert len(run.notifications()) == 2
    assert all(flag.notified_at is not None for flag in run.flags())


async def test_the_runs_open_flags_are_on_its_campaign_for_the_web(run: StuckHarness) -> None:
    run.advance(STEP_DEADLINE_SECONDS + 1)

    await run.sweep()

    entries = stuck_entries(run)
    assert sorted(e["member"] for e in entries) == ["alpha", "bravo"]
    first = entries[0]
    assert first["kind"] == "overdue"
    assert first["node_id"] == "make"
    assert first["attempt"] == 1
    assert first["session_id"]
    assert first["since"] and first["raised_at"] and first["detail"]


async def test_a_flag_resolves_and_leaves_the_campaign_when_its_step_closes(
    run: StuckHarness,
) -> None:
    run.advance(STEP_DEADLINE_SECONDS + 1)
    await run.sweep()
    alpha = run.step("alpha")

    await run.runs.record_step_closed(
        run_id=run.campaign.id,
        activation_id=alpha.id,
        session_id=alpha.session_id,
        attempt=alpha.attempt,
        report={"kind": "failed", "failure_reason": "gave up"},
    )
    await run.sweep()

    flag = run.state.flag(alpha.id, FlagKind.OVERDUE)
    assert flag is not None and flag.is_resolved and flag.resolution == "step is failed"
    assert [e["member"] for e in stuck_entries(run)] == ["bravo"]


# --- not started ----------------------------------------------------------------


async def _crash_before_the_session(run: StuckHarness) -> None:
    """The engine committed the step as active and died before it reached a session."""
    alpha = run.step("alpha")
    run.with_state(alpha, StepState.ACTIVE, session_id=None)
    run.forge.deliveries[:] = [d for d in run.forge.deliveries if d[1]["member"] != "alpha"]


async def test_a_step_without_a_session_is_left_alone_inside_the_start_grace(
    run: StuckHarness,
) -> None:
    await _crash_before_the_session(run)
    run.advance(START_GRACE_SECONDS - 1)

    await run.sweep()

    assert run.flags(live=False) == []
    assert run.step("alpha").session_id is None


async def test_a_step_that_never_got_a_session_is_flagged_and_started(
    run: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    await _crash_before_the_session(run)
    alpha = run.step("alpha")
    run.advance(START_GRACE_SECONDS + 1)

    with caplog.at_level(logging.WARNING):
        await run.sweep()

    flag = run.state.flag(alpha.id, FlagKind.NOT_STARTED)
    assert flag is not None and flag.since == alpha.activated_at
    started = run.step("alpha")
    kept = next(s.id for s in run.forge.sessions.values() if s.name.endswith("-alpha"))
    assert started.state is StepState.ACTIVE and started.session_id == kept
    assert len(run.forge.sessions) == 2  # the member's kept-alive session was found, not recreated
    [(delivered_to, body)] = run.forge.deliveries[-1:]
    assert delivered_to == kept
    assert body["activation_id"] == str(alpha.id) and body["member"] == "alpha"
    [recovery] = run.events(EVENT_RECOVERY)
    assert recovery.payload["action"] == "resume_start"
    assert recovery.payload["by"] == {"kind": "system", "id": "ting.stuck-step-sweep"}
    assert recovery.payload["activation_id"] == str(alpha.id)
    assert (
        recorder.counted(
            "ting_workflow_step_auto_recoveries_total", action="resume_start", kind="not_started"
        )
        == 1
    )
    assert any(
        record.levelno == logging.WARNING and str(alpha.id) in record.getMessage()
        for record in caplog.records
    )
    assert [e.data["kind"] for e in run.notifications()] == ["not_started"]


async def test_the_flag_of_a_started_step_clears_on_the_next_sweep(run: StuckHarness) -> None:
    await _crash_before_the_session(run)
    run.advance(START_GRACE_SECONDS + 1)
    await run.sweep()
    assert run.kinds() == ["not_started"]

    await run.sweep()

    flag = run.state.flag(run.step("alpha").id, FlagKind.NOT_STARTED)
    assert flag is not None and flag.resolution == "condition cleared"
    assert run.flags() == []
    assert STUCK_STEPS_KEY not in run.current.metadata
    assert len(run.forge.deliveries) == 2  # bravo's first delivery and alpha's restart


async def test_a_start_that_fails_again_is_loud_and_leaves_the_step_flagged_and_active(
    run: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    await _crash_before_the_session(run)
    run.forge.fail_deliver = ConnectionError("forge is down")
    run.advance(START_GRACE_SECONDS + 1)

    with caplog.at_level(logging.ERROR):
        await run.sweep()
        first = run.kinds()
        await run.sweep()

    alpha = run.step("alpha")
    # The step is not failed by the sweep: its outcome is not the sweep's to change.
    assert alpha.state is StepState.ACTIVE
    # The first attempt found it a session but could not deliver; the next sweep sees the
    # step as it now is, bound but undelivered, and tries again.
    assert first == ["not_started"]
    assert alpha.session_id
    assert run.kinds() == ["undelivered"]
    assert (
        recorder.counted("ting_workflow_step_auto_recovery_failures_total", action="resume_start")
        == 2
    )
    assert any("Could not resume_start" in record.getMessage() for record in caplog.records)
    assert sorted(e.data["kind"] for e in run.notifications()) == ["not_started", "undelivered"]


# --- undelivered ---------------------------------------------------------------


async def test_a_session_that_does_not_know_the_activation_gets_it_again(
    run: StuckHarness, recorder: Recorder
) -> None:
    alpha = run.step("alpha")
    run.forge.forgotten.add((alpha.session_id, str(alpha.id)))
    run.advance(START_GRACE_SECONDS - 1)
    await run.sweep()
    assert run.flags(live=False) == []

    run.advance(2)
    await run.sweep()

    flag = run.state.flag(alpha.id, FlagKind.UNDELIVERED)
    assert flag is not None and alpha.session_id in flag.detail
    redelivered = [b for s, b in run.forge.deliveries if b["activation_id"] == str(alpha.id)]
    assert len(redelivered) == 2 and redelivered[0] == redelivered[1]
    assert (
        recorder.counted(
            "ting_workflow_step_auto_recoveries_total", action="resume_start", kind="undelivered"
        )
        == 1
    )
    assert run.step("alpha").state is StepState.ACTIVE
    # The redelivery landed, so the next sweep resolves the flag.
    await run.sweep()
    assert run.flags() == []


async def test_a_session_that_is_still_starting_is_not_called_undelivered(
    run: StuckHarness,
) -> None:
    alpha = run.step("alpha")
    run.forge.set_status(alpha.session_id, "provisioning")
    run.advance(START_GRACE_SECONDS * 5)

    await run.sweep()

    assert FlagKind.UNDELIVERED not in {f.kind for f in run.flags(live=False)}
    assert FlagKind.SESSION_GONE not in {f.kind for f in run.flags(live=False)}


async def test_a_session_that_cannot_be_asked_keeps_its_flag_and_is_counted(
    run: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    alpha = run.step("alpha")
    run.forge.forgotten.add((alpha.session_id, str(alpha.id)))
    run.forge.fail_deliver = ConnectionError("forge is down")
    run.advance(START_GRACE_SECONDS + 1)
    await run.sweep()
    assert run.kinds() == ["undelivered"]
    run.forge.fail_deliver = None
    run.forge.forgotten.discard((alpha.session_id, str(alpha.id)))
    run.forge.unreachable.add(alpha.session_id)

    with caplog.at_level(logging.ERROR):
        await run.sweep()

    # Not known to have recovered, so the flag stays; the other step is still judged.
    assert run.kinds() == ["undelivered"]
    assert recorder.counted("ting_workflow_step_sweep_errors_total", stage="observe") == 1
    assert any("Could not ask session" in record.getMessage() for record in caplog.records)

    run.forge.unreachable.clear()
    await run.sweep()
    assert run.flags() == []


# --- unclosed -----------------------------------------------------------------


async def test_a_turn_that_ended_with_the_step_open_is_flagged_and_only_flagged(
    run: StuckHarness, recorder: Recorder
) -> None:
    alpha = run.step("alpha")
    await run.runs.record_step_unclosed(
        run_id=run.campaign.id,
        activation_id=alpha.id,
        session_id=alpha.session_id,
        attempt=alpha.attempt,
    )
    unclosed_at = run.events(EVENT_STEP_UNCLOSED)[0].created_at
    run.advance(5)
    deliveries = len(run.forge.deliveries)

    await run.sweep()
    await run.sweep()

    flag = run.state.flag(alpha.id, FlagKind.UNCLOSED)
    assert flag is not None and flag.since == unclosed_at
    assert run.step("alpha").state is StepState.ACTIVE
    assert len(run.forge.deliveries) == deliveries
    assert not run.events(EVENT_RECOVERY)
    assert recorder.counted("ting_workflow_step_flags_raised_total", kind="unclosed") == 1
    [told] = run.notifications()
    assert "only flags" in told.data["next"]


# --- session gone ---------------------------------------------------------------


@pytest.mark.parametrize("how", ["failed", "stopped", "deleted"])
async def test_a_step_whose_session_is_gone_is_flagged_then_failed_and_siblings_carry_on(
    run: StuckHarness, recorder: Recorder, how: str
) -> None:
    alpha = run.step("alpha")
    if how == "deleted":
        run.forge.delete_session(alpha.session_id)
    else:
        run.forge.set_status(alpha.session_id, how)

    await run.sweep()

    failed = run.step("alpha")
    assert failed.state is StepState.FAILED
    assert "session" in failed.note and alpha.session_id in failed.note
    flag = run.state.flag(alpha.id, FlagKind.SESSION_GONE)
    assert flag is not None and flag.is_resolved and flag.resolution == "step is failed"
    assert flag.notified_at is not None
    # The engine's sibling rule: bravo keeps going, the run is not failed.
    assert run.step("bravo").state is StepState.ACTIVE
    assert not run.state.finished
    [recovery] = run.events(EVENT_RECOVERY)
    assert recovery.payload["action"] == "fail_step"
    assert recovery.payload["by"]["kind"] == "system"
    assert (
        recorder.counted(
            "ting_workflow_step_auto_recoveries_total", action="fail_step", kind="session_gone"
        )
        == 1
    )
    [told] = run.notifications()
    assert told.data["kind"] == "session_gone"


async def test_the_run_fails_when_the_last_open_step_loses_its_session(
    run: StuckHarness,
) -> None:
    for member in ("alpha", "bravo"):
        run.forge.set_status(run.session_of(member), "failed")

    await run.sweep()

    assert run.state.finished
    assert run.current.status is WorkflowCampaignStatus.FAILED
    assert "session gone" in run.current.metadata["failure_error"]
    assert STUCK_STEPS_KEY not in run.current.metadata
    assert run.forge.stopped  # the run's sessions are stopped at its end


async def test_a_waiting_step_whose_session_is_gone_is_failed_too(run: StuckHarness) -> None:
    alpha = run.step("alpha")
    run.with_state(
        alpha,
        StepState.WAITING,
        waiting_on=WaitingOn(WaitingOnKind.PERSON, "alice", run.clock.now - timedelta(seconds=1)),
    )
    run.forge.set_status(alpha.session_id, "stopped")

    await run.sweep()

    assert run.step("alpha").state is StepState.FAILED


# --- a close the activity stream missed -----------------------------------------


async def test_a_close_the_session_holds_is_recorded_through_the_same_read(
    run: StuckHarness, recorder: Recorder
) -> None:
    alpha = run.step("alpha")
    run.forge.step_states[(alpha.session_id, str(alpha.id))] = {
        "state": "closed",
        "attempt": alpha.attempt,
        "ending": {"kind": "verdict", "verdict": "ok", "outcome": {}},
    }

    await run.sweep()

    assert run.step("alpha").state is StepState.DONE
    assert run.state.finished  # the end node joins on the first work.item
    assert run.current.status is WorkflowCampaignStatus.COMPLETED
    assert recorder.total("ting_workflow_step_closes_reconciled_total") == 1


async def test_a_close_for_another_attempt_is_not_applied(run: StuckHarness) -> None:
    alpha = run.step("alpha")
    run.forge.step_states[(alpha.session_id, str(alpha.id))] = {
        "state": "closed",
        "attempt": alpha.attempt + 1,
        "ending": {"kind": "verdict", "verdict": "ok", "outcome": {}},
    }

    await run.sweep()

    assert run.step("alpha").state is StepState.ACTIVE
    assert not run.state.finished


# --- the periodic task ------------------------------------------------------------


async def test_the_sweep_runs_on_its_interval_and_survives_a_failed_pass(
    run: StuckHarness, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    real = run.campaigns.list_active_campaigns

    async def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("database restarting")
        return await real()

    monkeypatch.setattr(run.campaigns, "list_active_campaigns", flaky)
    sweeper = StuckStepSweeper(
        engine=run.engine,
        runs=run.runs,
        steps=run.steps,
        campaigns=run.campaigns,  # type: ignore[arg-type]
        sessions=run.sessions,
        event_bus=run.bus,
        settings=run.sweeper._settings,
        interval_seconds=0.01,
        clock=run.clock,
    )

    await sweeper.start()
    assert sweeper.running
    await asyncio.sleep(0.1)
    await sweeper.stop()

    assert not sweeper.running
    assert calls >= 2
    assert recorder.counted("ting_workflow_step_sweep_errors_total", stage="sweep") == 1


def test_a_sweep_needs_a_positive_interval(run: StuckHarness) -> None:
    with pytest.raises(ValueError, match="positive interval"):
        StuckStepSweeper(
            engine=run.engine,
            runs=run.runs,
            steps=run.steps,
            campaigns=run.campaigns,  # type: ignore[arg-type]
            sessions=run.sessions,
            event_bus=run.bus,
            settings=run.sweeper._settings,
            interval_seconds=0,
        )


async def test_a_stopped_step_session_reports_liveness_to_the_sweep(run: StuckHarness) -> None:
    alpha = run.step("alpha")
    run.forge.set_status(alpha.session_id, "archived")

    seen = await run.sessions.observe(run.campaign.id, alpha.session_id, alpha.id)

    assert seen.liveness is SessionLiveness.STOPPED and seen.reported is None


async def test_a_late_close_after_the_sweep_failed_the_step_is_fenced_out(
    run: StuckHarness,
) -> None:
    alpha = run.step("alpha")
    run.forge.set_status(alpha.session_id, "failed")
    await run.sweep()

    with pytest.raises(StepFencingError, match="failed"):
        await run.engine.record_close(alpha.id, alpha.session_id, alpha.attempt, _verdict("ok"))

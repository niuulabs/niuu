"""The stuck-step sweep: every flag kind, raised once, the safe actions, and the read side."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from uuid import uuid4

import pytest

from ting.domain.campaign_run_projection import STUCK_STEPS_KEY
from ting.domain.models import WorkflowCampaignStatus
from ting.domain.services.workflow_step_watch import StuckStepSweeper
from ting.domain.workflow_steps import (
    EVENT_OFFER_PUBLISHED,
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
    PICKUP_GRACE_SECONDS,
    SLUG,
    STEP_DEADLINE_SECONDS,
    StuckHarness,
)

WATCH = "ting.domain.services.workflow_step_watch"
ENGINE = "ting.domain.services.workflow_step_engine"
RUNS = "ting.domain.services.workflow_runs"


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    counters = Recorder()
    for module in (WATCH, ENGINE, RUNS):
        monkeypatch.setattr(f"{module}.get_observability", lambda: counters)
    return counters


@pytest.fixture
async def run() -> StuckHarness:
    """A launched run whose two members listened and took the work up."""
    harness = StuckHarness()
    await harness.launch()
    return harness


@pytest.fixture
async def offered() -> StuckHarness:
    """A launched run whose offer is published and which nobody took up yet."""
    harness = StuckHarness()
    harness.run.auto = False
    await harness.launch()
    await listen_all(harness)
    return harness


async def listen_all(harness: StuckHarness) -> None:
    for member in ("alpha", "bravo"):
        await harness.run.listen(member)


def stuck_entries(harness: StuckHarness) -> list[dict]:
    return harness.current.metadata.get(STUCK_STEPS_KEY, [])


def _verdict(verdict: str):
    return Ending.with_verdict(verdict)


# --- a healthy run -----------------------------------------------------------------


async def test_a_healthy_run_is_not_flagged(run: StuckHarness, recorder: Recorder) -> None:
    run.advance(PICKUP_GRACE_SECONDS * 3)

    flagged = await run.sweep()

    assert run.flags(live=False) == []
    assert not any(flagged.values())
    assert run.notifications() == []
    assert STUCK_STEPS_KEY not in run.current.metadata
    assert recorder.total("ting_workflow_step_flags_raised_total") == 0
    assert recorder.gauge_value("ting_workflow_steps_flagged", kind="overdue") == 0
    assert len(run.published()) == 1  # nothing was published again


async def test_a_run_that_has_not_been_launched_or_has_finished_is_left_alone(
    run: StuckHarness,
) -> None:
    unlaunched = replace(run.campaign, id=uuid4(), slug="not-launched")
    await run.campaigns.save_campaign(unlaunched)
    alpha = run.step("alpha")
    await run.engine.record_close(alpha.id, alpha.session_id, alpha.attempt, _verdict("ok"))
    assert run.state.finished
    run.advance(STEP_DEADLINE_SECONDS * 10)
    published = len(run.sessions.offers)

    flagged = await run.sweep()

    assert run.flags(live=False) == []
    assert not any(flagged.values())
    assert len(run.sessions.offers) == published


# --- overdue --------------------------------------------------------------------


async def test_a_step_past_its_deadline_is_flagged_overdue_and_only_flagged(
    run: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    alpha = run.step("alpha")
    offers = len(run.sessions.offers)
    run.advance(STEP_DEADLINE_SECONDS + 1)

    with caplog.at_level(logging.ERROR):
        flagged = await run.sweep()

    assert flagged[FlagKind.OVERDUE] == 2
    overdue = run.state.flag(alpha.id, FlagKind.OVERDUE)
    assert overdue is not None and overdue.since == alpha.deadline_at
    assert "past its deadline" in overdue.detail
    # Only flagged: the step is still active, nothing was restarted, offered again or failed.
    assert run.step("alpha").state is StepState.ACTIVE
    assert len(run.sessions.offers) == offers
    assert run.sessions.replaced == []
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


# --- unclaimed: nobody took the offer up --------------------------------------------


async def test_an_offer_within_the_pickup_grace_is_left_alone(offered: StuckHarness) -> None:
    offered.advance(PICKUP_GRACE_SECONDS)

    await offered.sweep()

    assert offered.flags(live=False) == []
    assert len(offered.sessions.offers) == 1


async def test_an_offer_nobody_took_up_is_flagged_and_published_again_only_flagging_once(
    offered: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    offer = offered.offer()
    first_body = offered.sessions.offers[0]
    offered.advance(PICKUP_GRACE_SECONDS + 1)

    with caplog.at_level(logging.WARNING):
        flagged = await offered.sweep()

    assert flagged[FlagKind.UNCLAIMED] == 1
    flag = offered.state.flag(offer.id, FlagKind.UNCLAIMED)
    assert flag is not None
    assert "no member took it up or passed it" in flag.detail
    # The same offer went out again; the sweep claimed no action for it.
    assert len(offered.sessions.offers) == 2
    assert offered.sessions.offers[1]["offer"] == first_body["offer"]
    assert not flag.action_attempted
    publications = offered.events(EVENT_OFFER_PUBLISHED)
    assert [e.payload["publication"] for e in publications] == [1, 2]
    assert recorder.total("ting_workflow_offers_republished_total") == 1
    assert any(
        "was not taken up; published it again" in record.getMessage() for record in caplog.records
    )
    assert offered.offer().state is StepState.ACTIVE
    [told] = offered.notifications()
    assert told.data["kind"] == "unclaimed"
    assert told.data["member"] == ""
    assert "publishes the same offer again" in told.data["next"]
    assert [e["kind"] for e in stuck_entries(offered)] == ["unclaimed"]

    # It went out again just now, so the grace starts over and the flag clears.
    await offered.sweep()
    cleared = offered.state.flag(offer.id, FlagKind.UNCLAIMED)
    assert cleared is not None and cleared.resolution == "condition cleared"
    assert STUCK_STEPS_KEY not in offered.current.metadata


async def test_an_offer_is_published_again_each_grace_but_flagged_only_once(
    offered: StuckHarness, recorder: Recorder
) -> None:
    for _ in range(3):
        offered.advance(PICKUP_GRACE_SECONDS + 1)
        await offered.sweep()

    assert len(offered.sessions.offers) == 4
    assert len(offered.state.flags) == 1
    assert recorder.total("ting_workflow_offers_republished_total") == 3
    assert recorder.counted("ting_workflow_step_flags_raised_total", kind="unclaimed") == 1
    assert len(offered.notifications()) == 1


async def test_a_member_that_takes_the_republished_offer_up_ends_it(
    offered: StuckHarness,
) -> None:
    offer = offered.offer()
    offered.advance(PICKUP_GRACE_SECONDS + 1)
    await offered.sweep()
    body = offered.published()[-1]

    await offered.run.pickup(body, "alpha")
    offered.advance(PICKUP_GRACE_SECONDS * 3)
    await offered.sweep()

    assert offered.offer().state is StepState.DONE
    assert len(offered.sessions.offers) == 2
    flag = offered.state.flag(offer.id, FlagKind.UNCLAIMED)
    assert flag is not None and flag.is_resolved and flag.resolution == "step is done"


async def test_an_offer_every_member_passed_is_not_published_again_by_the_sweep(
    offered: StuckHarness,
) -> None:
    body = offered.published()[0]
    for member in ("alpha", "bravo"):
        await offered.run.decline(body, member)
    assert offered.offer().state is StepState.FAILED
    offered.advance(PICKUP_GRACE_SECONDS * 3)

    await offered.sweep()

    assert len(offered.sessions.offers) == 1


async def test_a_republication_that_fails_is_logged_counted_and_left_flagged(
    offered: StuckHarness,
    recorder: Recorder,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refused(run_id, offer_id):
        raise ConnectionError("the root does not answer")

    monkeypatch.setattr(offered.engine, "republish_offer", refused)
    offered.advance(PICKUP_GRACE_SECONDS + 1)

    with caplog.at_level(logging.ERROR):
        await offered.sweep()

    assert offered.kinds() == ["unclaimed"]
    assert len(offered.sessions.offers) == 1
    assert (
        recorder.counted(
            "ting_workflow_step_auto_recovery_failures_total", action="republish_offer"
        )
        == 1
    )
    assert any(
        record.levelno == logging.ERROR and "the root does not answer" in record.getMessage()
        for record in caplog.records
    )


async def test_a_publication_the_root_refuses_is_counted_and_goes_out_on_the_next_pass(
    offered: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    offered.advance(PICKUP_GRACE_SECONDS + 1)
    offered.sessions.fail_publish = 1

    with caplog.at_level(logging.ERROR):
        await offered.sweep()

    assert len(offered.sessions.offers) == 1
    assert recorder.total("ting_workflow_offer_publish_failures_total") == 1
    assert any("Could not publish offer" in record.getMessage() for record in caplog.records)
    assert offered.kinds() == ["unclaimed"]

    await offered.sweep()

    assert len(offered.sessions.offers) == 2


async def test_the_sweep_publishes_an_offer_that_could_not_go_out_when_its_members_listened(
    recorder: Recorder,
) -> None:
    harness = StuckHarness()
    harness.run.auto = False
    await harness.launch()
    harness.sessions.fail_publish = 1
    await listen_all(harness)
    assert harness.sessions.offers == []
    assert harness.offer().state is StepState.ACTIVE

    await harness.sweep()

    assert len(harness.sessions.offers) == 1
    assert harness.flags(live=False) == []


# --- a run whose sessions are stopped -------------------------------------------------


def root_of(harness: StuckHarness) -> str:
    return f"root-{harness.campaign.id}"


@pytest.mark.parametrize(
    "liveness", [SessionLiveness.STOPPED, SessionLiveness.FAILED, SessionLiveness.GONE]
)
async def test_an_offer_whose_run_root_ended_is_flagged_unit_stopped_and_told_once(
    offered: StuckHarness, recorder: Recorder, liveness: SessionLiveness
) -> None:
    offered.sessions.liveness[root_of(offered)] = liveness

    flagged = await offered.sweep()
    await offered.sweep()

    item = offered.offer()
    assert flagged[FlagKind.UNIT_STOPPED] == 1
    flag = offered.state.flag(item.id, FlagKind.UNIT_STOPPED)
    assert flag is not None and liveness.value in flag.detail
    # Only flagged: the offer waits for the run to be resumed, nothing is failed.
    assert item.state is StepState.ACTIVE
    assert not offered.state.finished
    [event] = [e for e in offered.notifications() if e.data["kind"] == "unit_stopped"]
    assert event.data["node_id"] == item.node_id
    assert "Resume the run's coordinator session" in event.data["next"]
    assert recorder.counted("ting_workflow_step_flags_raised_total", kind="unit_stopped") == 1


async def test_a_unit_stopped_flag_resolves_once_the_run_root_is_up_again(
    offered: StuckHarness,
) -> None:
    offered.sessions.liveness[root_of(offered)] = SessionLiveness.STOPPED
    await offered.sweep()
    assert offered.kinds() == ["unit_stopped"]

    offered.sessions.liveness[root_of(offered)] = SessionLiveness.STARTING
    await offered.sweep()

    assert offered.flags() == []
    assert offered.offer().state is StepState.ACTIVE


async def test_a_run_stopped_again_while_the_same_step_waits_is_flagged_and_told_again(
    offered: StuckHarness, recorder: Recorder
) -> None:
    root = root_of(offered)
    offered.sessions.liveness[root] = SessionLiveness.STOPPED
    await offered.sweep()
    offered.sessions.liveness[root] = SessionLiveness.RUNNING
    await offered.sweep()
    assert offered.flags() == []

    offered.advance(60)
    offered.sessions.liveness[root] = SessionLiveness.STOPPED
    await offered.sweep()

    flag = offered.state.flag(offered.offer().id, FlagKind.UNIT_STOPPED)
    assert flag is not None and not flag.is_resolved
    assert flag.raised_at == offered.clock.now and flag.notified_at == offered.clock.now
    told = [e for e in offered.notifications() if e.data["kind"] == "unit_stopped"]
    assert len(told) == 2
    assert recorder.counted("ting_workflow_step_flags_raised_total", kind="unit_stopped") == 2


async def test_other_flags_are_still_raised_once_per_step(offered: StuckHarness) -> None:
    offered.advance(PICKUP_GRACE_SECONDS + 1)
    await offered.sweep()  # unclaimed, published again
    await offered.sweep()  # within the grace of the republication: the flag resolves
    offered.advance(PICKUP_GRACE_SECONDS + 1)

    await offered.sweep()

    unclaimed = [e for e in offered.notifications() if e.data["kind"] == "unclaimed"]
    assert len(unclaimed) == 1


async def test_a_run_root_that_cannot_be_asked_keeps_the_offers_flag_and_is_counted(
    offered: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    offered.sessions.liveness[root_of(offered)] = SessionLiveness.STOPPED
    await offered.sweep()
    offered.sessions.fail_observe.add(root_of(offered))

    with caplog.at_level(logging.ERROR):
        await offered.sweep()

    # Not known to be up again, so the flag stays.
    assert offered.kinds() == ["unit_stopped"]
    assert recorder.counted("ting_workflow_step_sweep_errors_total", stage="observe") == 1
    assert any("Could not ask whether root session" in r.getMessage() for r in caplog.records)


async def test_a_run_without_an_open_offer_never_asks_about_its_root(run: StuckHarness) -> None:
    run.sessions.fail_observe.add(root_of(run))

    await run.sweep()

    assert run.flags(live=False) == []


# --- an offer that cannot be taken ----------------------------------------------------


async def test_an_offer_nobody_took_by_its_deadline_fails_naming_the_knob(
    offered: StuckHarness, recorder: Recorder
) -> None:
    offer = offered.offer()
    offered.advance(STEP_DEADLINE_SECONDS + 1)

    await offered.sweep()

    failed = offered.offer()
    assert failed.state is StepState.FAILED
    assert "workflow_steps.step_deadline_seconds" in failed.note
    assert "nobody took up the offer of 'make'" in failed.note
    overdue = offered.state.flag(offer.id, FlagKind.OVERDUE)
    assert overdue is not None and overdue.is_resolved and overdue.resolution == "step is failed"
    assert offered.state.finished
    assert offered.current.status is WorkflowCampaignStatus.FAILED
    assert "step_deadline_seconds" in offered.current.metadata["failure_error"]
    [recovery] = offered.events(EVENT_RECOVERY)
    assert recovery.payload["action"] == "fail_step"
    assert recovery.payload["by"]["kind"] == "system"
    assert recovery.payload["member"] is None
    assert (
        recorder.counted(
            "ting_workflow_step_auto_recoveries_total", action="fail_step", kind="overdue"
        )
        == 1
    )
    assert offered.sessions.stopped == [offered.campaign.id]
    assert STUCK_STEPS_KEY not in offered.current.metadata


async def test_an_offer_whose_members_never_listened_fails_at_its_deadline_too() -> None:
    harness = StuckHarness()
    harness.run.deaf.update({"alpha", "bravo"})
    await harness.launch()
    assert harness.sessions.offers == []
    harness.advance(STEP_DEADLINE_SECONDS + 1)

    await harness.sweep()

    assert harness.offer().state is StepState.FAILED
    assert "step_deadline_seconds" in harness.offer().note
    assert harness.state.finished


async def test_an_offer_waiting_for_a_member_whose_session_ended_fails_without_waiting(
    recorder: Recorder,
) -> None:
    harness = StuckHarness()
    harness.run.deaf.add("alpha")
    await harness.launch()
    assert harness.sessions.offers == []  # alpha never listened, so the offer is held back
    alpha_session = harness.session_of("alpha")
    harness.sessions.liveness[alpha_session] = SessionLiveness.FAILED
    harness.sessions.failures[alpha_session] = "the image could not be pulled"

    await harness.sweep()

    failed = harness.offer()
    assert failed.state is StepState.FAILED
    assert "member 'alpha' cannot take the offer of 'make'" in failed.note
    assert f"session {alpha_session} is failed: the image could not be pulled" in failed.note
    assert harness.state.finished
    assert harness.current.status is WorkflowCampaignStatus.FAILED
    assert (
        recorder.counted(
            "ting_workflow_step_auto_recoveries_total", action="fail_step", kind="session_gone"
        )
        == 1
    )


async def test_an_offer_whose_member_ended_after_it_listened_fails_naming_why(
    offered: StuckHarness,
) -> None:
    # A member that listened and then could not start again (after a restart) can no more
    # take the offer than one that never listened.
    bravo = offered.session_of("bravo")
    offered.sessions.liveness[bravo] = SessionLiveness.STOPPED
    offered.sessions.failures[bravo] = "MeshAttestationUnavailableError: Forge refused it"

    await offered.sweep()

    failed = offered.offer()
    assert failed.state is StepState.FAILED
    assert "member 'bravo' cannot take the offer of 'make'" in failed.note
    assert f"session {bravo} is stopped: MeshAttestationUnavailableError" in failed.note


async def test_an_offer_held_by_a_stopped_run_is_paused_not_failed_for_its_stopped_members(
    offered: StuckHarness,
) -> None:
    offered.sessions.liveness[root_of(offered)] = SessionLiveness.STOPPED
    for member in ("alpha", "bravo"):
        offered.sessions.liveness[offered.session_of(member)] = SessionLiveness.STOPPED

    await offered.sweep()

    assert offered.offer().state is StepState.ACTIVE
    assert offered.kinds() == ["unit_stopped"]
    assert not offered.state.finished


async def test_a_failure_recorded_but_whose_sessions_could_not_be_stopped_is_not_a_lost_step(
    recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    harness = StuckHarness()
    harness.run.deaf.add("alpha")
    await harness.launch()
    alpha_session = harness.session_of("alpha")
    harness.sessions.liveness[alpha_session] = SessionLiveness.FAILED
    harness.sessions.fail_stop = ConnectionError("the unit's pod is gone")

    with caplog.at_level("ERROR"):
        await harness.sweep()

    # The offer and the run are recorded failed even though ending the unit's sessions failed.
    assert harness.offer().state is StepState.FAILED
    assert harness.state.finished
    assert harness.current.status is WorkflowCampaignStatus.FAILED
    # It is its own loud error and counted as such, never "could not fail step".
    messages = [record.getMessage() for record in caplog.records]
    assert any("sessions could not all be stopped" in message for message in messages)
    assert not any("Could not fail step" in message for message in messages)
    assert recorder.counted("ting_workflow_run_stop_failures_total") == 1
    assert (
        recorder.counted(
            "ting_workflow_step_auto_recovery_failures_total", action="stop_run_sessions"
        )
        == 1
    )
    assert (
        recorder.counted("ting_workflow_step_auto_recovery_failures_total", action="fail_step") == 0
    )
    assert (
        recorder.counted(
            "ting_workflow_step_auto_recoveries_total", action="fail_step", kind="session_gone"
        )
        == 1
    )


@pytest.mark.parametrize("liveness", [SessionLiveness.STOPPED, SessionLiveness.GONE])
async def test_a_stopped_or_deleted_member_session_fails_the_waiting_offer_with_its_state(
    liveness: SessionLiveness,
) -> None:
    harness = StuckHarness()
    harness.run.deaf.add("bravo")
    await harness.launch()
    bravo_session = harness.session_of("bravo")
    harness.sessions.liveness[bravo_session] = liveness

    await harness.sweep()

    assert harness.offer().state is StepState.FAILED
    assert f"session {bravo_session} is {liveness.value}" in harness.offer().note


async def test_a_member_session_that_is_still_coming_up_does_not_fail_the_offer() -> None:
    harness = StuckHarness()
    harness.run.deaf.add("alpha")
    await harness.launch()
    harness.sessions.liveness[harness.session_of("alpha")] = SessionLiveness.STARTING
    harness.advance(PICKUP_GRACE_SECONDS * 2)

    await harness.sweep()

    assert harness.offer().state is StepState.ACTIVE
    assert not harness.state.finished
    assert harness.flags(live=False) == []


async def test_a_member_session_that_cannot_be_asked_is_loud_and_fails_nothing(
    caplog: pytest.LogCaptureFixture, recorder: Recorder
) -> None:
    harness = StuckHarness()
    harness.run.deaf.add("alpha")
    await harness.launch()
    harness.sessions.fail_observe.add(harness.session_of("alpha"))

    with caplog.at_level(logging.ERROR):
        await harness.sweep()

    assert harness.offer().state is StepState.ACTIVE
    assert recorder.counted("ting_workflow_step_sweep_errors_total", stage="run") == 1
    assert any("cannot ask" in str(record.exc_info) for record in caplog.records if record.exc_info)


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
    offers = len(run.sessions.offers)

    await run.sweep()
    await run.sweep()

    flag = run.state.flag(alpha.id, FlagKind.UNCLOSED)
    assert flag is not None and flag.since == unclosed_at
    assert run.step("alpha").state is StepState.ACTIVE
    assert len(run.sessions.offers) == offers
    assert run.sessions.replaced == []
    assert not run.events(EVENT_RECOVERY)
    assert recorder.counted("ting_workflow_step_flags_raised_total", kind="unclosed") == 1
    [told] = run.notifications()
    assert "only flags" in told.data["next"]


# --- session gone ---------------------------------------------------------------


@pytest.mark.parametrize(
    "liveness", [SessionLiveness.FAILED, SessionLiveness.STOPPED, SessionLiveness.GONE]
)
async def test_a_step_whose_session_is_gone_is_flagged_then_failed_and_siblings_carry_on(
    run: StuckHarness, recorder: Recorder, liveness: SessionLiveness
) -> None:
    alpha = run.step("alpha")
    run.sessions.liveness[alpha.session_id] = liveness

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
        run.sessions.liveness[run.session_of(member)] = SessionLiveness.FAILED

    await run.sweep()

    assert run.state.finished
    assert run.current.status is WorkflowCampaignStatus.FAILED
    assert "session gone" in run.current.metadata["failure_error"]
    assert STUCK_STEPS_KEY not in run.current.metadata
    assert run.sessions.stopped == [run.campaign.id]  # the run's sessions are stopped at its end


async def test_a_waiting_step_whose_session_is_gone_is_failed_too(run: StuckHarness) -> None:
    alpha = run.step("alpha")
    run.with_state(
        alpha,
        StepState.WAITING,
        waiting_on=WaitingOn(WaitingOnKind.PERSON, "alice", run.clock.now),
    )
    run.sessions.liveness[alpha.session_id] = SessionLiveness.STOPPED

    await run.sweep()

    assert run.step("alpha").state is StepState.FAILED


async def test_a_session_that_cannot_be_asked_keeps_its_flag_and_is_counted(
    run: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    alpha = run.step("alpha")
    await run.runs.record_step_unclosed(
        run_id=run.campaign.id,
        activation_id=alpha.id,
        session_id=alpha.session_id,
        attempt=alpha.attempt,
    )
    await run.sweep()
    assert run.kinds() == ["unclosed"]
    # The step parked itself, so the condition has cleared; Ting just cannot confirm it.
    run.with_state(
        alpha,
        StepState.WAITING,
        waiting_on=WaitingOn(WaitingOnKind.EVENT, "x", run.clock.now),
    )
    run.sessions.fail_observe.add(alpha.session_id)

    with caplog.at_level(logging.ERROR):
        await run.sweep()

    # Not known to have recovered, so the flag stays; the other step is still judged.
    assert run.kinds() == ["unclosed"]
    assert recorder.counted("ting_workflow_step_sweep_errors_total", stage="observe") == 1
    assert any("Could not ask whether session" in record.getMessage() for record in caplog.records)

    run.sessions.fail_observe.clear()
    await run.sweep()
    assert run.flags() == []


# --- what the sessions' durable logs hold -----------------------------------------


async def test_a_pickup_the_activity_stream_missed_is_recorded_from_the_session_log(
    offered: StuckHarness,
) -> None:
    body = offered.published()[0]
    activation = offered.log_pickup(body, "alpha")
    assert offered.steps.snapshot(offered.campaign.id).step(activation) is None

    await offered.sweep()

    step = offered.state.step(activation)
    assert step is not None
    assert (step.member, step.state, step.offer_id) == (
        "alpha",
        StepState.ACTIVE,
        body["offer_id"],
    )
    assert step.session_id == offered.session_of("alpha")
    assert offered.offer().state is StepState.DONE
    assert offered.flags(live=False) == []  # a taken offer is not left to be republished


async def test_a_pass_the_activity_stream_missed_is_recorded_and_fails_an_offer_all_passed(
    offered: StuckHarness,
) -> None:
    body = offered.published()[0]
    offered.log_pass(body, "alpha", "not my work")
    offered.log_pass(body, "bravo", "filters")

    await offered.sweep()

    assert offered.offer().state is StepState.FAILED
    assert "alpha: not my work" in offered.offer().note
    assert offered.state.finished


async def test_a_close_the_session_log_holds_is_recorded_through_the_same_read(
    run: StuckHarness,
) -> None:
    alpha = run.step("alpha")
    run.log_close(alpha, "ok")

    await run.sweep()

    assert run.step("alpha").state is StepState.DONE
    assert run.state.finished  # the end node joins on the first work.item
    assert run.current.status is WorkflowCampaignStatus.COMPLETED


async def test_a_log_is_read_on_from_where_the_last_sweep_stopped(run: StuckHarness) -> None:
    alpha = run.step("alpha")
    run.log_pickup(run.published()[0], "alpha")  # a repeat of the pickup Ting already holds

    await run.sweep()
    await run.sweep()

    reads = [after for session, after in run.forge.reads if session == alpha.session_id]
    assert reads == [0, 1]
    assert run.step("alpha").state is StepState.ACTIVE


async def test_a_close_for_another_attempt_is_refused_loudly_and_not_applied(
    run: StuckHarness, caplog: pytest.LogCaptureFixture
) -> None:
    alpha = run.step("alpha")
    run.forge.append(
        alpha.session_id or "",
        {
            "step": {
                "run_id": str(run.campaign.id),
                "activation_id": str(alpha.id),
                "attempt": alpha.attempt + 1,
                "member": "alpha",
                "state": "closed",
                "ending": {"kind": "verdict", "verdict": "ok", "outcome": {}},
            }
        },
    )

    with caplog.at_level(logging.ERROR):
        await run.sweep()

    assert run.step("alpha").state is StepState.ACTIVE
    assert not run.state.finished


async def test_a_session_log_that_cannot_be_read_is_loud(
    run: StuckHarness, recorder: Recorder, caplog: pytest.LogCaptureFixture
) -> None:
    run.forge.unreachable.add(run.session_of("alpha"))

    with caplog.at_level(logging.ERROR):
        await run.sweep()

    assert recorder.counted("ting_workflow_step_sweep_errors_total", stage="catch_up") == 1
    assert any(
        "Reading the session logs" in record.getMessage() and record.levelno == logging.ERROR
        for record in caplog.records
    )


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
        definitions=run.definitions,
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
            definitions=run.definitions,
            event_bus=run.bus,
            settings=run.sweeper._settings,
            interval_seconds=0,
        )


async def test_a_late_close_after_the_sweep_failed_the_step_is_fenced_out(
    run: StuckHarness,
) -> None:
    alpha = run.step("alpha")
    run.sessions.liveness[alpha.session_id] = SessionLiveness.FAILED
    await run.sweep()

    with pytest.raises(StepFencingError, match="failed"):
        await run.engine.record_close(alpha.id, alpha.session_id, alpha.attempt, _verdict("ok"))

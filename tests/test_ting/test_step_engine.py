"""Scenario tests for the step engine, driving the shipped workflows event by event."""

from __future__ import annotations

import asyncio
import copy
from datetime import timedelta
from uuid import uuid4

import pytest

from tests.test_ting.step_support import (
    SESSION_POLL_SECONDS,
    STEP_DEADLINE_SECONDS,
    FixedDefinitions,
    Recorder,
    Run,
    bundled,
    make_run,
)
from ting.domain.services.workflow_step_engine import WorkflowStepEngine
from ting.domain.step_engine import GATE_FEEDBACK_KEY, EngineSettings
from ting.domain.workflow_steps import (
    ActorKind,
    Ending,
    GateActor,
    GateDecision,
    GateRefusedError,
    InvalidEndingError,
    RunStateError,
    StepFencingError,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
)
from ting.ports.workflow_steps import SessionLiveness

REVIEWERS = (
    "developer-code-reviewer",
    "developer-security-reviewer",
    "developer-adversarial-reviewer",
)
BINDING = {"attempt_id": "attempt-1", "candidate_sha": "a" * 40, "candidate_tree": "b" * 40}


def binding(attempt: int) -> dict[str, str]:
    return {
        "attempt_id": f"attempt-{attempt}",
        "candidate_sha": f"{attempt}" * 40,
        "candidate_tree": f"{attempt}" * 39 + "t",
    }


async def drive_to_reviews(run: Run, attempt: int) -> None:
    """Code and verify one candidate; the three reviewers are then active."""
    await run.close(run.current("workstream-code"), "ready")
    await run.close(run.current("workstream-verify"), "verify", binding(attempt))


async def review(run: Run, member: str, verdict: str, attempt: int) -> None:
    await run.close(run.current("workstream-reviews", member), verdict, binding(attempt))


@pytest.fixture
def workstream() -> Run:
    return make_run(bundled("Developer Workstream"))


# --- developer-workstream: build, parallel reviews, rework, end ----------------


async def test_launch_activates_the_first_step_and_records_the_trigger(workstream: Run) -> None:
    state = await workstream.launch({"objective": "add a flag"})

    first = workstream.current("workstream-code")
    assert (first.member, first.attempt, first.generation) == ("developer-coder", 1, 0)
    assert first.session_id == "session-developer-coder"
    assert first.deadline_at == first.activated_at + timedelta(seconds=STEP_DEADLINE_SECONDS)
    assert [event.event_type for event in state.events[:2]] == [
        "engine.run.launched",
        "developer.workstream.requested",
    ]
    assert state.events[1].payload["outcome"] == {"objective": "add a flag"}
    delivered = workstream.sessions.delivered[0]
    assert delivered.member == "developer-coder"
    assert delivered.inputs[0].event_type == "developer.workstream.requested"
    assert "ready" in delivered.valid_verdicts


async def test_a_run_is_launched_once(workstream: Run) -> None:
    await workstream.launch()

    with pytest.raises(RunStateError, match="already launched"):
        await workstream.launch()


async def test_reviews_run_in_parallel_and_join_before_acceptance(workstream: Run) -> None:
    await workstream.launch()
    await drive_to_reviews(workstream, 1)

    assert {step.member for step in workstream.state.open_steps()} == set(REVIEWERS)
    await review(workstream, "developer-code-reviewer", "pass", 1)
    await review(workstream, "developer-security-reviewer", "pass", 1)
    assert workstream.steps("workstream-acceptance") == []

    await review(workstream, "developer-adversarial-reviewer", "pass", 1)

    acceptance = workstream.current("workstream-acceptance")
    assert acceptance.member == "developer-coordinator"
    assert {item.member for item in acceptance.inputs} == set(REVIEWERS)
    assert len(acceptance.inputs) == 3


async def test_failed_review_reworks_then_a_clean_rereview_ends_the_run(workstream: Run) -> None:
    await workstream.launch()
    await drive_to_reviews(workstream, 1)
    await review(workstream, "developer-code-reviewer", "pass", 1)
    await review(workstream, "developer-security-reviewer", "changes_required", 1)
    await review(workstream, "developer-adversarial-reviewer", "pass", 1)

    # The quorum does not pass, so acceptance cannot pass it.
    acceptance = workstream.current("workstream-acceptance")
    with pytest.raises(InvalidEndingError, match="do not pass"):
        await workstream.close(acceptance, "accept")
    assert workstream.current("workstream-acceptance").id == acceptance.id

    await workstream.close(acceptance, "repair")

    # A new generation: the coder works again, attempt 2, and the reviewers will rerun.
    rework = [e for e in workstream.state.events if e.event_type == "engine.rework.taken"]
    assert [(e.payload["edge_id"], e.payload["iteration"], e.generation) for e in rework] == [
        ("workstream-acceptance-repair", 1, 1)
    ]
    coder_again = workstream.current("workstream-code")
    assert (coder_again.attempt, coder_again.generation) == (2, 1)
    assert workstream.sessions.sessions[(workstream.run_id, "developer-coder")] == (
        coder_again.session_id
    )

    await drive_to_reviews(workstream, 2)
    reviewers = {step.member: step for step in workstream.state.open_steps()}
    assert set(reviewers) == set(REVIEWERS)
    assert {step.attempt for step in reviewers.values()} == {2}
    assert {step.generation for step in reviewers.values()} == {1}
    for member in REVIEWERS:
        await review(workstream, member, "pass", 2)

    second = workstream.current("workstream-acceptance")
    assert (second.attempt, second.generation) == (2, 1)
    # Only the generation-1 verdicts joined; the failed generation-0 verdict did not.
    assert all(
        workstream.state.event(item.seq).generation == 1  # type: ignore[union-attr]
        for item in second.inputs
    )
    await workstream.close(second, "accept")

    assert workstream.finished_with() == "engine.run.completed"
    assert workstream.state.open_steps() == ()
    end = workstream.steps("workstream-complete")[0]
    assert (end.kind, end.state) == (StepKind.END, StepState.DONE)
    assert "developer.workstream.completed" in workstream.event_types()
    assert workstream.sessions.stopped == [workstream.run_id]
    # One session per (run, member), reused across attempts.
    assert len(workstream.sessions.created) == len(set(workstream.sessions.created))


async def test_a_review_of_another_revision_never_passes(workstream: Run) -> None:
    await workstream.launch()
    await drive_to_reviews(workstream, 1)
    await review(workstream, "developer-code-reviewer", "pass", 1)
    await review(workstream, "developer-security-reviewer", "pass", 1)
    # The adversarial reviewer reviewed a different candidate than the one it was given.
    await workstream.close(
        workstream.current("workstream-reviews", "developer-adversarial-reviewer"),
        "pass",
        {**binding(1), "candidate_sha": "c" * 40},
    )

    acceptance = workstream.current("workstream-acceptance")
    with pytest.raises(InvalidEndingError, match="candidate_sha"):
        await workstream.close(acceptance, "accept")


async def test_a_verdict_the_persona_cannot_give_is_refused_and_the_step_stays_open(
    workstream: Run,
) -> None:
    await workstream.launch()
    coder = workstream.current("workstream-code")

    with pytest.raises(InvalidEndingError, match="valid verdicts"):
        await workstream.close(coder, "great-success")

    assert workstream.current("workstream-code").id == coder.id
    assert "engine.close.rejected" in workstream.event_types()


async def test_a_rework_edge_without_a_limit_goes_round_as_often_as_the_work_needs() -> None:
    run = make_run(bundled("Developer Workstream"))
    await run.launch()

    for attempt in range(1, 6):
        await drive_to_reviews(run, attempt)
        for member in REVIEWERS:
            await review(run, member, "pass", attempt)
        await run.close(run.current("workstream-acceptance"), "repair")

    assert run.finished_with() is None
    assert run.event_types().count("engine.rework.taken") == 5


async def test_an_author_set_rework_limit_fails_the_run_naming_the_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    workflow = bundled("Developer Workstream")
    graph = copy.deepcopy(dict(workflow.graph))
    next(e for e in graph["edges"] if e["id"] == "workstream-acceptance-repair")[
        "maxIterations"
    ] = 3
    run = make_run(workflow, graph=graph, monkeypatch=monkeypatch, recorder=recorder)
    await run.launch()

    for attempt in range(1, 4):
        await drive_to_reviews(run, attempt)
        for member in REVIEWERS:
            await review(run, member, "pass", attempt)
        await run.close(run.current("workstream-acceptance"), "repair")
    # The author allowed three reworks (maxIterations: 3); the fourth fails the run.
    assert run.finished_with() is None
    await drive_to_reviews(run, 4)
    for member in REVIEWERS:
        await review(run, member, "pass", 4)
    await run.close(run.current("workstream-acceptance"), "repair")

    assert run.finished_with() == "engine.run.failed"
    reason = run.failure_reason()
    assert "workstream-acceptance-repair" in reason
    assert "maxIterations=3" in reason
    assert "raise it on the edge" in reason
    assert (
        "ting_workflow_step_cap_hits_total",
        {"knob": "edge:workstream-acceptance-repair.maxIterations"},
    ) in recorder.counts
    assert run.state.open_steps() == ()
    assert run.sessions.stopped == [run.run_id]


# --- fencing --------------------------------------------------------------------


async def test_a_close_must_match_the_activation_attempt_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    run = make_run(bundled("Developer Workstream"), monkeypatch=monkeypatch, recorder=recorder)
    await run.launch()
    coder = run.current("workstream-code")
    ending = Ending.with_verdict("ready")

    with pytest.raises(StepFencingError, match="attempt 9"):
        await run.engine.record_close(coder.id, coder.session_id, 9, ending)
    with pytest.raises(StepFencingError, match="does not own"):
        await run.engine.record_close(coder.id, "someone-elses-session", coder.attempt, ending)
    with pytest.raises(StepFencingError, match="no such activation"):
        await run.engine.record_close(uuid4(), coder.session_id, 1, ending)

    assert run.current("workstream-code").id == coder.id
    assert recorder.total("ting_workflow_step_close_rejections_total") == 3
    rejected = [e for e in run.state.events if e.event_type == "engine.close.rejected"]
    assert [e.payload["kind"] for e in rejected] == ["fencing", "fencing"]


async def test_a_step_that_already_closed_cannot_be_closed_again(workstream: Run) -> None:
    await workstream.launch()
    coder = workstream.current("workstream-code")
    await workstream.close(coder, "ready")

    with pytest.raises(StepFencingError, match="is done"):
        await workstream.close(coder, "ready")


async def test_a_stale_step_cannot_close() -> None:
    workflow = bundled("Tool & Skill Builder")
    run = make_run(workflow, graph=parallel_reviews(workflow.graph))
    await run.launch()
    await run.close(run.current("capability-frame"), "framed")
    await run.close(run.current("capability-build"), "pass")
    reviewer = run.current("capability-review", "reviewer")
    auditor = run.current("capability-review", "security-auditor")

    # The reviewer sends the work back while the auditor is still looking at it.
    await run.close(reviewer, "needs_changes")

    stale = run.steps("capability-review", "security-auditor")[0]
    assert stale.state is StepState.STALE
    assert "engine.step.stale" in run.event_types()
    with pytest.raises(StepFencingError, match="is stale"):
        await run.close(auditor, "pass")
    # The coder is working on the new generation.
    rebuilt = run.current("capability-build")
    assert (rebuilt.attempt, rebuilt.generation) == (2, 1)


def parallel_reviews(source: dict) -> dict:
    """The tool-skill-builder graph with its review stage running both reviewers at once."""
    graph = copy.deepcopy(dict(source))
    for node in graph["nodes"]:
        if node["id"] == "capability-review":
            node["executionMode"] = "parallel"
    return graph


# --- siblings keep going ---------------------------------------------------------


async def test_a_failed_branch_fails_only_its_path_and_siblings_finish(
    workstream: Run,
) -> None:
    await workstream.launch()
    await drive_to_reviews(workstream, 1)

    await workstream.fail(
        workstream.current("workstream-reviews", "developer-security-reviewer"),
        "the sandbox for the security scan was lost",
    )

    # Nothing else was disturbed: the run is still going and the siblings are active.
    assert workstream.finished_with() is None
    assert {step.member for step in workstream.state.open_steps()} == {
        "developer-code-reviewer",
        "developer-adversarial-reviewer",
    }

    await review(workstream, "developer-code-reviewer", "pass", 1)
    assert workstream.finished_with() is None
    await review(workstream, "developer-adversarial-reviewer", "pass", 1)

    # Their verdicts were recorded, but acceptance can never join without the failed branch.
    verdicts = [
        (event.member, event.verdict)
        for event in workstream.state.events
        if event.event_type == "developer.review.completed"
    ]
    assert verdicts == [
        ("developer-code-reviewer", "pass"),
        ("developer-adversarial-reviewer", "pass"),
    ]
    assert workstream.steps("workstream-acceptance") == []
    assert workstream.finished_with() == "engine.run.failed"
    reason = workstream.failure_reason()
    assert "developer-security-reviewer" in reason
    assert "the sandbox for the security scan was lost" in reason


async def test_a_verdict_no_edge_carries_fails_the_step_not_silently(workstream: Run) -> None:
    await workstream.launch()

    await workstream.close(workstream.current("workstream-code"), "blocked")

    coder = workstream.steps("workstream-code")[0]
    assert coder.state is StepState.FAILED
    assert coder.verdict == "blocked"
    assert "developer.workstream.blocked" in coder.note
    assert workstream.finished_with() == "engine.run.failed"
    assert "developer.workstream.blocked" in workstream.failure_reason()
    # The verdict itself is still on the record.
    assert "developer.workstream.blocked" in workstream.event_types()


# --- waiting steps and end semantics ---------------------------------------------


async def test_a_step_can_park_waiting_and_be_closed_later(workstream: Run) -> None:
    await workstream.launch()
    coder = workstream.current("workstream-code")
    handle = WaitingOn(WaitingOnKind.PERSON, "jozef", workstream.state.events[0].created_at)

    await workstream.engine.record_close(
        coder.id, coder.session_id, coder.attempt, Ending.waiting(handle, note="needs a decision")
    )

    parked = workstream.steps("workstream-code")[0]
    assert (parked.state, parked.waiting_on) == (StepState.WAITING, handle)
    assert workstream.finished_with() is None
    await workstream.close(parked, "ready")
    assert workstream.current("workstream-verify")
    assert workstream.steps("workstream-code")[0].waiting_on is None


async def test_a_done_ending_is_refused_for_a_member_that_hands_off(workstream: Run) -> None:
    await workstream.launch()
    coder = workstream.current("workstream-code")

    with pytest.raises(InvalidEndingError, match="done ending is only for a member"):
        await workstream.engine.record_close(
            coder.id, coder.session_id, coder.attempt, Ending.done()
        )


async def test_cancel_cancels_open_steps_and_stops_sessions(workstream: Run) -> None:
    await workstream.launch()

    await workstream.engine.cancel(workstream.run_id, "operator asked")

    assert workstream.states("workstream-code") == ["canceled"]
    assert workstream.event_types()[-1] == "engine.run.canceled"
    assert workstream.sessions.stopped == [workstream.run_id]
    with pytest.raises(RunStateError, match="already finished"):
        await workstream.engine.cancel(workstream.run_id, "again")
    coder = workstream.steps("workstream-code")[0]
    with pytest.raises(StepFencingError):
        await workstream.close(coder, "ready")


async def test_cancel_needs_a_launched_run_and_a_reason(workstream: Run) -> None:
    with pytest.raises(RunStateError, match="never launched"):
        await workstream.engine.cancel(workstream.run_id, "too early")
    with pytest.raises(RunStateError, match="with a reason"):
        await workstream.engine.cancel(workstream.run_id, "  ")


async def test_a_step_that_cannot_reach_a_session_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    run = make_run(bundled("Developer Workstream"), monkeypatch=monkeypatch, recorder=recorder)
    run.sessions.fail_ensure.add("developer-coder")

    await run.launch()

    coder = run.steps("workstream-code")[0]
    assert coder.state is StepState.FAILED
    assert "no capacity for developer-coder" in coder.note
    assert run.finished_with() == "engine.run.failed"
    assert recorder.total("ting_workflow_step_start_failures_total") == 1


async def test_a_step_that_cannot_be_delivered_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = make_run(bundled("Developer Workstream"))
    run.sessions.fail_deliver.add("developer-coder")

    await run.launch()

    coder = run.steps("workstream-code")[0]
    assert coder.state is StepState.FAILED
    assert "developer-coder is unreachable" in coder.note
    assert run.finished_with() == "engine.run.failed"


# --- a session that is still starting ---------------------------------------------

CODER_SESSION = "session-developer-coder"


async def wait_for(condition: object, *, what: str) -> None:
    """Let the engine's background wait run until ``condition()`` holds."""
    for _ in range(200):
        if condition():  # type: ignore[operator]
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


async def test_a_step_for_a_starting_session_is_delivered_when_the_session_is_up() -> None:
    run = make_run(bundled("Developer Workstream"))
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.STARTING

    await run.launch()

    coder = run.current("workstream-code")
    assert coder.session_id == CODER_SESSION
    assert run.sessions.delivered == []
    assert run.finished_with() is None

    run.sessions.liveness[CODER_SESSION] = SessionLiveness.RUNNING
    await wait_for(lambda: run.sessions.delivered, what="the step to be delivered")

    assert [d.activation_id for d in run.sessions.delivered] == [coder.id]
    assert run.current("workstream-code").state is StepState.ACTIVE
    await run.engine.aclose()


async def test_a_session_that_ends_while_starting_fails_the_step_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    run = make_run(bundled("Developer Workstream"), monkeypatch=monkeypatch, recorder=recorder)
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.STARTING

    await run.launch()
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.FAILED
    await wait_for(lambda: run.finished_with(), what="the run to fail")

    coder = run.steps("workstream-code")[0]
    assert coder.state is StepState.FAILED
    assert f"session {CODER_SESSION} is failed" in coder.note
    assert run.sessions.delivered == []
    assert recorder.total("ting_workflow_step_start_failures_total") == 1


async def test_a_session_that_never_comes_up_fails_the_step_at_its_deadline() -> None:
    run = make_run(bundled("Developer Workstream"))
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.STARTING

    await run.launch()
    run.clock[0] += timedelta(seconds=STEP_DEADLINE_SECONDS + 1)
    await wait_for(lambda: run.finished_with(), what="the run to fail")

    coder = run.steps("workstream-code")[0]
    assert coder.state is StepState.FAILED
    assert "did not come up before the step's deadline" in coder.note
    assert "workflow_steps.step_deadline_seconds" in coder.note
    assert run.sessions.delivered == []


async def test_a_session_that_cannot_be_asked_leaves_the_step_for_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    run = make_run(bundled("Developer Workstream"), monkeypatch=monkeypatch, recorder=recorder)
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.STARTING

    await run.launch()
    run.sessions.fail_observe.add(CODER_SESSION)
    await wait_for(
        lambda: recorder.total("ting_workflow_step_wait_failures_total"),
        what="the failed ask to be counted",
    )

    assert run.current("workstream-code").state is StepState.ACTIVE
    assert run.finished_with() is None
    assert run.sessions.delivered == []


async def test_restarting_a_step_whose_session_is_still_starting_waits_instead_of_raising() -> None:
    run = make_run(bundled("Developer Workstream"))
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.STARTING
    await run.launch()
    coder = run.current("workstream-code")

    resumed = await run.engine.resume_start(run.run_id, coder.id, "stuck sweep")
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.RUNNING
    await wait_for(lambda: run.sessions.delivered, what="the step to be delivered")

    assert resumed is True
    assert run.sessions.delivered[0].activation_id == coder.id
    await run.engine.aclose()


def second_worker(run: Run) -> WorkflowStepEngine:
    """Another Ting process over the same ledger and sessions (a restart, or worker 2)."""
    return WorkflowStepEngine(
        repository=run.repository,
        sessions=run.sessions,
        definitions=FixedDefinitions(run.definition),
        settings=EngineSettings(
            step_deadline_seconds=STEP_DEADLINE_SECONDS, session_poll_seconds=SESSION_POLL_SECONDS
        ),
    )


async def test_a_restart_during_the_wait_delivers_the_step_once_the_session_is_up() -> None:
    run = make_run(bundled("Developer Workstream"))
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.STARTING
    await run.launch()
    coder = run.current("workstream-code")
    await run.engine.aclose()  # the process dies while it waits

    restarted = second_worker(run)
    assert await restarted.resume_deliveries() == 1
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.RUNNING
    await wait_for(lambda: run.sessions.delivered, what="the step to be delivered")

    assert [d.activation_id for d in run.sessions.delivered] == [coder.id]
    assert coder.id in run.repository.delivered
    assert await second_worker(run).resume_deliveries() == 0
    await restarted.aclose()


async def test_two_workers_waiting_on_the_same_step_deliver_it_once() -> None:
    run = make_run(bundled("Developer Workstream"))
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.STARTING
    await run.launch()
    other = second_worker(run)
    assert await other.resume_deliveries() == 1

    run.sessions.liveness[CODER_SESSION] = SessionLiveness.RUNNING
    await wait_for(
        lambda: not run.engine._deferred and not other._deferred,  # noqa: SLF001
        what="both waits to end",
    )

    assert len(run.sessions.delivered) == 1
    await other.aclose()


async def test_a_step_delivered_at_once_is_recorded_and_not_waited_for_again() -> None:
    run = make_run(bundled("Developer Workstream"))

    await run.launch()

    coder = run.current("workstream-code")
    assert [d.activation_id for d in run.sessions.delivered] == [coder.id]
    assert coder.id in run.repository.delivered
    assert await second_worker(run).resume_deliveries() == 0


async def test_a_step_that_closed_while_its_delivery_waited_is_not_delivered() -> None:
    run = make_run(bundled("Developer Workstream"))
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.STARTING

    await run.launch()
    coder = run.current("workstream-code")
    await run.engine.cancel(run.run_id, "operator aborted")
    run.sessions.liveness[CODER_SESSION] = SessionLiveness.RUNNING
    await wait_for(lambda: not run.engine._deferred, what="the waiting delivery to end")  # noqa: SLF001

    assert run.sessions.delivered == []
    assert run.steps("workstream-code")[0].id == coder.id


# --- a workflow with gates --------------------------------------------------------


@pytest.fixture
def ticket_work() -> Run:
    return make_run(bundled("Ticket Work Flow"))


def person(name: str = "alice") -> GateActor:
    return GateActor(ActorKind.PERSON, name)


async def reach_gate(run: Run) -> None:
    await run.launch()
    await run.close(run.current("ticket-work"), "done", {"summary": "implemented"})


async def test_a_gate_opens_as_a_waiting_step_for_a_person(ticket_work: Run) -> None:
    await reach_gate(ticket_work)

    gate = ticket_work.current("result-review-gate")
    assert (gate.kind, gate.state) == (StepKind.GATE, StepState.WAITING)
    assert gate.waiting_on is not None
    assert (gate.waiting_on.kind, gate.waiting_on.ref) == (
        WaitingOnKind.PERSON,
        "result-review-gate",
    )
    assert gate.member is None and gate.session_id is None
    assert "engine.gate.opened" in ticket_work.event_types()
    assert ticket_work.finished_with() is None


async def test_approving_a_gate_follows_its_approval_event_to_the_end(ticket_work: Run) -> None:
    await reach_gate(ticket_work)

    await ticket_work.engine.resolve_gate(
        ticket_work.run_id,
        "result-review-gate",
        GateDecision.APPROVE,
        person("alice"),
        "looks right",
    )

    gate = ticket_work.steps("result-review-gate")[0]
    assert (gate.state, gate.verdict, gate.note) == (StepState.DONE, "approve", "looks right")
    decided = next(e for e in ticket_work.state.events if e.event_type == "ticket.work.completed")
    assert decided.payload["by"] == {"kind": "person", "id": "alice"}
    assert decided.payload["outcome"] == {GATE_FEEDBACK_KEY: "looks right"}
    resolved = next(e for e in ticket_work.state.events if e.event_type == "engine.gate.resolved")
    assert resolved.payload["by"]["id"] == "alice"
    assert ticket_work.finished_with() == "engine.run.completed"


async def test_requesting_changes_sends_the_work_back_and_the_gate_reopens(
    ticket_work: Run,
) -> None:
    await reach_gate(ticket_work)

    await ticket_work.engine.resolve_gate(
        ticket_work.run_id,
        "result-review-gate",
        GateDecision.REQUEST_CHANGES,
        person(),
        "cover the empty case",
    )

    # The request for changes is the gate's own declared event, and it took the rework edge.
    assert "ticket.work.changes_requested" in ticket_work.event_types()
    rework = [e for e in ticket_work.state.events if e.event_type == "engine.rework.taken"]
    assert [e.payload["edge_id"] for e in rework] == ["edge-gate-to-work"]
    worker = ticket_work.current("ticket-work")
    assert (worker.attempt, worker.generation) == (2, 1)
    assert ticket_work.finished_with() is None
    redelivered = ticket_work.sessions.delivered[-1]
    assert redelivered.inputs[0].payload["outcome"] == {GATE_FEEDBACK_KEY: "cover the empty case"}

    await ticket_work.close(worker, "done")
    reopened = ticket_work.current("result-review-gate")
    assert (reopened.attempt, reopened.generation) == (2, 1)
    await ticket_work.engine.resolve_gate(
        ticket_work.run_id, "result-review-gate", GateDecision.APPROVE, person(), ""
    )
    assert ticket_work.finished_with() == "engine.run.completed"
    assert ticket_work.states("result-review-gate") == ["done", "done"]


async def test_an_agent_may_not_answer_a_gate_that_waits_for_a_person(ticket_work: Run) -> None:
    await reach_gate(ticket_work)

    with pytest.raises(GateRefusedError, match="waits for a person"):
        await ticket_work.engine.resolve_gate(
            ticket_work.run_id,
            "result-review-gate",
            GateDecision.APPROVE,
            GateActor(ActorKind.AGENT, "regin"),
        )

    assert ticket_work.current("result-review-gate").state is StepState.WAITING


async def test_an_automated_approval_gate_accepts_an_agent() -> None:
    workflow = bundled("Ticket Work Flow")
    graph = copy.deepcopy(dict(workflow.graph))
    next(n for n in graph["nodes"] if n["id"] == "result-review-gate")["mode"] = (
        "automated_approval"
    )
    run = make_run(workflow, graph=graph)
    await reach_gate(run)

    gate = run.current("result-review-gate")
    assert gate.waiting_on is not None and gate.waiting_on.kind is WaitingOnKind.AGENT
    await run.engine.resolve_gate(
        run.run_id, "result-review-gate", GateDecision.APPROVE, GateActor(ActorKind.AGENT, "regin")
    )

    assert run.finished_with() == "engine.run.completed"


async def test_a_gate_with_nothing_to_resolve_is_refused(ticket_work: Run) -> None:
    await ticket_work.launch()

    with pytest.raises(GateRefusedError, match="no open request"):
        await ticket_work.engine.resolve_gate(
            ticket_work.run_id, "result-review-gate", GateDecision.APPROVE, person()
        )
    with pytest.raises(GateRefusedError, match="not a gate"):
        await ticket_work.engine.resolve_gate(
            ticket_work.run_id, "ticket-work", GateDecision.APPROVE, person()
        )


async def test_a_gate_cannot_be_closed_as_a_step(ticket_work: Run) -> None:
    await reach_gate(ticket_work)
    gate = ticket_work.current("result-review-gate")

    with pytest.raises(InvalidEndingError, match="resolve_gate"):
        await ticket_work.engine.record_close(
            gate.id, gate.session_id, gate.attempt, Ending.with_verdict("approve")
        )


async def test_a_blocked_worker_reaches_the_blocked_gate_and_a_person_unblocks_it(
    ticket_work: Run,
) -> None:
    await ticket_work.launch()
    await ticket_work.close(ticket_work.current("ticket-work"), "blocked")

    assert ticket_work.current("work-blocked").state is StepState.WAITING
    await ticket_work.engine.resolve_gate(
        ticket_work.run_id, "work-blocked", GateDecision.APPROVE, person(), "ticket fixed"
    )

    # Both decisions of a blocked gate send the worker back to try again.
    assert ticket_work.current("ticket-work").attempt == 2


# --- other semantics ---------------------------------------------------------------


async def test_a_unique_activation_id_is_derived_from_run_node_member_and_attempt(
    workstream: Run,
) -> None:
    await workstream.launch()
    coder = workstream.current("workstream-code")

    other = make_run(bundled("Developer Workstream"))
    await other.launch()

    assert other.current("workstream-code").id != coder.id
    assert coder.id == workstream.steps("workstream-code")[0].id


# --- the largest shipped workflow, end to end -------------------------------------------


async def test_developer_delivery_runs_from_the_request_to_the_published_change() -> None:
    """Includes, a review quorum, a subworkflow, a wait that resumes, and the end's verdict."""
    run = make_run(bundled("Developer Delivery"))
    await run.launch({"request": "ship the flag"})
    plan = {"plan_revision": "r1"}

    # Planning (included from its own workflow): author, three reviewers, then approval.
    await run.close(run.current("delivery-plan-author"), "ready_for_review", plan)
    reviewers = [s for s in run.state.open_steps() if s.node_id == "delivery-plan-reviews"]
    assert len(reviewers) == 3
    for step in reviewers:
        await run.close(step, "pass", plan)
    approval = run.current("delivery-plan-author")
    assert (approval.attempt, approval.generation) == (2, 1)
    await run.close(approval, "approved")

    # The coordinator expands the plan into children; the subworkflow parks the run.
    await run.close(run.current("delivery-coordinate"), "expand")
    children = run.current("delivery-workstreams")
    assert children.waiting_on is not None and children.waiting_on.ref == (
        "subworkflow:delivery-workstreams"
    )
    await run.engine.record_close(children.id, None, 1, Ending.with_verdict("joined"))

    await run.close(run.current("delivery-integrate"), "integrate")
    await run.close(run.current("delivery-integration-tests"), "verify_integration")
    await run.close(run.current("delivery-integration-review"), "pass")

    # Publishing waits on the forge, then resumes over the bounded wait edge.
    await run.close(run.current("delivery-publish"), "wait_delivery")
    checks = run.current("delivery-publication-wait")
    assert checks.waiting_on is not None and checks.waiting_on.kind is WaitingOnKind.EVENT
    await run.engine.record_close(checks.id, None, 1, Ending.with_verdict("observed"))
    resumed = run.current("delivery-publish")
    assert (resumed.attempt, resumed.generation) == (2, 2)
    await run.close(resumed, "publish")

    assert run.finished_with() == "engine.run.completed"
    rework_edges = [
        e.payload["edge_id"] for e in run.state.events if e.event_type == "engine.rework.taken"
    ]
    assert rework_edges == ["delivery-planning:planning-review-revise", "delivery-wait-publish"]
    assert run.sessions.stopped == [run.run_id]

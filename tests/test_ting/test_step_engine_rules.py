"""The step engine's rules, one at a time, on small hand-written graphs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.test_ting.graph_builders import edge, end, persona, stage, trigger
from tests.test_ting.step_support import (
    STEP_DEADLINE_SECONDS,
    Run,
    bundled,
    custom_run,
    make_run,
)
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.services.workflow_step_engine import StepRunRequest
from ting.domain.step_engine import (
    EngineSettings,
    event_matches_filters,
    next_actions,
    plan_close,
    plan_gate_resolution,
    review_join,
)
from ting.domain.step_graph import PersonaContract, compile_step_graph
from ting.domain.workflow_steps import (
    ActorKind,
    Ending,
    EndingKind,
    GateActor,
    GateDecision,
    GateRefusedError,
    InvalidEndingError,
    RunEvent,
    RunState,
    StepEngineError,
    StepFencingError,
    StepInput,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
    activation_id_for,
    check_fence,
)

OK = {"ok": "item.done"}


def member(alias: str, **extra: object) -> dict[str, object]:
    return {"personaId": alias, **extra}


# --- joinMode any and the queue --------------------------------------------------


def fan_in_run() -> Run:
    """Two parallel producers feed one sequential consumer, which hands on to a finisher."""
    return custom_run(
        [
            trigger("go"),
            stage("make", [member("a1"), member("a2")], execution="parallel"),
            stage("take", [member("s")]),
            stage("finish", [member("t")]),
            end(),
        ],
        [
            edge("e1", "go", "make", "go"),
            edge("e2", "make", "take", "work.item"),
            edge("e3", "take", "finish", "item.done"),
            edge("e4", "finish", "done", "all.done"),
        ],
        {
            "a1": persona({"ok": "work.item"}, ["go"]),
            "a2": persona({"ok": "work.item"}, ["go"]),
            "s": persona(OK, ["work.item"]),
            "t": persona({"ok": "all.done"}, ["item.done"]),
        },
    )


async def test_any_join_activates_for_every_delivery_and_a_sequential_node_queues() -> None:
    run = fan_in_run()
    await run.launch()
    await run.close(run.current("make", "a1"), "ok")
    await run.close(run.current("make", "a2"), "ok")

    # Each event activates the consumer; a sequential node runs one at a time.
    assert run.states("take") == ["active", "pending"]
    queued = run.steps("take")[1]
    assert queued.session_id is None and queued.deadline_at is None
    assert "engine.step.queued" in run.event_types()

    await run.close(run.steps("take")[0], "ok")

    assert run.states("take") == ["done", "active"]
    promoted = run.steps("take")[1]
    assert promoted.session_id == "session-s" and promoted.activated_at is not None
    assert any(
        e.event_type == "engine.step.activated" and e.payload.get("promoted_from_queue")
        for e in run.state.events
    )
    assert run.sessions.delivered[-1].activation_id == promoted.id


async def test_a_member_never_has_two_active_steps_even_in_a_parallel_node() -> None:
    run = custom_run(
        [
            trigger("go"),
            stage("make", [member("a1"), member("a2")], execution="parallel"),
            stage("take", [member("s")], execution="parallel"),
            end(),
        ],
        [
            edge("e1", "go", "make", "go"),
            edge("e2", "make", "take", "work.item"),
            edge("e3", "take", "done", "item.done"),
        ],
        {
            "a1": persona({"ok": "work.item"}, ["go"]),
            "a2": persona({"ok": "work.item"}, ["go"]),
            "s": persona(OK, ["work.item"]),
        },
    )
    await run.launch()
    await run.close(run.current("make", "a1"), "ok")
    await run.close(run.current("make", "a2"), "ok")

    # One session runs one step at a time, whatever the node allows.
    assert run.states("take") == ["active", "pending"]


async def test_max_concurrent_limits_a_parallel_node() -> None:
    run = custom_run(
        [
            trigger("go"),
            stage(
                "make",
                [member("a1"), member("a2"), member("a3")],
                execution="parallel",
                maxConcurrent=2,
            ),
            end(),
        ],
        [edge("e1", "go", "make", "go"), edge("e2", "make", "done", "work.item")],
        {name: persona({"ok": "work.item"}, ["go"]) for name in ("a1", "a2", "a3")},
    )
    await run.launch()

    assert sorted(run.states("make")) == ["active", "active", "pending"]
    queued = next(step for step in run.steps("make") if step.state is StepState.PENDING)
    first = next(step for step in run.steps("make") if step.state is StepState.ACTIVE)

    await run.close(first, "ok")

    # Closing one frees a place, and the queued step takes it.
    assert run.steps("make", queued.member)[0].state is StepState.ACTIVE
    assert sorted(run.states("make")) == ["active", "active", "done"]


async def test_event_filters_decide_what_a_member_consumes() -> None:
    run = custom_run(
        [
            trigger("go"),
            stage("make", [member("a1"), member("a2")], execution="parallel"),
            stage("take", [member("s", eventFilters={"kind": "security"})], join="any"),
            end(),
        ],
        [
            edge("e1", "go", "make", "go"),
            edge("e2", "make", "take", "work.item"),
            edge("e3", "take", "done", "item.done"),
        ],
        {
            "a1": persona({"ok": "work.item"}, ["go"]),
            "a2": persona({"ok": "work.item"}, ["go"]),
            "s": persona(OK, ["work.item"]),
        },
    )
    await run.launch()
    await run.close(run.current("make", "a1"), "ok", {"kind": "docs"})

    assert run.steps("take") == []

    await run.close(run.current("make", "a2"), "ok", {"kind": "security"})

    assert run.states("take") == ["active"]


def test_event_filters_read_the_payload_then_fields_then_outcome() -> None:
    payload = {"kind": "top", "fields": {"area": "api"}, "outcome": {"tags": ["x", "y"]}}

    assert event_matches_filters(payload, {})
    assert event_matches_filters(payload, {"kind": "top", "area": "api", "tags": "y"})
    assert not event_matches_filters(payload, {"kind": "other"})
    assert not event_matches_filters(payload, {"tags": "z"})
    assert not event_matches_filters(payload, {"missing": "x"})


# --- joinMode all across groups ----------------------------------------------------


async def test_an_all_join_waits_for_every_event_type_it_consumes() -> None:
    workflow = bundled("Tool & Skill Builder")
    run = make_run(workflow)
    await run.launch()
    await run.close(run.current("capability-frame"), "framed")
    await run.close(run.current("capability-build"), "pass")

    await run.close(run.current("capability-review", "reviewer"), "pass")

    # publish needs review.passed AND security.passed; one is not enough.
    assert run.steps("capability-publish") == []

    await run.close(run.current("capability-review", "security-auditor"), "pass")

    publish = run.current("capability-publish")
    assert sorted(item.event_type for item in publish.inputs) == [
        "review.passed",
        "security.passed",
    ]
    await run.close(publish, "published")
    assert run.finished_with() == "engine.run.completed"


# --- review quorum on a rework edge -------------------------------------------------


async def test_a_review_quorum_joins_on_a_rework_edge_and_gates_approval() -> None:
    run = make_run(bundled("Developer Planning"))
    await run.launch()
    await run.close(run.current("planning-analysis"), "ready_for_review", {"plan_revision": "r1"})
    reviewers = [step for step in run.state.open_steps() if step.node_id == "planning-reviews"]
    assert len(reviewers) == 3

    review = {"plan_revision": "r1"}
    await run.close(reviewers[0], "pass", review)
    await run.close(reviewers[1], "changes_required", review)

    # Two of three reviews are in: the analyst is not reactivated yet, and no generation began.
    assert run.states("planning-analysis") == ["done"]
    assert "engine.rework.taken" not in run.event_types()

    await run.close(reviewers[2], "pass", review)

    revise = run.current("planning-analysis")
    assert (revise.attempt, revise.generation) == (2, 1)
    assert len(revise.inputs) == 3
    with pytest.raises(InvalidEndingError, match="do not pass"):
        await run.close(revise, "approved")
    await run.close(revise, "ready_for_review", {"plan_revision": "r2"})

    second = [step for step in run.state.open_steps() if step.node_id == "planning-reviews"]
    assert {step.attempt for step in second} == {2}
    for step in second:
        await run.close(step, "pass", {"plan_revision": "r2"})
    final = run.current("planning-analysis")
    await run.close(final, "approved")
    assert run.finished_with() == "engine.run.completed"


async def test_a_pass_outcome_needs_reviews_the_step_consumed() -> None:
    run = custom_run(
        [
            trigger("go"),
            stage(
                "judge",
                [member("judge")],
                reviewVerdictPolicy={
                    "eventType": "review.done",
                    "passOutcomes": ["accepted"],
                    "failOutcomes": ["rework"],
                    "bindingFields": [],
                },
            ),
            end(),
        ],
        [
            edge("e1", "go", "judge", "go"),
            edge("e2", "judge", "done", "accepted"),
            edge("e3", "judge", "judge", "rework", rework=True, maxIterations=2),
        ],
        {"judge": persona({"accept": "accepted", "redo": "rework"}, ["go", "rework"])},
    )
    await run.launch()

    with pytest.raises(InvalidEndingError, match="consumed no 'review.done' reviews"):
        await run.close(run.current("judge"), "accept")


# --- end nodes -----------------------------------------------------------------------


async def test_an_end_refuses_a_verdict_outside_its_passing_verdicts() -> None:
    run = custom_run(
        [
            trigger("go"),
            stage("work", [member("w")]),
            end(passingVerdicts=["ok"]),
        ],
        [edge("e1", "go", "work", "go"), edge("e2", "work", "done", "work.done")],
        {"w": persona({"ok": "work.done", "odd": "work.done"}, ["go"])},
    )
    await run.launch()

    await run.close(run.current("work"), "odd")

    assert "engine.end.rejected" in run.event_types()
    assert run.finished_with() == "engine.run.failed"
    assert "refused verdict 'odd'" in run.failure_reason()
    assert run.steps("done") == []


async def test_completing_the_run_cancels_branches_still_open() -> None:
    run = custom_run(
        [
            trigger("go"),
            stage("fast", [member("f")]),
            stage("slow", [member("s")]),
            end(joinMode="any"),
        ],
        [
            edge("e1", "go", "fast", "go"),
            edge("e2", "go", "slow", "go"),
            edge("e3", "fast", "done", "fast.done"),
            edge("e4", "slow", "done", "slow.done"),
        ],
        {
            "f": persona({"ok": "fast.done"}, ["go"]),
            "s": persona({"ok": "slow.done"}, ["go"]),
        },
    )
    await run.launch()

    await run.close(run.current("fast"), "ok")

    assert run.finished_with() == "engine.run.completed"
    assert run.states("slow") == ["canceled"]
    assert "engine.step.canceled" in run.event_types()


async def test_an_all_join_end_waits_for_every_incoming_branch() -> None:
    run = custom_run(
        [
            trigger("go"),
            stage("left", [member("l")]),
            stage("right", [member("r")]),
            end(joinMode="all"),
        ],
        [
            edge("e1", "go", "left", "go"),
            edge("e2", "go", "right", "go"),
            edge("e3", "left", "done", "left.done"),
            edge("e4", "right", "done", "right.done"),
        ],
        {
            "l": persona({"ok": "left.done"}, ["go"]),
            "r": persona({"ok": "right.done"}, ["go"]),
        },
    )
    await run.launch()

    await run.close(run.current("left"), "ok")
    assert run.finished_with() is None
    await run.close(run.current("right"), "ok")

    assert run.finished_with() == "engine.run.completed"
    done = run.steps("done")[0]
    assert sorted(item.node_id for item in done.inputs) == ["left", "right"]


# --- gates, waits and subworkflows ---------------------------------------------------


def gated_graph() -> tuple[list[dict], list[dict], dict]:
    nodes = [
        trigger("go"),
        stage("one", [member("p")], execution="parallel"),
        stage("two", [member("q")], execution="parallel"),
        {
            "id": "gate",
            "kind": "gate",
            "label": "Gate",
            "mode": "human_approval",
            "approvalEvent": "g.ok",
            "changesRequestedEvent": "g.changes",
        },
        end(),
    ]
    edges = [
        edge("e1", "go", "one", "go"),
        edge("e2", "go", "two", "go"),
        edge("e3", "one", "gate", "one.ready"),
        edge("e4", "two", "gate", "two.ready"),
        edge("e5", "gate", "done", "g.ok"),
        edge("e6", "gate", "one", "g.changes", rework=True, maxIterations=2),
    ]
    personas = {
        "p": persona({"ok": "one.ready"}, ["go", "g.changes"]),
        "q": persona({"ok": "two.ready"}, ["go"]),
    }
    return nodes, edges, personas


async def test_a_second_delivery_to_an_open_gate_does_not_open_it_twice() -> None:
    run = custom_run(*gated_graph())
    await run.launch()

    await run.close(run.current("one"), "ok")
    await run.close(run.current("two"), "ok")

    assert run.states("gate") == ["waiting"]
    coalesced = [e for e in run.state.events if e.event_type == "engine.delivery.coalesced"]
    assert [e.payload["node_id"] for e in coalesced] == ["gate"]


async def test_a_gate_deadline_exists_only_when_the_node_sets_one() -> None:
    nodes, edges, personas = gated_graph()
    run = custom_run(nodes, edges, personas)
    await run.launch()
    await run.close(run.current("one"), "ok")
    assert run.current("gate").deadline_at is None

    nodes[3]["deadlineSeconds"] = 86400
    timed = custom_run(nodes, edges, personas)
    await timed.launch()
    await timed.close(timed.current("one"), "ok")
    gate = timed.current("gate")
    assert gate.deadline_at == gate.activated_at + timedelta(seconds=86400)


async def test_a_stage_deadline_defaults_to_config_and_a_node_can_override_it() -> None:
    plain = custom_run(
        [trigger("go"), stage("work", [member("w")]), end()],
        [edge("e1", "go", "work", "go"), edge("e2", "work", "done", "work.done")],
        {"w": persona({"ok": "work.done"}, ["go"])},
    )
    await plain.launch()
    step = plain.current("work")
    assert step.deadline_at == step.activated_at + timedelta(seconds=STEP_DEADLINE_SECONDS)

    tight = custom_run(
        [trigger("go"), stage("work", [member("w")], deadlineSeconds=30), end()],
        [edge("e1", "go", "work", "go"), edge("e2", "work", "done", "work.done")],
        {"w": persona({"ok": "work.done"}, ["go"])},
    )
    await tight.launch()
    step = tight.current("work")
    assert step.deadline_at == step.activated_at + timedelta(seconds=30)


def wait_run(node: dict) -> Run:
    return custom_run(
        [
            trigger("go"),
            stage("ask", [member("a")]),
            node,
            stage("after", [member("b")]),
            end(),
        ],
        [
            edge("e1", "go", "ask", "go"),
            edge("e2", "ask", node["id"], "ask.done"),
            edge("e3", node["id"], "after", node.get("_out", "wait.observed")),
            edge("e4", "after", "done", "after.done"),
        ],
        {
            "a": persona({"ok": "ask.done"}, ["go"]),
            "b": persona({"ok": "after.done"}, [node.get("_out", "wait.observed")]),
        },
    )


async def test_a_timer_wait_parks_the_run_until_it_is_observed() -> None:
    run = wait_run({"id": "pause", "kind": "wait", "label": "Pause", "conditions": ["timer"]})
    await run.launch()
    await run.close(run.current("ask"), "ok")

    parked = run.current("pause")
    assert (parked.kind, parked.state) == (StepKind.WAIT, StepState.WAITING)
    assert parked.waiting_on is not None
    assert (parked.waiting_on.kind, parked.waiting_on.ref) == (
        WaitingOnKind.TIME,
        "wait:pause:timer",
    )
    assert parked.session_id is None
    with pytest.raises(InvalidEndingError, match="closes with 'observed'"):
        await run.engine.record_close(parked.id, None, 1, Ending.with_verdict("elapsed"))
    with pytest.raises(InvalidEndingError, match="not done"):
        await run.engine.record_close(parked.id, None, 1, Ending.done())

    await run.engine.record_close(parked.id, None, 1, Ending.with_verdict("observed"))

    assert run.steps("pause")[0].state is StepState.DONE
    assert run.current("after")
    assert "wait.observed" in run.event_types()


async def test_an_outside_event_wait_names_its_conditions() -> None:
    run = wait_run(
        {
            "id": "pause",
            "kind": "wait",
            "label": "Checks",
            "conditions": ["forge.checks", "forge.merge"],
        }
    )
    await run.launch()
    await run.close(run.current("ask"), "ok")

    handle = run.current("pause").waiting_on
    assert handle is not None
    assert (handle.kind, handle.ref) == (WaitingOnKind.EVENT, "wait:pause:forge.checks,forge.merge")


def subworkflow_run() -> Run:
    return wait_run(
        {
            "id": "kids",
            "kind": "subworkflow",
            "label": "Children",
            "blockedEvent": "kids.blocked",
            "_out": "kids.joined",
        }
    )


async def test_a_subworkflow_parks_as_a_wait_and_continues_when_its_children_join() -> None:
    run = subworkflow_run()
    await run.launch()
    await run.close(run.current("ask"), "ok")

    parked = run.current("kids")
    assert parked.kind is StepKind.WAIT
    assert parked.waiting_on is not None and parked.waiting_on.ref == "subworkflow:kids"
    with pytest.raises(InvalidEndingError, match="'joined' or 'blocked'"):
        await run.engine.record_close(parked.id, None, 1, Ending.with_verdict("observed"))

    await run.engine.record_close(parked.id, None, 1, Ending.with_verdict("joined"))

    assert run.current("after")


async def test_blocked_children_fail_the_subworkflow_path_when_no_edge_carries_it() -> None:
    run = subworkflow_run()
    await run.launch()
    await run.close(run.current("ask"), "ok")
    parked = run.current("kids")

    await run.engine.record_close(parked.id, None, 1, Ending.with_verdict("blocked"))

    assert run.steps("kids")[0].state is StepState.FAILED
    assert run.finished_with() == "engine.run.failed"
    assert "kids.blocked" in run.failure_reason()


async def test_a_member_that_produces_nothing_ends_done() -> None:
    run = custom_run(
        [
            trigger("go"),
            stage("work", [member("w"), member("observer")], execution="parallel"),
            end(),
        ],
        [edge("e1", "go", "work", "go"), edge("e2", "work", "done", "work.done")],
        {
            "w": persona({"ok": "work.done"}, ["go"]),
            "observer": persona(None, ["go"]),
        },
    )
    await run.launch()
    watcher = run.current("work", "observer")

    await run.engine.record_close(watcher.id, watcher.session_id, 1, Ending.done(note="seen"))

    done = run.steps("work", "observer")[0]
    assert (done.state, done.verdict, done.note) == (StepState.DONE, None, "seen")
    assert run.finished_with() is None
    assert run.current("work", "w")


# --- launch -----------------------------------------------------------------------


async def test_a_graph_the_engine_cannot_run_is_refused_at_launch_and_writes_nothing() -> None:
    run = custom_run(
        [trigger("go"), stage("work", [member("w")]), end()],
        [edge("e1", "go", "work", "go")],
        {"w": persona({"ok": "work.done"}, ["go"])},
    )

    with pytest.raises(WorkflowDocumentError, match="dead end"):
        await run.launch()

    assert run.state.events == ()


async def test_a_workflow_with_several_triggers_needs_the_one_to_launch() -> None:
    run = custom_run(
        [
            trigger("go", "first"),
            trigger("go-too", "second"),
            stage("work", [member("w")]),
            end(),
        ],
        [
            edge("e1", "first", "work", "go"),
            edge("e2", "second", "work", "go-too", "go"),
            edge("e3", "work", "done", "work.done"),
        ],
        {"w": persona({"ok": "work.done"}, ["go"])},
    )

    with pytest.raises(StepEngineError, match="2 triggers"):
        await run.launch()
    with pytest.raises(StepEngineError, match="not a trigger"):
        await run.engine.launch(StepRunRequest(run.run_id, {}, trigger_node_id="work"))

    await run.engine.launch(StepRunRequest(run.run_id, {}, trigger_node_id="second"))
    assert run.current("work").inputs[0].node_id == "second"


async def test_a_stage_member_without_a_pinned_persona_cannot_launch() -> None:
    run = custom_run(
        [trigger("go"), stage("work", [member("w")]), end()],
        [edge("e1", "go", "work", "go"), edge("e2", "work", "done", "work.done")],
        {"w": persona({"ok": "work.done"}, ["go"])},
    )
    run.definition.persona_refs.pop("w")  # type: ignore[attr-defined]

    with pytest.raises(WorkflowDocumentError, match="no pinned persona.*w"):
        await run.launch()


# --- the pure pieces -----------------------------------------------------------------


def test_next_actions_does_nothing_for_a_finished_run() -> None:
    run_id = uuid4()
    now = datetime(2026, 11, 8, tzinfo=UTC)
    events = (
        RunEvent(run_id, 1, "engine.run.launched", 0, now),
        RunEvent(run_id, 2, "engine.run.completed", 0, now),
    )
    graph = compile_step_graph(
        {"nodes": [trigger("go"), end()], "edges": [edge("e1", "go", "done", "go")]}
    )

    actions = next_actions(
        graph,
        RunState(run_id, (), events),
        events[-1],
        now=now,
        settings=EngineSettings(step_deadline_seconds=1.0, session_poll_seconds=0.01),
    )

    assert actions == []


def test_an_event_from_a_node_the_graph_lacks_is_an_engine_error() -> None:
    run_id = uuid4()
    now = datetime(2026, 11, 8, tzinfo=UTC)
    stray = RunEvent(run_id, 1, "x.happened", 0, now, {"node_id": "ghost"})
    graph = compile_step_graph(
        {"nodes": [trigger("go"), end()], "edges": [edge("e1", "go", "done", "go")]}
    )

    with pytest.raises(StepEngineError, match="ghost"):
        next_actions(
            graph,
            RunState(run_id, (), (stray,)),
            stray,
            now=now,
            settings=EngineSettings(step_deadline_seconds=1.0, session_poll_seconds=0.01),
        )


def test_engine_settings_need_a_positive_deadline() -> None:
    with pytest.raises(ValueError, match="positive"):
        EngineSettings(step_deadline_seconds=0, session_poll_seconds=1.0)


def test_engine_settings_need_a_positive_session_poll() -> None:
    with pytest.raises(ValueError, match="session_poll_seconds must be positive"):
        EngineSettings(step_deadline_seconds=1.0, session_poll_seconds=0)


def test_activation_ids_are_deterministic_per_run_node_member_and_attempt() -> None:
    run_id = uuid4()

    assert activation_id_for(run_id, "n", "m", 1) == activation_id_for(run_id, "n", "m", 1)
    assert (
        len(
            {
                activation_id_for(run_id, "n", "m", 1),
                activation_id_for(run_id, "n", "m", 2),
                activation_id_for(run_id, "n", None, 1),
                activation_id_for(run_id, "other", "m", 1),
                activation_id_for(uuid4(), "n", "m", 1),
            }
        )
        == 5
    )


def test_a_step_has_a_waiting_handle_exactly_while_it_waits() -> None:
    now = datetime(2026, 11, 8, tzinfo=UTC)
    base = {
        "id": uuid4(),
        "run_id": uuid4(),
        "node_id": "n",
        "member": "m",
        "kind": StepKind.STAGE_MEMBER,
        "attempt": 1,
        "generation": 0,
        "created_at": now,
    }
    handle = WaitingOn(WaitingOnKind.PERSON, "alice", now)

    with pytest.raises(ValueError, match="waiting_on"):
        WorkflowStep(**base, state=StepState.WAITING)
    with pytest.raises(ValueError, match="waiting_on"):
        WorkflowStep(**base, state=StepState.ACTIVE, waiting_on=handle)
    waiting = WorkflowStep(**base, state=StepState.WAITING, waiting_on=handle)
    assert waiting.with_state(StepState.DONE).waiting_on is None


def test_endings_are_validated_when_built() -> None:
    now = datetime(2026, 11, 8, tzinfo=UTC)

    with pytest.raises(InvalidEndingError, match="needs a verdict"):
        Ending(EndingKind.VERDICT, verdict=" ")
    with pytest.raises(InvalidEndingError, match="waiting_on"):
        Ending(EndingKind.WAITING)
    with pytest.raises(InvalidEndingError, match="reason"):
        Ending.failed(" ")
    with pytest.raises(InvalidEndingError, match="finite JSON"):
        Ending.with_verdict("ok", outcome={"x": float("nan")})
    assert Ending.waiting(WaitingOn(WaitingOnKind.TIME, "t", now)).kind is EndingKind.WAITING


def test_waiting_handles_and_step_inputs_round_trip_through_json() -> None:
    now = datetime(2026, 11, 8, 9, 30, tzinfo=UTC)
    handle = WaitingOn(WaitingOnKind.SESSION, "session-1", now)
    item = StepInput(seq=4, event_type="x.y", edge_id="e1", node_id="n", member=None)

    assert WaitingOn.from_json(handle.to_json()) == handle
    assert StepInput.from_json(item.to_json()) == item


def test_the_fence_names_what_is_wrong() -> None:
    activation = uuid4()

    with pytest.raises(StepFencingError, match="no such activation"):
        check_fence(None, activation_id=activation, session_id="s", attempt=1)
    assert StepFencingError(activation, "x").activation_id == activation


# --- more rules ------------------------------------------------------------------------


async def test_hitting_the_rework_bound_cancels_what_is_still_open() -> None:
    run = custom_run(
        [
            trigger("go"),
            stage("work", [member("w")]),
            stage("check", [member("c1"), member("c2")], execution="parallel"),
            end(),
        ],
        [
            edge("e1", "go", "work", "go"),
            edge("e2", "work", "check", "work.done"),
            edge("e3", "check", "work", "check.redo", "go", rework=True, maxIterations=1),
            edge("e4", "check", "done", "check.ok"),
        ],
        {
            "w": persona({"ok": "work.done"}, ["go"]),
            "c1": persona({"redo": "check.redo", "ok": "check.ok"}, ["work.done"]),
            "c2": persona({"ok": "check.ok"}, ["work.done"]),
        },
    )
    await run.launch()
    await run.close(run.current("work"), "ok")
    await run.close(run.current("check", "c1"), "redo")
    await run.close(run.current("work"), "ok")

    # The second redo exceeds maxIterations=1 while c2 is still looking at the work.
    await run.close(run.steps("check", "c1")[1], "redo")

    assert run.finished_with() == "engine.run.failed"
    assert "edge 'e3'" in run.failure_reason() and "maxIterations=1" in run.failure_reason()
    assert run.steps("check", "c2")[1].state is StepState.CANCELED
    assert run.state.open_steps() == ()


async def test_evidence_travels_with_the_outcome_event() -> None:
    run = custom_run(
        [trigger("go"), stage("work", [member("w")]), end()],
        [edge("e1", "go", "work", "go"), edge("e2", "work", "done", "work.done")],
        {"w": persona({"ok": "work.done"}, ["go"])},
    )
    await run.launch()
    step = run.current("work")

    await run.engine.record_close(
        step.id,
        step.session_id,
        1,
        Ending.with_verdict("ok", evidence={"receipt": "r-1"}, note="verified"),
    )

    produced = next(e for e in run.state.events if e.event_type == "work.done")
    assert produced.payload["evidence"] == {"receipt": "r-1"}
    assert produced.payload["note"] == "verified"
    assert run.steps("work")[0].evidence == {"receipt": "r-1"}


async def test_a_review_of_the_wrong_revision_is_caught_even_when_reviewers_agree() -> None:
    run = make_run(bundled("Developer Workstream"))
    await run.launch()
    await run.close(run.current("workstream-code"), "ready")
    given = {"attempt_id": "a1", "candidate_sha": "1" * 40, "candidate_tree": "2" * 40}
    await run.close(run.current("workstream-verify"), "verify", given)
    # All three reviewers agree with each other, on a candidate other than the one given.
    other = {**given, "candidate_sha": "9" * 40}
    for reviewer in (
        "developer-code-reviewer",
        "developer-security-reviewer",
        "developer-adversarial-reviewer",
    ):
        await run.close(run.current("workstream-reviews", reviewer), "pass", other)

    with pytest.raises(
        InvalidEndingError, match="reviewed 'candidate_sha' other than the one it was given"
    ):
        await run.close(run.current("workstream-acceptance"), "accept")


async def test_a_binding_field_a_reviewer_leaves_out_does_not_pass() -> None:
    run = make_run(bundled("Developer Workstream"))
    await run.launch()
    await run.close(run.current("workstream-code"), "ready")
    given = {"attempt_id": "a1", "candidate_sha": "1" * 40, "candidate_tree": "2" * 40}
    await run.close(run.current("workstream-verify"), "verify", given)
    for reviewer in (
        "developer-code-reviewer",
        "developer-security-reviewer",
        "developer-adversarial-reviewer",
    ):
        await run.close(run.current("workstream-reviews", reviewer), "pass", {"attempt_id": "a1"})

    with pytest.raises(
        InvalidEndingError, match="binding field 'candidate_sha' differs or is missing"
    ):
        await run.close(run.current("workstream-acceptance"), "accept")


def test_review_join_is_none_for_a_node_without_a_policy() -> None:
    graph = compile_step_graph(
        {"nodes": [trigger("go"), end()], "edges": [edge("e1", "go", "done", "go")]}
    )
    now = datetime(2026, 11, 8, tzinfo=UTC)
    step = WorkflowStep(
        id=uuid4(),
        run_id=uuid4(),
        node_id="go",
        member=None,
        kind=StepKind.STAGE_MEMBER,
        attempt=1,
        generation=0,
        state=StepState.ACTIVE,
        created_at=now,
    )

    assert review_join(graph, RunState(step.run_id), graph.nodes["go"], step) is None


def test_a_gate_without_a_declared_event_cannot_be_resolved() -> None:
    graph = compile_step_graph(
        {
            "nodes": [
                trigger("go"),
                {"id": "gate", "kind": "gate", "label": "Gate", "approvalEvent": "g.ok"},
                end(),
            ],
            "edges": [edge("e1", "go", "gate", "go"), edge("e2", "gate", "done", "g.ok")],
        }
    )
    now = datetime(2026, 11, 8, tzinfo=UTC)
    run_id = uuid4()
    gate = WorkflowStep(
        id=uuid4(),
        run_id=run_id,
        node_id="gate",
        member=None,
        kind=StepKind.GATE,
        attempt=1,
        generation=0,
        state=StepState.WAITING,
        created_at=now,
        waiting_on=WaitingOn(WaitingOnKind.PERSON, "gate", now),
    )

    with pytest.raises(GateRefusedError, match="declares no event for 'request_changes'"):
        plan_gate_resolution(
            graph,
            RunState(run_id, (gate,)),
            node_id="gate",
            decision=GateDecision.REQUEST_CHANGES,
            by=GateActor(ActorKind.PERSON, "alice"),
            note="",
            now=now,
        )


def test_an_event_from_a_member_the_graph_does_not_expect_fills_no_join_slot() -> None:
    nodes = [
        trigger("go"),
        stage("make", [member("a1"), member("a2")], execution="parallel"),
        stage("join", [member("j")], join="all"),
        end(),
    ]
    edges = [
        edge("e1", "go", "make", "go"),
        edge("e2", "make", "join", "work.item"),
        edge("e3", "join", "done", "joined"),
    ]
    personas = {
        "a1": persona({"ok": "work.item"}, ["go"]),
        "a2": persona({"ok": "work.item"}, ["go"]),
        "j": persona({"ok": "joined"}, ["work.item"]),
    }
    graph = compile_step_graph({"nodes": nodes, "edges": edges}, personas)
    now = datetime(2026, 11, 8, tzinfo=UTC)
    run_id = uuid4()
    events = tuple(
        RunEvent(run_id, seq, "work.item", 0, now, {"node_id": "make", "member": who})
        for seq, who in enumerate(("a1", "a2", "ghost"), start=1)
    )
    stray = events[-1]

    with pytest.raises(StepEngineError, match="fills no join slot.*ghost"):
        next_actions(
            graph,
            RunState(run_id, (), events),
            stray,
            now=now,
            settings=EngineSettings(step_deadline_seconds=1.0, session_poll_seconds=0.01),
        )


def test_a_member_without_a_persona_contract_cannot_close_with_a_verdict() -> None:
    graph = compile_step_graph(
        {
            "nodes": [trigger("go"), stage("work", [member("w")]), end()],
            "edges": [edge("e1", "go", "work", "go"), edge("e2", "work", "done", "work.done")],
        }
    )
    now = datetime(2026, 11, 8, tzinfo=UTC)
    run_id = uuid4()
    step = WorkflowStep(
        id=uuid4(),
        run_id=run_id,
        node_id="work",
        member="w",
        kind=StepKind.STAGE_MEMBER,
        attempt=1,
        generation=0,
        state=StepState.ACTIVE,
        created_at=now,
    )

    with pytest.raises(InvalidEndingError, match="has no persona contract"):
        plan_close(graph, RunState(run_id, (step,)), step, Ending.with_verdict("ok"), now=now)


def test_compiling_a_graph_names_what_is_wrong_with_a_node() -> None:
    with pytest.raises(WorkflowDocumentError, match="declared twice"):
        compile_step_graph({"nodes": [trigger("go"), trigger("go")], "edges": []})
    with pytest.raises(WorkflowDocumentError, match="needs an id"):
        compile_step_graph({"nodes": [{"kind": "end"}], "edges": []})
    with pytest.raises(WorkflowDocumentError, match="unknown kind 'cond'"):
        compile_step_graph({"nodes": [{"id": "c", "kind": "cond"}], "edges": []})
    with pytest.raises(WorkflowDocumentError, match="unresolved include"):
        compile_step_graph({"nodes": [{"id": "i", "kind": "include"}], "edges": []})


def test_a_persona_without_a_verdict_map_hands_off_one_event_whatever_it_says() -> None:
    contract = PersonaContract(produces_event="review.completed")

    assert contract.event_for("anything") == "review.completed"
    assert contract.produced_events == frozenset({"review.completed"})
    assert PersonaContract().event_for("anything") is None

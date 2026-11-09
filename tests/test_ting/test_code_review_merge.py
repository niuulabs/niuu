"""The one shipped workflow: code, review and security review in parallel, then merge.

Driven event by event through the real step engine, the way harness sessions would close
the steps, and checked against Ting's runnable-graph validator.
"""

from __future__ import annotations

import pytest

from tests.test_ting.step_support import Run, bundled, make_run
from ting.domain.step_graph import NodeKind, compile_step_graph
from ting.domain.workflow_document import validate_workflow_graph
from ting.domain.workflow_steps import (
    ActorKind,
    GateActor,
    GateDecision,
    StepFencingError,
    StepKind,
    StepState,
)
from ting.system_workflows import load_system_workflows

NAME = "Code, Review and Merge"
MEMBERS = {"coder", "reviewer", "security-auditor"}


@pytest.fixture
def run() -> Run:
    return make_run(bundled(NAME))


async def reach_checks(run: Run) -> None:
    """Launch, and let the coder finish a change: the reviewer and the auditor are then active."""
    await run.launch()
    await run.close(run.current("code-implement"), "pass", {"summary": "done"})


async def check(run: Run, reviewer: str, verdict: str) -> None:
    await run.close(run.current("code-checks", reviewer), verdict)


def person() -> GateActor:
    return GateActor(ActorKind.PERSON, "alice")


# --- shape --------------------------------------------------------------------------


def test_it_is_the_only_shipped_workflow_and_uses_only_the_three_shipped_personas() -> None:
    (workflow,) = load_system_workflows()

    assert workflow.name == NAME
    assert set(workflow.persona_dependencies) == MEMBERS
    assert not workflow.workflow_dependencies


def test_the_runnable_graph_validator_accepts_it_with_the_persona_contracts() -> None:
    workflow = bundled(NAME)

    validate_workflow_graph(workflow.graph, persona_definitions=workflow.persona_definitions)


def test_every_verdict_is_carried_by_an_edge_and_the_run_has_one_explicit_end() -> None:
    workflow = bundled(NAME)
    graph = compile_step_graph(workflow.graph, workflow.persona_definitions)

    for node in graph.nodes.values():
        if node.kind is not NodeKind.STAGE:
            continue
        carried = {edge.source_event for edge in graph.outgoing(node.id)}
        for member in node.members:
            assert member.contract is not None
            produced = member.contract.produced_events
            assert produced <= carried, (node.id, member.alias, sorted(produced - carried))
    ends = [node for node in graph.nodes.values() if node.kind is NodeKind.END]
    assert [node.id for node in ends] == ["code-merged"]
    assert ends[0].passing_verdicts == ("pass",)
    assert not graph.outgoing("code-merged")


def test_no_rework_edge_has_a_round_limit() -> None:
    graph = bundled(NAME).graph

    rework = [edge for edge in graph["edges"] if edge.get("rework")]
    assert len(rework) == 3
    assert not [edge for edge in rework if "maxIterations" in edge]


# --- the happy path -----------------------------------------------------------------


async def test_the_checks_run_in_parallel_and_the_merge_waits_for_both(run: Run) -> None:
    await reach_checks(run)

    assert {step.member for step in run.state.open_steps()} == {"reviewer", "security-auditor"}
    await check(run, "reviewer", "pass")
    assert run.steps("code-merge") == []

    await check(run, "security-auditor", "pass")

    merge = run.current("code-merge")
    assert merge.member == "coder"
    assert {item.member for item in merge.inputs} == {"reviewer", "security-auditor"}


async def test_the_run_ends_only_when_the_coder_reports_the_merge_done(run: Run) -> None:
    await reach_checks(run)
    await check(run, "reviewer", "pass")
    await check(run, "security-auditor", "pass")

    assert run.finished_with() is None
    await run.close(run.current("code-merge"), "pass", {"summary": "merged"})

    assert run.finished_with() == "engine.run.completed"
    end = run.steps("code-merged")[0]
    assert (end.kind, end.state) == (StepKind.END, StepState.DONE)
    assert "code.merged" in run.event_types()
    assert run.state.open_steps() == ()
    # One session per member, reused for the implement and the merge steps.
    assert len(run.sessions.created) == len(set(run.sessions.created))
    assert sum(1 for run_id, member in run.sessions.sessions if member == "coder") == 1


# --- rework -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("asking", "verdict", "other"),
    [
        ("reviewer", "needs_changes", "security-auditor"),
        ("reviewer", "fail", "security-auditor"),
        ("security-auditor", "fail", "reviewer"),
        ("security-auditor", "needs_review", "reviewer"),
    ],
)
async def test_either_check_asking_for_changes_sends_the_work_back_and_both_check_again(
    run: Run, asking: str, verdict: str, other: str
) -> None:
    await reach_checks(run)
    await check(run, other, "pass")
    await check(run, asking, verdict)

    assert run.steps("code-merge") == []
    rework = [e for e in run.state.events if e.event_type == "engine.rework.taken"]
    assert len(rework) == 1
    coder = run.current("code-implement")
    assert (coder.attempt, coder.generation) == (2, 1)

    await run.close(coder, "pass")

    # The check that passed the first change does not carry over to the new one.
    assert {step.member for step in run.state.open_steps()} == {"reviewer", "security-auditor"}
    assert {step.generation for step in run.state.open_steps()} == {1}
    await check(run, "reviewer", "pass")
    await check(run, "security-auditor", "pass")
    await run.close(run.current("code-merge"), "pass")
    assert run.finished_with() == "engine.run.completed"


async def test_the_work_goes_round_as_often_as_the_checks_ask(run: Run) -> None:
    await reach_checks(run)

    for _ in range(6):
        await check(run, "security-auditor", "pass")
        await check(run, "reviewer", "needs_changes")
        await run.close(run.current("code-implement"), "pass")

    assert run.finished_with() is None
    assert run.current("code-checks", "reviewer").generation == 6
    await check(run, "reviewer", "pass")
    await check(run, "security-auditor", "pass")
    await run.close(run.current("code-merge"), "pass")
    assert run.finished_with() == "engine.run.completed"


async def test_the_first_ask_for_changes_wins_and_the_other_check_of_that_change_is_moot(
    run: Run,
) -> None:
    await reach_checks(run)
    await check(run, "reviewer", "needs_changes")

    # The coder has one piece of work, and the auditor's step on the old change is stale.
    assert len([step for step in run.steps("code-implement") if step.is_open]) == 1
    assert run.states("code-checks", "security-auditor") == ["stale"]
    with pytest.raises(StepFencingError):
        await run.close(run.steps("code-checks", "security-auditor")[0], "fail")

    await run.close(run.current("code-implement"), "pass")
    assert run.steps("code-merge") == []
    assert {step.member for step in run.state.open_steps()} == {"reviewer", "security-auditor"}


# --- blocked ------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", ["blocked", "help_needed", "fail"])
async def test_a_coder_that_cannot_finish_waits_for_a_person_instead_of_dead_ending(
    run: Run, verdict: str
) -> None:
    await run.launch()
    await run.close(run.current("code-implement"), verdict, {"reason": "no access"})

    gate = run.current("code-blocked")
    assert (gate.kind, gate.state) == (StepKind.GATE, StepState.WAITING)
    assert run.finished_with() is None

    await run.engine.resolve_gate(
        run.run_id, "code-blocked", GateDecision.APPROVE, person(), "use the other token"
    )

    coder = run.current("code-implement")
    assert (coder.attempt, coder.generation) == (2, 1)


async def test_a_merge_that_cannot_land_waits_for_a_person_and_never_completes_the_run(
    run: Run,
) -> None:
    await reach_checks(run)
    await check(run, "reviewer", "pass")
    await check(run, "security-auditor", "pass")

    await run.close(run.current("code-merge"), "blocked", {"reason": "merge conflict"})

    assert run.finished_with() is None
    assert run.current("code-blocked").state is StepState.WAITING
    assert "code.merged" not in run.event_types()

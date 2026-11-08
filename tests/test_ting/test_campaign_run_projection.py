"""A campaign's state, read off a run's ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ting.adapters.campaign_step_definitions import definition_of_snapshot
from ting.domain.campaign_run_projection import (
    CANCELED_KEY,
    PENDING_GATES_KEY,
    open_gates,
    project_run,
    run_gates,
)
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.workflow_steps import (
    EVENT_RUN_CANCELED,
    EVENT_RUN_COMPLETED,
    EVENT_RUN_FAILED,
    EVENT_RUN_LAUNCHED,
    RunEvent,
    RunState,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
)

from .test_workflow_runs import _snapshot

NOW = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
RUN = uuid4()


def _campaign(**changes) -> WorkflowCampaign:
    values = {
        "id": RUN,
        "slug": "run",
        "name": "run",
        "owner_id": "user-1",
        "workflow_id": uuid4(),
        "workflow_version": "1",
        "workflow_name": "Build and review",
        "workflow_snapshot": _snapshot(),
        "session_id": "",
        "session_name": "",
        "status": WorkflowCampaignStatus.PENDING,
        "active_stage_id": None,
        "stage_state": [],
        "metadata": {},
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return WorkflowCampaign(**values)


def _event(seq: int, event_type: str, **payload) -> RunEvent:
    return RunEvent(
        run_id=RUN,
        seq=seq,
        event_type=event_type,
        generation=0,
        created_at=NOW,
        payload=payload,
    )


def _step(
    node_id: str, state: StepState, *, member: str | None = "coder", **changes
) -> WorkflowStep:
    waiting = WaitingOn(WaitingOnKind.PERSON, node_id, NOW) if state is StepState.WAITING else None
    values = {
        "id": uuid4(),
        "run_id": RUN,
        "node_id": node_id,
        "member": member,
        "kind": StepKind.STAGE_MEMBER if member else StepKind.GATE,
        "attempt": 1,
        "generation": 0,
        "state": state,
        "created_at": NOW,
        "activated_at": NOW,
        "waiting_on": waiting,
    }
    values.update(changes)
    return WorkflowStep(**values)


def _state(steps=(), events=()) -> RunState:
    return RunState(run_id=RUN, steps=tuple(steps), events=tuple(events))


LAUNCHED = _event(1, EVENT_RUN_LAUNCHED)


def test_a_run_that_was_never_launched_is_pending() -> None:
    projection = project_run(_campaign(), _state(), now=NOW)

    assert projection.status is WorkflowCampaignStatus.PENDING
    assert projection.active_stage_id is None


def test_a_run_with_a_working_step_is_running_at_that_stage() -> None:
    state = _state([_step("implement", StepState.ACTIVE)], [LAUNCHED])

    projection = project_run(_campaign(), state, now=NOW)

    assert projection.status is WorkflowCampaignStatus.RUNNING
    assert projection.active_stage_id == "implement"
    assert [(s.stage_id, s.status) for s in projection.stage_state] == [
        ("implement", "active"),
        ("review", "pending"),
    ]


def test_a_step_waiting_on_a_person_blocks_the_campaign() -> None:
    state = _state([_step("approve", StepState.WAITING, member=None)], [LAUNCHED])

    projection = project_run(_campaign(), state, now=NOW)

    assert projection.status is WorkflowCampaignStatus.BLOCKED
    assert [gate["id"] for gate in projection.metadata[PENDING_GATES_KEY]] == ["approve"]


def test_a_step_waiting_on_something_else_does_not_block_on_a_person() -> None:
    parked = _step(
        "implement",
        StepState.WAITING,
        waiting_on=WaitingOn(WaitingOnKind.EVENT, "ci.finished", NOW),
    )

    projection = project_run(_campaign(), _state([parked], [LAUNCHED]), now=NOW)

    assert projection.status is WorkflowCampaignStatus.RUNNING


def test_the_pending_gates_leave_the_metadata_once_nothing_waits() -> None:
    campaign = _campaign(metadata={PENDING_GATES_KEY: [{"id": "approve"}], "keep": "me"})
    state = _state([_step("implement", StepState.ACTIVE)], [LAUNCHED])

    projection = project_run(campaign, state, now=NOW)

    assert PENDING_GATES_KEY not in projection.metadata
    assert projection.metadata["keep"] == "me"


def test_a_completed_run_completes_every_stage() -> None:
    state = _state([], [LAUNCHED, _event(2, EVENT_RUN_COMPLETED)])

    projection = project_run(_campaign(), state, now=NOW)

    assert projection.status is WorkflowCampaignStatus.COMPLETED
    assert {s.status for s in projection.stage_state} == {"complete"}
    assert projection.active_stage_id is None


def test_a_failed_run_carries_the_reason_and_names_the_stage_that_failed() -> None:
    failed = _step("review", StepState.FAILED, member="reviewer", closed_at=NOW)
    state = _state([failed], [LAUNCHED, _event(2, EVENT_RUN_FAILED, reason="review blew up")])

    projection = project_run(_campaign(), state, now=NOW)

    assert projection.status is WorkflowCampaignStatus.FAILED
    assert projection.failure == "review blew up"
    assert projection.metadata["failure_error"] == "review blew up"
    assert projection.metadata["failure_stage_id"] == "review"
    assert projection.active_stage_id == "review"
    assert {s.stage_id: s.status for s in projection.stage_state}["review"] == "failed"


def test_a_canceled_run_is_failed_with_the_reason_and_marked_canceled() -> None:
    state = _state([], [LAUNCHED, _event(2, EVENT_RUN_CANCELED, reason="owner stopped it")])

    projection = project_run(_campaign(), state, now=NOW)

    assert projection.status is WorkflowCampaignStatus.FAILED
    assert projection.metadata["failure_error"] == "owner stopped it"
    assert projection.metadata[CANCELED_KEY] is True


def test_a_failed_run_without_a_stated_reason_still_says_so() -> None:
    state = _state([], [LAUNCHED, _event(2, EVENT_RUN_FAILED)])

    assert project_run(_campaign(), state, now=NOW).failure == "the run failed"


def test_only_current_generation_stages_count_as_complete() -> None:
    old = _step("implement", StepState.DONE, closed_at=NOW)
    reactivated = _step("implement", StepState.ACTIVE, attempt=2, generation=1)
    rework = RunEvent(
        run_id=RUN,
        seq=2,
        event_type="engine.rework.taken",
        generation=1,
        created_at=NOW,
        payload={},
    )

    projection = project_run(_campaign(), _state([old, reactivated], [LAUNCHED, rework]), now=NOW)

    assert {s.stage_id: s.status for s in projection.stage_state}["implement"] == "active"


def test_the_gates_of_a_run_are_listed_newest_first_with_their_decisions() -> None:
    first = _step(
        "approve",
        StepState.DONE,
        member=None,
        verdict="request_changes",
        note="add a test",
        closed_at=NOW,
        activated_at=datetime(2026, 11, 8, 9, 0, tzinfo=UTC),
    )
    second = _step(
        "approve",
        StepState.WAITING,
        member=None,
        attempt=2,
        generation=1,
        activated_at=datetime(2026, 11, 8, 10, 0, tzinfo=UTC),
    )
    decision = RunEvent(
        run_id=RUN,
        seq=2,
        event_type="engine.gate.resolved",
        generation=0,
        created_at=NOW,
        payload={"by": {"kind": "person", "id": "alice"}},
        source_activation_id=first.id,
    )
    state = _state([first, second], [LAUNCHED, decision])
    campaign = _campaign()

    gates = run_gates(campaign, state)

    assert [(g["status"], g["attempt"]) for g in gates] == [
        ("pending", 2),
        ("changes_requested", 1),
    ]
    assert gates[1]["decided_by"] == "alice" and gates[1]["notes"] == "add a test"
    assert [g["attempt"] for g in open_gates(campaign, state)] == [2]


def test_the_definition_of_a_snapshot_is_its_graph_documents_and_pins() -> None:
    definition = definition_of_snapshot(_snapshot(), run_id=RUN)

    assert definition.workflow_name == "Build and review"
    assert set(definition.persona_definitions) == {"coder", "reviewer"}
    assert definition.persona_refs["coder"].revision == "r1"
    assert definition.graph["nodes"][0]["id"] == "go"


def test_a_snapshot_without_a_graph_is_refused() -> None:
    with pytest.raises(WorkflowDocumentError, match="has no graph"):
        definition_of_snapshot({"name": "x"}, run_id=RUN)


def test_a_malformed_persona_pin_is_refused() -> None:
    snapshot = {**_snapshot(), "persona_dependencies": {"coder": "r1"}}

    with pytest.raises(WorkflowDocumentError, match="is not an object"):
        definition_of_snapshot(snapshot, run_id=RUN)

"""A run drawn as its workflow's graph, each node with where the run is at it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ting.api.research import resolve_workflow_campaign_repo
from ting.api.work import create_work_router
from ting.config import AuthConfig, Settings
from ting.domain.run_graph import run_graph
from ting.domain.workflow_steps import (
    RunState,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
)

from .stuck_support import SLUG, StuckHarness, make_campaign

OWNER_HEADERS = {"x-auth-user-id": "user-1", "x-auth-tenant": "tenant-1"}
AT = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _step(node: str, kind: StepKind, state: StepState, **changes) -> WorkflowStep:
    values = {
        "id": uuid4(),
        "run_id": uuid4(),
        "node_id": node,
        "member": None,
        "kind": kind,
        "attempt": 1,
        "generation": 0,
        "state": state,
        "created_at": AT,
    }
    if state is StepState.WAITING:
        values["waiting_on"] = WaitingOn(WaitingOnKind.PERSON, "gate", AT)
    values.update(changes)
    return WorkflowStep(**values)


def _states(campaign, steps) -> dict[str, str]:
    drawn = run_graph(campaign, RunState(campaign.id, steps=tuple(steps)))
    return {node.node_id: node.state for node in drawn.nodes}


def test_a_run_that_has_not_started_has_every_node_pending() -> None:
    assert _states(make_campaign(), []) == {"go": "pending", "make": "pending", "done": "pending"}


def test_the_trigger_is_done_and_a_node_at_work_is_active_with_its_members() -> None:
    campaign = make_campaign()
    steps = [
        _step("make", StepKind.OFFER, StepState.DONE),
        _step(
            "make",
            StepKind.STAGE_MEMBER,
            StepState.DONE,
            member="alpha",
            verdict="ok",
            session_id="s-alpha",
        ),
        _step(
            "make",
            StepKind.STAGE_MEMBER,
            StepState.ACTIVE,
            member="bravo",
            session_id="s-b",
            created_at=AT + timedelta(seconds=1),
        ),
    ]

    drawn = run_graph(campaign, RunState(campaign.id, steps=tuple(steps)))
    make = next(node for node in drawn.nodes if node.node_id == "make")

    assert {node.node_id: node.state for node in drawn.nodes} == {
        "go": "done",
        "make": "active",
        "done": "pending",
    }
    assert [(m.member, m.state, m.verdict, m.session_id) for m in make.members] == [
        ("alpha", "done", "ok", "s-alpha"),
        ("bravo", "active", None, "s-b"),
    ]
    assert drawn.workflow_name == "Make things"
    assert [node["id"] for node in drawn.graph["nodes"]] == ["go", "make", "done"]


def test_a_waiting_step_makes_its_node_wait_and_a_rework_replaces_the_earlier_round() -> None:
    campaign = make_campaign()
    steps = [
        _step("make", StepKind.STAGE_MEMBER, StepState.FAILED, member="alpha"),
        _step(
            "make",
            StepKind.STAGE_MEMBER,
            StepState.WAITING,
            member="alpha",
            generation=1,
            created_at=AT + timedelta(minutes=1),
        ),
        _step("done", StepKind.END, StepState.DONE),
    ]

    drawn = run_graph(campaign, RunState(campaign.id, steps=tuple(steps)))
    make = next(node for node in drawn.nodes if node.node_id == "make")

    assert make.state == "waiting"
    assert [(m.member, m.state) for m in make.members] == [("alpha", "waiting")]
    assert next(node for node in drawn.nodes if node.node_id == "done").state == "done"


@pytest.fixture
async def launched() -> StuckHarness:
    run = StuckHarness()
    await run.launch()
    return run


def _client(run: StuckHarness) -> TestClient:
    app = FastAPI()
    app.state.settings = Settings(auth=AuthConfig(allow_anonymous_dev=False))
    app.state.workflow_runs = run.runs
    app.include_router(create_work_router())
    app.dependency_overrides[resolve_workflow_campaign_repo] = lambda: run.campaigns
    return TestClient(app)


async def test_the_route_answers_with_the_runs_graph_and_its_node_states(
    launched: StuckHarness,
) -> None:
    response = _client(launched).get(f"/api/v1/ting/work/runs/{SLUG}/graph", headers=OWNER_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["workflowName"] == "Make things"
    assert [node["id"] for node in body["graph"]["nodes"]] == ["go", "make", "done"]
    states = {node["nodeId"]: node for node in body["nodes"]}
    assert states["go"]["state"] == "done"
    assert states["make"]["state"] == "active"
    assert {member["member"] for member in states["make"]["members"]} == {"alpha", "bravo"}
    assert all(member["sessionId"] for member in states["make"]["members"])
    assert states["done"]["state"] == "pending"


async def test_someone_elses_run_is_not_found(launched: StuckHarness) -> None:
    response = _client(launched).get(
        f"/api/v1/ting/work/runs/{SLUG}/graph",
        headers={"x-auth-user-id": "someone-else", "x-auth-tenant": "tenant-1"},
    )

    assert response.status_code == 404

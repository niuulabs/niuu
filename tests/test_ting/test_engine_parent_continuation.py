"""The engine parent continuation is domain-neutral.

It is constructed once, in ``main.py``, and handed to both the generic
``WorkflowExecutionService``/``WorkflowWaitService`` and (today) the code delivery
specialization. These tests exercise it against a plain, non-delivery
``WorkflowExecution`` to prove a workflow that isn't code delivery can still close its
parent run's waiting steps and cancel the run.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ting.adapters.engine_parent_continuation import EngineParentWorkflowContinuation
from ting.domain.workflow_execution import (
    ChildTemplate,
    ExecutionBudget,
    ExpansionPolicy,
    WorkflowExecution,
)
from ting.domain.workflow_wait import (
    WaitObservation,
    WaitObservationStatus,
    WorkflowWait,
    WorkflowWaitState,
)


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode()).hexdigest()


def _editorial_execution(**changes) -> WorkflowExecution:
    now = datetime.now(UTC)
    schema = {"type": "object", "properties": {}, "additionalProperties": True}
    value = WorkflowExecution(
        id=uuid4(),
        name="Publish translated handbook",
        prompt="Translate, fact-check, and publish the handbook",
        owner_id="editor-1",
        tenant_id="publishing",
        workflow_id=uuid4(),
        workflow_revision=_digest("workflow"),
        workflow_digest=_digest("workflow"),
        parent_session_id="",
        parent_node_id="commission-translations",
        connection_id="",
        policy=ExpansionPolicy(
            coordinator_id="editorial-coordinator",
            templates={
                "translation": ChildTemplate(
                    dependency_alias="translation-assignment",
                    id=uuid4(),
                    revision="v1",
                    digest=_digest("child-template"),
                ),
            },
            input_schema=schema,
            result_schema=schema,
            max_children=5,
            max_attempts=3,
            max_active_children=4,
        ),
        budget=ExecutionBudget(total_units=100),
        deadline=now + timedelta(hours=1),
        workflow_snapshot={"graph": {"nodes": [], "edges": []}},
        created_at=now,
        updated_at=now,
    )
    return replace(value, **changes)


class _Runs:
    def __init__(self, *, finished: bool = False) -> None:
        self.closed: list[tuple] = []
        self.canceled: list[tuple] = []
        self._finished = finished

    async def close_wait(self, run_id, node_id, verdict, *, outcome, note=""):
        self.closed.append((run_id, node_id, verdict, outcome, note))

    async def state(self, run_id):
        return SimpleNamespace(finished=self._finished)

    async def cancel(self, campaign, reason):
        self.canceled.append((campaign, reason))


class _Campaigns:
    def __init__(self, campaign) -> None:
        self._campaign = campaign

    async def get_campaign(self, campaign_id):
        if self._campaign is not None and self._campaign.id == campaign_id:
            return self._campaign
        return None


def _continuation(runs: _Runs, campaign=None) -> EngineParentWorkflowContinuation:
    return EngineParentWorkflowContinuation(
        runs=lambda: runs, campaigns=lambda: _Campaigns(campaign)
    )


@pytest.mark.asyncio
async def test_children_joined_closes_the_subworkflow_step_with_the_joined_verdict() -> None:
    execution = _editorial_execution()
    runs = _Runs()

    await _continuation(runs).resume_parent(
        execution,
        generation=1,
        results=[{"childKey": "fr", "attemptId": "attempt-1"}],
    )

    [(run_id, node_id, verdict, outcome, _)] = runs.closed
    assert (run_id, node_id, verdict) == (execution.id, "commission-translations", "joined")
    assert outcome["generation"] == 1
    assert outcome["results"] == [{"childKey": "fr", "attemptId": "attempt-1"}]


@pytest.mark.asyncio
async def test_blocked_children_close_the_subworkflow_step_with_the_blocked_verdict() -> None:
    execution = _editorial_execution()
    runs = _Runs()

    await _continuation(runs).notify_parent(
        execution,
        generation=1,
        correlation_revision=3,
        children=[{"childKey": "fr", "failureKind": "transient"}],
    )

    [(_, node_id, verdict, outcome, _)] = runs.closed
    assert (node_id, verdict) == ("commission-translations", "blocked")
    assert outcome["revision"] == 3
    assert outcome["children"] == [{"childKey": "fr", "failureKind": "transient"}]


@pytest.mark.asyncio
async def test_a_terminal_wait_observation_closes_that_waits_step() -> None:
    execution = _editorial_execution()
    runs = _Runs()
    wait = WorkflowWait(
        id=uuid4(),
        execution_id=execution.id,
        node_id="await-fact-check",
        condition_type="fact-check",
        request={},
        request_digest=_digest("wait-request"),
        execution_generation=1,
        execution_revision=1,
        state=WorkflowWaitState.READY,
        next_poll_at=datetime.now(UTC),
    )
    observation = WaitObservation(
        status=WaitObservationStatus.SATISFIED,
        observed_at=datetime.now(UTC),
        reason="fact-check passed",
        detail={"summary": "translations complete"},
    )

    await _continuation(runs).notify_wait_observation(execution, wait, observation)

    [(_, node_id, verdict, outcome, _)] = runs.closed
    assert (node_id, verdict) == ("await-fact-check", "observed")
    assert outcome["waitId"] == str(wait.id)
    assert outcome["nodeId"] == "await-fact-check"
    assert outcome["conditionType"] == "fact-check"


@pytest.mark.asyncio
async def test_stopping_the_parent_cancels_its_run() -> None:
    execution = _editorial_execution()
    campaign = SimpleNamespace(id=execution.id)
    runs = _Runs()

    await _continuation(runs, campaign).stop_parent(execution)

    assert runs.canceled == [(campaign, "the execution was canceled by its owner")]


@pytest.mark.asyncio
async def test_stopping_a_parent_whose_run_ended_changes_nothing() -> None:
    execution = _editorial_execution()
    runs = _Runs(finished=True)

    await _continuation(runs, SimpleNamespace(id=execution.id)).stop_parent(execution)

    assert runs.canceled == []


@pytest.mark.asyncio
async def test_stopping_a_parent_with_no_run_is_an_error() -> None:
    with pytest.raises(LookupError, match="no run to stop"):
        await _continuation(_Runs(), None).stop_parent(_editorial_execution())

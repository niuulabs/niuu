"""The Volundr parent continuation adapter is domain-neutral.

It is constructed once, in ``main.py``, and handed to both the generic
``WorkflowExecutionService``/``WorkflowWaitService`` and (today) the code
delivery specialization. These tests exercise it against a plain, non-
delivery ``WorkflowExecution`` — no ``ting.delivery`` import anywhere in this
module — to prove a workflow that isn't code delivery can still resume its
parent session and forward a wait observation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest

from ting.adapters.parent_workflow_continuation import VolundrParentWorkflowContinuation
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


def _graph_snapshot() -> dict:
    """A pinned graph for a non-delivery editorial workflow.

    Names a subworkflow node (for resume/notify) and a wait node (for a
    terminal observation), each with event names unlike anything code
    delivery uses, to prove the continuation reads them from the pinned
    graph rather than assuming a fixed vocabulary.
    """
    return {
        "graph": {
            "nodes": [
                {
                    "id": "commission-translations",
                    "kind": "subworkflow",
                    "blockedEvent": "translations.blocked",
                },
                {
                    "id": "await-fact-check",
                    "kind": "wait",
                    "conditions": ["fact-check"],
                },
            ],
            "edges": [
                {
                    "id": "translations-joined",
                    "source": "commission-translations",
                    "target": "publish-handbook",
                    "label": "translations.verified -> translations.verified",
                },
                {
                    "id": "fact-check-observed",
                    "source": "await-fact-check",
                    "target": "publish-handbook",
                    "label": "fact-check.observed -> fact-check.observed",
                },
            ],
        }
    }


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
        parent_session_id="session-1",
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
        workflow_snapshot=_graph_snapshot(),
        created_at=now,
        updated_at=now,
    )
    return replace(value, **changes)


class _RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    async def publish_workflow_event(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))


class _SingleOwnerFactory:
    def __init__(self, adapter: _RecordingAdapter) -> None:
        self._adapter = adapter

    async def primary_for_owner(self, owner_id: str):
        assert owner_id == "editor-1"
        return self._adapter

    async def for_connection(self, owner_id: str, connection_id: str):
        raise AssertionError("no explicit connection expected for this execution")


@pytest.mark.asyncio
async def test_generic_continuation_resumes_a_plain_non_delivery_execution() -> None:
    """A workflow execution with no delivery row can still resume its parent."""
    execution = _editorial_execution()
    adapter = _RecordingAdapter()
    continuation = VolundrParentWorkflowContinuation(volundr_factory=_SingleOwnerFactory(adapter))

    await continuation.resume_parent(
        execution,
        generation=1,
        results=[{"childKey": "fr", "attemptId": "attempt-1"}],
    )

    assert len(adapter.calls) == 1
    args, kwargs = adapter.calls[0]
    # The event name comes from the pinned graph's own outgoing edge, not a
    # literal the caller supplied.
    assert args[:2] == ("session-1", "translations.verified")
    assert kwargs["payload"]["parentNodeId"] == "commission-translations"
    assert kwargs["payload"]["generation"] == 1
    assert kwargs["request_id"] == kwargs["payload"]["continuationId"]


@pytest.mark.asyncio
async def test_generic_continuation_notifies_parent_of_blocked_children() -> None:
    execution = _editorial_execution()
    adapter = _RecordingAdapter()
    continuation = VolundrParentWorkflowContinuation(volundr_factory=_SingleOwnerFactory(adapter))

    await continuation.notify_parent(
        execution,
        generation=1,
        correlation_revision=3,
        children=[{"childKey": "fr", "failureKind": "transient"}],
    )

    assert len(adapter.calls) == 1
    args, kwargs = adapter.calls[0]
    assert args[:2] == ("session-1", "translations.blocked")
    assert kwargs["payload"]["revision"] == 3


@pytest.mark.asyncio
async def test_generic_continuation_forwards_a_terminal_wait_observation() -> None:
    execution = _editorial_execution()
    adapter = _RecordingAdapter()
    continuation = VolundrParentWorkflowContinuation(volundr_factory=_SingleOwnerFactory(adapter))
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

    await continuation.notify_wait_observation(execution, wait, observation)

    assert len(adapter.calls) == 1
    args, kwargs = adapter.calls[0]
    assert args[:2] == ("session-1", "fact-check.observed")
    assert kwargs["payload"]["waitId"] == str(wait.id)
    assert kwargs["payload"]["nodeId"] == "await-fact-check"

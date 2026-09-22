"""Edge-case coverage for the timer wait observer's validation and constructor.

``tests/test_ting/test_workflow_wait.py`` already exercises ``TimerWaitObserver``
end to end through the generic wait service with well-formed requests; this file
covers the observer's own malformed-input and construction-time rejections.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest

from ting.adapters.timer_wait_observer import TimerWaitObserver
from ting.domain.workflow_execution import (
    ChildTemplate,
    ExecutionBudget,
    ExecutionState,
    ExpansionPolicy,
    WorkflowExecution,
)
from ting.domain.workflow_wait import WorkflowWait, WorkflowWaitState


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode()).hexdigest()


def _execution() -> WorkflowExecution:
    now = datetime.now(UTC)
    schema = {"type": "object", "additionalProperties": True}
    return WorkflowExecution(
        id=uuid4(),
        name="Timer host execution",
        prompt="Do something",
        owner_id="owner-1",
        tenant_id="tenant-1",
        workflow_id=uuid4(),
        workflow_revision="rev-1",
        workflow_digest=_digest("workflow"),
        parent_session_id="parent-session",
        parent_node_id="wait-node",
        connection_id="conn-1",
        policy=ExpansionPolicy(
            coordinator_id="coordinator-1",
            templates={
                "child": ChildTemplate(
                    dependency_alias="child-assignment",
                    id=uuid4(),
                    revision="child-v1",
                    digest=_digest("child-template"),
                ),
            },
            input_schema=schema,
            result_schema=schema,
            max_children=1,
            max_attempts=1,
            max_active_children=1,
        ),
        budget=ExecutionBudget(total_units=1),
        deadline=now + timedelta(hours=1),
        state=ExecutionState.RUNNING,
        current_generation=1,
        created_at=now,
        updated_at=now,
    )


def _wait(request: dict) -> WorkflowWait:
    now = datetime.now(UTC)
    return WorkflowWait(
        id=uuid4(),
        execution_id=uuid4(),
        node_id="wait-node",
        condition_type="timer",
        request=request,
        request_digest="digest",
        execution_generation=1,
        execution_revision=1,
        state=WorkflowWaitState.PENDING,
        next_poll_at=now,
    )


def test_constructor_rejects_non_positive_poll_interval() -> None:
    with pytest.raises(ValueError, match="poll_interval_seconds must be positive"):
        TimerWaitObserver(poll_interval_seconds=0)


def test_constructor_rejects_negative_poll_interval() -> None:
    with pytest.raises(ValueError, match="poll_interval_seconds must be positive"):
        TimerWaitObserver(poll_interval_seconds=-5.0)


def test_validate_rejects_request_missing_both_fields() -> None:
    observer = TimerWaitObserver()
    with pytest.raises(ValueError, match="exactly one of until or after_seconds"):
        observer.validate({}, _execution())


def test_validate_rejects_request_with_both_fields() -> None:
    observer = TimerWaitObserver()
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="exactly one of until or after_seconds"):
        observer.validate({"until": now.isoformat(), "after_seconds": 5}, _execution())


def test_validate_rejects_non_string_until() -> None:
    observer = TimerWaitObserver()
    with pytest.raises(ValueError, match="until must be an RFC 3339 timestamp string"):
        observer.validate({"until": 12345}, _execution())


def test_validate_rejects_malformed_until_string() -> None:
    observer = TimerWaitObserver()
    with pytest.raises(ValueError, match="until must be an RFC 3339 timestamp"):
        observer.validate({"until": "not-a-timestamp"}, _execution())


def test_validate_rejects_until_without_timezone() -> None:
    observer = TimerWaitObserver()
    naive = datetime.now().replace(tzinfo=None)  # noqa: DTZ005 - deliberately naive
    with pytest.raises(ValueError, match="until must include a timezone"):
        observer.validate({"until": naive.isoformat()}, _execution())


def test_validate_rejects_negative_after_seconds() -> None:
    observer = TimerWaitObserver()
    with pytest.raises(ValueError, match="after_seconds must be a non-negative integer"):
        observer.validate({"after_seconds": -1}, _execution())


def test_validate_rejects_boolean_after_seconds() -> None:
    observer = TimerWaitObserver()
    with pytest.raises(ValueError, match="after_seconds must be a non-negative integer"):
        observer.validate({"after_seconds": True}, _execution())


def test_validate_rejects_non_integer_after_seconds() -> None:
    observer = TimerWaitObserver()
    with pytest.raises(ValueError, match="after_seconds must be a non-negative integer"):
        observer.validate({"after_seconds": "5"}, _execution())


@pytest.mark.asyncio
async def test_observe_reports_pending_before_deadline() -> None:
    observer = TimerWaitObserver(poll_interval_seconds=10.0)
    deadline = datetime.now(UTC) + timedelta(seconds=120)
    wait = _wait({"until": deadline.isoformat()})

    observation = await observer.observe(wait, _execution())

    assert observation.status.value == "pending"
    assert observation.retry_after_seconds == 10.0


@pytest.mark.asyncio
async def test_observe_reports_satisfied_after_deadline() -> None:
    observer = TimerWaitObserver()
    deadline = datetime.now(UTC) - timedelta(seconds=1)
    wait = _wait({"until": deadline.isoformat()})

    observation = await observer.observe(wait, _execution())

    assert observation.status.value == "satisfied"
    assert observation.reason == "The configured deadline has passed"

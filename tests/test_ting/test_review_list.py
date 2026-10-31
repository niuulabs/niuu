"""Tests for the list of runs a person reviews across projects."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ting.domain.models import Saga, SagaStatus, WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.review_list import ReviewKind, ReviewState, review_list

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
SAGA_ID = uuid4()


def _campaign(
    metadata: dict[str, Any],
    *,
    status: WorkflowCampaignStatus = WorkflowCampaignStatus.RUNNING,
    workflow_name: str = "Ticket Planning Flow",
    updated_at: datetime = NOW,
) -> WorkflowCampaign:
    return WorkflowCampaign(
        id=uuid4(),
        slug="a2a-1",
        name="Plan the ticket",
        owner_id="owner-1",
        workflow_id=uuid4(),
        workflow_version="1.1.0",
        workflow_name=workflow_name,
        workflow_snapshot={},
        session_id="s1",
        session_name="plan",
        status=status,
        active_stage_id=None,
        stage_state=[],
        metadata=metadata,
        created_at=NOW,
        updated_at=updated_at,
    )


def _ticket(**extra: Any) -> dict[str, Any]:
    return {
        "ticket": {"sagaId": str(SAGA_ID), "issueId": "issue-1", "identifier": "PLT-212"},
        **extra,
    }


def _saga() -> Saga:
    return Saga(
        id=SAGA_ID,
        tracker_id="project-1",
        tracker_type="linear",
        slug="platform",
        name="Platform reliability",
        repos=[],
        feature_branch="work",
        status=SagaStatus.ACTIVE,
        created_at=NOW,
        base_branch="main",
    )


def test_a_ticket_run_waits_on_the_gate_its_campaign_records() -> None:
    gate = {"label": "Plan review", "node_id": "plan-review-gate"}

    (item,) = review_list([_campaign(_ticket(pending_workflow_gates=[gate]))], [_saga()])

    assert item.kind == ReviewKind.TICKET
    assert item.state == ReviewState.WAITING
    assert item.gate_label == "Plan review"
    assert (item.project_id, item.issue_id) == (str(SAGA_ID), "issue-1")
    assert item.identifier == "PLT-212"
    assert item.project_name == "Platform reliability"


def test_a_run_with_no_open_gate_is_running() -> None:
    (item,) = review_list([_campaign(_ticket())], [])

    assert item.state == ReviewState.RUNNING
    assert item.gate_label == ""
    assert item.project_name is None


def test_finished_and_failed_runs_say_so() -> None:
    done, failed = review_list(
        [
            _campaign(_ticket(), status=WorkflowCampaignStatus.COMPLETED),
            _campaign(
                _ticket(),
                status=WorkflowCampaignStatus.FAILED,
                updated_at=NOW - timedelta(hours=1),
            ),
        ],
        [],
    )

    assert done.state == ReviewState.DONE
    assert failed.state == ReviewState.FAILED


def test_a_project_plan_is_listed_and_names_its_project_once_committed() -> None:
    planned, committed = review_list(
        [
            _campaign({"surface": "ting.plan"}),
            _campaign(
                {"saga_id": str(SAGA_ID)},
                workflow_name="Saga Planning",
                updated_at=NOW - timedelta(hours=1),
            ),
        ],
        [_saga()],
    )

    assert planned.kind == ReviewKind.PROJECT_PLAN
    assert planned.project_id is None
    assert committed.project_id == str(SAGA_ID)
    assert committed.project_name == "Platform reliability"


def test_other_runs_are_not_reviews() -> None:
    assert review_list([_campaign({}, workflow_name="Research Campaign")], []) == ()


def test_the_newest_comes_first() -> None:
    older = _campaign(_ticket(), updated_at=NOW - timedelta(days=1))
    newer = _campaign(_ticket(), updated_at=NOW)

    assert [item.updated_at for item in review_list([older, newer], [])] == [NOW, older.updated_at]


def test_an_unusable_gate_record_is_not_a_waiting_gate() -> None:
    (item,) = review_list([_campaign(_ticket(pending_workflow_gates=["gate"]))], [])

    assert item.state == ReviewState.RUNNING

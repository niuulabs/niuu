"""Tests for the list of runs a person reviews across projects."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ting.domain.models import Saga, SagaStatus, WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.review_list import ReviewKind, ReviewState, review_list, review_run

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
    (item,) = review_list([_campaign(_ticket())], [_saga()])

    assert item.state == ReviewState.RUNNING
    assert item.gate_label == ""


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
        [_saga()],
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


def test_a_ticket_whose_project_was_deleted_is_not_listed() -> None:
    gate = {"label": "Plan review"}

    assert review_list([_campaign(_ticket(pending_workflow_gates=[gate]))], []) == ()


def test_a_committed_plan_stays_listed_after_its_project_is_deleted() -> None:
    (item,) = review_list([_campaign({"surface": "ting.plan", "saga_id": str(SAGA_ID)})], [])

    assert item.kind == ReviewKind.PROJECT_PLAN
    assert item.project_name is None


def test_a_stopped_plan_names_no_gate_anyone_is_waiting_on() -> None:
    (item,) = review_list(
        [
            _campaign(
                {"surface": "ting.plan", "pending_workflow_gates": [{"node_id": "plan-brief"}]},
                status=WorkflowCampaignStatus.FAILED,
            )
        ],
        [],
    )

    assert item.state == ReviewState.FAILED
    assert item.gate_label == ""


def test_a_run_with_no_ticket_is_a_review_while_it_waits() -> None:
    metadata = {
        "prompt": "Plan NIU-1125 only\nRead the complete ticket.",
        "actor_id": "system:serviceaccount:nats:regin",
        "pending_workflow_gates": [{"node_id": "spec-prd-gate"}],
    }

    (item,) = review_list([_campaign(metadata, status=WorkflowCampaignStatus.BLOCKED)], [])

    assert item.kind == ReviewKind.RUN
    assert item.state == ReviewState.WAITING
    assert item.task_id == "a2a-1"
    assert item.title == "Plan NIU-1125 only"
    assert item.gate_label == "spec-prd-gate"
    assert item.started_by == "regin"
    assert item.project_id is None


def test_a_run_a_person_started_needs_no_note_of_who_did() -> None:
    metadata = {
        "actor_id": "76475334-b685-4299-b91d-1ec37f57e10f",
        "pending_workflow_gates": [{"node_id": "gate"}],
    }

    (item,) = review_list([_campaign(metadata)], [])

    assert item.started_by is None
    assert item.title == "Plan the ticket"


def test_a_run_with_no_ticket_leaves_the_list_once_nothing_waits() -> None:
    gated = {"pending_workflow_gates": [{"node_id": "gate"}]}

    assert (
        review_list(
            [
                _campaign(gated, status=WorkflowCampaignStatus.FAILED),
                _campaign(gated, status=WorkflowCampaignStatus.COMPLETED),
                _campaign({}),
            ],
            [],
        )
        == ()
    )


def test_a_run_introduces_itself_to_its_review_page() -> None:
    run = review_run(
        _campaign(
            {
                "prompt": "Plan NIU-1125 only\nRead the complete ticket.",
                "actor_id": "system:serviceaccount:nats:regin",
                "repo": "https://github.com/niuulabs/niuu",
                "branch": "regin",
            }
        )
    )

    assert run.slug == "a2a-1"
    assert run.title == "Plan NIU-1125 only"
    assert run.prompt == "Plan NIU-1125 only\nRead the complete ticket."
    assert run.session_id == "s1"
    assert (run.repo, run.branch, run.started_by) == (
        "https://github.com/niuulabs/niuu",
        "regin",
        "regin",
    )


def test_other_runs_are_not_reviews() -> None:
    assert review_list([_campaign({}, workflow_name="Research Campaign")], []) == ()


def test_the_newest_comes_first() -> None:
    older = _campaign(_ticket(), updated_at=NOW - timedelta(days=1))
    newer = _campaign(_ticket(), updated_at=NOW)

    assert [item.updated_at for item in review_list([older, newer], [_saga()])] == [
        NOW,
        older.updated_at,
    ]


def test_an_unusable_gate_record_is_not_a_waiting_gate() -> None:
    (item,) = review_list([_campaign(_ticket(pending_workflow_gates=["gate"]))], [_saga()])

    assert item.state == ReviewState.RUNNING


def test_a_ticket_run_shows_the_ticket_title_it_recorded() -> None:
    metadata = _ticket()
    metadata["ticket"]["title"] = "Trace queue wait time"

    (item,) = review_list([_campaign(metadata)], [_saga()])

    assert item.title == "Trace queue wait time"

"""Tests for starting a ticket's workflow runs and closing the ticket's record."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from niuu.domain.models import Principal
from ting.domain.models import (
    Saga,
    SagaBoardWorkflow,
    SagaStatus,
    TrackerIssue,
    WorkflowCampaignStatus,
)
from ting.domain.services.ticket_coordinator import (
    TicketCoordinatorService,
    TicketWorkflow,
    ticket_branch,
)
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.domain.ticket_coordinator import (
    TicketCoordinator,
    TicketCoordinatorStatus,
    ticket_metadata,
    ticket_of,
)
from ting.domain.workflow_steps import RunStateError

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
PRINCIPAL = Principal(user_id="owner-1", email="", tenant_id="tenant-1", roles=[])
WORKFLOW_ID = uuid4()
WORKFLOW = TicketWorkflow(id=str(WORKFLOW_ID), name="Reviewed Delivery Flow")
SNAPSHOT = {"name": "Reviewed Delivery Flow", "graph": {"nodes": [], "edges": []}}


def _saga(**changes) -> Saga:
    fields = dict(
        id=uuid4(),
        tracker_id="project-1",
        tracker_type="linear",
        slug="platform",
        name="Platform",
        repos=["jve/repo"],
        feature_branch="feat/platform",
        status=SagaStatus.ACTIVE,
        created_at=NOW,
        base_branch="main",
        workflow_id=WORKFLOW_ID,
        workflow_version="1.0.0",
        workflow_snapshot=SNAPSHOT,
    )
    fields.update(changes)
    return Saga(**fields)


def _issue() -> TrackerIssue:
    return TrackerIssue(
        id="issue-1",
        identifier="NIU-1",
        title="Ship it",
        description="The details.",
        status="Todo",
        url="https://linear.example/NIU-1",
    )


def _coordinator(saga: Saga, *, status=TicketCoordinatorStatus.ACTIVE) -> TicketCoordinator:
    return TicketCoordinator(
        saga_id=saga.id,
        issue_id="issue-1",
        owner_id="owner-1",
        tenant_id="tenant-1",
        session_id="",
        connection_id=None,
        status=status,
        created_at=NOW,
        advanced_from="earlier-run",
    )


def _service(*, current: TicketCoordinator | None, active_run: str | None = None):
    repo = MagicMock()
    repo.get = AsyncMock(return_value=current)
    repo.save = AsyncMock(side_effect=lambda item: item)
    repo.close = AsyncMock()
    campaigns = MagicMock()
    campaigns.save_ticket_campaign = AsyncMock(return_value=active_run)
    campaigns.save_campaign = AsyncMock(side_effect=lambda item: item)
    runs = MagicMock()
    runs.launch = AsyncMock()
    service = TicketCoordinatorService(
        repo=repo, campaigns=campaigns, runs=runs, principal=PRINCIPAL
    )
    return service, repo, campaigns, runs


@pytest.mark.parametrize(
    "current_status",
    [None, TicketCoordinatorStatus.ACTIVE, TicketCoordinatorStatus.CLOSED],
    ids=["no-record", "active-record", "closed-record"],
)
async def test_starting_a_workflow_creates_the_tickets_run_and_launches_it(current_status) -> None:
    saga = _saga()
    current = None if current_status is None else _coordinator(saga, status=current_status)
    service, repo, campaigns, runs = _service(current=current)

    record = await service.start_workflow(saga, _issue(), WORKFLOW, connection_id="forge-1")

    (campaign,) = [call.args[0] for call in campaigns.save_ticket_campaign.await_args_list]
    assert campaigns.save_ticket_campaign.await_args.kwargs == {
        "saga_id": saga.id,
        "issue_id": "issue-1",
    }
    assert ticket_of(campaign.metadata) == (str(saga.id), "issue-1")
    assert campaign.workflow_id == WORKFLOW_ID
    assert campaign.workflow_snapshot is SNAPSHOT
    assert campaign.session_id == ""
    assert campaign.connection_id == "forge-1"
    assert campaign.status == WorkflowCampaignStatus.PENDING
    assert campaign.owner_id == "owner-1"
    assert campaign.metadata["repo"] == "jve/repo"
    assert campaign.metadata["branch"] == ticket_branch(saga, _issue()) == "feat/platform-niu-1"
    assert campaign.metadata["base_branch"] == "main"
    runs.launch.assert_awaited_once()
    launched, payload = runs.launch.await_args.args
    assert launched is campaign
    assert payload["prompt"].startswith("Ticket NIU-1: Ship it")
    assert payload["ticket"] == campaign.metadata["ticket"]
    assert payload["branch"] == "feat/platform-niu-1"
    assert record.session_id == ""
    assert record.status == TicketCoordinatorStatus.ACTIVE
    assert record.requested_workflow_id == WORKFLOW.id
    assert record.start_error is None
    assert record.requested_at <= campaign.created_at
    if current_status is not None:
        assert record.advanced_from == "earlier-run"
        assert record.created_at == NOW
    repo.save.assert_awaited_once()


async def test_a_project_board_runs_the_version_it_pinned() -> None:
    other_id = uuid4()
    pinned = {"name": "Pinned Flow", "graph": {"nodes": [], "edges": []}}
    saga = _saga(
        board_workflows=(
            SagaBoardWorkflow(
                workflow_id=other_id, workflow_version="2.0.0", workflow_snapshot=pinned
            ),
        )
    )
    service, _, campaigns, _ = _service(current=None)

    await service.start_workflow(
        saga, _issue(), TicketWorkflow(id=str(other_id), name="Pinned Flow"), connection_id=None
    )

    campaign = campaigns.save_ticket_campaign.await_args.args[0]
    assert campaign.workflow_snapshot is pinned
    assert campaign.workflow_version == "2.0.0"
    assert campaign.workflow_name == "Pinned Flow"


async def test_an_isolated_workflow_works_on_its_own_branch() -> None:
    isolated = {
        "name": "Isolated",
        "graph": {"nodes": [], "edges": [], "workingBranch": "isolated"},
    }
    saga = _saga(workflow_snapshot=isolated)
    service, _, campaigns, runs = _service(current=None)

    await service.start_workflow(saga, _issue(), WORKFLOW, connection_id=None)

    campaign = campaigns.save_ticket_campaign.await_args.args[0]
    assert campaign.metadata["branch"] == f"workflow/{campaign.slug}"
    assert campaign.metadata["base_branch"] == "main"
    assert runs.launch.await_args.args[1]["branch"] == f"workflow/{campaign.slug}"


async def test_a_workflow_not_on_the_board_is_refused_before_anything_is_saved() -> None:
    service, repo, campaigns, runs = _service(current=None)

    with pytest.raises(RunStateError, match="not on this project's board"):
        await service.start_workflow(
            _saga(), _issue(), TicketWorkflow(id=str(uuid4()), name="Elsewhere"), connection_id=None
        )

    repo.save.assert_not_awaited()
    campaigns.save_ticket_campaign.assert_not_awaited()
    runs.launch.assert_not_awaited()


async def test_a_ticket_with_a_run_in_progress_is_refused() -> None:
    service, _, _, runs = _service(current=None, active_run="niu-1-running")

    with pytest.raises(RunStateError, match="already has a run in progress"):
        await service.start_workflow(_saga(), _issue(), WORKFLOW, connection_id=None)

    runs.launch.assert_not_awaited()


async def test_a_run_that_cannot_start_fails_its_campaign_and_says_why_on_the_ticket() -> None:
    service, repo, campaigns, runs = _service(current=None)
    runs.launch.side_effect = RuntimeError("no persona pinned")

    with pytest.raises(RuntimeError, match="no persona pinned"):
        await service.start_workflow(_saga(), _issue(), WORKFLOW, connection_id=None)

    failed = campaigns.save_campaign.await_args.args[0]
    assert failed.status == WorkflowCampaignStatus.FAILED
    assert failed.metadata["failure_error"] == "no persona pinned"
    assert repo.save.await_args.args[0].start_error == (
        "Reviewed Delivery Flow could not start: no persona pinned"
    )


def test_ticket_metadata_round_trips() -> None:
    saga_id = uuid4()
    metadata = {
        "ticket": ticket_metadata(saga_id=saga_id, issue_id="i", identifier="N-1", title="T")
    }

    assert ticket_of(metadata) == (str(saga_id), "i")


@pytest.mark.parametrize("metadata", [{}, {"ticket": "x"}, {"ticket": {"sagaId": "s"}}])
def test_campaigns_without_a_full_ticket_link_have_none(metadata: dict) -> None:
    assert ticket_of(metadata) is None


async def test_closing_records_the_ticket_as_closed() -> None:
    saga = _saga()
    current = _coordinator(saga)
    service, repo, _, _ = _service(current=current)

    await service.close(current)

    repo.close.assert_awaited_once_with(saga.id, "issue-1")


async def test_a_ticket_waiting_at_a_gate_never_expires() -> None:
    projector = WorkflowCampaignProjector.__new__(WorkflowCampaignProjector)
    projector._input_wait_timeout = 1.0
    campaign = SimpleNamespace(
        status=WorkflowCampaignStatus.BLOCKED,
        session_id="",
        metadata={"ticket": {"sagaId": "s", "issueId": "i"}},
        last_activity_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert await projector._stale_expiry(campaign, now=NOW) is None

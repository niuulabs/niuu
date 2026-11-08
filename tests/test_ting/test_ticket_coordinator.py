"""Tests for the persistent coordinator session a ticket works through."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from niuu.domain.models import Principal
from ting.domain.exceptions import (
    WORKFLOW_STEPS_HAVE_NO_RUNNER_MESSAGE,
    WorkflowStepsHaveNoRunnerError,
)
from ting.domain.models import Saga, SagaStatus, TrackerIssue
from ting.domain.services.ticket_coordinator import (
    TicketCoordinatorService,
    TicketWorkflow,
    child_membership,
    ticket_membership,
)
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.domain.ticket_coordinator import (
    TicketCoordinator,
    TicketCoordinatorStatus,
    ticket_metadata,
    ticket_of,
)
from ting.ports.volundr import ForgeProjectRef

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
PRINCIPAL = Principal(user_id="owner-1", email="", tenant_id="tenant-1", roles=[])
WORKFLOW = TicketWorkflow(id="workflow-1", name="Reviewed Delivery Flow")


def _saga() -> Saga:
    return Saga(
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
    )


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
        session_id="session-1",
        connection_id=None,
        status=status,
        created_at=NOW,
    )


def _project(project_id: str, name: str, **_: object) -> ForgeProjectRef:
    return ForgeProjectRef(id=project_id, name=name, instance_id="forge-host")


def _service(*, current: TicketCoordinator | None, session_status: str = "running"):
    repo = MagicMock()
    repo.get = AsyncMock(return_value=current)
    repo.save = AsyncMock(side_effect=lambda item: item)
    repo.close = AsyncMock()
    volundr = MagicMock()
    volundr.get_session = AsyncMock(
        return_value=SimpleNamespace(status=session_status, activity_state=None)
    )
    volundr.spawn_session = AsyncMock(return_value=SimpleNamespace(id="session-2"))
    volundr.ensure_project = AsyncMock(side_effect=_project)
    volundr.join_project = AsyncMock()
    volundr.send_directed_room_message = AsyncMock()
    volundr.start_session = AsyncMock()
    volundr.stop_session = AsyncMock()
    service = TicketCoordinatorService(
        repo=repo,
        volundr=volundr,
        principal=Principal(user_id="owner-1", email="", tenant_id="tenant-1", roles=[]),
        auth_token="token",
    )
    return service, repo, volundr


@pytest.mark.parametrize(
    "current",
    [
        None,
        _coordinator(_saga()),
        _coordinator(_saga(), status=TicketCoordinatorStatus.CLOSED),
    ],
    ids=["no-coordinator", "active-coordinator", "closed-coordinator"],
)
async def test_starting_a_workflow_is_refused_because_steps_have_no_runner(
    current: TicketCoordinator | None,
) -> None:
    service, repo, volundr = _service(current=current)

    with pytest.raises(WorkflowStepsHaveNoRunnerError) as raised:
        await service.start_workflow(_saga(), _issue(), WORKFLOW)

    assert str(raised.value) == WORKFLOW_STEPS_HAVE_NO_RUNNER_MESSAGE
    volundr.spawn_session.assert_not_awaited()
    volundr.send_directed_room_message.assert_not_awaited()
    volundr.start_session.assert_not_awaited()
    repo.save.assert_not_awaited()


def test_ticket_metadata_round_trips() -> None:
    saga_id = uuid4()
    metadata = {
        "ticket": ticket_metadata(saga_id=saga_id, issue_id="i", identifier="N-1", title="T")
    }

    assert ticket_of(metadata) == (str(saga_id), "i")


@pytest.mark.parametrize("metadata", [{}, {"ticket": "x"}, {"ticket": {"sagaId": "s"}}])
def test_campaigns_without_a_full_ticket_link_have_none(metadata: dict) -> None:
    assert ticket_of(metadata) is None


async def test_closing_stops_the_session_and_records_it() -> None:
    saga = _saga()
    current = _coordinator(saga)
    service, repo, volundr = _service(current=current)

    await service.close(current)

    volundr.stop_session.assert_awaited_once()
    repo.close.assert_awaited_once_with(saga.id, "issue-1")


async def test_a_ticket_waiting_at_a_gate_never_expires() -> None:
    projector = WorkflowCampaignProjector.__new__(WorkflowCampaignProjector)
    projector._input_wait_timeout = 1.0
    campaign = SimpleNamespace(
        status="blocked",
        session_id="session-1",
        metadata={"ticket": {"sagaId": "s", "issueId": "i"}},
        last_activity_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert await projector._stale_expiry(campaign, now=NOW) is None


async def test_a_workflow_session_joins_under_the_tickets_coordinator() -> None:
    saga = _saga()
    _, _, volundr = _service(current=None)

    membership = await ticket_membership(
        volundr, saga, _coordinator(saga), "Delivery", auth_token="token", principal=PRINCIPAL
    )

    volundr.join_project.assert_awaited_once_with(
        _coordinator(saga).session_id,
        str(saga.id),
        role="coordinator",
        auth_token="token",
        principal=PRINCIPAL,
    )
    assert membership.role == "worker"
    assert membership.parent_instance_id == "forge-host"
    assert membership.parent_session_id == _coordinator(saga).session_id
    assert membership.objective == "Delivery"
    assert membership.labels == (f"work-project:{saga.id}",)


async def test_a_ticket_with_no_coordinator_joins_its_project_without_a_parent() -> None:
    saga = _saga()
    _, _, volundr = _service(current=None)

    membership = await ticket_membership(
        volundr, saga, None, "Delivery", auth_token=None, principal=PRINCIPAL
    )

    volundr.join_project.assert_not_awaited()
    assert membership.project_id == str(saga.id)
    assert membership.parent_session_id == ""
    assert membership.labels == (f"work-project:{saga.id}",)


def _parent_forge(project_id: str | None, *, known: bool = True) -> MagicMock:
    forge = MagicMock()
    forge.session_project = AsyncMock(return_value=project_id)
    forge.get_project = AsyncMock(
        return_value=(
            ForgeProjectRef(id=project_id or "", name="Home name", instance_id="parent-host")
            if known
            else None
        )
    )
    return forge


async def test_a_child_of_a_session_outside_any_project_joins_none() -> None:
    target = MagicMock()

    membership = await child_membership(
        target, _parent_forge(None), "parent-1", "Work", auth_token=None, principal=PRINCIPAL
    )

    assert membership is None


async def test_a_child_on_another_forge_links_back_to_its_parents_host() -> None:
    _, _, target = _service(current=None)

    membership = await child_membership(
        target, _parent_forge("project-1"), "parent-1", "Work", auth_token=None, principal=PRINCIPAL
    )

    target.ensure_project.assert_awaited_once_with(
        "project-1", "Home name", auth_token=None, principal=PRINCIPAL
    )
    assert membership.project_id == "project-1"
    assert membership.parent_instance_id == "parent-host"
    assert membership.parent_session_id == "parent-1"


async def test_a_parent_naming_a_vanished_project_stops_the_launch() -> None:
    with pytest.raises(LookupError, match="project-1"):
        await child_membership(
            MagicMock(),
            _parent_forge("project-1", known=False),
            "parent-1",
            "Work",
            auth_token=None,
            principal=PRINCIPAL,
        )

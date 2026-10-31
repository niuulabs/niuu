"""A finished run starts the ticket's next workflow, once, by itself."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from ting.domain.models import (
    RunStatus,
    Saga,
    SagaBoardWorkflow,
    WorkflowCampaign,
    WorkflowCampaignStatus,
)
from ting.domain.services.ticket_advance import TicketAdvancer
from ting.domain.ticket_coordinator import (
    TicketCoordinator,
    TicketCoordinatorStatus,
    ticket_metadata,
)
from ting.system_workflows import load_system_workflows

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
PLANNING = "Ticket Planning Flow"
DELIVERY = "Reviewed Delivery Flow"
ISSUE = "issue-1"


def _workflow(name: str):
    return next(item for item in load_system_workflows() if item.name == name)


def _pin(name: str) -> SagaBoardWorkflow:
    workflow = _workflow(name)
    return SagaBoardWorkflow(
        workflow_id=workflow.id,
        workflow_version=workflow.version,
        workflow_snapshot={"name": name, "version": workflow.version, "graph": workflow.graph},
    )


def _saga(*names: str) -> Saga:
    return SimpleNamespace(
        id=uuid4(),
        tracker_id="project-1",
        tracker_connection_id="",
        board_workflows=tuple(_pin(name) for name in names),
        workflow_id=None,
        workflow_snapshot=None,
    )


def _coordinator(saga: Saga, **overrides) -> TicketCoordinator:
    fields = {
        "saga_id": saga.id,
        "issue_id": ISSUE,
        "owner_id": "owner-1",
        "tenant_id": "tenant-1",
        "session_id": "coordinator-session",
        "connection_id": None,
        "status": TicketCoordinatorStatus.ACTIVE,
        "created_at": NOW - timedelta(hours=2),
        "requested_workflow_id": str(_workflow(PLANNING).id),
        "requested_at": NOW - timedelta(hours=1),
    }
    return TicketCoordinator(**{**fields, **overrides})


def _campaign(saga: Saga, name: str = PLANNING, **overrides) -> WorkflowCampaign:
    fields = {
        "id": uuid4(),
        "slug": "a2a-1",
        "owner_id": "owner-1",
        "workflow_id": _workflow(name).id,
        "status": WorkflowCampaignStatus.COMPLETED,
        "created_at": NOW - timedelta(minutes=50),
        "metadata": {
            "ticket": ticket_metadata(saga_id=saga.id, issue_id=ISSUE, identifier="NIU-1")
        },
    }
    return SimpleNamespace(**{**fields, **overrides})


class _Coordinators:
    """Claims the way the table does: at most one start per finished campaign."""

    def __init__(self, coordinator: TicketCoordinator | None) -> None:
        self.row = coordinator
        self.get = AsyncMock(side_effect=self._get)
        self.save = AsyncMock(side_effect=self._save)

    async def _get(self, saga_id: UUID, issue_id: str) -> TicketCoordinator | None:
        return self.row

    async def _save(self, coordinator: TicketCoordinator) -> TicketCoordinator:
        self.row = coordinator
        return coordinator

    async def claim_advance(
        self, saga_id, issue_id, *, finished_campaign_id, workflow_id, requested_at
    ):
        if self.row is None or self.row.advanced_from == finished_campaign_id:
            return None
        self.row = replace(
            self.row,
            advanced_from=finished_campaign_id,
            requested_workflow_id=workflow_id,
            requested_at=requested_at,
            start_error=None,
        )
        return self.row


def _advancer(
    saga: Saga,
    coordinators: _Coordinators,
    *,
    campaigns: list | None = None,
    status_type: str = "started",
    runs: list | None = None,
    start_error: Exception | None = None,
    forge=MagicMock(),
) -> tuple[TicketAdvancer, MagicMock]:
    service = MagicMock()
    service.start_workflow = AsyncMock(side_effect=start_error)
    tracker = MagicMock()
    tracker.connection_id = ""
    tracker.list_issues = AsyncMock(
        return_value=[SimpleNamespace(id=ISSUE, status_type=status_type)]
    )
    tracker.get_run_progress_for_saga = AsyncMock(return_value=runs or [])
    advancer = TicketAdvancer(
        saga_repo=SimpleNamespace(get_saga=AsyncMock(return_value=saga)),
        campaign_repo=SimpleNamespace(list_campaigns=AsyncMock(return_value=campaigns or [])),
        coordinator_repo=coordinators,
        tracker_factory=SimpleNamespace(for_owner=AsyncMock(return_value=[tracker])),
        volundr_factory=SimpleNamespace(
            primary_for_owner=AsyncMock(return_value=forge),
            for_connection=AsyncMock(return_value=forge),
        ),
        coordinators=MagicMock(return_value=service),
    )
    return advancer, service


async def test_a_finished_run_starts_the_next_workflow_through_the_same_coordinator() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    coordinators = _Coordinators(_coordinator(saga))
    forge = MagicMock()
    advancer, service = _advancer(saga, coordinators, campaigns=[finished], forge=forge)

    await advancer.advance(finished)

    service.start_workflow.assert_awaited_once()
    workflow = service.start_workflow.await_args.args[2]
    assert workflow.id == str(_workflow(DELIVERY).id)
    adapter, principal = advancer._coordinators.call_args.args
    assert adapter is forge
    assert principal.user_id == "owner-1"
    assert principal.tenant_id == "tenant-1"


async def test_a_redelivered_completion_starts_nothing_more() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    coordinators = _Coordinators(_coordinator(saga))
    advancer, service = _advancer(saga, coordinators, campaigns=[finished])

    await advancer.advance(finished)
    await advancer.advance(finished)

    service.start_workflow.assert_awaited_once()


async def test_a_lost_race_for_the_claim_starts_nothing() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    coordinators = _Coordinators(_coordinator(saga))
    coordinators.claim_advance = AsyncMock(return_value=None)
    advancer, service = _advancer(saga, coordinators, campaigns=[finished])

    await advancer.advance(finished)

    service.start_workflow.assert_not_awaited()


async def test_a_restart_catches_up_on_a_run_that_finished_unnoticed() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    coordinators = _Coordinators(_coordinator(saga))
    advancer, service = _advancer(saga, coordinators, campaigns=[finished])

    await advancer.advance_finished("owner-1")
    await advancer.advance_finished("owner-1")

    service.start_workflow.assert_awaited_once()


async def test_a_restart_skips_a_ticket_whose_latest_run_did_not_finish() -> None:
    saga = _saga(PLANNING, DELIVERY)
    running = _campaign(saga, status=WorkflowCampaignStatus.RUNNING)
    advancer, service = _advancer(saga, _Coordinators(_coordinator(saga)), campaigns=[running])

    await advancer.advance_finished("owner-1")

    service.start_workflow.assert_not_awaited()


@pytest.mark.parametrize(
    "status",
    [
        WorkflowCampaignStatus.FAILED,
        WorkflowCampaignStatus.BLOCKED,
        WorkflowCampaignStatus.RUNNING,
    ],
)
async def test_only_a_successful_run_advances(status: WorkflowCampaignStatus) -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga, status=status)
    coordinators = _Coordinators(_coordinator(saga))
    advancer, service = _advancer(saga, coordinators, campaigns=[finished])

    await advancer.advance(finished)

    service.start_workflow.assert_not_awaited()
    assert coordinators.row.advanced_from is None


async def test_the_last_workflow_has_nothing_to_start() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga, DELIVERY)
    advancer, service = _advancer(saga, _Coordinators(_coordinator(saga)), campaigns=[finished])

    await advancer.advance(finished)

    service.start_workflow.assert_not_awaited()


@pytest.mark.parametrize("status_type", ["completed", "canceled"])
async def test_a_closed_ticket_does_not_advance_and_keeps_its_last_request(
    status_type: str,
) -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    original = _coordinator(saga)
    coordinators = _Coordinators(original)
    advancer, service = _advancer(saga, coordinators, campaigns=[finished], status_type=status_type)

    await advancer.advance(finished)

    service.start_workflow.assert_not_awaited()
    assert coordinators.row.requested_at == original.requested_at
    assert coordinators.row.requested_workflow_id == original.requested_workflow_id


async def test_a_ticket_without_a_coordinator_stays_ready() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    advancer, service = _advancer(saga, _Coordinators(None), campaigns=[finished])

    await advancer.advance(finished)

    service.start_workflow.assert_not_awaited()


async def test_a_closed_coordinator_does_not_advance() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    closed = _coordinator(saga, status=TicketCoordinatorStatus.CLOSED)
    advancer, service = _advancer(saga, _Coordinators(closed), campaigns=[finished])

    await advancer.advance(finished)

    service.start_workflow.assert_not_awaited()


async def test_a_run_someone_else_started_wins() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    active = _campaign(saga, DELIVERY, status=WorkflowCampaignStatus.RUNNING)
    advancer, service = _advancer(
        saga, _Coordinators(_coordinator(saga)), campaigns=[finished, active]
    )

    await advancer.advance(finished)

    service.start_workflow.assert_not_awaited()


async def test_a_person_who_asked_for_something_after_the_run_began_wins() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    asked = _coordinator(saga, requested_at=NOW)
    advancer, service = _advancer(saga, _Coordinators(asked), campaigns=[finished])

    await advancer.advance(finished)

    service.start_workflow.assert_not_awaited()


async def test_an_active_dispatched_run_is_never_doubled() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    original = _coordinator(saga)
    coordinators = _Coordinators(original)
    dispatched = SimpleNamespace(tracker_id=ISSUE, status=RunStatus.RUNNING)
    advancer, service = _advancer(saga, coordinators, campaigns=[finished], runs=[dispatched])

    await advancer.advance(finished)

    service.start_workflow.assert_not_awaited()
    assert coordinators.row.requested_at == original.requested_at


async def test_a_failed_start_is_kept_with_its_reason_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    coordinators = _Coordinators(_coordinator(saga))
    advancer, _ = _advancer(
        saga,
        coordinators,
        campaigns=[finished],
        start_error=RuntimeError("the session would not start"),
    )

    await advancer.advance(finished)

    assert coordinators.row.start_error == (
        f"{DELIVERY} could not start: the session would not start"
    )
    assert coordinators.row.requested_workflow_id == str(_workflow(DELIVERY).id)
    assert coordinators.row.advanced_from == str(finished.id)
    assert any(record.levelname == "ERROR" for record in caplog.records)


async def test_a_tracker_that_cannot_be_reached_blocks_rather_than_reads_as_ready() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    coordinators = _Coordinators(_coordinator(saga))
    advancer, service = _advancer(saga, coordinators, campaigns=[finished])
    advancer._tracker_factory.for_owner = AsyncMock(return_value=[])

    await advancer.advance(finished)

    service.start_workflow.assert_not_awaited()
    assert "tracker source is unavailable" in coordinators.row.start_error


async def test_an_owner_with_no_forge_connection_blocks_with_the_reason() -> None:
    saga = _saga(PLANNING, DELIVERY)
    finished = _campaign(saga)
    coordinators = _Coordinators(_coordinator(saga))
    advancer, _ = _advancer(saga, coordinators, campaigns=[finished])
    advancer._volundr_factory.primary_for_owner = AsyncMock(return_value=None)

    await advancer.advance(finished)

    assert "no Forge connection" in coordinators.row.start_error

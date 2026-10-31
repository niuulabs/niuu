"""Start a ticket's next workflow when its current one finishes.

This runs from where Ting learns a run completed, never from a read. A finished
run may advance its ticket once: the claim on the ticket's coordinator is atomic
and durable, so a redelivered event or a restart cannot start a second run. A
restart that lost the moment between the completion and the claim is picked up
by ``advance_finished``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from niuu.domain.models import Principal
from ting.domain.models import RunStatus, Saga, TrackerIssue, WorkflowCampaign
from ting.domain.models import WorkflowCampaignStatus as CampaignStatus
from ting.domain.services.ticket_coordinator import TicketCoordinatorService, TicketWorkflow
from ting.domain.services.tracker_selection import tracker_for_saga
from ting.domain.ticket_coordinator import (
    TicketCoordinator,
    TicketCoordinatorStatus,
    ticket_of,
)
from ting.domain.workflow_board import BoardWorkflow, project_board, ticket_is_closed
from ting.ports.saga_repository import SagaRepository
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository
from ting.ports.tracker import TrackerFactory, TrackerPort
from ting.ports.volundr import VolundrFactory, VolundrPort
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository

logger = logging.getLogger(__name__)

CoordinatorServiceFactory = Callable[[VolundrPort, Principal], TicketCoordinatorService]

_ACTIVE_CAMPAIGN_STATUSES = frozenset(
    {CampaignStatus.PENDING, CampaignStatus.RUNNING, CampaignStatus.BLOCKED}
)
_ACTIVE_RUN_STATUSES = frozenset({RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.REVIEW})


class TicketAdvancer:
    def __init__(
        self,
        *,
        saga_repo: SagaRepository,
        campaign_repo: WorkflowCampaignRepository,
        coordinator_repo: TicketCoordinatorRepository,
        tracker_factory: TrackerFactory,
        volundr_factory: VolundrFactory,
        coordinators: CoordinatorServiceFactory,
    ) -> None:
        self._saga_repo = saga_repo
        self._campaign_repo = campaign_repo
        self._coordinator_repo = coordinator_repo
        self._tracker_factory = tracker_factory
        self._volundr_factory = volundr_factory
        self._coordinators = coordinators

    async def advance(self, finished: WorkflowCampaign) -> None:
        """Start what follows a finished run. A failure lands on the ticket, never on the run."""
        try:
            await self._advance(finished)
        except Exception:
            logger.error(
                "Could not work out what follows finished campaign %s",
                finished.slug,
                exc_info=True,
            )

    async def advance_finished(self, owner_id: str) -> None:
        """Catch up on runs that finished while nothing here was listening."""
        campaigns = await self._campaign_repo.list_campaigns(owner_id=owner_id)
        latest: dict[tuple[str, str], WorkflowCampaign] = {}
        for campaign in sorted(campaigns, key=lambda item: item.created_at):
            ticket = ticket_of(campaign.metadata)
            if ticket is not None:
                latest[ticket] = campaign
        for campaign in latest.values():
            if campaign.status == CampaignStatus.COMPLETED:
                await self.advance(campaign)

    async def _advance(self, finished: WorkflowCampaign) -> None:
        ticket = ticket_of(finished.metadata)
        if ticket is None or finished.status != CampaignStatus.COMPLETED:
            return
        saga_id, issue_id = UUID(ticket[0]), ticket[1]
        coordinator = await self._coordinator_repo.get(saga_id, issue_id)
        if coordinator is None or coordinator.status != TicketCoordinatorStatus.ACTIVE:
            return
        if coordinator.advanced_from == str(finished.id):
            return
        saga = await self._saga_repo.get_saga(saga_id, owner_id=finished.owner_id)
        following = _following_workflow(saga, finished) if saga is not None else None
        if saga is None or following is None:
            return
        if await self._person_moved_on(finished, coordinator, ticket):
            return

        claimed = await self._coordinator_repo.claim_advance(
            saga_id,
            issue_id,
            finished_campaign_id=str(finished.id),
            workflow_id=following.id,
            requested_at=datetime.now(UTC),
        )
        if claimed is None:
            return
        try:
            await self._start(saga, issue_id, following, coordinator, claimed)
        except Exception as exc:
            logger.error(
                "Could not start %s for ticket %s after campaign %s finished",
                following.name,
                issue_id,
                finished.slug,
                exc_info=True,
            )
            await self._coordinator_repo.save(
                replace(
                    await self._current(claimed),
                    start_error=f"{following.name} could not start: {exc}",
                )
            )

    async def _current(self, claimed: TicketCoordinator) -> TicketCoordinator:
        current = await self._coordinator_repo.get(claimed.saga_id, claimed.issue_id)
        return current if current is not None else claimed

    async def _person_moved_on(
        self,
        finished: WorkflowCampaign,
        coordinator: TicketCoordinator,
        ticket: tuple[str, str],
    ) -> bool:
        """Whether someone started something for this ticket after the finished run began."""
        if coordinator.requested_at is not None and coordinator.requested_at > finished.created_at:
            return True
        campaigns = await self._campaign_repo.list_campaigns(owner_id=finished.owner_id)
        return any(
            ticket_of(campaign.metadata) == ticket
            and campaign.id != finished.id
            and (
                campaign.status in _ACTIVE_CAMPAIGN_STATUSES
                or campaign.created_at > finished.created_at
            )
            for campaign in campaigns
        )

    async def _start(
        self,
        saga: Saga,
        issue_id: str,
        following: BoardWorkflow,
        coordinator: TicketCoordinator,
        claimed: TicketCoordinator,
    ) -> None:
        tracker = tracker_for_saga(
            await self._tracker_factory.for_owner(coordinator.owner_id), saga
        )
        if tracker is None:
            raise LookupError("the project's tracker source is unavailable")
        issue = await _tracker_issue(tracker, saga, issue_id)
        if ticket_is_closed(issue.status_type) or await _has_active_run(tracker, saga, issue_id):
            await self._coordinator_repo.save(
                replace(
                    claimed,
                    requested_workflow_id=coordinator.requested_workflow_id,
                    requested_at=coordinator.requested_at,
                )
            )
            return
        adapter = await self._adapter(coordinator)
        service = self._coordinators(adapter, _owner_principal(coordinator))
        await service.start_workflow(
            saga, issue, TicketWorkflow(id=following.id, name=following.name)
        )

    async def _adapter(self, coordinator: TicketCoordinator) -> VolundrPort:
        """Act with the owner's own connection, as the dispatcher does in the background."""
        adapter = (
            await self._volundr_factory.for_connection(
                coordinator.owner_id, coordinator.connection_id
            )
            if coordinator.connection_id
            else await self._volundr_factory.primary_for_owner(coordinator.owner_id)
        )
        if adapter is None:
            raise LookupError("the project owner has no Forge connection")
        return adapter


def _following_workflow(saga: Saga, finished: WorkflowCampaign) -> BoardWorkflow | None:
    """The next workflow on the board after a finished one, if there is one."""
    project = project_board(saga)
    if project is None or project.workflow(str(finished.workflow_id)) is None:
        return None
    ordered = [item.id for item in project.workflows]
    position = ordered.index(str(finished.workflow_id))
    following = project.workflows[position + 1 :]
    return following[0] if following else None


async def _tracker_issue(tracker: TrackerPort, saga: Saga, issue_id: str) -> TrackerIssue:
    issues = await tracker.list_issues(saga.tracker_id)
    issue = next((item for item in issues if item.id == issue_id), None)
    if issue is None:
        raise LookupError(f"ticket {issue_id} is not in the project's tracker")
    return issue


async def _has_active_run(tracker: TrackerPort, saga: Saga, issue_id: str) -> bool:
    runs = await tracker.get_run_progress_for_saga(saga.tracker_id)
    return any(run.tracker_id == issue_id and run.status in _ACTIVE_RUN_STATUSES for run in runs)


def _owner_principal(coordinator: TicketCoordinator) -> Principal:
    return Principal(
        user_id=coordinator.owner_id,
        email="",
        tenant_id=coordinator.tenant_id,
        roles=[],
    )

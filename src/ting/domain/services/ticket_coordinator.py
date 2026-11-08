"""Start a ticket's workflow runs, and close the ticket's record when the ticket closes.

A ticket used to work through a persistent coordinator session, a Ravn resident that
started its workflows over A2A. That resident is gone: Ting starts a ticket's workflow
itself. ``TicketCoordinatorService.start_workflow`` creates the run's campaign, tied to
the ticket, and starts it on the step engine. The ticket keeps one ``TicketCoordinator``
row, now only the ticket's record: which workflow Ting was asked to start and when, a
start that failed, and the finished run that already advanced the ticket. It names no
session, because nothing coordinates a ticket by conversation any more.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from niuu.domain.models import Principal
from ting.domain.models import (
    Saga,
    TrackerIssue,
    WorkflowCampaign,
    WorkflowCampaignStatus,
)
from ting.domain.services.workflow_runs import WorkflowRuns
from ting.domain.ticket_coordinator import (
    TICKET_METADATA_KEY,
    TicketCoordinator,
    TicketCoordinatorStatus,
    ticket_metadata,
)
from ting.domain.utils import _slugify
from ting.domain.workflow_snapshot import workflow_uses_isolated_branch
from ting.domain.workflow_steps import RunStateError
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository

TICKET_SURFACE = "ting.ticket"
#: Prefix of the branch an isolated-branch workflow's sessions work on.
_ISOLATED_BRANCH_PREFIX = "workflow"
_CAMPAIGN_SLUG_MAX = 60
_BRANCH_UNSAFE = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class TicketWorkflow:
    """The workflow a ticket should run next."""

    id: str
    name: str


def ticket_branch(saga: Saga, issue: TrackerIssue) -> str:
    """A ticket's own branch, so no ticket can overwrite another's commits.

    It sits beside the project's branch, not under it: git cannot hold a ref
    ``feat/x/niu-1`` while ``feat/x`` exists. Forge creates it from the base
    branch the first time a session asks for it.
    """
    identifier = _BRANCH_UNSAFE.sub("-", issue.identifier.lower()).strip("-.")
    return f"{saga.feature_branch}-{identifier}"


def ticket_brief(issue: TrackerIssue, *, repo: str, workflow: TicketWorkflow) -> str:
    """What a ticket's run is about, as its first step reads it."""
    repository = f"Repository: {repo}\n" if repo else ""
    return (
        f"Ticket {issue.identifier}: {issue.title}\n"
        f"URL: {issue.url}\n"
        f"{repository}\n"
        f"{issue.description}\n\n"
        f"Work this ticket through the workflow {workflow.name!r}."
    )


def _pinned_workflow(saga: Saga, workflow: TicketWorkflow) -> tuple[UUID, str, dict]:
    """The workflow's id, version and snapshot as the project pinned them."""
    for pin in saga.board_workflows:
        if str(pin.workflow_id) == workflow.id:
            return pin.workflow_id, pin.workflow_version, pin.workflow_snapshot
    if saga.workflow_id is not None and str(saga.workflow_id) == workflow.id:
        return saga.workflow_id, saga.workflow_version or "", saga.workflow_snapshot or {}
    raise RunStateError(
        f"{workflow.name or workflow.id} is not on this project's board, so a ticket cannot "
        "run it; add it to the board first"
    )


class TicketCoordinatorService:
    def __init__(
        self,
        *,
        repo: TicketCoordinatorRepository,
        campaigns: WorkflowCampaignRepository,
        runs: WorkflowRuns,
        principal: Principal,
    ) -> None:
        self._repo = repo
        self._campaigns = campaigns
        self._runs = runs
        self._principal = principal

    async def start_workflow(
        self,
        saga: Saga,
        issue: TrackerIssue,
        workflow: TicketWorkflow,
        *,
        connection_id: str | None,
    ) -> TicketCoordinator:
        """Start a workflow run for the ticket, on the project's pinned version of it.

        The run's campaign carries the ticket, so the board finds it. ``connection_id``
        is the Forge connection the run's sessions are created on. A start that fails
        is recorded on the ticket and raised: the ticket can be started again.
        """
        workflow_id, version, snapshot = _pinned_workflow(saga, workflow)
        isolated = workflow_uses_isolated_branch(snapshot)
        record = await self._request(saga, issue, workflow, connection_id)
        campaign_id = uuid4()
        name = str(snapshot.get("name") or workflow.name)
        slug = _slugify(f"{issue.identifier}-{name}")[:_CAMPAIGN_SLUG_MAX].strip("-")
        slug = f"{slug}-{campaign_id.hex[:12]}"
        repo = saga.repos[0] if saga.repos else ""
        branch, base_branch = ticket_branch(saga, issue), saga.base_branch
        if isolated:
            branch, base_branch = f"{_ISOLATED_BRANCH_PREFIX}/{slug}", base_branch or branch
        brief = ticket_brief(issue, repo=repo, workflow=workflow)
        ticket = ticket_metadata(
            saga_id=saga.id, issue_id=issue.id, identifier=issue.identifier, title=issue.title
        )
        now = datetime.now(UTC)
        campaign = WorkflowCampaign(
            id=campaign_id,
            tenant_id=self._principal.tenant_id,
            slug=slug,
            name=f"{issue.identifier} {name}",
            owner_id=self._principal.user_id,
            workflow_id=workflow_id,
            workflow_version=version,
            workflow_name=name,
            workflow_snapshot=snapshot,
            session_id="",
            session_name="",
            status=WorkflowCampaignStatus.PENDING,
            active_stage_id=None,
            stage_state=[],
            metadata={
                "surface": TICKET_SURFACE,
                "prompt": brief,
                "launch_slug": slug,
                TICKET_METADATA_KEY: ticket,
                **({"repo": repo, "branch": branch, "base_branch": base_branch} if repo else {}),
                **({"actor_id": self._principal.actor} if self._principal.actor else {}),
            },
            created_at=now,
            updated_at=now,
            last_activity_at=now,
            connection_id=connection_id,
        )
        active = await self._campaigns.save_ticket_campaign(
            campaign, saga_id=saga.id, issue_id=issue.id
        )
        if active is not None:
            raise RunStateError(f"{issue.identifier} already has a run in progress ({active})")
        payload = {
            "prompt": brief,
            "initiative_context": brief,
            TICKET_METADATA_KEY: ticket,
            **({"repo": repo, "branch": branch, "base_branch": base_branch} if repo else {}),
        }
        try:
            await self._runs.launch(campaign, payload)
        except Exception as exc:
            await self._fail_start(campaign, record, workflow, exc)
            raise
        return record

    async def close(self, coordinator: TicketCoordinator) -> None:
        """End a ticket's record with its ticket."""
        await self._repo.close(coordinator.saga_id, coordinator.issue_id)

    async def _request(
        self,
        saga: Saga,
        issue: TrackerIssue,
        workflow: TicketWorkflow,
        connection_id: str | None,
    ) -> TicketCoordinator:
        """Record that Ting was asked to start this workflow, before its run exists."""
        current = await self._repo.get(saga.id, issue.id)
        now = datetime.now(UTC)
        if current is None or current.status == TicketCoordinatorStatus.CLOSED:
            record = TicketCoordinator(
                saga_id=saga.id,
                issue_id=issue.id,
                owner_id=self._principal.user_id,
                tenant_id=self._principal.tenant_id,
                session_id="",
                connection_id=connection_id,
                status=TicketCoordinatorStatus.ACTIVE,
                created_at=current.created_at if current is not None else now,
                advanced_from=current.advanced_from if current is not None else None,
            )
        else:
            record = current
        return await self._repo.save(
            replace(
                record,
                requested_workflow_id=workflow.id,
                requested_at=now,
                pending_brief=None,
                start_error=None,
            )
        )

    async def _fail_start(
        self,
        campaign: WorkflowCampaign,
        record: TicketCoordinator,
        workflow: TicketWorkflow,
        exc: Exception,
    ) -> None:
        """A run that could not start fails its campaign and says why on the ticket."""
        now = datetime.now(UTC)
        await self._campaigns.save_campaign(
            replace(
                campaign,
                status=WorkflowCampaignStatus.FAILED,
                metadata={**campaign.metadata, "failure_error": str(exc)},
                updated_at=now,
            )
        )
        await self._repo.save(
            replace(record, start_error=f"{workflow.name or workflow.id} could not start: {exc}")
        )

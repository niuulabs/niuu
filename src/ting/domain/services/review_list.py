"""Everything a person is reviewing, or has reviewed, across their projects.

A list spans every project, so it reads only what campaigns already record.
Asking each session would mean one Forge call per run, and the campaign's own
metadata already says which gate is waiting.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from niuu.domain.models import actor_name
from ting.domain.models import Saga, WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.ticket_coordinator import PENDING_GATES_KEY, TICKET_METADATA_KEY, ticket_of
from ting.domain.workflow_steps import RunState

PLAN_SURFACE = "ting.plan"
PLANNING_WORKFLOW_NAME = "Saga Planning"
# What a committed project plan records about the project and tickets it became.
PLAN_SAGA_KEY = "saga_id"
PLAN_TICKETS_KEY = "tickets"
RUN_PROMPT_KEY = "prompt"
RUN_ACTOR_KEY = "actor_id"
_SERVICE_ACCOUNT_ACTOR = "system:serviceaccount:"


class ReviewState(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class ReviewKind(StrEnum):
    TICKET = "ticket"
    PROJECT_PLAN = "projectPlan"
    RUN = "run"


@dataclass(frozen=True)
class ReviewItem:
    task_id: str
    kind: ReviewKind
    title: str
    workflow_name: str
    state: ReviewState
    gate_label: str
    updated_at: datetime
    project_id: str | None = None
    project_name: str | None = None
    issue_id: str | None = None
    identifier: str | None = None
    started_by: str | None = None


@dataclass(frozen=True)
class ReviewTicket:
    """The ticket a run was started for, so its review page can name and reach it."""

    project_id: str
    issue_id: str


@dataclass(frozen=True)
class ReviewSession:
    """A Forge session a run worked in: one per member of a step-engine run."""

    session_id: str
    #: The member (persona) whose steps the session ran; empty for a run's own session.
    member: str = ""
    #: The Forge connection the session is on: the run's own.
    connection_id: str | None = None


@dataclass(frozen=True)
class ReviewRun:
    """A run, as its review page introduces it."""

    slug: str
    title: str
    workflow_name: str
    prompt: str
    session_id: str
    repo: str
    branch: str
    started_by: str | None = None
    ticket: ReviewTicket | None = None
    #: Every session the run worked in, in the order the run first used them.
    sessions: tuple[ReviewSession, ...] = ()


def is_project_plan(campaign: WorkflowCampaign) -> bool:
    return (
        campaign.metadata.get("surface") == PLAN_SURFACE
        or campaign.workflow_name == PLANNING_WORKFLOW_NAME
    )


def _waiting_gate(metadata: Mapping[str, Any]) -> str | None:
    """The label of the gate a campaign records as waiting, or None if there is none."""
    gates = metadata.get(PENDING_GATES_KEY)
    if not isinstance(gates, list) or not gates:
        return None
    first = gates[0]
    if not isinstance(first, Mapping):
        return None
    return str(first.get("label") or first.get("node_id") or "")


def _run_title(campaign: WorkflowCampaign) -> str:
    """What the run was asked to do; its name is usually just the workflow's."""
    lines = str(campaign.metadata.get(RUN_PROMPT_KEY) or "").strip().splitlines()
    return lines[0] if lines else campaign.name


def _started_by(metadata: Mapping[str, Any]) -> str | None:
    """The resident that started a run. A person's own run needs no such note."""
    actor = str(metadata.get(RUN_ACTOR_KEY) or "")
    return actor_name(actor) if actor.startswith(_SERVICE_ACCOUNT_ACTOR) else None


def run_sessions(campaign: WorkflowCampaign, state: RunState | None) -> tuple[ReviewSession, ...]:
    """The run's own session when no step ran in it, then each member's session from the ledger."""
    members: dict[str, ReviewSession] = {}
    steps = sorted(state.steps, key=lambda step: step.created_at) if state else ()
    for step in steps:
        if step.session_id and step.session_id not in members:
            members[step.session_id] = ReviewSession(
                session_id=step.session_id,
                member=step.member or "",
                connection_id=campaign.connection_id,
            )
    if not campaign.session_id or campaign.session_id in members:
        return tuple(members.values())
    own = ReviewSession(session_id=campaign.session_id, connection_id=campaign.connection_id)
    return (own, *members.values())


def review_run(campaign: WorkflowCampaign, state: RunState | None = None) -> ReviewRun:
    return ReviewRun(
        slug=campaign.slug,
        title=_run_title(campaign),
        workflow_name=campaign.workflow_name,
        prompt=str(campaign.metadata.get(RUN_PROMPT_KEY) or ""),
        session_id=campaign.session_id or "",
        repo=str(campaign.metadata.get("repo") or ""),
        branch=str(campaign.metadata.get("branch") or ""),
        started_by=_started_by(campaign.metadata),
        ticket=_ticket(campaign.metadata),
        sessions=run_sessions(campaign, state),
    )


def _ticket(metadata: Mapping[str, Any]) -> ReviewTicket | None:
    ticket = ticket_of(dict(metadata))
    return ReviewTicket(project_id=ticket[0], issue_id=ticket[1]) if ticket else None


def _state(campaign: WorkflowCampaign, gate_label: str | None) -> ReviewState:
    if campaign.status == WorkflowCampaignStatus.COMPLETED:
        return ReviewState.DONE
    if campaign.status == WorkflowCampaignStatus.FAILED:
        return ReviewState.FAILED
    if campaign.status == WorkflowCampaignStatus.CANCELED:
        return ReviewState.CANCELED
    # A gate is only waiting while it holds the run. Recording one blocks the campaign,
    # so a running campaign that still names a gate has already been answered.
    if campaign.status == WorkflowCampaignStatus.BLOCKED and gate_label is not None:
        return ReviewState.WAITING
    return ReviewState.RUNNING


def _item(campaign: WorkflowCampaign, saga_names: Mapping[str, str]) -> ReviewItem | None:
    ticket = ticket_of(campaign.metadata)
    gate_label = _waiting_gate(campaign.metadata)
    state = _state(campaign, gate_label)
    common = {
        "task_id": campaign.slug,
        "title": campaign.name,
        "workflow_name": campaign.workflow_name,
        "state": state,
        # A stopped run's record can still name a gate nobody is waiting on any more.
        "gate_label": (gate_label or "") if state == ReviewState.WAITING else "",
        "updated_at": campaign.updated_at,
    }
    if ticket is None and not is_project_plan(campaign):
        # A run with no ticket has no history to keep here: it is a review while it waits.
        if state != ReviewState.WAITING:
            return None
        return ReviewItem(
            kind=ReviewKind.RUN,
            started_by=_started_by(campaign.metadata),
            **{**common, "title": _run_title(campaign)},
        )
    if ticket is None:
        project_id = campaign.metadata.get(PLAN_SAGA_KEY)
        return ReviewItem(
            kind=ReviewKind.PROJECT_PLAN,
            project_id=str(project_id) if project_id else None,
            project_name=saga_names.get(str(project_id)),
            **common,
        )
    project_id, issue_id = ticket
    # A ticket's review opens through its project; a deleted project leaves nothing to open.
    if project_id not in saga_names:
        return None
    recorded = campaign.metadata[TICKET_METADATA_KEY]
    identifier = recorded.get("identifier")
    # Runs started before tickets recorded their title show the run's name.
    return ReviewItem(
        kind=ReviewKind.TICKET,
        project_id=project_id,
        project_name=saga_names[project_id],
        issue_id=issue_id,
        identifier=str(identifier) if identifier else issue_id,
        **{**common, "title": str(recorded.get("title") or campaign.name)},
    )


def review_list(
    campaigns: Iterable[WorkflowCampaign],
    sagas: Iterable[Saga],
) -> tuple[ReviewItem, ...]:
    """Reviewable runs, newest first; the page groups them by state."""
    saga_names = {str(saga.id): saga.name for saga in sagas}
    items = (_item(campaign, saga_names) for campaign in campaigns)
    return tuple(
        sorted(
            (item for item in items if item is not None),
            key=lambda item: item.updated_at,
            reverse=True,
        )
    )

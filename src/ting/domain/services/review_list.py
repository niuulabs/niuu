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

from ting.domain.models import Saga, WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.ticket_coordinator import PENDING_GATES_KEY, TICKET_METADATA_KEY, ticket_of

PLAN_SURFACE = "ting.plan"
PLANNING_WORKFLOW_NAME = "Saga Planning"
# What a committed project plan records about the project and tickets it became.
PLAN_SAGA_KEY = "saga_id"
PLAN_TICKETS_KEY = "tickets"


class ReviewState(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ReviewKind(StrEnum):
    TICKET = "ticket"
    PROJECT_PLAN = "projectPlan"


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


def _state(campaign: WorkflowCampaign, gate_label: str | None) -> ReviewState:
    if campaign.status == WorkflowCampaignStatus.COMPLETED:
        return ReviewState.DONE
    if campaign.status == WorkflowCampaignStatus.FAILED:
        return ReviewState.FAILED
    return ReviewState.WAITING if gate_label is not None else ReviewState.RUNNING


def _item(campaign: WorkflowCampaign, saga_names: Mapping[str, str]) -> ReviewItem | None:
    ticket = ticket_of(campaign.metadata)
    if ticket is None and not is_project_plan(campaign):
        return None
    gate_label = _waiting_gate(campaign.metadata)
    common = {
        "task_id": campaign.slug,
        "title": campaign.name,
        "workflow_name": campaign.workflow_name,
        "state": _state(campaign, gate_label),
        "gate_label": gate_label or "",
        "updated_at": campaign.updated_at,
    }
    if ticket is None:
        project_id = campaign.metadata.get(PLAN_SAGA_KEY)
        return ReviewItem(
            kind=ReviewKind.PROJECT_PLAN,
            project_id=str(project_id) if project_id else None,
            project_name=saga_names.get(str(project_id)),
            **common,
        )
    project_id, issue_id = ticket
    identifier = campaign.metadata[TICKET_METADATA_KEY].get("identifier")
    return ReviewItem(
        kind=ReviewKind.TICKET,
        project_id=project_id,
        project_name=saga_names.get(project_id),
        issue_id=issue_id,
        identifier=str(identifier) if identifier else issue_id,
        **common,
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

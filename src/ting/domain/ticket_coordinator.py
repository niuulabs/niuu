"""The one persistent coordinator session a tracker ticket has while it is open."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class TicketCoordinatorStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


#: Directed message metadata that puts a start brief ahead of waiting notices.
BRIEF_MESSAGE_METADATA: dict[str, object] = {"queue_first": True}
#: Campaign metadata key that ties a workflow run to its ticket.
TICKET_METADATA_KEY = "ticket"
#: Campaign metadata key recording that the coordinator sits in the run's room.
COORDINATOR_JOINED_KEY = "coordinator_joined"
#: Campaign metadata key holding the gates a run was last known to wait on.
PENDING_GATES_KEY = "pending_workflow_gates"
#: Campaign metadata key holding the pull request Forge reports for the run.
PULL_REQUEST_KEY = "pull_request"


@dataclass(frozen=True)
class TicketCoordinator:
    saga_id: UUID
    issue_id: str
    owner_id: str
    tenant_id: str
    session_id: str
    connection_id: str | None
    status: TicketCoordinatorStatus
    created_at: datetime
    closed_at: datetime | None = None
    requested_workflow_id: str | None = None
    requested_at: datetime | None = None
    pending_brief: str | None = None
    start_error: str | None = None
    advanced_from: str | None = None


def ticket_metadata(*, saga_id: UUID, issue_id: str, identifier: str, title: str) -> dict[str, str]:
    """The link a coordinator's A2A launches carry back to their ticket."""
    return {"sagaId": str(saga_id), "issueId": issue_id, "identifier": identifier, "title": title}


def ticket_of(metadata: dict[str, Any]) -> tuple[str, str] | None:
    """Return (saga id, issue id) when campaign metadata names a ticket."""
    raw = metadata.get(TICKET_METADATA_KEY)
    if not isinstance(raw, dict):
        return None
    saga_id = str(raw.get("sagaId") or "")
    issue_id = str(raw.get("issueId") or "")
    return (saga_id, issue_id) if saga_id and issue_id else None

"""The one record a tracker ticket keeps while it is open: what Ting was asked to start for it.

It used to name a persistent coordinator session. Nothing coordinates a ticket by conversation
any more, so ``session_id`` is empty; the row remains for the requests, start errors and
advances the ticket needs to remember.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class TicketCoordinatorStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


#: Campaign metadata key that ties a workflow run to its ticket.
TICKET_METADATA_KEY = "ticket"
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


def ticket_label(metadata: dict[str, Any]) -> str | None:
    """Return "NIU-1 · Title" when campaign metadata names a ticket by identifier and title."""
    raw = metadata.get(TICKET_METADATA_KEY)
    if not isinstance(raw, dict):
        return None
    identifier = str(raw.get("identifier") or "").strip()
    title = str(raw.get("title") or "").strip()
    return f"{identifier} · {title}" if identifier and title else None


def ticket_of(metadata: dict[str, Any]) -> tuple[str, str] | None:
    """Return (saga id, issue id) when campaign metadata names a ticket."""
    raw = metadata.get(TICKET_METADATA_KEY)
    if not isinstance(raw, dict):
        return None
    saga_id = str(raw.get("sagaId") or "")
    issue_id = str(raw.get("issueId") or "")
    return (saga_id, issue_id) if saga_id and issue_id else None

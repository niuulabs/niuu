"""Durable receiver-side A2A launch intent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID


@dataclass(frozen=True)
class A2ALaunchReservation:
    id: UUID
    owner_id: str
    tenant_id: str
    message_id: str
    request_digest: str
    workflow_id: UUID
    task_id: str
    campaign_id: UUID
    state: str = "reserved"
    session_id: str = ""
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime = datetime.min.replace(tzinfo=UTC)
    updated_at: datetime = datetime.min.replace(tzinfo=UTC)

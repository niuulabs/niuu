"""REST adapter for the Sleipnir audit log (read-only query API).

Exposes a single endpoint::

    GET /audit/events
        ?event_type=ravn.*
        &from=2026-04-01T00:00:00Z
        &to=2026-04-02T00:00:00Z
        &correlation_id=abc-123
        &source=ravn:agent
        &limit=100

Used by the Hliðskjálf timeline view and incident investigation tooling.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from niuu.http_compat import LegacyRouteNotice, warn_on_legacy_route
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.audit import AuditQuery, AuditRepository

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 1000


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class AuditEventResponse(BaseModel):
    """A single audit log entry."""

    event_id: str = Field(description="Unique event identifier")
    event_type: str = Field(description="Hierarchical dot-separated event type")
    source: str = Field(description="Publisher identity")
    service: str = Field(description="Service prefix derived from the source identifier")
    summary: str = Field(description="Human-readable one-liner")
    urgency: float = Field(description="Priority hint 0.0–1.0")
    domain: str = Field(description="High-level domain tag")
    correlation_id: str | None = Field(default=None, description="Correlation group")
    causation_id: str | None = Field(default=None, description="Causing event ID")
    tenant_id: str | None = Field(default=None, description="Tenant scope")
    payload: dict = Field(description="Event-specific data")
    timestamp: str = Field(description="ISO 8601 event timestamp")
    ttl: int | None = Field(default=None, description="Seconds until expiry")

    @classmethod
    def from_event(cls, event: SleipnirEvent) -> AuditEventResponse:
        return cls(
            event_id=event.event_id,
            event_type=event.event_type,
            source=event.source,
            service=event.source.split(":", 1)[0],
            summary=event.summary,
            urgency=event.urgency,
            domain=event.domain,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            tenant_id=event.tenant_id,
            payload=event.payload,
            timestamp=event.timestamp.isoformat(),
            ttl=event.ttl,
        )


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def _build_audit_router(
    repository: AuditRepository,
    *,
    prefix: str,
    deprecated: bool = False,
    canonical_prefix: str | None = None,
) -> APIRouter:
    """Create the FastAPI router for audit log queries."""
    router = APIRouter(prefix=prefix, tags=["Audit"])

    @router.get("/events", response_model=list[AuditEventResponse])
    async def query_audit_events(
        request: Request,
        response: Response,
        event_type: str | None = Query(
            default=None,
            description=(
                "Glob pattern for event type (e.g. ``ravn.*``, ``tyr.task.*``). "
                "Omit to match all event types."
            ),
            examples=["ravn.*"],
        ),
        from_: datetime | None = Query(
            default=None,
            alias="from",
            description="Return events at or after this ISO 8601 timestamp.",
            examples=["2026-04-01T00:00:00Z"],
        ),
        to: datetime | None = Query(
            default=None,
            description="Return events at or before this ISO 8601 timestamp.",
            examples=["2026-04-02T00:00:00Z"],
        ),
        correlation_id: str | None = Query(
            default=None,
            description="Filter to events with this exact correlation ID.",
        ),
        source: str | None = Query(
            default=None,
            description="Filter to events from this exact source (e.g. ``ravn:agent``).",
        ),
        service: str | None = Query(
            default=None,
            description="Filter to events from a service prefix (e.g. ``ravn``, ``tyr``).",
        ),
        limit: int = Query(
            default=_DEFAULT_LIMIT,
            ge=1,
            le=_MAX_LIMIT,
            description=f"Maximum events to return (1–{_MAX_LIMIT}).",
        ),
    ) -> list[AuditEventResponse]:
        """Query the audit log.

        Returns events in reverse-chronological order (newest first).
        Supports glob-style ``event_type`` patterns such as ``ravn.*`` or
        ``tyr.task.*``.
        """
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/events",
                    canonical_path=f"{canonical_prefix}/events",
                ),
            )
        q = AuditQuery(
            event_type_pattern=event_type,
            from_ts=from_,
            to_ts=to,
            correlation_id=correlation_id,
            source=source,
            limit=_MAX_LIMIT if service else limit,
        )
        events = await repository.query(q)
        if service:
            events = [event for event in events if event.source.split(":", 1)[0] == service]
        events = events[:limit]
        return [AuditEventResponse.from_event(e) for e in events]

    return router


def create_audit_router(repository: AuditRepository) -> APIRouter:
    """Create the legacy audit router."""
    return _build_audit_router(
        repository,
        prefix="/audit",
        deprecated=True,
        canonical_prefix="/api/v1/audit",
    )


def create_canonical_audit_router(repository: AuditRepository) -> APIRouter:
    """Create the canonical audit router."""
    return _build_audit_router(repository, prefix="/api/v1/audit")

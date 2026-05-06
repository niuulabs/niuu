"""Compatibility audit endpoints for Tyr's web-next HTTP adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from niuu.domain.models import Principal
from niuu.http_compat import LegacyRouteNotice, warn_on_legacy_route
from tyr.adapters.inbound.auth import extract_principal
from tyr.api.dispatcher import resolve_event_bus
from tyr.ports.event_bus import EventBusPort, TyrEvent


class AuditEntryResponse(BaseModel):
    id: str
    kind: str
    summary: str
    actor: str
    payload: dict | None = None
    created_at: datetime


def _parse_optional_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalised = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalised)


def _matches_window(
    event: TyrEvent,
    *,
    kinds: set[str] | None,
    actor: str | None,
    since: datetime | None,
    until: datetime | None,
) -> bool:
    if kinds and event.event not in kinds:
        return False
    if actor and event.owner_id != actor:
        return False

    timestamp = event.timestamp.astimezone(UTC)
    if since and timestamp < since.astimezone(UTC):
        return False
    if until and timestamp > until.astimezone(UTC):
        return False
    return True


def create_audit_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr", tags=["Tyr Audit"])

    @router.get("/audit", response_model=list[AuditEntryResponse])
    async def list_audit_entries(
        request: Request,
        response: Response,
        kinds: str | None = Query(default=None, description="Comma-separated audit event kinds."),
        actor: str | None = Query(default=None),
        since: str | None = Query(default=None),
        until: str | None = Query(default=None),
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        _principal: Principal = Depends(extract_principal),
        event_bus: EventBusPort = Depends(resolve_event_bus),
    ) -> list[AuditEntryResponse]:
        try:
            since_dt = _parse_optional_timestamp(since)
            until_dt = _parse_optional_timestamp(until)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid timestamp filter: {exc}") from exc

        requested_kinds = (
            {part.strip() for part in kinds.split(",") if part.strip()} if kinds else None
        )
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path="/api/v1/tyr/audit",
                canonical_path="/api/v1/audit",
            ),
        )
        events = event_bus.get_log(limit)
        filtered = [
            event
            for event in events
            if _matches_window(
                event,
                kinds=requested_kinds,
                actor=actor,
                since=since_dt,
                until=until_dt,
            )
        ]
        return [
            AuditEntryResponse(
                id=event.id,
                kind=event.event,
                summary=event.event.replace(".", " "),
                actor=event.owner_id or "system",
                payload=event.data or None,
                created_at=event.timestamp,
            )
            for event in filtered
        ]

    return router

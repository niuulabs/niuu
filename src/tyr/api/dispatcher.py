"""REST API for dispatcher state — get and update per-user dispatcher config."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
from tyr.adapters.inbound.auth import extract_principal
from tyr.ports.dispatcher_repository import DispatcherRepository
from tyr.ports.event_bus import EventBusPort

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class DispatcherStateResponse(BaseModel):
    id: str
    running: bool
    threshold: float
    max_concurrent_raids: int
    auto_continue: bool
    updated_at: datetime


class PatchDispatcherRequest(BaseModel):
    running: bool | None = None
    threshold: float | None = Field(None, ge=0.0, le=1.0)
    max_concurrent_raids: int | None = Field(None, ge=1, le=20)
    auto_continue: bool | None = None


class ActivityEventResponse(BaseModel):
    id: str
    event: str
    data: dict
    owner_id: str
    timestamp: datetime


class ActivityLogResponse(BaseModel):
    events: list[ActivityEventResponse]
    total: int


def _format_activity_log_line(event_type: str, timestamp: datetime, payload: dict) -> str:
    stamp = timestamp.isoformat()
    if not payload:
        return f"[{stamp}] {event_type}"
    return f"[{stamp}] {event_type} {json.dumps(payload, sort_keys=True)}"


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def resolve_dispatcher_repo() -> DispatcherRepository:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Dispatcher repository not configured",
    )


async def resolve_event_bus() -> EventBusPort:  # pragma: no cover
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Event bus not configured",
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def create_dispatcher_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr/dispatcher", tags=["Dispatcher"])

    @router.get("", response_model=DispatcherStateResponse)
    async def get_dispatcher_state(
        principal: Principal = Depends(extract_principal),
        repo: DispatcherRepository = Depends(resolve_dispatcher_repo),
    ) -> DispatcherStateResponse:
        """Return the dispatcher state for the authenticated user, creating a default if needed."""
        state = await repo.get_or_create(principal.user_id)
        return DispatcherStateResponse(
            id=str(state.id),
            running=state.running,
            threshold=state.threshold,
            max_concurrent_raids=state.max_concurrent_raids,
            auto_continue=state.auto_continue,
            updated_at=state.updated_at,
        )

    @router.patch("", response_model=DispatcherStateResponse)
    async def patch_dispatcher_state(
        body: PatchDispatcherRequest,
        principal: Principal = Depends(extract_principal),
        repo: DispatcherRepository = Depends(resolve_dispatcher_repo),
    ) -> DispatcherStateResponse:
        """Partially update the dispatcher state for the authenticated user."""
        updates = body.model_dump(exclude_none=True)
        state = await repo.update(principal.user_id, **updates)
        return DispatcherStateResponse(
            id=str(state.id),
            running=state.running,
            threshold=state.threshold,
            max_concurrent_raids=state.max_concurrent_raids,
            auto_continue=state.auto_continue,
            updated_at=state.updated_at,
        )

    @router.get("/log")
    async def get_activity_log(
        n: Annotated[
            int | None,
            Query(ge=1, le=1000, description="Number of events to return."),
        ] = None,
        limit: Annotated[
            int | None,
            Query(ge=1, le=1000, description="Legacy alias for number of events to return."),
        ] = None,
        _principal: Principal = Depends(extract_principal),
        event_bus: EventBusPort = Depends(resolve_event_bus),
    ) -> ActivityLogResponse | list[str]:
        """Return the last N activity events from the dispatcher event bus.

        Events are ordered oldest-first.  Use the ``n`` query parameter to
        control how many events are returned (default 100, max 1000).
        """
        size = limit if limit is not None else (n if n is not None else 100)
        events = event_bus.get_log(size)

        if limit is not None:
            return ActivityLogResponse(
                events=[
                    ActivityEventResponse(
                        id=e.id,
                        event=e.event,
                        data=e.data,
                        owner_id=e.owner_id,
                        timestamp=e.timestamp,
                    )
                    for e in events
                ],
                total=len(events),
            )

        return [
            _format_activity_log_line(
                event_type=e.event,
                timestamp=e.timestamp,
                payload=e.data,
            )
            for e in events
        ]

    return router

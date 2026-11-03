"""REST adapter for artifact publications (F1), their bursts (F1n) and host identity (F7).

* ``GET  /artifact-publications``           the publication feed, ``after=<seq>`` (oldest first)
* ``GET  /artifact-bursts``                 bursts in change order, ``after=<rev>``
* ``GET  /artifact-bursts/{burst_id}``      one burst with its publications
* ``POST /artifact-publications/backfill``  admin: replay the durable log (always silent)
* ``GET  /instance``                        this host's name and tailnet name (F7)

Both feeds are read like notifications: a reader keeps the last cursor it saw and asks
for what came after. A burst's ``rev`` advances on every change (a late join widening its
window, its notice, a suppression), so a reader that follows ``/artifact-bursts`` always
holds each burst's current window and outcome.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from niuu.domain.notifications import NOTIFICATION_ADMIN_ROLE
from niuu.domain.services.token_scope import FORGE_SESSION_READ_SCOPE, require_scope
from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import Principal
from volundr.domain.notifications import NotificationScope
from volundr.domain.services.artifacts import ArtifactService


class BackfillRequest(BaseModel):
    since: datetime | None = Field(default=None, description="Rows at or after this time")
    until: datetime | None = Field(default=None, description="Rows before this time")
    session_id: UUID | None = Field(default=None, description="One session only")


class InstanceIdentity(BaseModel):
    """What this Forge says it is (F7). The control plane's host map stays the authority."""

    host: str
    tailnet_name: str
    instance_id: str
    service: str = "forge-api"


def _scope(principal: Principal) -> NotificationScope:
    return NotificationScope.for_principal(principal)


def create_artifacts_router(
    service: ArtifactService,
    *,
    prefix: str = "/api/v1/forge",
    default_page_size: int,
    max_page_size: int,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Artifacts"])

    @router.get(
        "/artifact-publications",
        dependencies=[Depends(require_scope(FORGE_SESSION_READ_SCOPE))],
    )
    async def list_publications(
        after: int = Query(default=0, ge=0, description="Return rows with seq > after"),
        limit: int = Query(default=default_page_size, ge=1, le=max_page_size),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        items = await service.list_after(_scope(principal), after=after, limit=limit)
        return {
            "host": service.host,
            "items": [item.wire() for item in items],
            "next_after": items[-1].seq if items else after,
            "has_more": len(items) == limit,
        }

    @router.get(
        "/artifact-bursts",
        dependencies=[Depends(require_scope(FORGE_SESSION_READ_SCOPE))],
    )
    async def list_bursts(
        after: int = Query(default=0, ge=0, description="Return bursts with rev > after"),
        limit: int = Query(default=default_page_size, ge=1, le=max_page_size),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        rows = await service.list_bursts_after(_scope(principal), after=after, limit=limit)
        return {
            "host": service.host,
            "items": [{"rev": rev, **burst.wire()} for rev, burst in rows],
            "next_after": rows[-1][0] if rows else after,
            "has_more": len(rows) == limit,
        }

    @router.get(
        "/artifact-bursts/{burst_id}",
        dependencies=[Depends(require_scope(FORGE_SESSION_READ_SCOPE))],
    )
    async def get_burst(
        burst_id: UUID, principal: Principal = Depends(extract_principal)
    ) -> dict[str, Any]:
        rows = await service.burst_publications(_scope(principal), burst_id)
        burst = await service.get_burst(burst_id)
        if burst is None or not rows:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Burst not found")
        return {**burst.wire(), "items": [row.wire() for row in rows]}

    @router.post("/artifact-publications/backfill")
    async def backfill(
        body: BackfillRequest, principal: Principal = Depends(extract_principal)
    ) -> dict[str, Any]:
        if NOTIFICATION_ADMIN_ROLE not in (principal.roles or []):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Backfill needs the admin role")
        report = await service.backfill(
            since=body.since, until=body.until, session_id=body.session_id
        )
        return {
            "scanned_rows": report.scanned_rows,
            "recorded": report.recorded,
            "last_session_id": str(report.last_session_id) if report.last_session_id else None,
            "last_seq": report.last_seq,
            "origin": "backfill",
        }

    return router


def create_instance_router(
    identity: InstanceIdentity, *, prefix: str = "/api/v1/forge"
) -> APIRouter:
    """``GET /instance``: unauthenticated, like ``/version`` (names, never secrets)."""
    router = APIRouter(prefix=prefix, tags=["Forge"])

    @router.get("/instance", response_model=InstanceIdentity)
    async def instance() -> InstanceIdentity:
        return identity

    return router

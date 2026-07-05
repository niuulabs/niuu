"""Registry-backed Ravn read aggregation for the shared Niuu shell.

Each registered Volundr instance's ingress host also serves the cluster's
ravn service under ``/api/v1/ravn/*``. This router fans the ravn READ
endpoints out across the same instance registry the Forge aggregate uses
(`rest_volundr`), so the central UI sees fleet-wide residents:

- list endpoints merge per-instance lists and tag items with instance
  metadata exactly like ``GET /api/v1/forge/sessions``;
- single lookups probe visible instances until one returns non-404,
  mirroring ``_find_session_owner``.

Failures of individual instances degrade the same way the Forge aggregate
does: errored or non-2xx instances are skipped and the rest are merged.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from starlette.types import ASGIApp

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.rest_volundr import (
    _normalize_timestamp,
    _query_params,
    _request_remote,
    _visible_instances,
    _with_instance,
)
from niuu.domain.models import Principal
from niuu.domain.services.instances import InstanceService

_RAVN_REMOTE_PREFIX = "/api/v1/ravn"


def create_ravn_router(
    service: InstanceService,
    *,
    embedded_forge_app: ASGIApp | None = None,
) -> APIRouter:
    """Create a registry-aware aggregate router for the ravn read endpoints."""
    router = APIRouter(prefix="/api/v1/ravn", tags=["Ravn"])

    async def _aggregate_list(
        request: Request,
        principal: Principal,
        path: str,
    ) -> list[dict[str, Any]]:
        instances = await _visible_instances(service, principal)
        params = _query_params(request)
        results = await asyncio.gather(
            *[
                _request_remote(
                    instance,
                    request,
                    method="GET",
                    path=path,
                    remote_prefix=_RAVN_REMOTE_PREFIX,
                    params=params,
                    embedded_app=embedded_forge_app,
                )
                for instance in instances
            ],
            return_exceptions=True,
        )

        merged: dict[str, dict[str, Any]] = {}
        for instance, result in zip(instances, results, strict=False):
            if isinstance(result, Exception):
                continue
            if result.status_code >= 400:
                continue
            payload = result.json()
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict):
                    continue
                merged[str(item.get("id") or "")] = _with_instance(item, instance)

        items = [item for item in merged.values() if item.get("id")]
        items.sort(
            key=lambda item: _normalize_timestamp(
                item.get("last_active") or item.get("lastActive")
            ),
            reverse=True,
        )
        return items

    async def _find_owner_payload(
        request: Request,
        principal: Principal,
        path: str,
        not_found_detail: str,
    ) -> dict[str, Any]:
        for instance in await _visible_instances(service, principal):
            response = await _request_remote(
                instance,
                request,
                method="GET",
                path=path,
                remote_prefix=_RAVN_REMOTE_PREFIX,
                embedded_app=embedded_forge_app,
            )
            if response.status_code == status.HTTP_404_NOT_FOUND:
                continue
            if response.status_code == status.HTTP_403_FORBIDDEN:
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:  # pragma: no cover - defensive transport mapping
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=str(exc),
                ) from exc
            payload = response.json()
            if isinstance(payload, dict):
                return _with_instance(payload, instance)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=not_found_detail,
        )

    @router.get("/ravens")
    async def list_ravens(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        """Aggregate discovered resident ravns across visible instances."""
        return await _aggregate_list(request, principal, "/ravens")

    @router.get("/sessions")
    async def list_ravn_sessions(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        """Aggregate live ravn sessions across visible instances."""
        return await _aggregate_list(request, principal, "/sessions")

    @router.get("/ravens/{ravn_id}")
    async def get_raven(
        request: Request,
        ravn_id: str = Path(description="Resident ravn identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        """Return one resident ravn from whichever instance owns it."""
        return await _find_owner_payload(
            request,
            principal,
            f"/ravens/{ravn_id}",
            f"Ravn not found: {ravn_id}",
        )

    @router.get("/sessions/{session_id}")
    async def get_ravn_session(
        request: Request,
        session_id: str = Path(description="Ravn session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        """Return one live ravn session from whichever instance owns it."""
        return await _find_owner_payload(
            request,
            principal,
            f"/sessions/{session_id}",
            f"Session not found: {session_id}",
        )

    return router

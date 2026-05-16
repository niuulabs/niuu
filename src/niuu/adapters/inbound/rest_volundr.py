"""Registry-backed Volundr aggregation endpoints for the shared Niuu shell."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, Response, status

from niuu.adapters.inbound.auth import extract_principal
from niuu.domain.models import InstanceKind, Principal, RegisteredInstance
from niuu.domain.services.instances import InstanceService


def _forward_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in (
        "authorization",
        "x-auth-user-id",
        "x-auth-email",
        "x-auth-tenant",
        "x-auth-roles",
    ):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    return headers


async def _visible_instances(
    service: InstanceService,
    principal: Principal,
) -> list[RegisteredInstance]:
    return await service.list_visible(
        principal,
        kind=InstanceKind.VOLUNDR,
        enabled_only=True,
    )


def _with_instance(payload: Any, instance: RegisteredInstance) -> Any:
    if not isinstance(payload, dict):
        return payload
    enriched = dict(payload)
    enriched["instance_id"] = instance.id
    enriched["instance_name"] = instance.name
    enriched["instance_slug"] = instance.slug
    return enriched


def _query_params(request: Request) -> list[tuple[str, str]]:
    return list(request.query_params.multi_items())


def _normalize_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _merge_sparklines(items: list[dict[str, Any]]) -> dict[str, list[float]]:
    merged: dict[str, list[float]] = {}
    for item in items:
        sparklines = item.get("sparklines")
        if not isinstance(sparklines, Mapping):
            continue
        for key, raw_points in sparklines.items():
            if not isinstance(raw_points, list):
                continue
            bucket = merged.setdefault(str(key), [0.0] * len(raw_points))
            if len(bucket) < len(raw_points):
                bucket.extend([0.0] * (len(raw_points) - len(bucket)))
            for index, raw_point in enumerate(raw_points):
                if isinstance(raw_point, (int, float)):
                    bucket[index] += float(raw_point)
    return merged


def _ensure_remote_success(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = response.text.strip() or response.reason_phrase
    raise HTTPException(status_code=response.status_code, detail=detail[:1000])


async def _request_remote(
    instance: RegisteredInstance,
    request: Request,
    *,
    method: str,
    path: str,
    json_body: Any | None = None,
    params: list[tuple[str, str]] | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.request(
            method,
            f"{instance.base_url}/api/v1/forge{path}",
            headers=_forward_headers(request),
            params=params,
            json=json_body,
        )
    return response


async def _find_session_owner(
    service: InstanceService,
    principal: Principal,
    request: Request,
    session_id: str,
) -> tuple[RegisteredInstance, dict[str, Any]]:
    for instance in await _visible_instances(service, principal):
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}",
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
            return instance, _with_instance(payload, instance)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Session not found: {session_id}",
    )


def create_volundr_router(service: InstanceService) -> APIRouter:
    """Create a registry-aware Volundr aggregate router."""
    router = APIRouter(prefix="/api/v1/niuu/volundr", tags=["Shared", "Volundr"])

    @router.get("/sessions")
    async def list_sessions(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        instances = await _visible_instances(service, principal)
        params = _query_params(request)
        results = await asyncio.gather(
            *[
                _request_remote(instance, request, method="GET", path="/sessions", params=params)
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

        sessions = [item for item in merged.values() if item.get("id")]
        sessions.sort(
            key=lambda item: _normalize_timestamp(
                item.get("last_active") or item.get("lastActive")
            ),
            reverse=True,
        )
        return sessions

    @router.get("/sessions/{session_id}")
    async def get_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        _, payload = await _find_session_owner(service, principal, request, session_id)
        return payload

    @router.get("/stats")
    async def get_stats(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instances = await _visible_instances(service, principal)
        results = await asyncio.gather(
            *[
                _request_remote(instance, request, method="GET", path="/stats")
                for instance in instances
            ],
            return_exceptions=True,
        )

        payloads = [
            result.json()
            for result in results
            if not isinstance(result, Exception)
            and result.status_code < 400
            and isinstance(result.json(), dict)
        ]

        def _sum_int(snake_key: str, camel_key: str) -> int:
            return sum(int(item.get(snake_key) or item.get(camel_key) or 0) for item in payloads)

        def _sum_float(snake_key: str, camel_key: str) -> float:
            return sum(float(item.get(snake_key) or item.get(camel_key) or 0) for item in payloads)

        return {
            "active_sessions": _sum_int("active_sessions", "activeSessions"),
            "total_sessions": _sum_int("total_sessions", "totalSessions"),
            "tokens_today": _sum_int("tokens_today", "tokensToday"),
            "local_tokens": _sum_int("local_tokens", "localTokens"),
            "cloud_tokens": _sum_int("cloud_tokens", "cloudTokens"),
            "cost_today": _sum_float("cost_today", "costToday"),
            "sparklines": _merge_sparklines(payloads),
        }

    @router.post("/sessions/{session_id}/stop")
    async def stop_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(service, principal, request, session_id)
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/stop",
        )
        _ensure_remote_success(response)
        payload = response.json()
        return _with_instance(payload, instance) if isinstance(payload, dict) else {}

    @router.post("/sessions/{session_id}/archive")
    async def archive_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(service, principal, request, session_id)
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/archive",
        )
        _ensure_remote_success(response)
        payload = response.json()
        return _with_instance(payload, instance) if isinstance(payload, dict) else {}

    @router.post("/sessions/{session_id}/restore")
    async def restore_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(service, principal, request, session_id)
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/restore",
        )
        _ensure_remote_success(response)
        payload = response.json()
        return _with_instance(payload, instance) if isinstance(payload, dict) else {}

    @router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        instance, _ = await _find_session_owner(service, principal, request, session_id)
        response = await _request_remote(
            instance,
            request,
            method="DELETE",
            path=f"/sessions/{session_id}",
            params=_query_params(request),
        )
        _ensure_remote_success(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/sessions/{session_id}/conversation")
    async def get_conversation(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(service, principal, request, session_id)
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/conversation",
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"turns": []}

    @router.post("/sessions/{session_id}/messages")
    async def send_message(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(service, principal, request, session_id)
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/messages",
            json_body=body,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.get("/sessions/{session_id}/logs")
    async def get_logs(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(service, principal, request, session_id)
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/logs",
            params=_query_params(request),
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"lines": []}

    @router.get("/sessions/{session_id}/logs/aggregate")
    async def get_aggregated_logs(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(service, principal, request, session_id)
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/logs/aggregate",
            params=_query_params(request),
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"lines": []}

    @router.get("/chronicles/{session_id}/timeline")
    async def get_chronicle(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
        limit: int | None = Query(default=None),
    ) -> Any:
        instance, _ = await _find_session_owner(service, principal, request, session_id)
        params: list[tuple[str, str]] = []
        if limit is not None:
            params.append(("limit", str(limit)))
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/chronicles/{session_id}/timeline",
            params=params,
        )
        _ensure_remote_success(response)
        return response.json()

    return router

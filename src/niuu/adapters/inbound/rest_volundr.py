"""Registry-backed Forge runtime facade for the shared Niuu shell."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from starlette.types import ASGIApp

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.remote_urls import build_remote_url
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


def _strip_instance_hints(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    sanitized = dict(payload)
    for key in (
        "instance_id",
        "instanceId",
        "instance_name",
        "instanceName",
        "target_tags",
        "targetTags",
        "target_match",
        "targetMatch",
    ):
        sanitized.pop(key, None)
    return sanitized


def _with_instance(
    payload: Any,
    instance: RegisteredInstance,
    *,
    rebase_chat_endpoint: bool = True,
) -> Any:
    if not isinstance(payload, dict):
        return payload
    enriched = dict(payload)
    enriched["instance_id"] = instance.id
    enriched["instance_name"] = instance.name
    enriched["instance_slug"] = instance.slug
    if rebase_chat_endpoint and not _uses_embedded_transport(instance):
        for key in ("chat_endpoint", "chatEndpoint"):
            value = enriched.get(key)
            if isinstance(value, str) and value.startswith("/"):
                enriched[key] = _instance_websocket_url(instance, value)
    return enriched


def _instance_websocket_url(instance: RegisteredInstance, path: str) -> str:
    """Resolve a target-relative session socket against its public instance origin."""
    parsed = urlsplit(instance.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Instance {instance.id} has no public HTTP origin")
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


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
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
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


def _merge_cluster_resources(
    items: list[dict[str, Any]],
    instances: list[RegisteredInstance],
) -> dict[str, Any]:
    resource_types: dict[str, dict[str, Any]] = {}
    nodes: list[dict[str, Any]] = []
    instance_items: list[dict[str, Any]] = []
    for instance, item in zip(instances, items, strict=False):
        instance_items.append(
            {
                "id": instance.id,
                "name": instance.name,
                "slug": instance.slug,
                "is_default": instance.is_default,
                "tags": instance.tags,
            }
        )
        for raw_type in item.get("resource_types") or item.get("resourceTypes") or []:
            if isinstance(raw_type, dict):
                key = str(raw_type.get("name") or raw_type.get("resource_key") or raw_type)
                resource_types.setdefault(key, raw_type)
        for raw_node in item.get("nodes") or []:
            if not isinstance(raw_node, dict):
                continue
            node = dict(raw_node)
            node["instance_id"] = instance.id
            node["instance_name"] = instance.name
            node["instance_slug"] = instance.slug
            if node.get("name"):
                node["name"] = f"{instance.slug}/{node['name']}"
            nodes.append(node)
    resource_type_items = [_with_resource_type_aliases(item) for item in resource_types.values()]
    return {
        "instances": instance_items,
        "resource_types": resource_type_items,
        "resourceTypes": resource_type_items,
        "nodes": nodes,
    }


def _with_resource_type_aliases(item: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    for snake_key, camel_key in (
        ("resource_key", "resourceKey"),
        ("display_name", "displayName"),
    ):
        if snake_key in enriched and camel_key not in enriched:
            enriched[camel_key] = enriched[snake_key]
        if camel_key in enriched and snake_key not in enriched:
            enriched[snake_key] = enriched[camel_key]
    return enriched


def _ensure_remote_success(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    detail = response.text.strip() or response.reason_phrase
    raise HTTPException(status_code=response.status_code, detail=detail[:1000])


def _uses_embedded_transport(instance: RegisteredInstance) -> bool:
    return str(instance.config.get("transport", "")).strip().lower() == "embedded"


async def _request_remote(
    instance: RegisteredInstance,
    request: Request,
    *,
    method: str,
    path: str,
    remote_prefix: str = "/api/v1/forge",
    base_url: str | None = None,
    json_body: Any | None = None,
    content_body: bytes | None = None,
    params: list[tuple[str, str]] | None = None,
    extra_headers: dict[str, str] | None = None,
    embedded_app: ASGIApp | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    headers = _forward_headers(request)
    if extra_headers:
        headers.update(extra_headers)
    request_kwargs: dict[str, Any] = {
        "headers": headers,
        "params": params,
    }
    if content_body is not None:
        request_kwargs["content"] = content_body
    elif json_body is not None:
        request_kwargs["json"] = json_body
    if _uses_embedded_transport(instance):
        if embedded_app is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Embedded Forge target is not available in this process",
            )
        remote_url = build_remote_url("http://embedded.local", remote_prefix, path)
        transport = httpx.ASGITransport(app=embedded_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://embedded.local",
        ) as client:
            return await client.request(
                method,
                remote_url,
                **request_kwargs,
            )

    try:
        remote_url = build_remote_url(base_url or instance.base_url, remote_prefix, path)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.request(
            method,
            remote_url,
            **request_kwargs,
        )
    return response


async def _sync_persona_to_instance(
    instance: RegisteredInstance,
    request: Request,
    principal: Principal,
    persona_name: object,
    *,
    embedded_app: ASGIApp | None,
) -> None:
    """Materialize the central user persona on a remote target before launch."""
    name = str(persona_name or "").strip()
    if not name or embedded_app is None or _uses_embedded_transport(instance):
        return

    registry = getattr(getattr(embedded_app, "state", None), "persona_registry", None)
    if registry is None:
        return
    view = await registry.get_persona(principal.user_id, name)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Persona not found: {name}",
        )

    encoded_name = quote(name, safe="")
    existing = await _request_remote(
        instance,
        request,
        method="GET",
        path=f"/personas/{encoded_name}",
        remote_prefix="/api/v1",
        embedded_app=embedded_app,
    )
    if existing.status_code < 400 and not view.has_override:
        return
    if existing.status_code not in {status.HTTP_200_OK, status.HTTP_404_NOT_FOUND}:
        _ensure_remote_success(existing)

    method = "PUT" if existing.status_code == status.HTTP_200_OK else "POST"
    path = f"/personas/{encoded_name}" if method == "PUT" else "/personas"
    synced = await _request_remote(
        instance,
        request,
        method=method,
        path=path,
        remote_prefix="/api/v1",
        json_body=view.payload,
        embedded_app=embedded_app,
    )
    if synced.status_code == status.HTTP_409_CONFLICT and method == "POST":
        synced = await _request_remote(
            instance,
            request,
            method="PUT",
            path=f"/personas/{encoded_name}",
            remote_prefix="/api/v1",
            json_body=view.payload,
            embedded_app=embedded_app,
        )
    _ensure_remote_success(synced)


async def _find_session_owner(
    service: InstanceService,
    principal: Principal,
    request: Request,
    session_id: str,
    *,
    embedded_app: ASGIApp | None = None,
) -> tuple[RegisteredInstance, dict[str, Any]]:
    for instance in await _visible_instances(service, principal):
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}",
            embedded_app=embedded_app,
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


async def _find_resident_owner(
    service: InstanceService,
    principal: Principal,
    request: Request,
    runtime_id: str,
    *,
    embedded_app: ASGIApp | None = None,
) -> tuple[RegisteredInstance, dict[str, Any]]:
    for instance in await _visible_instances(service, principal):
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/resident-runtimes/{runtime_id}",
            embedded_app=embedded_app,
        )
        if response.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}:
            continue
        _ensure_remote_success(response)
        payload = response.json()
        if isinstance(payload, dict):
            return instance, _with_instance(payload, instance, rebase_chat_endpoint=False)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Resident runtime not found: {runtime_id}",
    )


async def _resolve_target_instance(
    service: InstanceService,
    principal: Principal,
    instance_id: str | None,
    *,
    tags: list[str] | None = None,
    match: str = "all",
) -> RegisteredInstance:
    if instance_id:
        instance = await service.get_visible(principal, instance_id)
        if instance is None or instance.kind != InstanceKind.VOLUNDR or not instance.enabled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target not found: {instance_id}",
            )
        return instance

    instances = await service.list_visible(
        principal,
        kind=InstanceKind.VOLUNDR,
        enabled_only=True,
        tags=tags,
        match=match,
    )
    if not instances:
        # Fail loud: never silently fall back to an untagged backend when a tag
        # selector was requested.
        if tags:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(f"No enabled Volundr instance matches tags {sorted(tags)} (match={match})"),
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No enabled Volundr instances are registered for this user",
        )
    default_instance = next((instance for instance in instances if instance.is_default), None)
    return default_instance or instances[0]


def create_volundr_router(
    service: InstanceService,
    *,
    embedded_forge_app: ASGIApp | None = None,
) -> APIRouter:
    """Create a registry-aware Forge runtime router."""
    router = APIRouter(prefix="/api/v1/forge", tags=["Forge"])

    @router.get("/resident-profiles")
    async def list_resident_profiles(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        instances = await _visible_instances(service, principal)
        results = await asyncio.gather(
            *[
                _request_remote(
                    instance,
                    request,
                    method="GET",
                    path="/resident-profiles",
                    embedded_app=embedded_forge_app,
                )
                for instance in instances
            ],
            return_exceptions=True,
        )
        profiles: list[dict[str, Any]] = []
        for instance, result in zip(instances, results, strict=False):
            if isinstance(result, Exception) or result.status_code >= 400:
                continue
            payload = result.json()
            if not isinstance(payload, list):
                continue
            profiles.extend(
                _with_instance(item, instance, rebase_chat_endpoint=False)
                for item in payload
                if isinstance(item, dict)
            )
        return profiles

    @router.get("/resident-runtimes")
    async def list_resident_runtimes(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        instances = await _visible_instances(service, principal)
        results = await asyncio.gather(
            *[
                _request_remote(
                    instance,
                    request,
                    method="GET",
                    path="/resident-runtimes",
                    embedded_app=embedded_forge_app,
                )
                for instance in instances
            ],
            return_exceptions=True,
        )
        runtimes: list[dict[str, Any]] = []
        for instance, result in zip(instances, results, strict=False):
            if isinstance(result, Exception) or result.status_code >= 400:
                continue
            payload = result.json()
            if not isinstance(payload, list):
                continue
            runtimes.extend(
                _with_instance(item, instance, rebase_chat_endpoint=False)
                for item in payload
                if isinstance(item, dict)
            )
        return runtimes

    @router.post("/resident-runtimes", status_code=status.HTTP_201_CREATED)
    async def create_resident_runtime(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        requested_instance_id = body.get("instance_id") or body.get("instanceId")
        instance = await _resolve_target_instance(
            service,
            principal,
            str(requested_instance_id) if requested_instance_id else None,
        )
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path="/resident-runtimes",
            json_body=_strip_instance_hints(body),
            embedded_app=embedded_forge_app,
            timeout=600.0,
        )
        _ensure_remote_success(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="Unexpected resident create response")
        return _with_instance(payload, instance, rebase_chat_endpoint=False)

    @router.get("/resident-runtimes/{runtime_id}")
    async def get_resident_runtime(
        request: Request,
        runtime_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        _, payload = await _find_resident_owner(
            service,
            principal,
            request,
            runtime_id,
            embedded_app=embedded_forge_app,
        )
        return payload

    async def _resident_owner_request(
        request: Request,
        principal: Principal,
        runtime_id: str,
        *,
        method: str,
        path_suffix: str = "",
        json_body: Any | None = None,
        timeout: float = 30.0,
    ) -> tuple[RegisteredInstance, httpx.Response]:
        instance, _ = await _find_resident_owner(
            service,
            principal,
            request,
            runtime_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method=method,
            path=f"/resident-runtimes/{runtime_id}{path_suffix}",
            json_body=json_body,
            params=_query_params(request),
            embedded_app=embedded_forge_app,
            timeout=timeout,
        )
        _ensure_remote_success(response)
        return instance, response

    async def _control_resident_runtime(
        request: Request,
        runtime_id: str,
        action: str,
        principal: Principal,
    ) -> dict[str, Any]:
        instance, response = await _resident_owner_request(
            request,
            principal,
            runtime_id,
            method="POST",
            path_suffix=f"/{action}",
            timeout=600.0,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="Unexpected resident lifecycle response")
        return _with_instance(payload, instance, rebase_chat_endpoint=False)

    @router.post("/resident-runtimes/{runtime_id}/restart")
    async def restart_resident_runtime(
        request: Request,
        runtime_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        return await _control_resident_runtime(request, runtime_id, "restart", principal)

    @router.post("/resident-runtimes/{runtime_id}/suspend")
    async def suspend_resident_runtime(
        request: Request,
        runtime_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        return await _control_resident_runtime(request, runtime_id, "suspend", principal)

    @router.post("/resident-runtimes/{runtime_id}/resume")
    async def resume_resident_runtime(
        request: Request,
        runtime_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        return await _control_resident_runtime(request, runtime_id, "resume", principal)

    @router.get("/resident-runtimes/{runtime_id}/logs")
    async def get_resident_runtime_logs(
        request: Request,
        runtime_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, response = await _resident_owner_request(
            request,
            principal,
            runtime_id,
            method="GET",
            path_suffix="/logs",
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="Unexpected resident logs response")
        return payload

    @router.post("/resident-runtimes/{runtime_id}/usage")
    async def record_resident_runtime_usage(
        request: Request,
        runtime_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, response = await _resident_owner_request(
            request,
            principal,
            runtime_id,
            method="POST",
            path_suffix="/usage",
            json_body=body,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="Unexpected resident usage response")
        return _with_instance(payload, instance, rebase_chat_endpoint=False)

    @router.get("/resident-runtimes/{runtime_id}/sessions")
    async def list_resident_sessions(
        request: Request,
        runtime_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        instance, response = await _resident_owner_request(
            request,
            principal,
            runtime_id,
            method="GET",
            path_suffix="/sessions",
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise HTTPException(status_code=502, detail="Unexpected resident sessions response")
        return [
            _with_instance(item, instance, rebase_chat_endpoint=False)
            for item in payload
            if isinstance(item, dict)
        ]

    @router.post(
        "/resident-runtimes/{runtime_id}/sessions",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_resident_session(
        request: Request,
        runtime_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, response = await _resident_owner_request(
            request,
            principal,
            runtime_id,
            method="POST",
            path_suffix="/sessions",
            json_body=body,
        )
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail="Unexpected resident session response")
        return _with_instance(payload, instance, rebase_chat_endpoint=False)

    @router.delete(
        "/resident-runtimes/{runtime_id}/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_resident_session(
        request: Request,
        runtime_id: str,
        session_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        await _resident_owner_request(
            request,
            principal,
            runtime_id,
            method="DELETE",
            path_suffix=f"/sessions/{session_id}",
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete(
        "/resident-runtimes/{runtime_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_resident_runtime(
        request: Request,
        runtime_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        await _resident_owner_request(
            request,
            principal,
            runtime_id,
            method="DELETE",
            timeout=600.0,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/sessions")
    async def list_sessions(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        instances = await _visible_instances(service, principal)
        params = _query_params(request)
        results = await asyncio.gather(
            *[
                _request_remote(
                    instance,
                    request,
                    method="GET",
                    path="/sessions",
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

        sessions = [item for item in merged.values() if item.get("id")]
        sessions.sort(
            key=lambda item: _normalize_timestamp(
                item.get("last_active") or item.get("lastActive")
            ),
            reverse=True,
        )
        return sessions

    @router.get("/sessions/stream")
    async def stream_sessions(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> StreamingResponse:
        """Proxy the backing instance's Server-Sent Events session stream.

        MUST be declared before GET /sessions/{session_id} — otherwise the
        literal path "stream" is captured as a session id, the request falls
        through to a session lookup, and the frontend's FleetStream hangs on a
        connection that never emits an event (status frozen until a manual
        reload). Single-instance/mini deployments stream the default backing
        instance; fleet-wide fan-in across instances is a separate feature.
        """
        instance = await _resolve_target_instance(service, principal, None)
        headers = _forward_headers(request)
        embedded = _uses_embedded_transport(instance)

        # For the in-process instance, subscribe to its event bus directly.
        # httpx's ASGITransport buffers the entire response and never returns for
        # an infinite stream, so it cannot proxy SSE — but the embedded volundr
        # app shares this process, so we read its broadcaster. If it is somehow
        # unavailable, fail fast with 503 so the frontend degrades to polling
        # (a hung 200 stream would freeze status until a manual reload).
        broadcaster = None
        if embedded:
            broadcaster = getattr(getattr(embedded_forge_app, "state", None), "broadcaster", None)
            if broadcaster is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Session event stream unavailable",
                )

        async def _proxy() -> Any:
            if embedded:
                async for event in broadcaster.subscribe():
                    if await request.is_disconnected():
                        break
                    yield (
                        f"event: {event.type.value}\ndata: {json.dumps(event.data)}\n\n"
                    ).encode()
                return
            url = build_remote_url(instance.base_url, "/api/v1/forge", "/sessions/stream")
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url, headers=headers) as resp:
                    async for chunk in resp.aiter_raw():
                        yield chunk

        return StreamingResponse(
            _proxy(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/sessions/{session_id}")
    async def get_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        _, payload = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        return payload

    @router.post("/sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        requested_instance_id = body.get("instance_id") or body.get("instanceId")
        target_tags = body.get("target_tags") or body.get("targetTags")
        target_match = body.get("target_match") or body.get("targetMatch") or "all"
        instance = await _resolve_target_instance(
            service,
            principal,
            str(requested_instance_id) if requested_instance_id else None,
            tags=list(target_tags) if target_tags else None,
            match=str(target_match),
        )
        await _sync_persona_to_instance(
            instance,
            request,
            principal,
            body.get("persona_name") or body.get("personaName"),
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path="/sessions",
            json_body=_strip_instance_hints(body),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unexpected response from target Volundr instance",
            )
        return _with_instance(payload, instance)

    @router.get("/stats")
    async def get_stats(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instances = await _visible_instances(service, principal)
        results = await asyncio.gather(
            *[
                _request_remote(
                    instance,
                    request,
                    method="GET",
                    path="/stats",
                    embedded_app=embedded_forge_app,
                )
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
            "sessions_today": _sum_int("sessions_today", "sessionsToday"),
            "tokens_today": _sum_int("tokens_today", "tokensToday"),
            "local_tokens": _sum_int("local_tokens", "localTokens"),
            "cloud_tokens": _sum_int("cloud_tokens", "cloudTokens"),
            "cost_today": _sum_float("cost_today", "costToday"),
            "sparklines": _merge_sparklines(payloads),
        }

    @router.get("/cluster/resources")
    async def get_cluster_resources(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instances = await _visible_instances(service, principal)
        results = await asyncio.gather(
            *[
                _request_remote(
                    instance,
                    request,
                    method="GET",
                    path="/resources",
                    remote_prefix="/api/v1/volundr",
                    embedded_app=embedded_forge_app,
                )
                for instance in instances
            ],
            return_exceptions=True,
        )
        payload_instances: list[RegisteredInstance] = []
        payloads: list[dict[str, Any]] = []
        for instance, result in zip(instances, results, strict=False):
            if isinstance(result, Exception) or result.status_code >= 400:
                continue
            payload = result.json()
            if isinstance(payload, dict):
                payload_instances.append(instance)
                payloads.append(payload)
        return _merge_cluster_resources(payloads, payload_instances)

    @router.get("/mcp-servers")
    async def list_mcp_servers(
        request: Request,
        instance_id: str | None = Query(default=None),
        target_tags: list[str] | None = Query(default=None),
        target_match: str = Query(default="all"),
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        instance = await _resolve_target_instance(
            service,
            principal,
            instance_id,
            tags=target_tags,
            match=target_match,
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            remote_prefix="/api/v1/credentials",
            path="/mcp-servers",
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        if not isinstance(payload, list):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unexpected MCP server response from target Volundr instance",
            )
        return [item for item in payload if isinstance(item, dict)]

    @router.get("/mcp-servers/{server_name}")
    async def get_mcp_server(
        request: Request,
        server_name: str = Path(description="MCP server name to retrieve"),
        instance_id: str | None = Query(default=None),
        target_tags: list[str] | None = Query(default=None),
        target_match: str = Query(default="all"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance = await _resolve_target_instance(
            service,
            principal,
            instance_id,
            tags=target_tags,
            match=target_match,
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            remote_prefix="/api/v1/credentials",
            path=f"/mcp-servers/{server_name}",
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unexpected MCP server response from target Volundr instance",
            )
        return payload

    @router.post("/sessions/archive-stopped")
    async def archive_stopped_sessions(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[str]:
        instances = await _visible_instances(service, principal)
        results = await asyncio.gather(
            *[
                _request_remote(
                    instance,
                    request,
                    method="POST",
                    path="/sessions/archive-stopped",
                    embedded_app=embedded_forge_app,
                )
                for instance in instances
            ],
            return_exceptions=True,
        )
        archived: list[str] = []
        for result in results:
            if isinstance(result, Exception) or result.status_code >= 400:
                continue
            payload = result.json()
            if isinstance(payload, list):
                archived.extend(str(item) for item in payload)
        return archived

    @router.get("/feature-flags")
    async def get_feature_flags(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance = await _resolve_target_instance(service, principal, None)
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path="/feature-flags",
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.get("/external-sessions")
    async def list_external_sessions(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        """Aggregate discoverable external CLI sessions across instances.

        Returns 503 only when every visible instance reports discovery as
        unavailable, so the UI can hide the import affordance.
        """
        instances = await _visible_instances(service, principal)
        params = _query_params(request)
        results = await asyncio.gather(
            *[
                _request_remote(
                    instance,
                    request,
                    method="GET",
                    path="/external-sessions",
                    params=params,
                    embedded_app=embedded_forge_app,
                )
                for instance in instances
            ],
            return_exceptions=True,
        )

        merged: list[dict[str, Any]] = []
        succeeded = False
        for instance, result in zip(instances, results, strict=False):
            if isinstance(result, Exception):
                continue
            if result.status_code >= 400:
                continue
            payload = result.json()
            if not isinstance(payload, list):
                continue
            succeeded = True
            merged.extend(
                _with_instance(item, instance) for item in payload if isinstance(item, dict)
            )

        if not succeeded:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="External session discovery not available",
            )

        merged.sort(
            key=lambda item: _normalize_timestamp(item.get("updated_at") or item.get("updatedAt")),
            reverse=True,
        )
        return merged

    @router.post("/sessions/import", status_code=status.HTTP_201_CREATED)
    async def import_external_session(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        requested_instance_id = body.get("instance_id") or body.get("instanceId")
        target_tags = body.get("target_tags") or body.get("targetTags")
        target_match = body.get("target_match") or body.get("targetMatch") or "all"
        instance = await _resolve_target_instance(
            service,
            principal,
            str(requested_instance_id) if requested_instance_id else None,
            tags=list(target_tags) if target_tags else None,
            match=str(target_match),
        )
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path="/sessions/import",
            json_body=_strip_instance_hints(body),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unexpected response from target Volundr instance",
            )
        return _with_instance(payload, instance)

    async def _start_session_on_owner(
        request: Request,
        session_id: str,
        principal: Principal,
        path_suffix: str,
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        try:
            json_body = await request.json()
        except Exception:
            json_body = None
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/{path_suffix}",
            json_body=json_body if isinstance(json_body, dict) else None,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return _with_instance(payload, instance) if isinstance(payload, dict) else {}

    @router.post("/sessions/{session_id}/start")
    async def start_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        return await _start_session_on_owner(request, session_id, principal, "start")

    @router.post("/sessions/{session_id}/resume")
    async def resume_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        return await _start_session_on_owner(request, session_id, principal, "resume")

    @router.post("/sessions/{session_id}/log", status_code=status.HTTP_201_CREATED)
    async def append_log(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service, principal, request, session_id, embedded_app=embedded_forge_app
        )
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/log",
            json_body=body,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.get("/sessions/{session_id}/log/head")
    async def get_log_head(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service, principal, request, session_id, embedded_app=embedded_forge_app
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/log/head",
            params=_query_params(request),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.get("/sessions/{session_id}/log")
    async def get_log(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> Any:
        instance, _ = await _find_session_owner(
            service, principal, request, session_id, embedded_app=embedded_forge_app
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/log",
            params=_query_params(request),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        # The backing replay endpoint returns a JSON LIST of log entries
        # (response_model=list[SessionLogEntryResponse]). Pass it through
        # verbatim: coercing a non-dict to {"entries": []} silently dropped the
        # ENTIRE transcript, so the web's durable-log replay rendered nothing
        # ("session looks dead / no final message") even with hundreds of
        # frames stored (latest_seq high, replay empty).
        return response.json()

    @router.post("/sessions/{session_id}/activity", status_code=status.HTTP_204_NO_CONTENT)
    async def report_activity(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        # Skuld brokers post activity heartbeats (incl. the cli_session_id used
        # for restart resume) to the public /api/v1/forge prefix; without this
        # proxy they 404 at the aggregate and the session never becomes
        # resumable in the full host profile.
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/activity",
            json_body=body,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/sessions/{session_id}/usage", status_code=status.HTTP_201_CREATED)
    async def report_usage(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        # Token-usage reports take the same broker ingestion path as activity.
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/usage",
            json_body=body,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return _with_instance(payload, instance) if isinstance(payload, dict) else {}

    @router.post("/sessions/{session_id}/stop")
    async def stop_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/stop",
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return _with_instance(payload, instance) if isinstance(payload, dict) else {}

    @router.patch("/sessions/{session_id}/archive")
    async def archive_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="PATCH",
            path=f"/sessions/{session_id}/archive",
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return _with_instance(payload, instance) if isinstance(payload, dict) else {}

    @router.patch("/sessions/{session_id}/restore")
    async def restore_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="PATCH",
            path=f"/sessions/{session_id}/restore",
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return _with_instance(payload, instance) if isinstance(payload, dict) else {}

    @router.put("/sessions/{session_id}")
    async def update_session(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        # Rename/update (contract §2.1: PUT /sessions/{id} with {name|model|…}).
        # The aggregate owns /api/v1/forge but only registered GET/DELETE on this
        # path, so web + iOS renames 405'd — same gap class as activity/log/start.
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="PUT",
            path=f"/sessions/{session_id}",
            json_body=body,
            embedded_app=embedded_forge_app,
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
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="DELETE",
            path=f"/sessions/{session_id}",
            params=_query_params(request),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/sessions/{session_id}/conversation")
    async def get_conversation(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        # PERF PROFILE (aggregate layer): the broker prepares the shallow payload in ~200ms, but
        # the client-observed open is multiple seconds — time THIS layer's three steps (owner
        # resolve, remote proxy fetch, JSON re-parse) to localize the gap. Merged into `_prep`.
        t0 = time.perf_counter()
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        t_owner = time.perf_counter()
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/conversation",
            params=_query_params(request),
            embedded_app=embedded_forge_app,
        )
        t_remote = time.perf_counter()
        _ensure_remote_success(response)
        payload = response.json()
        t_json = time.perf_counter()
        if isinstance(payload, dict):
            prep = dict(payload.get("_prep") or {})
            prep["agg_find_owner_ms"] = round((t_owner - t0) * 1000, 1)
            prep["agg_request_remote_ms"] = round((t_remote - t_owner) * 1000, 1)
            prep["agg_json_parse_ms"] = round((t_json - t_remote) * 1000, 1)
            # Total server-side time the client subtracts from its round-trip to isolate network
            # TRANSIT (the dominant phone cost): transit = client_fetch_ms - server_total_ms.
            prep["server_total_ms"] = round((t_json - t0) * 1000, 1)
            payload["_prep"] = prep
        return payload if isinstance(payload, dict) else {"turns": []}

    @router.get("/sessions/{session_id}/tool-result/{tool_use_id}")
    async def get_tool_result(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        tool_use_id: str = Path(description="tool_use_id of the result to fetch"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=(f"/sessions/{session_id}/tool-result/{quote(tool_use_id, safe='')}"),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.get("/sessions/{session_id}/workflow/gates")
    async def get_workflow_gates(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/workflow/gates",
            params=_query_params(request),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"gates": []}

    @router.post("/sessions/{session_id}/workflow/gates/{gate_id}/resolve")
    async def resolve_workflow_gate(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        gate_id: str = Path(description="Workflow gate identifier"),
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        extra_headers: dict[str, str] = {}
        intent = request.headers.get("x-niuu-workflow-gate-intent")
        if intent:
            extra_headers["x-niuu-workflow-gate-intent"] = intent
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/workflow/gates/{quote(gate_id, safe='')}/resolve",
            json_body=body,
            extra_headers=extra_headers,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.post("/sessions/{session_id}/messages")
    async def send_message(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/messages",
            json_body=body,
            embedded_app=embedded_forge_app,
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
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/logs",
            params=_query_params(request),
            embedded_app=embedded_forge_app,
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
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/logs/aggregate",
            params=_query_params(request),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"lines": []}

    @router.get("/sessions/{session_id}/trace")
    async def get_session_trace(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/trace",
            params=_query_params(request),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"spans": [], "lanes": []}

    @router.get("/sessions/{session_id}/trace/summary")
    async def get_session_trace_summary(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/trace/summary",
            params=_query_params(request),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def _proxy_session_file_api(
        request: Request,
        principal: Principal,
        session_id: str,
        *,
        method: str,
        path: str,
    ) -> httpx.Response:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        extra_headers: dict[str, str] = {}
        for name in ("content-type", "accept"):
            value = request.headers.get(name)
            if value:
                extra_headers[name] = value
        body = await request.body()
        return await _request_remote(
            instance,
            request,
            method=method,
            path=path,
            params=_query_params(request),
            content_body=body if body else None,
            extra_headers=extra_headers,
            embedded_app=embedded_forge_app,
        )

    @router.get("/sessions/{session_id}/files")
    async def list_session_files(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        response = await _proxy_session_file_api(
            request,
            principal,
            session_id,
            method="GET",
            path=f"/sessions/{session_id}/files",
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"entries": []}

    @router.get("/sessions/{session_id}/files/download")
    async def download_session_file(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        response = await _proxy_session_file_api(
            request,
            principal,
            session_id,
            method="GET",
            path=f"/sessions/{session_id}/files/download",
        )
        _ensure_remote_success(response)
        headers: dict[str, str] = {}
        disposition = response.headers.get("content-disposition")
        if disposition:
            headers["content-disposition"] = disposition
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "application/octet-stream"),
            headers=headers,
        )

    @router.get("/sessions/{session_id}/files/presented/{file_id}")
    async def download_presented_file(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        file_id: str = Path(description="Opaque presented-file id (present-file command)"),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        response = await _proxy_session_file_api(
            request,
            principal,
            session_id,
            method="GET",
            path=f"/sessions/{session_id}/files/presented/{file_id}",
        )
        _ensure_remote_success(response)
        headers: dict[str, str] = {}
        disposition = response.headers.get("content-disposition")
        if disposition:
            headers["content-disposition"] = disposition
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "application/octet-stream"),
            headers=headers,
        )

    @router.post("/sessions/{session_id}/files/upload")
    async def upload_session_files(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        response = await _proxy_session_file_api(
            request,
            principal,
            session_id,
            method="POST",
            path=f"/sessions/{session_id}/files/upload",
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"entries": []}

    @router.put("/sessions/{session_id}/files/upload")
    async def write_session_file(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        response = await _proxy_session_file_api(
            request,
            principal,
            session_id,
            method="PUT",
            path=f"/sessions/{session_id}/files/upload",
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.post("/sessions/{session_id}/files/mkdir")
    async def mkdir_session_file(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        response = await _proxy_session_file_api(
            request,
            principal,
            session_id,
            method="POST",
            path=f"/sessions/{session_id}/files/mkdir",
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.delete("/sessions/{session_id}/files")
    async def delete_session_file(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        response = await _proxy_session_file_api(
            request,
            principal,
            session_id,
            method="DELETE",
            path=f"/sessions/{session_id}/files",
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.get("/chronicles/{session_id}/timeline")
    async def get_chronicle(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
        limit: int | None = Query(default=None),
    ) -> Any:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            session_id,
            embedded_app=embedded_forge_app,
        )
        params: list[tuple[str, str]] = []
        if limit is not None:
            params.append(("limit", str(limit)))
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/chronicles/{session_id}/timeline",
            params=params,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        return response.json()

    # --- Skuld broker telemetry ingestion -----------------------------------
    # The in-instance Skuld broker POSTs trace spans, session events, and
    # chronicle-timeline entries back to the Forge API. These were missing
    # from the aggregate (same gap class as the activity/usage routes above),
    # so every session logged 404/405 noise. Session-keyed routes resolve the
    # owning instance; span/event telemetry carries no session id in the
    # path, so it forwards to the default backing instance (correct for
    # single-instance/mini deployments — the only place a broker reports to).

    async def _forward_to_default(
        request: Request,
        principal: Principal,
        *,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        instance = await _resolve_target_instance(service, principal, None)
        return await _request_remote(
            instance,
            request,
            method=method,
            path=path,
            json_body=body,
            embedded_app=embedded_forge_app,
        )

    @router.post("/chronicles/{session_id}/timeline", status_code=status.HTTP_201_CREATED)
    async def post_chronicle_timeline(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service, principal, request, session_id, embedded_app=embedded_forge_app
        )
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/chronicles/{session_id}/timeline",
            json_body=body,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.post("/spans/start", status_code=status.HTTP_201_CREATED)
    async def span_start(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        response = await _forward_to_default(
            request, principal, method="POST", path="/spans/start", body=body
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.post("/spans/complete", status_code=status.HTTP_201_CREATED)
    async def span_complete(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        response = await _forward_to_default(
            request, principal, method="POST", path="/spans/complete", body=body
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.post("/spans/{span_id}/finish")
    async def span_finish(
        request: Request,
        span_id: str = Path(description="Trace span identifier"),
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        response = await _forward_to_default(
            request, principal, method="POST", path=f"/spans/{span_id}/finish", body=body
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @router.post("/events", status_code=status.HTTP_201_CREATED)
    async def post_event(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        response = await _forward_to_default(
            request, principal, method="POST", path="/events", body=body
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    return router

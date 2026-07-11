"""Registry-backed Ravn read aggregation for the shared Niuu shell.

Each registered Volundr instance's ingress host also serves the cluster's
ravn service under ``/api/v1/ravn/*``. This router fans Ravn reads and resident
lifecycle commands across the same instance registry the Forge aggregate uses
(`rest_volundr`), so the central UI sees fleet-wide residents:

- list endpoints merge per-instance lists and tag items with instance
  metadata exactly like ``GET /api/v1/forge/sessions``;
- single lookups probe visible instances until one returns non-404,
  mirroring ``_find_session_owner``.

List failures degrade the same way the Forge aggregate does: errored or non-2xx
instances are skipped and the rest are merged. Commands resolve one visible
target and preserve its response status.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    WebSocket,
    status,
)
from starlette.types import ASGIApp

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.rest_volundr import (
    _ensure_remote_success,
    _normalize_timestamp,
    _query_params,
    _request_remote,
    _resolve_target_instance,
    _strip_instance_hints,
    _visible_instances,
    _with_instance,
)
from niuu.domain.models import Principal, RegisteredInstance
from niuu.domain.services.instances import InstanceService
from niuu.session_proxy import (
    _bearer_token_from_ws,
    _proxy_forward_headers,
    _proxy_ws_identity,
    bridge_websocket,
)

_RAVN_REMOTE_PREFIX = "/api/v1/ravn"
_RESIDENT_COMMAND_TIMEOUT_SECONDS = 900.0
logger = logging.getLogger(__name__)


def _ravn_base_url(instance: RegisteredInstance) -> str:
    """Resolve an optional Ravn service endpoint for split-service targets."""
    configured = instance.config.get("ravn_base_url") or instance.config.get("ravnBaseUrl")
    return str(configured).strip() if configured else instance.base_url


def create_ravn_session_proxy_router(service: InstanceService) -> APIRouter:
    """Proxy Yggdrasil Ravn chat sockets to their registry-owned target."""
    router = APIRouter(tags=["Ravn"])

    @router.websocket("/s/{session_id}/session")
    async def proxy_ravn_session(websocket: WebSocket, session_id: str) -> None:
        user_id, tenant_id, roles = _proxy_ws_identity(websocket)
        if not user_id:
            await websocket.close(code=1008, reason="Not authorized for this session")
            return

        principal = Principal(
            user_id=user_id,
            email="",
            tenant_id=tenant_id or "",
            roles=list(roles),
        )
        instance_hint = str(websocket.query_params.get("instance_id") or "").strip()
        if instance_hint:
            try:
                instances = [await _resolve_target_instance(service, principal, instance_hint)]
            except HTTPException:
                await websocket.close(code=1008, reason="Target is not visible")
                return
        else:
            instances = await _visible_instances(service, principal)

        headers = _proxy_forward_headers(
            websocket,
            include_cookie=False,
            forward_dev_params=True,
        )
        token = _bearer_token_from_ws(websocket)
        if token and not any(key.lower() == "authorization" for key in headers):
            headers["authorization"] = f"Bearer {token}"

        owner: RegisteredInstance | None = None
        async with httpx.AsyncClient(timeout=15.0) as client:
            for instance in instances:
                target_base = _ravn_base_url(instance).rstrip("/")
                try:
                    response = await client.get(
                        f"{target_base}{_RAVN_REMOTE_PREFIX}/sessions/{quote(session_id, safe='')}",
                        headers=headers,
                    )
                except httpx.HTTPError:
                    continue
                if response.status_code == status.HTTP_200_OK:
                    owner = instance
                    break

        if owner is None:
            await websocket.close(code=4410, reason="Session is no longer running")
            return

        parsed = urlsplit(owner.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            await websocket.close(code=1011, reason="Target has no public endpoint")
            return
        query = urlencode(
            [
                (key, value)
                for key, value in websocket.query_params.multi_items()
                if key != "instance_id"
            ]
        )
        connect_url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                f"/s/{quote(session_id, safe='')}/session",
                query,
                "",
            )
        )
        try:
            await bridge_websocket(
                websocket,
                connect_url,
                additional_headers=headers,
                include_cookie=False,
                forward_dev_params=True,
            )
        except Exception:
            logger.debug("Remote Ravn socket ended for %s", session_id)
        finally:
            with suppress(Exception):
                await websocket.close()

    @router.websocket("/s/{ravn_id}/sessions/{session_id}/session")
    async def proxy_resident_session(
        websocket: WebSocket,
        ravn_id: str,
        session_id: str,
    ) -> None:
        user_id, tenant_id, roles = _proxy_ws_identity(websocket)
        if not user_id:
            await websocket.close(code=1008, reason="Not authorized for this resident")
            return
        principal = Principal(
            user_id=user_id,
            email="",
            tenant_id=tenant_id or "",
            roles=list(roles),
        )
        instance_hint = str(websocket.query_params.get("instance_id") or "").strip()
        if instance_hint:
            try:
                instances = [await _resolve_target_instance(service, principal, instance_hint)]
            except HTTPException:
                await websocket.close(code=1008, reason="Target is not visible")
                return
        else:
            instances = await _visible_instances(service, principal)

        headers = _proxy_forward_headers(
            websocket,
            include_cookie=False,
            forward_dev_params=True,
        )
        token = _bearer_token_from_ws(websocket)
        if token and not any(key.lower() == "authorization" for key in headers):
            headers["authorization"] = f"Bearer {token}"

        owner: RegisteredInstance | None = None
        async with httpx.AsyncClient(timeout=15.0) as client:
            for instance in instances:
                try:
                    response = await client.get(
                        f"{_ravn_base_url(instance).rstrip('/')}{_RAVN_REMOTE_PREFIX}/ravens/"
                        f"{quote(ravn_id, safe='')}",
                        headers=headers,
                    )
                except httpx.HTTPError:
                    continue
                if response.status_code == status.HTTP_200_OK:
                    owner = instance
                    break
        if owner is None:
            await websocket.close(code=4410, reason="Resident is no longer available")
            return

        parsed = urlsplit(owner.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            await websocket.close(code=1011, reason="Target has no public endpoint")
            return
        query = urlencode(
            [
                (key, value)
                for key, value in websocket.query_params.multi_items()
                if key != "instance_id"
            ]
        )
        connect_url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                "/api/v1/forge/resident-runtimes/"
                f"{quote(ravn_id, safe='')}/sessions/{quote(session_id, safe='')}/chat",
                query,
                "",
            )
        )
        try:
            await bridge_websocket(
                websocket,
                connect_url,
                additional_headers=headers,
                include_cookie=False,
                forward_dev_params=True,
            )
        except Exception:
            logger.debug("Remote resident socket ended for %s/%s", ravn_id, session_id)
        finally:
            with suppress(Exception):
                await websocket.close()

    return router


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
                    base_url=_ravn_base_url(instance),
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
                item_id = str(item.get("id") or "")
                merged[f"{instance.id}:{item_id}"] = _with_instance(
                    item,
                    instance,
                    rebase_chat_endpoint=False,
                )

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
        instance_id: str | None = None,
        params: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        if instance_id is not None:
            instances = [
                await _resolve_target_instance(
                    service,
                    principal,
                    instance_id,
                )
            ]
        else:
            instances = await _visible_instances(service, principal)
        for instance in instances:
            response = await _request_remote(
                instance,
                request,
                method="GET",
                path=path,
                remote_prefix=_RAVN_REMOTE_PREFIX,
                base_url=_ravn_base_url(instance),
                params=params,
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
                return _with_instance(payload, instance, rebase_chat_endpoint=False)
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

    @router.post("/ravens", status_code=status.HTTP_201_CREATED)
    async def create_raven(
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        """Deploy a resident on one visible target."""
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
            path="/ravens",
            remote_prefix=_RAVN_REMOTE_PREFIX,
            base_url=_ravn_base_url(instance),
            json_body=_strip_instance_hints(body),
            embedded_app=embedded_forge_app,
            timeout=_RESIDENT_COMMAND_TIMEOUT_SECONDS,
        )
        _ensure_remote_success(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unexpected response from target Ravn instance",
            )
        return _with_instance(payload, instance, rebase_chat_endpoint=False)

    @router.get("/sessions")
    async def list_ravn_sessions(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        """Aggregate live ravn sessions across visible instances."""
        return await _aggregate_list(request, principal, "/sessions")

    @router.get("/deployment-profiles")
    async def list_deployment_profiles(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        """List target-compatible resident deployment profiles."""
        return await _aggregate_list(request, principal, "/deployment-profiles")

    @router.get("/ravens/{ravn_id}")
    async def get_raven(
        request: Request,
        ravn_id: str = Path(description="Resident ravn identifier"),
        instance_id: str | None = Query(default=None),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        """Return one resident ravn from whichever instance owns it."""
        return await _find_owner_payload(
            request,
            principal,
            f"/ravens/{ravn_id}",
            f"Ravn not found: {ravn_id}",
            instance_id,
        )

    async def _control_raven(
        request: Request,
        principal: Principal,
        ravn_id: str,
        action: str,
        instance_id: str,
    ) -> dict[str, Any]:
        instance = await _resolve_target_instance(service, principal, instance_id)
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/ravens/{ravn_id}/{action}",
            remote_prefix=_RAVN_REMOTE_PREFIX,
            base_url=_ravn_base_url(instance),
            embedded_app=embedded_forge_app,
            timeout=_RESIDENT_COMMAND_TIMEOUT_SECONDS,
        )
        _ensure_remote_success(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unexpected response from target Ravn instance",
            )
        return _with_instance(payload, instance, rebase_chat_endpoint=False)

    @router.post("/ravens/{ravn_id}/restart")
    async def restart_raven(
        request: Request,
        ravn_id: str,
        instance_id: str = Query(description="Owning target instance UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        return await _control_raven(request, principal, ravn_id, "restart", instance_id)

    @router.get("/ravens/{ravn_id}/logs")
    async def get_raven_logs(
        request: Request,
        ravn_id: str,
        instance_id: str | None = Query(default=None),
        lines: int = Query(default=200, ge=1, le=5000),
        source: list[str] = Query(default_factory=list),
        min_level: str = Query(default="", max_length=32),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        """Return normalized resident logs from the owning target."""
        params = [
            ("lines", str(lines)),
            *[("source", value) for value in source],
        ]
        if min_level:
            params.append(("min_level", min_level))
        return await _find_owner_payload(
            request,
            principal,
            f"/ravens/{ravn_id}/logs",
            f"Ravn not found: {ravn_id}",
            instance_id,
            params,
        )

    @router.get("/ravens/{ravn_id}/sessions")
    async def list_resident_sessions(
        request: Request,
        ravn_id: str,
        instance_id: str | None = Query(default=None),
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        """List native sessions from the resident-owning target."""
        if instance_id:
            instance = await _resolve_target_instance(service, principal, instance_id)
            response = await _request_remote(
                instance,
                request,
                method="GET",
                path=f"/ravens/{ravn_id}/sessions",
                remote_prefix=_RAVN_REMOTE_PREFIX,
                base_url=_ravn_base_url(instance),
                embedded_app=embedded_forge_app,
            )
            _ensure_remote_success(response)
            payload = response.json()
            if not isinstance(payload, list):
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Unexpected session payload")
            return [
                _with_instance(item, instance, rebase_chat_endpoint=False)
                for item in payload
                if isinstance(item, dict)
            ]
        raven = await _find_owner_payload(
            request,
            principal,
            f"/ravens/{ravn_id}",
            f"Ravn not found: {ravn_id}",
            None,
        )
        return await list_resident_sessions(
            request,
            ravn_id,
            str(raven.get("instance_id") or raven.get("instanceId") or ""),
            principal,
        )

    @router.post(
        "/ravens/{ravn_id}/sessions",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_resident_session(
        request: Request,
        ravn_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        instance_id: str = Query(description="Owning target instance UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance = await _resolve_target_instance(service, principal, instance_id)
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/ravens/{ravn_id}/sessions",
            remote_prefix=_RAVN_REMOTE_PREFIX,
            base_url=_ravn_base_url(instance),
            json_body=body,
            embedded_app=embedded_forge_app,
            timeout=_RESIDENT_COMMAND_TIMEOUT_SECONDS,
        )
        _ensure_remote_success(response)
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Unexpected session payload")
        return _with_instance(payload, instance, rebase_chat_endpoint=False)

    @router.delete(
        "/ravens/{ravn_id}/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_resident_session(
        request: Request,
        ravn_id: str,
        session_id: str,
        instance_id: str = Query(description="Owning target instance UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        instance = await _resolve_target_instance(service, principal, instance_id)
        response = await _request_remote(
            instance,
            request,
            method="DELETE",
            path=f"/ravens/{ravn_id}/sessions/{session_id}",
            remote_prefix=_RAVN_REMOTE_PREFIX,
            base_url=_ravn_base_url(instance),
            embedded_app=embedded_forge_app,
            timeout=_RESIDENT_COMMAND_TIMEOUT_SECONDS,
        )
        _ensure_remote_success(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/ravens/{ravn_id}/suspend")
    async def suspend_raven(
        request: Request,
        ravn_id: str,
        instance_id: str = Query(description="Owning target instance UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        return await _control_raven(request, principal, ravn_id, "suspend", instance_id)

    @router.post("/ravens/{ravn_id}/resume")
    async def resume_raven(
        request: Request,
        ravn_id: str,
        instance_id: str = Query(description="Owning target instance UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        return await _control_raven(request, principal, ravn_id, "resume", instance_id)

    @router.delete(
        "/ravens/{ravn_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_raven(
        request: Request,
        ravn_id: str,
        instance_id: str = Query(description="Owning target instance UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        instance = await _resolve_target_instance(service, principal, instance_id)
        response = await _request_remote(
            instance,
            request,
            method="DELETE",
            path=f"/ravens/{ravn_id}",
            remote_prefix=_RAVN_REMOTE_PREFIX,
            base_url=_ravn_base_url(instance),
            embedded_app=embedded_forge_app,
            timeout=_RESIDENT_COMMAND_TIMEOUT_SECONDS,
        )
        _ensure_remote_success(response)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/sessions/{session_id}")
    async def get_ravn_session(
        request: Request,
        session_id: str = Path(description="Ravn session identifier"),
        instance_id: str | None = Query(default=None),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        """Return one live ravn session from whichever instance owns it."""
        return await _find_owner_payload(
            request,
            principal,
            f"/sessions/{session_id}",
            f"Session not found: {session_id}",
            instance_id,
        )

    return router

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
import json
import logging
from contextlib import suppress
from typing import Any
from urllib.parse import SplitResult, quote, urlencode, urlsplit, urlunsplit
from uuid import UUID

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
    WebSocketDisconnect,
    status,
)
from starlette.types import ASGIApp

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.remote_urls import build_remote_url, forward_identity_headers
from niuu.adapters.inbound.rest_volundr import (
    _ensure_remote_success,
    _normalize_timestamp,
    _query_params,
    _request_remote,
    _resolve_target_instance,
    _strip_instance_hints,
    _sync_persona_to_instance,
    _sync_realm_to_instance,
    _visible_instances,
    _with_instance,
)
from niuu.adapters.inbound.source_health import (
    instance_source_failures,
    set_source_health_header,
)
from niuu.adapters.outbound.guild_transport import (
    GuildTLSPinMismatchError,
    GuildTransportError,
    GuildTransportUnreachableError,
    build_guild_httpx_client,
    resolve_guild_ws_ssl,
)
from niuu.domain.models import Principal, RegisteredInstance
from niuu.domain.services.instances import InstanceService
from niuu.session_proxy import (
    _bearer_token_from_ws,
    _proxy_principal,
    _without_dev_params,
    bridge_websocket,
)

_RAVN_REMOTE_PREFIX = "/api/v1/ravn"
_RESIDENT_COMMAND_TIMEOUT_SECONDS = 900.0
logger = logging.getLogger(__name__)


def _ravn_base_url(instance: RegisteredInstance) -> str:
    """Resolve an optional Ravn service endpoint for split-service targets."""
    configured = instance.config.get("ravn_base_url") or instance.config.get("ravnBaseUrl")
    return str(configured).strip() if configured else instance.base_url


def _safe_log_value(value: str) -> str:
    """Keep request identifiers from forging additional log records."""
    return value.replace("\r", "").replace("\n", "")


def _ws_close_reason(exc: GuildTransportError) -> str:
    """A short, WS-close-safe (<=123 bytes) reason; the full detail is logged."""
    if isinstance(exc, GuildTLSPinMismatchError):
        return "Remote certificate does not match its configured pin"
    if isinstance(exc, GuildTransportUnreachableError):
        return "Remote instance was unreachable for its certificate pin"
    return "Remote instance transport is not permitted"


async def _ws_connect_kwargs(
    owner: RegisteredInstance,
    parsed_owner_url: SplitResult,
    websocket: WebSocket,
    *,
    timeout: float,
) -> dict[str, object] | None:
    """Resolve ``websockets.connect`` kwargs for *owner*, enforcing transport
    policy and pinning TLS when configured, against the URL the bridge is
    actually about to dial (*parsed_owner_url*, i.e. ``owner.base_url``).

    Returns ``None`` (after closing *websocket* with the reason) when *owner*
    fails the https-unless-allow_plaintext policy, or pins a
    ``tls_fingerprint`` whose live certificate does not match — the bridge
    must never be attempted on a connection that failed either check.
    """
    try:
        ssl_context = await resolve_guild_ws_ssl(
            owner, dial_url=parsed_owner_url.geturl(), timeout=timeout
        )
    except GuildTransportError as exc:
        logger.warning(
            "Guild transport refused bridging to instance %s: %s",
            _safe_log_value(owner.id),
            _safe_log_value(str(exc)),
        )
        await websocket.close(code=1011, reason=_ws_close_reason(exc))
        return None
    if ssl_context is None:
        return {}
    return {"ssl": ssl_context}


def create_ravn_session_proxy_router(
    service: InstanceService,
    *,
    embedded_forge_app: ASGIApp | None = None,
    owner_probe_timeout_seconds: float = 15.0,
) -> APIRouter:
    """Proxy Yggdrasil Ravn chat sockets to their registry-owned target.

    A target may be a remote Guild instance on another machine, so only the
    caller's bearer token crosses the wire — never a client-supplied
    ``x-auth-*`` header or dev-identity query param, and never the identity
    this process resolved for its own local trust boundary (see
    ``niuu.adapters.inbound.remote_urls.forward_identity_headers``). The
    remote verifies the bearer itself.

    ``owner_probe_timeout_seconds`` bounds how long a single candidate
    instance's owner-probe HTTP GET and TLS-pin handshake may take,
    independent of anything an instance's own config sets — sourced from
    ``Settings.guild_owner_probe_timeout_seconds``.
    """
    router = APIRouter(tags=["Ravn"])

    def _upstream_headers(websocket: WebSocket) -> dict[str, str]:
        headers = forward_identity_headers(websocket)
        token = _bearer_token_from_ws(websocket)
        if token and "authorization" not in headers:
            headers["authorization"] = f"Bearer {token}"
        return headers

    def _upstream_query(websocket: WebSocket) -> str:
        return urlencode(
            _without_dev_params(
                [
                    (key, value)
                    for key, value in websocket.query_params.multi_items()
                    if key != "instance_id"
                ],
                dev_identity=False,
            )
        )

    async def _caller(websocket: WebSocket) -> Principal | None:
        principal = await _proxy_principal(websocket)
        if principal is None or not principal.user_id:
            return None
        return principal

    @router.websocket("/s/{session_id}/session")
    async def proxy_ravn_session(websocket: WebSocket, session_id: str) -> None:
        principal = await _caller(websocket)
        if principal is None:
            await websocket.close(code=1008, reason="Not authorized for this session")
            return

        instance_hint = str(websocket.query_params.get("instance_id") or "").strip()
        if instance_hint:
            try:
                instances = [await _resolve_target_instance(service, principal, instance_hint)]
            except HTTPException:
                await websocket.close(code=1008, reason="Target is not visible")
                return
        else:
            instances = await _visible_instances(service, principal)

        headers = _upstream_headers(websocket)

        owner: RegisteredInstance | None = None
        transport_failure: GuildTransportError | None = None
        for instance in instances:
            ravn_dial_url = _ravn_base_url(instance)
            try:
                target_url = build_remote_url(
                    ravn_dial_url,
                    _RAVN_REMOTE_PREFIX,
                    f"/sessions/{quote(session_id, safe='')}",
                )
                client = await build_guild_httpx_client(
                    instance,
                    dial_url=ravn_dial_url,
                    timeout_seconds=owner_probe_timeout_seconds,
                )
                async with client:
                    response = await client.get(target_url, headers=headers)
            except GuildTransportUnreachableError:
                # "We could not check" is an ordinary connection failure for
                # this candidate, not a policy/pin refusal — try the next one
                # exactly like a plain httpx.HTTPError would.
                continue
            except GuildTransportError as exc:
                transport_failure = exc
                logger.warning(
                    "Guild transport refused probing instance %s for session %s: %s",
                    _safe_log_value(instance.id),
                    _safe_log_value(session_id),
                    _safe_log_value(str(exc)),
                )
                continue
            except (ValueError, httpx.HTTPError):
                continue
            if response.status_code == status.HTTP_200_OK:
                owner = instance
                break

        if owner is None:
            if transport_failure is not None:
                await websocket.close(code=1011, reason=_ws_close_reason(transport_failure))
                return
            await websocket.close(code=4410, reason="Session is no longer running")
            return

        parsed = urlsplit(owner.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            await websocket.close(code=1011, reason="Target has no public endpoint")
            return
        connect_kwargs = await _ws_connect_kwargs(
            owner, parsed, websocket, timeout=owner_probe_timeout_seconds
        )
        if connect_kwargs is None:
            return
        connect_url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                f"/s/{quote(session_id, safe='')}/session",
                _upstream_query(websocket),
                "",
            )
        )
        try:
            await bridge_websocket(
                websocket, connect_url, headers=headers, connect_kwargs=connect_kwargs
            )
        except Exception:
            logger.debug("Remote Ravn socket ended for %s", _safe_log_value(session_id))
        finally:
            with suppress(Exception):
                await websocket.close()

    @router.websocket("/s/{ravn_id}/sessions/{session_id}/session")
    async def proxy_resident_session(
        websocket: WebSocket,
        ravn_id: str,
        session_id: str,
    ) -> None:
        principal = await _caller(websocket)
        if principal is None:
            await websocket.close(code=1008, reason="Not authorized for this resident")
            return
        instance_hint = str(websocket.query_params.get("instance_id") or "").strip()
        if instance_hint:
            try:
                instances = [await _resolve_target_instance(service, principal, instance_hint)]
            except HTTPException:
                await websocket.close(code=1008, reason="Target is not visible")
                return
        else:
            instances = await _visible_instances(service, principal)

        headers = _upstream_headers(websocket)

        owner: RegisteredInstance | None = None
        embedded_connection: Any | None = None
        transport_failure: GuildTransportError | None = None
        for instance in instances:
            if instance.config.get("transport") == "embedded":
                resident_service = getattr(
                    getattr(embedded_forge_app, "state", None),
                    "resident_runtime_service",
                    None,
                )
                if resident_service is None:
                    continue
                try:
                    embedded_connection = await resident_service.connect_chat(
                        principal,
                        UUID(ravn_id),
                        UUID(session_id),
                    )
                except Exception:
                    continue
                owner = instance
                break
            ravn_dial_url = _ravn_base_url(instance)
            try:
                target_url = build_remote_url(
                    ravn_dial_url,
                    _RAVN_REMOTE_PREFIX,
                    f"/ravens/{quote(ravn_id, safe='')}",
                )
                client = await build_guild_httpx_client(
                    instance,
                    dial_url=ravn_dial_url,
                    timeout_seconds=owner_probe_timeout_seconds,
                )
                async with client:
                    response = await client.get(target_url, headers=headers)
            except GuildTransportUnreachableError:
                # "We could not check" is an ordinary connection failure for
                # this candidate, not a policy/pin refusal — try the next one
                # exactly like a plain httpx.HTTPError would.
                continue
            except GuildTransportError as exc:
                transport_failure = exc
                logger.warning(
                    "Guild transport refused probing instance %s for resident %s: %s",
                    _safe_log_value(instance.id),
                    _safe_log_value(ravn_id),
                    _safe_log_value(str(exc)),
                )
                continue
            except (ValueError, httpx.HTTPError):
                continue
            if response.status_code == status.HTTP_200_OK:
                owner = instance
                break
        if owner is None:
            if transport_failure is not None:
                await websocket.close(code=1011, reason=_ws_close_reason(transport_failure))
                return
            await websocket.close(code=4410, reason="Resident is no longer available")
            return

        if embedded_connection is not None:
            await websocket.accept()

            async def browser_to_resident() -> None:
                while True:
                    await embedded_connection.send(json.loads(await websocket.receive_text()))

            async def resident_to_browser() -> None:
                while True:
                    await websocket.send_json(await embedded_connection.receive())

            tasks = [
                asyncio.create_task(browser_to_resident()),
                asyncio.create_task(resident_to_browser()),
            ]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            except WebSocketDisconnect:
                pass
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await embedded_connection.close()
                with suppress(Exception):
                    await websocket.close()
            return

        parsed = urlsplit(owner.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            await websocket.close(code=1011, reason="Target has no public endpoint")
            return
        connect_kwargs = await _ws_connect_kwargs(
            owner, parsed, websocket, timeout=owner_probe_timeout_seconds
        )
        if connect_kwargs is None:
            return
        connect_url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                "/api/v1/forge/resident-runtimes/"
                f"{quote(ravn_id, safe='')}/sessions/{quote(session_id, safe='')}/chat",
                _upstream_query(websocket),
                "",
            )
        )
        try:
            await bridge_websocket(
                websocket, connect_url, headers=headers, connect_kwargs=connect_kwargs
            )
        except Exception:
            logger.debug(
                "Remote resident socket ended for %s/%s",
                _safe_log_value(ravn_id),
                _safe_log_value(session_id),
            )
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
        *,
        response: Response,
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
        set_source_health_header(response, instance_source_failures(instances, results))

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
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        """Aggregate discovered resident ravns across visible instances."""
        return await _aggregate_list(request, principal, "/ravens", response=response)

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
        await _sync_persona_to_instance(
            instance,
            request,
            principal,
            body.get("persona_name") or body.get("personaName"),
            embedded_app=embedded_forge_app,
        )
        await _sync_realm_to_instance(
            instance,
            request,
            body.get("realm_id") or body.get("realmId"),
            embedded_app=embedded_forge_app,
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
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        """Aggregate live ravn sessions across visible instances."""
        return await _aggregate_list(request, principal, "/sessions", response=response)

    @router.get("/deployment-profiles")
    async def list_deployment_profiles(
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        """List target-compatible resident deployment profiles."""
        return await _aggregate_list(request, principal, "/deployment-profiles", response=response)

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
            f"/ravens/{quote(ravn_id, safe='')}",
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
            path=f"/ravens/{quote(ravn_id, safe='')}/{action}",
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
            f"/ravens/{quote(ravn_id, safe='')}/logs",
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
                path=f"/ravens/{quote(ravn_id, safe='')}/sessions",
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
            f"/ravens/{quote(ravn_id, safe='')}",
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
            path=f"/ravens/{quote(ravn_id, safe='')}/sessions",
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
            path=(f"/ravens/{quote(ravn_id, safe='')}/sessions/{quote(session_id, safe='')}"),
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
            path=f"/ravens/{quote(ravn_id, safe='')}",
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
            f"/sessions/{quote(session_id, safe='')}",
            f"Session not found: {session_id}",
            instance_id,
        )

    return router

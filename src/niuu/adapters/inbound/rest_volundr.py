"""Registry-backed Forge runtime facade for the shared Niuu shell."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote, urlsplit, urlunsplit
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
    WebSocketException,
    status,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp

from niuu.adapters.inbound.auth import extract_principal, presented_session_claims
from niuu.adapters.inbound.forge_notification_feed import (
    CursorError,
    InstancePage,
    decode_cursor,
    encode_cursor,
    merge_after,
    merge_newest,
)
from niuu.adapters.inbound.forge_session_stream import merge_events, remote_events
from niuu.adapters.inbound.remote_urls import (
    build_remote_url,
)
from niuu.adapters.inbound.remote_urls import (
    forward_identity_headers as _forward_headers,
)
from niuu.adapters.inbound.remote_urls import (
    forward_local_identity_headers as _forward_local_headers,
)
from niuu.adapters.inbound.remote_urls import (
    forward_websocket_identity_headers as _forward_websocket_headers,
)
from niuu.adapters.inbound.source_health import (
    instance_source_failures,
    set_source_health_header,
)
from niuu.adapters.inbound.ws_forge_replay import forward_replay
from niuu.adapters.outbound.guild_transport import (
    GuildTransportError,
    build_guild_httpx_client,
)
from niuu.domain.guild_membership import GuildInstance, GuildMembership
from niuu.domain.models import InstanceKind, Principal, RegisteredInstance
from niuu.domain.notifications import notification_visible_to
from niuu.domain.services.forge_session_policy import policy_refusal
from niuu.domain.services.instances import InstanceService
from niuu.domain.services.token_scope import FORGE_SESSION_TOKEN_USE
from niuu.forge_mcp.credentials import token_scopes

UNAVAILABLE_INSTANCES_HEADER = "X-Forge-Unavailable-Instances"
#: The Forge-hosted MCP endpoint, under the Forge API prefix on every node.
MCP_PATH = "/mcp"
NOTIFICATION_EVENT = "session_notification"
# Gateway-only selectors: never forwarded to a Forge node, whose ids differ.
_FLEET_SELECTORS = frozenset({"instance_id", "all_instances", "scope"})

_DELIVERY_POST_PATHS = frozenset(
    {
        "refs/resolve",
        "forge/reviews",
        "forge/branches",
        "workspaces/allocate",
        "workspaces/verify",
        "workspaces/integration/inspect",
        "workspaces/integration/inspect-chain",
        "evidence/policy",
        "evidence/validate",
        "workspaces/integrate",
        "forge/inspect",
        "forge/merge",
        "forge/reconcile",
    }
)


class SessionProjectAssignment(BaseModel):
    """Attach, move, re-parent or detach a session (see the host contract).

    ``parent`` may name its host by Guild instance id or slug; the facade translates it
    to that host's project instance id before forwarding.
    """

    model_config = ConfigDict(extra="forbid")
    project_id: UUID | None = None
    project_instance_id: str | None = Field(default=None, min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    role: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    parent: dict[str, Any] | None = None


#: Public project fields a metadata replica carries; never a checkout, scope or credential.
_PROJECT_REPLICA_FIELDS = ("id", "slug", "name", "description", "brief", "repo_url")
#: Fields refreshed on a replica from its home copy before a launch or assignment.
_PROJECT_SYNC_FIELDS = ("name", "description", "brief", "repo_url", "status")


LOCAL_NODE_ONLY_DETAIL = (
    "Forge session tokens are local credentials: peer supervision is limited to the "
    "session's own Forge node, so this request cannot go to instance {instance_id}. "
    "Use a PAT or your own identity to act on other nodes."
)
#: WebSocket close code mirroring HTTP 403, and the close-frame reason bound.
_WS_FORBIDDEN = 4403
_WS_REASON_CHARS = 120


def _is_session_principal(principal: Principal) -> bool:
    return principal.token_use == FORGE_SESSION_TOKEN_USE


def _local_only(instances: list[RegisteredInstance]) -> list[RegisteredInstance]:
    return [instance for instance in instances if _uses_embedded_transport(instance)]


async def _visible_instances(
    service: InstanceService,
    principal: Principal,
) -> list[RegisteredInstance]:
    instances = await service.list_visible(
        principal,
        kind=InstanceKind.VOLUNDR,
        enabled_only=True,
    )
    # A session credential is only valid on its own (the local) node.
    return _local_only(instances) if _is_session_principal(principal) else instances


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
    # The selector belongs to this gateway's registry. A downstream Forge has
    # its own IDs; forwarding ours makes it look up a target that does not exist.
    return [
        (key, value) for key, value in request.query_params.multi_items() if key != "instance_id"
    ]


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


def _upstream_detail(response: httpx.Response) -> str:
    """The upstream's own message: the ``detail`` of a FastAPI error body,
    otherwise the body text, otherwise the reason phrase. Forwarding the raw
    JSON body used to wrap the message in a second ``{"detail": ...}`` layer,
    which the clients then showed verbatim."""
    text = response.text.strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
            return payload["detail"]
    return text or response.reason_phrase


def _ensure_remote_success(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    raise HTTPException(status_code=response.status_code, detail=_upstream_detail(response)[:1000])


def _ensure_history_success(response: httpx.Response) -> None:
    """Preserve machine-readable history recovery through the aggregate facade."""
    if response.status_code < 400:
        return
    try:
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
    except ValueError:
        detail = None
    if isinstance(detail, dict) and detail.get("code") in {
        "history_cursor_invalid",
        "history_busy",
        "history_page_too_large",
    }:
        headers = {"Retry-After": "1"} if detail["code"] == "history_busy" else None
        raise HTTPException(response.status_code, detail, headers=headers)
    _ensure_remote_success(response)


def _event_visible(name: str, data: Any, principal: Principal) -> bool:
    """Owner-scope ``session_notification`` events; every other event passes."""
    if name != NOTIFICATION_EVENT:
        return True
    if not isinstance(data, dict):
        return False
    return notification_visible_to(
        user_id=principal.user_id,
        roles=principal.roles,
        tenant_id=principal.tenant_id,
        owner_id=data.get("owner_id"),
        notification_tenant_id=data.get("tenant_id"),
    )


def _fleet_request(request: Request) -> bool:
    params = request.query_params
    return (
        params.get("all_instances") == "true"
        and not params.get("instance_id")
        and params.get("scope", "guild") != "local"
    )


def _forward_params(request: Request, *, drop: frozenset[str]) -> list[tuple[str, str]]:
    return [(key, value) for key, value in request.query_params.multi_items() if key not in drop]


def _remote_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Forge returned invalid JSON") from exc


def _tag_items(payload: Any, instance: RegisteredInstance) -> Any:
    if not isinstance(payload, list):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Unexpected Forge notification response")
    return [_with_instance(item, instance, rebase_chat_endpoint=False) for item in payload]


def _instance_page(response: httpx.Response, instance: RegisteredInstance) -> InstancePage | None:
    """Parse one node's feed page, or ``None`` when it is malformed."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return None
    counters = [payload.get(key) for key in ("head_seq", "read_through_seq", "unread_count")]
    next_before = payload.get("next_before")
    if any(type(value) is not int for value in counters):
        return None
    if next_before is not None and type(next_before) is not int:
        return None
    return InstancePage(
        items=[
            _with_instance(item, instance, rebase_chat_endpoint=False)
            for item in payload["items"]
            if isinstance(item, dict) and type(item.get("seq")) is int
        ],
        next_before=next_before,
        head_seq=counters[0],
        read_through_seq=counters[1],
        unread_count=counters[2],
        revision=payload.get("revision") if type(payload.get("revision")) is int else None,
    )


def _optional_limit(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "limit must be an integer"
        ) from exc
    if value < 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "limit must be at least 1")
    return value


def _is_client_error(response: httpx.Response) -> bool:
    """A request error every node would repeat (bad filter or body), not an outage."""
    return response.status_code in {
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    }


#: Bound on an upstream error message echoed into a device-route failure detail.
_DEVICE_FAILURE_DETAIL_CHARS = 200


def _raise_device_failures(
    action: str,
    instances: list[RegisteredInstance],
    responses: dict[str, httpx.Response],
    unavailable: set[str],
    *,
    accepted: Callable[[httpx.Response], bool],
) -> None:
    """Fail a device fan-out unless every instance accepted it.

    A device registry is per node, so a call that only some nodes honoured leaves
    the others without the device: that is reported, never returned as success.
    ``accepted`` says whether one node's answer counts as done. The first node
    that refused with a 4xx decides the status (its refusal would repeat on a
    retry); an unreachable node or a 5xx is a 502.
    """
    reasons: list[str] = []
    refusal: int | None = None
    for instance in instances:
        result = responses.get(instance.id)
        if instance.id in unavailable or result is None:
            reasons.append(f"{instance.id} (unreachable)")
            continue
        if accepted(result):
            continue
        detail = _upstream_detail(result)[:_DEVICE_FAILURE_DETAIL_CHARS]
        reasons.append(f"{instance.id} (HTTP {result.status_code}: {detail})")
        if refusal is None and status.HTTP_400_BAD_REQUEST <= result.status_code < 500:
            refusal = result.status_code
    if not reasons:
        return
    raise HTTPException(
        status_code=refusal or status.HTTP_502_BAD_GATEWAY,
        detail=f"Device {action} failed on Forge instance(s): {', '.join(reasons)}",
    )


def _set_unavailable(response: Response, unavailable: set[str]) -> None:
    if unavailable:
        response.headers[UNAVAILABLE_INSTANCES_HEADER] = ",".join(sorted(unavailable))


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
    embedded = _uses_embedded_transport(instance)
    if not embedded and presented_session_claims(request) is not None:
        # Never forward a session credential to another node: it is local, and the
        # remote node could not (and must not) honour it.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=LOCAL_NODE_ONLY_DETAIL.format(instance_id=instance.id),
        )
    # An embedded target shares this process (ASGITransport, no network hop),
    # so it is not a separate trust domain and gets the caller's full resolved
    # identity, same as today. A non-embedded target is a genuinely remote
    # Guild instance and gets only the bearer token — see
    # niuu.adapters.inbound.remote_urls.forward_identity_headers.
    headers = _forward_local_headers(request) if embedded else _forward_headers(request)
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
    if embedded:
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

    dial_url = base_url or instance.base_url
    try:
        remote_url = build_remote_url(dial_url, remote_prefix, path)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        client = await build_guild_httpx_client(
            instance, dial_url=dial_url, timeout_seconds=timeout
        )
    except GuildTransportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    async with client:
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


async def _sync_realm_to_instance(
    instance: RegisteredInstance,
    request: Request,
    principal: Principal,
    realm_id: object,
    *,
    embedded_app: ASGIApp | None,
) -> None:
    """Sync a realm from this host's local database onto a remote target.

    Deploying a resident bound to a realm (realm_id in the create body)
    forwards that id verbatim to the target instance, whose ResidentRuntime
    FK requires a matching realms row on ITS OWN database. Realms otherwise
    only exist wherever they were created (e.g. ymir), so without this a
    resident deployed on a different instance (e.g. noatun, valhalla) fails
    at create with "Realm not found" and is left half-made. This makes the
    same realm identity (id + slug) exist on the target first — the same
    "materialize the source of truth on the remote before launch" pattern
    _sync_persona_to_instance uses for personas.

    Raises on any failure: a realm_id was explicitly requested, so a resident
    created without it actually being synced would silently drop the link
    the caller asked for (see .claude/rules/no-fallbacks.md) — worse than
    refusing the create.

    This syncs the realm ROW, not its Mímir charter page (realms/<slug>/
    charter.md, written by the create-realm wizard to whichever Mímir mount
    it targeted). The deployed resident resolves that page for itself at
    startup (environment.charter_mimir_page) and fails loudly if it cannot
    read it — see resident_runtime_wiring.py's _resolve_environment_charter.
    For that to succeed on a resident deployed on a DIFFERENT instance than
    the one the realm was created on, the target instance's resident
    deployment profile (resident.mimir.instances in its deployment.values)
    must itself be configured with a Mímir mount that can read the SAME
    underlying pages — either a Mímir shared/reachable across instances, or
    an operator-configured mount pointing at the realm-owning instance's
    Mímir. This function cannot discover or provision that network path; if
    it is not configured, the resident's own charter check raises with a
    precise "page does not exist" error naming the missing page, which is
    the intended fail-loud outcome documented here rather than solved here.
    """
    raw_id = str(realm_id or "").strip()
    if not raw_id:
        return
    if _uses_embedded_transport(instance):
        return  # The target IS this host; the realm already lives here.

    try:
        parsed_id = UUID(raw_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid realm id: {raw_id!r}",
        ) from exc

    realm_service = getattr(getattr(embedded_app, "state", None), "realm_service", None)
    if realm_service is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Cannot deploy a resident bound to realm {parsed_id}: this host has no "
                "local realm service to read it from before syncing it to the target "
                "instance."
            ),
        )
    realm = await realm_service.get_realm(parsed_id, principal.tenant_id)
    if realm is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Realm not found: {parsed_id}",
        )

    synced = await _request_remote(
        instance,
        request,
        method="PUT",
        path=f"/realms/by-id/{parsed_id}",
        remote_prefix="/api/v1",
        json_body={
            "slug": realm.slug,
            "name": realm.name,
            "sleipnir_domain": realm.sleipnir_domain,
            "owner_id": realm.owner_id,
            "instance_id": realm.instance_id,
            "autonomy_profile": realm.autonomy_profile,
        },
        embedded_app=embedded_app,
    )
    _ensure_remote_success(synced)


async def _local_session_instance(
    service: InstanceService,
    principal: Principal,
    request: Request,
) -> RegisteredInstance:
    """Resolve this shell's local runtime, never a default/remote guild member.

    Use the existing explicit embedded transport identity and normal visibility
    policy. A pure registry shell has no local sessions service: that is an error,
    not permission to silently query an arbitrary registered node.
    """
    local = [
        instance
        for instance in await _visible_instances(service, principal)
        if _uses_embedded_transport(instance)
    ]
    if len(local) != 1:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local sessions require exactly one visible, enabled embedded Forge runtime",
        )
    selected = request.query_params.get("instance_id")
    if selected and selected != local[0].id:
        raise HTTPException(422, "scope=local cannot select a different guild instance")
    return local[0]


def _local_session_scope(request: Request) -> bool:
    scope = request.query_params.get("scope", "guild")
    if scope not in {"guild", "local"}:
        raise HTTPException(422, "Session scope must be local or guild")
    return scope == "local"


async def _find_runtime_owner(
    service: InstanceService,
    principal: Principal,
    request: Request,
    path: str,
    not_found_detail: str,
    *,
    embedded_app: ASGIApp | None,
    rebase_chat_endpoint: bool = True,
    local_scope: bool = False,
    extra_headers: dict[str, str] | None = None,
) -> tuple[RegisteredInstance, dict[str, Any]]:
    async def probe(instance: RegisteredInstance) -> tuple[RegisteredInstance, httpx.Response]:
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=path,
            extra_headers=extra_headers,
            embedded_app=embedded_app,
            timeout=5.0,
        )
        return instance, response

    selected = request.query_params.get("instance_id")
    if local_scope:
        instances = [await _local_session_instance(service, principal, request)]
    elif selected:
        instances = [await _resolve_target_instance(service, principal, selected)]
    else:
        instances = await _visible_instances(service, principal)
    if not instances:
        # With nothing to search, "not found" would be a guess; say why.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No enabled Volundr instances are registered for this user",
        )
    tasks = [asyncio.create_task(probe(instance)) for instance in instances]
    failure: HTTPException | None = None
    try:
        # Ownership is unknown until a visible instance returns the resource.
        # An unrelated offline instance must not delay a healthy owner's reply.
        for task in asyncio.as_completed(tasks):
            try:
                instance, response = await task
            except httpx.RequestError:
                failure = HTTPException(502, "An instance was unreachable during owner lookup")
                continue
            except HTTPException as exc:
                failure = exc
                continue
            if response.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}:
                continue
            if response.is_error:
                failure = HTTPException(502, "An instance failed during owner lookup")
                continue
            payload = response.json()
            if isinstance(payload, dict):
                return instance, _with_instance(
                    payload, instance, rebase_chat_endpoint=rebase_chat_endpoint
                )
        if failure is not None:
            # A partial search cannot establish that the resource is absent.
            raise failure
        raise HTTPException(status_code=404, detail=not_found_detail)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _find_session_owner(
    service: InstanceService,
    principal: Principal,
    request: Request,
    session_id: str,
    *,
    embedded_app: ASGIApp | None = None,
    extra_headers: dict[str, str] | None = None,
) -> tuple[RegisteredInstance, dict[str, Any]]:
    return await _find_runtime_owner(
        service,
        principal,
        request,
        f"/sessions/{session_id}",
        f"Session not found: {session_id}",
        embedded_app=embedded_app,
        local_scope=_local_session_scope(request),
        extra_headers=extra_headers,
    )


async def _find_resident_owner(
    service: InstanceService,
    principal: Principal,
    request: Request,
    runtime_id: str,
    *,
    embedded_app: ASGIApp | None = None,
) -> tuple[RegisteredInstance, dict[str, Any]]:
    return await _find_runtime_owner(
        service,
        principal,
        request,
        f"/resident-runtimes/{runtime_id}",
        f"Resident runtime not found: {runtime_id}",
        embedded_app=embedded_app,
        rebase_chat_endpoint=False,
    )


async def _find_trace_owner(
    service: InstanceService,
    principal: Principal,
    request: Request,
    subject_id: str,
    *,
    embedded_app: ASGIApp | None = None,
) -> RegisteredInstance:
    try:
        instance, _ = await _find_session_owner(
            service,
            principal,
            request,
            subject_id,
            embedded_app=embedded_app,
        )
        return instance
    except HTTPException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise

    instance, _ = await _find_resident_owner(
        service,
        principal,
        request,
        subject_id,
        embedded_app=embedded_app,
    )
    return instance


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
    if _is_session_principal(principal):
        instances = _local_only(instances)
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


async def _session_route_guard(connection: HTTPConnection) -> None:
    """Apply the session-credential allow-list at the edge (Forge enforces it too)."""
    claims = presented_session_claims(connection)
    if claims is None:
        return
    websocket = connection.scope["type"] == "websocket"
    method = "GET" if websocket else str(connection.scope.get("method", ""))
    refusal = policy_refusal(method, connection.url.path, token_scopes(claims))
    if refusal is None:
        return
    if websocket:
        raise WebSocketException(code=_WS_FORBIDDEN, reason=refusal[:_WS_REASON_CHARS])
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=refusal)


def create_volundr_router(
    service: InstanceService,
    *,
    embedded_forge_app: ASGIApp | None = None,
    forge_stream_remote_timeout_seconds: float = 45.0,
    forge_stream_remote_connect_timeout_seconds: float = 5.0,
    forge_stream_retry_seconds: float = 5.0,
    forge_stream_keepalive_seconds: float = 15.0,
    forge_stream_queue_maxsize: int = 256,
    guild_membership: GuildMembership | None = None,
) -> APIRouter:
    """Create a registry-aware Forge runtime router.

    ``guild_membership`` is this host's ``guild:`` configuration: on a Guild, its
    own public origin, served at ``GET /guild`` with the node it fronts.
    """
    router = APIRouter(
        prefix="/api/v1/forge",
        tags=["Forge"],
        dependencies=[Depends(_session_route_guard)],
    )

    @router.post("/delivery/{operation:path}")
    async def delivery_operation(
        request: Request,
        operation: str,
        instance_id: str | None = Query(default=None),
        target_tags: list[str] | None = Query(default=None),
        target_match: str = Query(default="all"),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """Forward the typed coordinator delivery surface to one visible Forge."""
        if operation not in _DELIVERY_POST_PATHS:
            raise HTTPException(status_code=404, detail="Unknown delivery operation")
        instance = await _resolve_target_instance(
            service,
            principal,
            instance_id,
            tags=target_tags,
            match=target_match,
        )
        body = await request.body()
        content_type = request.headers.get("content-type")
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/delivery/{operation}",
            content_body=body,
            params=[
                (key, value)
                for key, value in request.query_params.multi_items()
                if key not in {"instance_id", "target_tags", "target_match"}
            ],
            extra_headers={"content-type": content_type} if content_type else None,
            embedded_app=embedded_forge_app,
        )
        forwarded_headers = {
            name: response.headers[name]
            for name in ("content-type", "retry-after", "www-authenticate")
            if name in response.headers
        }
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=forwarded_headers,
        )

    @router.get("/storage/home")
    @router.delete("/storage/home")
    async def manage_user_home(
        request: Request,
        instance_id: str = Query(...),
        path: str = Query(default=""),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        instance = await _resolve_target_instance(service, principal, instance_id)
        response = await _request_remote(
            instance,
            request,
            method=request.method,
            path="/storage/home",
            params=[("path", path)],
            embedded_app=embedded_forge_app,
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type="application/json",
        )

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

    @router.get("/resident-runtimes/{runtime_id}/a2a/.well-known/agent-card.json")
    async def get_resident_agent_card(
        request: Request,
        runtime_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """The resident's A2A Agent Card, from the target that runs it."""
        _, response = await _resident_owner_request(
            request,
            principal,
            runtime_id,
            method="GET",
            path_suffix="/a2a/.well-known/agent-card.json",
        )
        return Response(
            content=response.content,
            media_type="application/json",
            headers={"Cache-Control": response.headers.get("cache-control", "no-store")},
        )

    @router.post("/resident-runtimes/{runtime_id}/a2a")
    async def resident_a2a(
        request: Request,
        runtime_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """Relay one A2A JSON-RPC call to the target that runs the resident."""
        instance, _ = await _find_resident_owner(
            service,
            principal,
            request,
            runtime_id,
            embedded_app=embedded_forge_app,
        )
        version = request.headers.get("a2a-version", "")
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=f"/resident-runtimes/{runtime_id}/a2a",
            content_body=await request.body(),
            extra_headers={
                "Content-Type": "application/json",
                **({"A2A-Version": version} if version else {}),
            },
            embedded_app=embedded_forge_app,
            timeout=300.0,
        )
        _ensure_remote_success(response)
        return Response(content=response.content, media_type="application/json")

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

    async def _host_project_instance_id(
        instance: RegisteredInstance, request: Request
    ) -> str | None:
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path="/feature-flags",
            embedded_app=embedded_forge_app,
        )
        if response.status_code >= 400:
            return None
        try:
            value = response.json().get("project_instance_id")
        except (ValueError, AttributeError):
            return None
        return value if isinstance(value, str) and value else None

    async def _normalize_parent(request: Request, principal: Principal, parent: Any) -> Any:
        """Clients name hosts by Guild id or slug; project links use the host's project id."""
        if not isinstance(parent, dict):
            return parent
        value = parent.get("instance_id")
        if not isinstance(value, str) or not value:
            return parent
        instances = await _visible_instances(service, principal)
        match = next((item for item in instances if value in (item.id, item.slug)), None)
        if match is None:
            return parent
        project_instance = await _host_project_instance_id(match, request)
        return {**parent, "instance_id": project_instance} if project_instance else parent

    async def _locate_project(
        request: Request,
        principal: Principal,
        project_id: str,
        hint: str | None,
        exclude: RegisteredInstance,
    ) -> tuple[RegisteredInstance, dict[str, Any]]:
        """The authoritative copy of a project: the hinted host, else the first home copy."""
        path = f"/projects/{project_id}"
        if hint:
            holder = await _resolve_target_instance(service, principal, hint)
            response = await _request_remote(
                holder, request, method="GET", path=path, embedded_app=embedded_forge_app
            )
            _ensure_remote_success(response)
            return holder, response.json()
        candidates = [item for item in await _visible_instances(service, principal)]
        candidates.sort(key=lambda item: not item.is_default)
        found: list[tuple[RegisteredInstance, dict[str, Any]]] = []
        for candidate in candidates:
            if candidate.id == exclude.id:
                continue
            response = await _request_remote(
                candidate, request, method="GET", path=path, embedded_app=embedded_forge_app
            )
            if response.status_code != 200:
                continue
            payload = response.json()
            if isinstance(payload, dict):
                found.append((candidate, payload))
                if not payload.get("home_instance_id"):
                    return candidate, payload
        if found:
            return found[0]
        raise HTTPException(404, "Project not found on any Forge host")

    async def _ensure_project_on_instance(
        request: Request,
        principal: Principal,
        project_id: str,
        target: RegisteredInstance,
        *,
        hint: str | None = None,
    ) -> dict[str, Any]:
        """Make *target* know the project before a session there joins it.

        Missing: register a metadata replica pointing at its home host. Present as a
        replica: refresh its public fields from home (best effort; an unreachable home
        leaves the replica as it was). An independent registration is left alone.
        """
        path = f"/projects/{project_id}"
        local = await _request_remote(
            target, request, method="GET", path=path, embedded_app=embedded_forge_app
        )
        if local.status_code == 200:
            copy = local.json()
            home_id = copy.get("home_instance_id") if isinstance(copy, dict) else None
            if not home_id or home_id == target.id:
                return copy
            try:
                home, source = await _locate_project(
                    request, principal, project_id, home_id, target
                )
            except HTTPException:
                return copy
            changes = {
                key: source.get(key, "")
                for key in _PROJECT_SYNC_FIELDS
                if key in source and source.get(key, "") != copy.get(key, "")
            }
            if not changes:
                return copy
            refreshed = await _request_remote(
                target,
                request,
                method="PATCH",
                path=path,
                json_body={**changes, "revision": copy.get("revision", 1)},
                embedded_app=embedded_forge_app,
            )
            return refreshed.json() if refreshed.status_code == 200 else copy
        if local.status_code != 404:
            _ensure_remote_success(local)
        holder, project = await _locate_project(request, principal, project_id, hint, target)
        if not isinstance(project, dict) or str(project.get("id")) != str(project_id):
            raise HTTPException(502, "Project host returned a different project")
        if any(
            not isinstance(project.get(key), str) or not project[key] for key in ("slug", "name")
        ):
            raise HTTPException(502, "Project host returned incomplete project metadata")
        if project.get("status") != "active":
            raise HTTPException(409, "Restore the archived project before adding sessions")
        replica = {key: project[key] for key in _PROJECT_REPLICA_FIELDS if project.get(key)}
        replica.update(home_instance_id=holder.id, workspace_path="")
        registered = await _request_remote(
            target,
            request,
            method="POST",
            path="/projects",
            json_body=replica,
            embedded_app=embedded_forge_app,
        )
        if registered.status_code == 422 and project.get("repo_url"):
            # A host from before lightweight projects: send its version-1 shape.
            legacy = {key: project.get(key, "") for key in ("id", "slug", "name", "description")}
            legacy.update(repo_url=project["repo_url"], workspace_path="")
            registered = await _request_remote(
                target,
                request,
                method="POST",
                path="/projects",
                json_body=legacy,
                embedded_app=embedded_forge_app,
            )
        if registered.status_code == 422:
            raise HTTPException(
                501, "Update this session's Forge host to use projects without a repository"
            )
        if registered.status_code != 409:
            _ensure_remote_success(registered)
        # A simultaneous registration may have won; validate the actual record.
        existing = await _request_remote(
            target, request, method="GET", path=path, embedded_app=embedded_forge_app
        )
        _ensure_remote_success(existing)
        local_project = existing.json()
        if not isinstance(local_project, dict) or (
            str(local_project.get("id")),
            local_project.get("status"),
        ) != (str(project_id), "active"):
            raise HTTPException(409, "Project registration on the session host conflicts")
        return local_project

    @router.get("/projects")
    async def list_projects(
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        instances = await _visible_instances(service, principal)
        selected = request.query_params.get("instance_id")
        if selected:
            instances = [await _resolve_target_instance(service, principal, selected)]
        results = await asyncio.gather(
            *[
                _request_remote(
                    instance,
                    request,
                    method="GET",
                    path="/projects",
                    embedded_app=embedded_forge_app,
                )
                for instance in instances
            ],
            return_exceptions=True,
        )
        projects, unavailable = [], []
        successful = 0
        for instance, result in zip(instances, results, strict=True):
            if isinstance(result, Exception) or result.status_code >= 400:
                unavailable.append(instance.id)
                continue
            try:
                payload = result.json()
            except ValueError:
                unavailable.append(instance.id)
                continue
            if not isinstance(payload, list):
                unavailable.append(instance.id)
                continue
            successful += 1
            projects.extend(
                _with_instance(item, instance) for item in payload if isinstance(item, dict)
            )
        if instances and not successful:
            raise HTTPException(503, "No Forge host could load projects")
        if unavailable:
            response.headers["X-Forge-Unavailable-Instances"] = ",".join(unavailable)
        return projects

    @router.post("/projects", status_code=201)
    async def register_project(
        request: Request,
        body: dict[str, Any] = Body(...),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance = await _resolve_target_instance(service, principal, body.get("instance_id"))
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path="/projects",
            json_body=_strip_instance_hints(body),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        return _with_instance(response.json(), instance)

    async def _project_checkout_request(request, body, principal, path):
        instance = await _resolve_target_instance(
            service, principal, body.get("instance_id") or request.query_params.get("instance_id")
        )
        response = await _request_remote(
            instance,
            request,
            method="POST",
            path=path,
            json_body=_strip_instance_hints(body),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        return _with_instance(response.json(), instance)

    @router.post("/projects/discover")
    async def discover_project_checkout(
        request: Request,
        body: dict[str, Any] = Body(...),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        return await _project_checkout_request(request, body, principal, "/projects/discover")

    @router.post("/projects/connect", status_code=201)
    async def connect_project_checkout(
        request: Request,
        body: dict[str, Any] = Body(...),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        return await _project_checkout_request(request, body, principal, "/projects/connect")

    @router.get("/projects/{project_id}")
    @router.patch("/projects/{project_id}")
    @router.get("/projects/{project_id}/{operation:path}")
    @router.post("/projects/{project_id}/{operation:path}")
    async def project_operation(
        request: Request,
        project_id: UUID,
        operation: str = "",
        principal: Principal = Depends(extract_principal),
    ) -> Any:
        allowed = {
            ("GET", ""),
            ("PATCH", ""),
            ("GET", "context"),
            ("GET", "receipts"),
            ("POST", "receipts"),
            ("POST", "export"),
        }
        pieces = operation.split("/")
        is_ack = len(pieces) == 3 and pieces[0] == "receipts" and pieces[2] == "ack"
        if is_ack:
            try:
                UUID(pieces[1])
            except ValueError as exc:
                raise HTTPException(422, "Invalid receipt ID") from exc
        if (request.method, operation) not in allowed and not (request.method == "POST" and is_ack):
            raise HTTPException(404, "Unknown project operation")
        instance = await _resolve_target_instance(
            service,
            principal,
            request.query_params.get("instance_id"),
        )
        body = await request.body()
        try:
            document = json.loads(body) if body else None
        except ValueError as exc:
            raise HTTPException(422, "Project request must contain valid JSON") from exc
        if document is not None and not isinstance(document, dict):
            raise HTTPException(422, "Project request must be a JSON object")
        response = await _request_remote(
            instance,
            request,
            method=request.method,
            path=f"/projects/{project_id}" + (f"/{operation}" if operation else ""),
            params=_query_params(request),
            json_body=document,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return _with_instance(payload, instance) if not operation else payload

    @router.get("/sessions")
    async def list_sessions(
        request: Request,
        response: Response,
        scope: Literal["guild", "local"] = Query(
            default="guild", description="Guild aggregation, or only this server's embedded runtime"
        ),
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        if scope == "local":
            instance = await _local_session_instance(service, principal, request)
            result = await _request_remote(
                instance,
                request,
                method="GET",
                path="/sessions",
                params=_query_params(request),
                embedded_app=embedded_forge_app,
            )
            # Unlike best-effort guild aggregation, a failed local read must not
            # look like an empty successful list or fall through to remote nodes.
            _ensure_remote_success(result)
            try:
                payload = result.json()
            except ValueError as exc:
                raise HTTPException(502, "Local Forge sessions response is not JSON") from exc
            if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                raise HTTPException(502, "Unexpected local Forge sessions response")
            response.headers["X-Forge-Session-Scope"] = "local"
            return [_with_instance(item, instance) for item in payload]
        selected = request.query_params.get("instance_id")
        instances = (
            [await _resolve_target_instance(service, principal, selected)]
            if selected
            else await _visible_instances(service, principal)
        )
        # A session credential only reads its own node, so that node's refusal (a
        # revoked token, a missing scope) is the answer, not a skipped host.
        strict = bool(selected) or _is_session_principal(principal)
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
                if strict:
                    if isinstance(result, HTTPException):
                        raise result
                    raise HTTPException(
                        status_code=502, detail="Selected Forge host is unavailable"
                    ) from result
                continue
            if result.status_code >= 400:
                if strict:
                    _ensure_remote_success(result)
                continue
            try:
                payload = result.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=502, detail="Invalid Forge session response"
                ) from exc
            if not isinstance(payload, list):
                if selected:
                    raise HTTPException(status_code=502, detail="Invalid Forge session response")
                continue
            for item in payload:
                if selected and (not isinstance(item, dict) or not item.get("id")):
                    raise HTTPException(status_code=502, detail="Invalid Forge session response")
                if not isinstance(item, dict):
                    continue
                merged[f"{instance.id}:{item.get('id') or ''}"] = _with_instance(item, instance)

        sessions = [item for item in merged.values() if item.get("id")]
        sessions.sort(
            key=lambda item: _normalize_timestamp(
                item.get("last_active") or item.get("lastActive")
            ),
            reverse=True,
        )
        if not selected:
            set_source_health_header(response, instance_source_failures(instances, results))
        return sessions

    @router.get("/sessions/stream")
    async def stream_sessions(
        request: Request,
        scope: Literal["guild", "local"] = Query(default="guild"),
        principal: Principal = Depends(extract_principal),
    ) -> StreamingResponse:
        """Stream a selected/default host, or opt in to every visible Guild host.

        Remote reads deliberately omit all_instances so facade-to-facade
        connections never recursively fan out to each other's registries.
        Session credentials never get here: the stream is not on their allow-list.
        """
        selected = request.query_params.get("instance_id")
        fleet = (
            scope != "local"
            and request.query_params.get("all_instances") == "true"
            and not selected
        )
        instances = (
            [await _local_session_instance(service, principal, request)]
            if scope == "local"
            else await _visible_instances(service, principal)
            if fleet
            else [await _resolve_target_instance(service, principal, selected)]
        )
        headers = _forward_headers(request)
        # The embedded Forge's principal-scoped stream, never its raw broadcaster:
        # a subscriber only receives events for sessions it may list.
        local_stream = getattr(
            getattr(embedded_forge_app, "state", None), "session_event_stream", None
        )
        if not fleet and _uses_embedded_transport(instances[0]) and local_stream is None:
            raise HTTPException(status_code=503, detail="Session event stream unavailable")
        if local_stream is not None and any(map(_uses_embedded_transport, instances)):
            try:
                local_stream.authorize(principal)
            except PermissionError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc

        async def events(instance: RegisteredInstance) -> Any:
            if _uses_embedded_transport(instance):
                if local_stream is None:
                    raise RuntimeError("Session event stream unavailable")
                async for event in local_stream.subscribe(principal):
                    # Notifications are the owner's (or an admin's) only.
                    if not _event_visible(event.type.value, event.data, principal):
                        continue
                    yield event.type.value, _with_instance(event.data, instance)
            else:
                url = build_remote_url(instance.base_url, "/api/v1/forge", "/sessions/stream")
                # A GuildTransportError here (policy refusal or pin mismatch)
                # is not caught specially: it propagates to merge_events'
                # own per-source Exception handler, which already logs and
                # retries this source exactly like any other stream failure.
                client = await build_guild_httpx_client(
                    instance,
                    dial_url=instance.base_url,
                    timeout_seconds=forge_stream_remote_timeout_seconds,
                    connect_timeout_seconds=forge_stream_remote_connect_timeout_seconds,
                )
                async for name, payload in remote_events(client, url, headers):
                    # The node filters too; re-check so an older node cannot leak
                    # another owner's notification through the fleet stream.
                    if not _event_visible(name, payload, principal):
                        continue
                    yield name, _with_instance(payload, instance)

        return StreamingResponse(
            merge_events(
                {instance.id: lambda item=instance: events(item) for instance in instances},
                retry_seconds=forge_stream_retry_seconds,
                keepalive_seconds=forge_stream_keepalive_seconds,
                queue_maxsize=forge_stream_queue_maxsize,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Forge-Session-Scope": scope,
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
        coordination = body.get("coordination")
        if isinstance(coordination, dict) and coordination.get("project_id"):
            body = {
                **body,
                "coordination": {
                    **coordination,
                    **(
                        {
                            "parent": await _normalize_parent(
                                request, principal, coordination["parent"]
                            )
                        }
                        if coordination.get("parent")
                        else {}
                    ),
                },
            }
            await _ensure_project_on_instance(
                request,
                principal,
                str(coordination["project_id"]),
                instance,
                hint=body.get("project_instance_id") or None,
            )
            body.pop("project_instance_id", None)
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

    @router.post("/repos/prs", status_code=status.HTTP_201_CREATED)
    async def create_pull_request(
        request: Request,
        body: dict[str, Any] = Body(...),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        """Open a pull request from a session on the Forge that owns it.

        The PR is built from that instance's session branch and chronicle, so
        only the owning instance can serve it. Reading, merging and CI status
        of PRs need no session and live at ``/api/v1/niuu/repos/prs``.
        """
        raw_session_id = body.get("session_id")
        try:
            session_id = str(UUID(str(raw_session_id)))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"create_pr needs the UUID of a Forge session, got {raw_session_id!r}",
            ) from exc
        owner, _ = await _find_runtime_owner(
            service,
            principal,
            request,
            f"/sessions/{session_id}",
            (
                f"Session {session_id} is not owned by any Forge instance visible to you; "
                "create_pr needs the id of an existing session that has a repository"
            ),
            embedded_app=embedded_forge_app,
            local_scope=_local_session_scope(request),
        )
        response = await _request_remote(
            owner,
            request,
            method="POST",
            path="/repos/prs",
            json_body=_strip_instance_hints(body),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        return _with_instance(response.json(), owner, rebase_chat_endpoint=False)

    @router.get("/stats")
    async def get_stats(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        selected = request.query_params.get("instance_id")
        if selected:
            instance = await _resolve_target_instance(service, principal, selected)
            response = await _request_remote(
                instance,
                request,
                method="GET",
                path="/stats",
                embedded_app=embedded_forge_app,
            )
            _ensure_remote_success(response)
            payload = response.json()
            if not isinstance(payload, dict):
                raise HTTPException(status_code=502, detail="Unexpected Forge metrics response")
            return _with_instance(payload, instance, rebase_chat_endpoint=False)
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
        selected = request.query_params.get("instance_id")
        if selected:
            instance = await _resolve_target_instance(service, principal, selected)
            response = await _request_remote(
                instance,
                request,
                method="GET",
                path="/resources",
                remote_prefix="/api/v1/volundr",
                embedded_app=embedded_forge_app,
            )
            _ensure_remote_success(response)
            payload = response.json()
            if not isinstance(payload, dict):
                raise HTTPException(status_code=502, detail="Unexpected Forge resources response")
            return _merge_cluster_resources([payload], [instance])
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

    async def _mcp_target(request: Request, principal: Principal) -> RegisteredInstance:
        """The node whose MCP endpoint serves this request: named, else the local one."""
        selected = request.query_params.get("instance_id")
        if selected:
            return await _resolve_target_instance(service, principal, selected)
        local = _local_only(await _visible_instances(service, principal))
        if len(local) == 1:
            return local[0]
        return await _resolve_target_instance(service, principal, None)

    @router.post("/mcp")
    async def forge_mcp(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """The Forge-hosted MCP (Streamable HTTP) of the local node, or ``?instance_id=``.

        The body and the MCP transport headers pass through unchanged; the node
        validates Origin, negotiates the protocol and authorizes every tool call.
        A Forge session token only reaches its own (local) node.
        """
        instance = await _mcp_target(request, principal)
        passthrough = {
            name: value
            for name in ("content-type", "accept", "origin", "mcp-protocol-version")
            if (value := request.headers.get(name)) is not None
        }
        upstream = await _request_remote(
            instance,
            request,
            method="POST",
            path=MCP_PATH,
            content_body=await request.body(),
            params=_forward_params(request, drop=_FLEET_SELECTORS),
            extra_headers=passthrough,
            embedded_app=embedded_forge_app,
        )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    @router.api_route("/mcp", methods=["GET", "DELETE"], include_in_schema=False)
    async def forge_mcp_not_offered() -> Response:
        """The endpoint is stateless and offers no server stream (MCP JSON mode)."""
        return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED, headers={"Allow": "POST"})

    @router.get("/feature-flags")
    async def get_feature_flags(
        request: Request,
        instance_id: str | None = Query(default=None),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance = (
            await _local_session_instance(service, principal, request)
            if _local_session_scope(request)
            else await _resolve_target_instance(service, principal, instance_id)
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path="/feature-flags",
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        flags = dict(payload) if isinstance(payload, dict) else {}
        # Advertise this facade's public surface, not routes that exist only on
        # its embedded service. Clients can avoid expected 404s and keep the
        # existing Bifrost model fallback.
        flags["capabilities"] = {
            "models": False,
            "chronicles": False,
            "session_chronicle": False,
            "session_events": False,
            "message_delivery": True,
            "native_history_import": True,
            "local_session_scope": True,
            "notifications": True,
        }
        return flags

    # -- Guild discovery -------------------------------------------------------

    @router.get("/guild")
    async def get_guild(
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        """The Guild a client reached through this facade.

        Same shape as a Forge node's ``GET /api/v1/forge/guild``. The instance is the
        one this host's own ``guild:`` names (a joined host), else the node the facade
        targets by default here (its embedded Forge, else the registry default), else
        null.
        """
        if guild_membership is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "This host is not configured as part of a Guild"
            )
        if guild_membership.instance is not None:
            return guild_membership.wire()
        visible = await _visible_instances(service, principal)
        default = next(
            (instance for instance in visible if _uses_embedded_transport(instance)),
            next((instance for instance in visible if instance.is_default), None),
        )
        if default is None:
            return guild_membership.wire()
        return GuildMembership(
            guild_url=guild_membership.guild_url,
            instance=GuildInstance(id=default.id, name=default.name, slug=default.slug),
        ).wire()

    # -- Notifications (contract §6) -------------------------------------------

    async def _notification_target(request: Request, principal: Principal) -> RegisteredInstance:
        """The selected instance, or the default (local) one."""
        if _local_session_scope(request):
            return await _local_session_instance(service, principal, request)
        return await _resolve_target_instance(
            service, principal, request.query_params.get("instance_id")
        )

    async def _fan_out(
        instances: list[RegisteredInstance],
        request: Request,
        *,
        method: str,
        path: str,
        params_for: Any,
        json_for: Any = None,
    ) -> tuple[dict[str, httpx.Response], set[str]]:
        async def call(instance: RegisteredInstance) -> httpx.Response:
            return await _request_remote(
                instance,
                request,
                method=method,
                path=path,
                params=params_for(instance),
                json_body=json_for(instance) if json_for else None,
                embedded_app=embedded_forge_app,
            )

        results = await asyncio.gather(*(call(i) for i in instances), return_exceptions=True)
        responses: dict[str, httpx.Response] = {}
        unavailable: set[str] = set()
        for instance, result in zip(instances, results, strict=True):
            if isinstance(result, Exception):
                unavailable.add(instance.id)
                continue
            if isinstance(result, BaseException):
                raise result
            responses[instance.id] = result
        return responses, unavailable

    async def _proxy_to(
        instance: RegisteredInstance,
        request: Request,
        *,
        method: str,
        path: str,
        body: Any = None,
    ) -> httpx.Response:
        response = await _request_remote(
            instance,
            request,
            method=method,
            path=path,
            params=_forward_params(request, drop=_FLEET_SELECTORS),
            json_body=body,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        return response

    @router.get("/notifications")
    async def list_notifications(
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        """One node's feed, or with ``all_instances=true`` every visible node's feed
        merged newest first with opaque per-node cursors."""
        if not _fleet_request(request):
            instance = await _notification_target(request, principal)
            remote = await _proxy_to(instance, request, method="GET", path="/notifications")
            payload = _remote_json(remote)
            if not isinstance(payload, dict):
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Unexpected Forge feed response")
            payload["items"] = _tag_items(payload.get("items"), instance)
            payload["instance_id"] = instance.id
            return payload

        query = request.query_params
        try:
            before = decode_cursor(query["before"]) if "before" in query else None
            after = decode_cursor(query["after"]) if "after" in query else None
        except CursorError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if before is not None and after is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Use either before or after, not both"
            )
        limit = _optional_limit(query.get("limit"))
        visible = await _visible_instances(service, principal)
        # A cursor names the nodes it continues: a node missing from a before-cursor
        # has no older rows left, and one missing from an after-cursor is not being
        # gap-filled (a client that wants a node from the start sends it with seq 0).
        cursor = before if before is not None else after
        instances = [i for i in visible if i.id in cursor] if cursor is not None else visible
        base = _forward_params(request, drop=_FLEET_SELECTORS | {"before", "after"})

        def params_for(instance: RegisteredInstance) -> list[tuple[str, str]]:
            if before is not None and before.get(instance.id) is not None:
                return [*base, ("before", str(before[instance.id]))]
            if after is not None:
                return [*base, ("after", str(after.get(instance.id) or 0))]
            return base

        responses, unavailable = await _fan_out(
            instances, request, method="GET", path="/notifications", params_for=params_for
        )
        pages: dict[str, InstancePage] = {}
        for instance in instances:
            result = responses.get(instance.id)
            if result is None:
                continue
            if _is_client_error(result):
                _ensure_remote_success(result)
            page = _instance_page(result, instance) if result.status_code < 400 else None
            if page is None:
                unavailable.add(instance.id)
                continue
            pages[instance.id] = page
        if instances and not pages:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "No Forge instance answered the notification feed"
            )
        _set_unavailable(response, unavailable)
        merged = (
            merge_after(pages, previous=after, unavailable=unavailable)
            if after is not None
            else merge_newest(pages, previous=before or {}, unavailable=unavailable, limit=limit)
        )
        return {
            "items": merged.items,
            "next_before": encode_cursor(merged.next_before) if merged.next_before else None,
            "next_after": encode_cursor(merged.next_after),
            # Seqs are per node: the fleet-wide values live under ``instances``.
            "head_seq": None,
            "read_through_seq": None,
            "unread_count": sum(page.unread_count for page in pages.values()),
            "instances": merged.summaries,
        }

    @router.get("/notifications/read-state")
    async def get_notification_read_state(
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        if not _fleet_request(request):
            instance = await _notification_target(request, principal)
            remote = await _proxy_to(
                instance, request, method="GET", path="/notifications/read-state"
            )
            payload = _remote_json(remote)
            return {**payload, "instance_id": instance.id} if isinstance(payload, dict) else {}
        instances = await _visible_instances(service, principal)
        base = _forward_params(request, drop=_FLEET_SELECTORS)
        responses, unavailable = await _fan_out(
            instances,
            request,
            method="GET",
            path="/notifications/read-state",
            params_for=lambda _instance: base,
        )
        states: dict[str, Any] = {}
        for instance_id, result in responses.items():
            payload = _remote_json(result) if result.status_code < 400 else None
            if not isinstance(payload, dict) or type(payload.get("unread_count")) is not int:
                unavailable.add(instance_id)
                continue
            states[instance_id] = payload
        if instances and not states:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "No Forge instance answered")
        _set_unavailable(response, unavailable)
        return {
            "unread_count": sum(state["unread_count"] for state in states.values()),
            "instances": states,
        }

    @router.put("/notifications/read-state")
    async def put_notification_read_state(
        request: Request,
        response: Response,
        body: dict[str, Any] = Body(...),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        """Advance one node's watermark, or several with ``{"instances": {id: {...}}}``."""
        targets = body.get("instances")
        if targets is None:
            instance = await _notification_target(request, principal)
            remote = await _proxy_to(
                instance, request, method="PUT", path="/notifications/read-state", body=body
            )
            payload = _remote_json(remote)
            return {**payload, "instance_id": instance.id} if isinstance(payload, dict) else {}
        if not isinstance(targets, dict) or not targets:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "instances must map instance ids to {read_through_seq, expected_revision}",
            )
        instances = [
            await _resolve_target_instance(service, principal, instance_id)
            for instance_id in targets
        ]
        base = _forward_params(request, drop=_FLEET_SELECTORS)
        responses, unavailable = await _fan_out(
            instances,
            request,
            method="PUT",
            path="/notifications/read-state",
            params_for=lambda _instance: base,
            json_for=lambda instance: targets[instance.id],
        )
        states: dict[str, Any] = {}
        conflicts: list[str] = []
        for instance in instances:
            result = responses.get(instance.id)
            if result is None:
                continue
            if result.status_code == status.HTTP_409_CONFLICT:
                conflicts.append(instance.id)
                continue
            if status.HTTP_400_BAD_REQUEST <= result.status_code < 500:
                _ensure_remote_success(result)
            payload = _remote_json(result) if result.status_code < 400 else None
            if not isinstance(payload, dict):
                unavailable.add(instance.id)
                continue
            states[instance.id] = payload
        _set_unavailable(response, unavailable)
        if conflicts:
            headers = (
                {UNAVAILABLE_INSTANCES_HEADER: ",".join(sorted(unavailable))}
                if unavailable
                else None
            )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "message": "Notification read state changed; refresh before changing it",
                    "conflicts": sorted(conflicts),
                    "instances": states,
                },
                headers=headers,
            )
        if not states:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "No Forge instance answered")
        return {
            "unread_count": sum(
                state["unread_count"]
                for state in states.values()
                if type(state.get("unread_count")) is int
            ),
            "instances": states,
        }

    async def _proxy_rule_request(
        request: Request, principal: Principal, *, method: str, path: str, body: Any = None
    ) -> Response:
        instance = await _notification_target(request, principal)
        remote = await _proxy_to(instance, request, method=method, path=path, body=body)
        if remote.status_code == status.HTTP_204_NO_CONTENT or not remote.content:
            return Response(status_code=remote.status_code)
        payload = _remote_json(remote)
        if isinstance(payload, list):
            payload = _tag_items(payload, instance)
        elif isinstance(payload, dict):
            payload = _with_instance(payload, instance, rebase_chat_endpoint=False)
        return JSONResponse(payload, status_code=remote.status_code)

    @router.put("/notifications/{notification_id}/read")
    async def mark_notification_read(
        request: Request,
        notification_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance = await _notification_target(request, principal)
        remote = await _proxy_to(
            instance, request, method="PUT", path=f"/notifications/{notification_id}/read"
        )
        payload = _remote_json(remote)
        if not isinstance(payload, dict):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Invalid activity read response")
        return {**payload, "instance_id": instance.id}

    @router.get("/notifications/rules")
    async def list_notification_rules(
        request: Request, principal: Principal = Depends(extract_principal)
    ) -> Response:
        return await _proxy_rule_request(
            request, principal, method="GET", path="/notifications/rules"
        )

    @router.post("/notifications/rules", status_code=status.HTTP_201_CREATED)
    async def create_notification_rule(
        request: Request,
        body: dict[str, Any] = Body(...),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        return await _proxy_rule_request(
            request, principal, method="POST", path="/notifications/rules", body=body
        )

    @router.put("/notifications/rules/{rule_id}")
    async def update_notification_rule(
        request: Request,
        rule_id: str = Path(description="Rule id"),
        body: dict[str, Any] = Body(...),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        return await _proxy_rule_request(
            request,
            principal,
            method="PUT",
            path=f"/notifications/rules/{quote(rule_id, safe='')}",
            body=body,
        )

    @router.delete("/notifications/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_notification_rule(
        request: Request,
        rule_id: str = Path(description="Rule id"),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        return await _proxy_rule_request(
            request,
            principal,
            method="DELETE",
            path=f"/notifications/rules/{quote(rule_id, safe='')}",
        )

    @router.get("/notifications/sinks")
    async def list_notification_sinks(
        request: Request, principal: Principal = Depends(extract_principal)
    ) -> Response:
        return await _proxy_rule_request(
            request, principal, method="GET", path="/notifications/sinks"
        )

    @router.get("/notifications/{notification_id}/deliveries")
    async def list_notification_deliveries(
        request: Request,
        notification_id: str = Path(description="Notification id"),
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        """Delivery rows from the node that holds the notification."""
        selected = request.query_params.get("instance_id")
        instances = (
            [await _resolve_target_instance(service, principal, selected)]
            if selected
            else await _visible_instances(service, principal)
        )
        path = f"/notifications/{quote(notification_id, safe='')}/deliveries"
        for instance in instances:
            remote = await _request_remote(
                instance, request, method="GET", path=path, embedded_app=embedded_forge_app
            )
            if remote.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}:
                continue
            _ensure_remote_success(remote)
            return _tag_items(_remote_json(remote), instance)
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Notification not found: {notification_id}")

    @router.get("/sessions/{session_id}/notifications")
    async def list_session_notifications(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> list[dict[str, Any]]:
        instance, _ = await _find_session_owner(
            service, principal, request, session_id, embedded_app=embedded_forge_app
        )
        remote = await _proxy_to(
            instance, request, method="GET", path=f"/sessions/{session_id}/notifications"
        )
        return _tag_items(_remote_json(remote), instance)

    @router.post("/sessions/{session_id}/notifications", status_code=status.HTTP_201_CREATED)
    async def submit_session_notification(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        body: dict[str, Any] = Body(...),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """Direct submit on the owning node; 201 when new, 200 when deduplicated."""
        instance, _ = await _find_session_owner(
            service, principal, request, session_id, embedded_app=embedded_forge_app
        )
        remote = await _proxy_to(
            instance,
            request,
            method="POST",
            path=f"/sessions/{session_id}/notifications",
            body=body,
        )
        payload = _with_instance(_remote_json(remote), instance, rebase_chat_endpoint=False)
        return JSONResponse(payload, status_code=remote.status_code)

    # -- Push devices -----------------------------------------------------------
    #
    # The Guild has no device registry of its own: every node Forge pushes to the
    # devices registered with *it*, so a device must be registered on every visible
    # node. These routes fan out to all of them and succeed only when all agree.

    async def _device_fan_out(
        request: Request,
        principal: Principal,
        *,
        method: str,
        path: str,
        body: Any = None,
    ) -> tuple[list[RegisteredInstance], dict[str, httpx.Response], set[str]]:
        instances = await _visible_instances(service, principal)
        if not instances:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, "No Forge instance is visible to handle devices"
            )
        responses, unavailable = await _fan_out(
            instances,
            request,
            method=method,
            path=path,
            params_for=lambda _instance: [],
            json_for=(lambda _instance: body) if body is not None else None,
        )
        return instances, responses, unavailable

    @router.post("/devices", status_code=status.HTTP_201_CREATED)
    async def register_device(
        request: Request,
        body: dict[str, Any] = Body(...),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """Register a push device on every visible Forge node.

        Idempotent on (owner, token) per node, so a failed registration is simply
        retried. 201 with one node's device record when every node accepted; a node's
        4xx refusal propagates, anything else (unreachable, 5xx) is 502, and the
        detail names each instance that did not accept.
        """
        instances, responses, unavailable = await _device_fan_out(
            request, principal, method="POST", path="/devices", body=body
        )
        _raise_device_failures(
            "registration",
            instances,
            responses,
            unavailable,
            accepted=lambda result: result.status_code == status.HTTP_201_CREATED,
        )
        return JSONResponse(
            _remote_json(responses[instances[0].id]), status_code=status.HTTP_201_CREATED
        )

    @router.get("/devices")
    async def list_devices(
        request: Request, principal: Principal = Depends(extract_principal)
    ) -> list[dict[str, Any]]:
        """The caller's push devices across every visible node, one row per token.

        Nodes may share a device store, so a token can come back from several;
        the first node's row wins. An unreachable or refusing node fails the call
        (unlike the feed, a partial list would read as "device not registered").
        """
        instances, responses, unavailable = await _device_fan_out(
            request, principal, method="GET", path="/devices"
        )
        _raise_device_failures(
            "listing",
            instances,
            responses,
            unavailable,
            accepted=lambda result: result.status_code == status.HTTP_200_OK,
        )
        devices: dict[str, dict[str, Any]] = {}
        for instance in instances:
            payload = _remote_json(responses[instance.id])
            if not isinstance(payload, list):
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    f"Unexpected device list from Forge instance {instance.id}",
                )
            for device in payload:
                if not isinstance(device, dict) or not isinstance(device.get("token"), str):
                    raise HTTPException(
                        status.HTTP_502_BAD_GATEWAY,
                        f"Unexpected device row from Forge instance {instance.id}",
                    )
                devices.setdefault(device["token"], device)
        return list(devices.values())

    @router.delete("/devices/{token}", status_code=status.HTTP_204_NO_CONTENT)
    async def unregister_device(
        request: Request,
        token: str = Path(description="The push token to unregister"),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """Unregister a push device on every visible Forge node.

        A node that no longer knows the token (404) counts as done. Any other
        failure is reported with the instances that did not remove it.
        """
        instances, responses, unavailable = await _device_fan_out(
            request,
            principal,
            method="DELETE",
            path=f"/devices/{quote(token, safe='')}",
        )
        _raise_device_failures(
            "unregistration",
            instances,
            responses,
            unavailable,
            accepted=lambda result: (
                result.status_code in {status.HTTP_204_NO_CONTENT, status.HTTP_404_NOT_FOUND}
            ),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

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

    @router.post("/sessions/{session_id}/history/import")
    async def import_session_history(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        return await _start_session_on_owner(request, session_id, principal, "history/import")

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

    @router.get("/sessions/{session_id}/log/page")
    async def get_log_page(
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
            path=f"/sessions/{session_id}/log/page",
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

    @router.websocket("/sessions/{session_id}/replay")
    async def replay_session(websocket: WebSocket, session_id: str) -> None:
        principal = await extract_principal(websocket)
        # Browsers cannot set Authorization on a WebSocket, so the bearer may
        # have arrived as ?token=/?access_token=. Carry it as a header on the
        # owner probes and the bridged socket, or every owner answers 401.
        headers = _forward_websocket_headers(websocket)
        try:
            instance, _ = await _find_session_owner(
                service,
                principal,
                websocket,
                session_id,
                embedded_app=embedded_forge_app,
                extra_headers=headers,
            )
        except HTTPException:
            await websocket.close(code=1008)
            return
        await forward_replay(
            websocket,
            instance,
            session_id,
            headers=headers,
            embedded_app=embedded_forge_app,
        )

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

    @router.get("/sessions/{session_id}/runtime-version")
    async def get_session_runtime_version(
        request: Request,
        session_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service, principal, request, session_id, embedded_app=embedded_forge_app
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/runtime-version",
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        return response.json()

    @router.get("/sessions/{session_id}/read-state")
    async def get_session_read_state(
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
            path=f"/sessions/{session_id}/read-state",
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        return response.json()

    @router.patch("/sessions/{session_id}/read-state")
    async def change_session_read_state(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        body: dict[str, Any] = Body(...),
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service, principal, request, session_id, embedded_app=embedded_forge_app
        )
        response = await _request_remote(
            instance,
            request,
            method="PATCH",
            path=f"/sessions/{session_id}/read-state",
            json_body=body,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        return response.json()

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

    @router.get("/sessions/{session_id}/project")
    async def get_session_project(
        request: Request,
        session_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instance, _ = await _find_session_owner(
            service, principal, request, str(session_id), embedded_app=embedded_forge_app
        )
        response = await _request_remote(
            instance,
            request,
            method="GET",
            path=f"/sessions/{session_id}/project",
            embedded_app=embedded_forge_app,
        )
        if response.status_code == 404:
            raise HTTPException(
                501, "Update this session's Forge host to enable project assignment"
            )
        _ensure_remote_success(response)
        return _with_instance(response.json(), instance)

    @router.put("/sessions/{session_id}/project")
    async def assign_session_project(
        request: Request,
        session_id: UUID,
        data: SessionProjectAssignment,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        owner, _ = await _find_session_owner(
            service, principal, request, str(session_id), embedded_app=embedded_forge_app
        )
        # Check support/revision before making a metadata replica on a worker host.
        current = await _request_remote(
            owner,
            request,
            method="GET",
            path=f"/sessions/{session_id}/project",
            embedded_app=embedded_forge_app,
        )
        if current.status_code == 404:
            raise HTTPException(
                501, "Update this session's Forge host to enable project assignment"
            )
        _ensure_remote_success(current)
        if current.json().get("revision") != data.expected_revision:
            raise HTTPException(409, "Session project changed; reload before assigning")
        if data.project_id is not None:
            await _ensure_project_on_instance(
                request,
                principal,
                str(data.project_id),
                owner,
                hint=data.project_instance_id,
            )
        forwarded: dict[str, Any] = {
            "project_id": str(data.project_id) if data.project_id is not None else None,
            "expected_revision": data.expected_revision,
        }
        if data.role is not None:
            forwarded["role"] = data.role
        if "parent" in data.model_fields_set:
            forwarded["parent"] = await _normalize_parent(request, principal, data.parent)
        response = await _request_remote(
            owner,
            request,
            method="PUT",
            path=f"/sessions/{session_id}/project",
            json_body=forwarded,
            embedded_app=embedded_forge_app,
        )
        if response.status_code == 422 and set(forwarded) - {"project_id", "expected_revision"}:
            raise HTTPException(
                501, "Update this session's Forge host to set project roles and coordinators"
            )
        _ensure_remote_success(response)
        return _with_instance(response.json(), owner)

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
        _ensure_history_success(response)
        payload = response.json()
        if isinstance(payload, dict) and payload.get("history_protocol") == 2:
            # The owner fitted the WHOLE page envelope to the requested byte
            # limit. Adding aggregate timings afterwards can exceed that limit.
            return payload
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

    @router.get("/sessions/{session_id}/conversation/turns/{turn_id}")
    async def get_conversation_item(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        turn_id: str = Path(description="Stable conversation row identity"),
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
            path=f"/sessions/{session_id}/conversation/turns/{quote(turn_id, safe='')}",
            embedded_app=embedded_forge_app,
        )
        _ensure_history_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

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

    @router.get("/sessions/{session_id}/tool-result/{tool_use_id}/preview")
    async def get_tool_result_preview(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        tool_use_id: str = Path(description="tool_use_id of the image result"),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """Byte-proxy a scaled-down JPEG tool-result preview from the owning instance.

        Unlike the sibling JSON routes this forwards raw image bytes; 404/501 from
        the owner pass through via ``_ensure_remote_success`` so the client can
        fall back to the full tool-result fetch.
        """
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
            params=_query_params(request),
            path=(f"/sessions/{session_id}/tool-result/{quote(tool_use_id, safe='')}/preview"),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        headers = {
            name: value
            for name in ("cache-control", "etag")
            if (value := response.headers.get(name))
        }
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "image/jpeg"),
            headers=headers,
        )

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

    @router.get("/sessions/{session_id}/help/requests")
    async def get_help_requests(
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
            path=f"/sessions/{session_id}/help/requests",
            params=_query_params(request),
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"requests": []}

    @router.post("/sessions/{session_id}/help/requests/{request_id}/answer")
    async def answer_help_request(
        request: Request,
        session_id: str = Path(description="Volundr session identifier"),
        request_id: str = Path(description="Help request identifier"),
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
            path=f"/sessions/{session_id}/help/requests/{quote(request_id, safe='')}/answer",
            json_body=body,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

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
            method="POST",
            path=f"/sessions/{session_id}/messages",
            json_body=body,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        payload = response.json()
        return JSONResponse(
            content=payload if isinstance(payload, dict) else {}, status_code=response.status_code
        )

    @router.api_route("/sessions/{session_id}/message-deliveries/{request_id}", methods=["GET"])
    @router.api_route(
        "/sessions/{session_id}/message-deliveries/{request_id}/{operation}", methods=["POST"]
    )
    async def message_delivery(
        request: Request,
        session_id: str,
        request_id: str = Path(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$"),
        operation: str | None = None,
        body: dict[str, Any] = Body(default_factory=dict),
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        if request.method == "POST" and operation not in ("claim", "settle"):
            raise HTTPException(404, "Unknown delivery operation")
        instance, _ = await _find_session_owner(
            service, principal, request, session_id, embedded_app=embedded_forge_app
        )
        # GET has no operation path segment. Never treat an unrelated query
        # parameter as part of a forwarded URL.
        suffix = f"/{operation}" if request.method == "POST" else ""
        response = await _request_remote(
            instance,
            request,
            method=request.method,
            path=f"/sessions/{session_id}/message-deliveries/{request_id}{suffix}",
            json_body=body if request.method == "POST" else None,
            embedded_app=embedded_forge_app,
        )
        _ensure_remote_success(response)
        return JSONResponse(content=response.json(), status_code=response.status_code)

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
        instance = await _find_trace_owner(
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
        instance = await _find_trace_owner(
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

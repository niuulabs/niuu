"""Shared instance registry REST endpoints — /api/v1/niuu/instances."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field
from starlette.types import ASGIApp

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.remote_urls import build_remote_url
from niuu.domain.agent_directory import (
    AgentDirectoryEntry,
    AgentDirectoryFilters,
    AgentDirectoryPage,
)
from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.domain.observatory import ObservatoryFragment, TopologySourceHealth
from niuu.domain.services.agent_directory import AgentDirectoryAggregationService
from niuu.domain.services.instances import (
    InstanceAccessError,
    InstanceService,
    InstanceValidationError,
)
from niuu.domain.services.observatory_fragments import ObservatoryFragmentInboxService
from niuu.domain.services.token_scope import TOPOLOGY_PUSH_SCOPE, require_build_scope


class InstanceResponse(BaseModel):
    id: str
    kind: str
    slug: str
    name: str
    base_url: str = Field(serialization_alias="baseUrl")
    visibility: str
    owner_id: str | None = Field(default=None, serialization_alias="ownerId")
    tenant_id: str | None = Field(default=None, serialization_alias="tenantId")
    enabled: bool
    is_default: bool = Field(serialization_alias="isDefault")
    config: dict[str, Any]
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class InstanceCreateRequest(BaseModel):
    kind: str = Field(default=InstanceKind.VOLUNDR.value)
    slug: str
    name: str
    base_url: str = Field(serialization_alias="baseUrl", validation_alias="baseUrl")
    visibility: str = Field(default=InstanceVisibility.USER.value)
    enabled: bool = True
    is_default: bool = Field(
        default=False,
        serialization_alias="isDefault",
        validation_alias="isDefault",
    )
    owner_id: str | None = Field(
        default=None,
        serialization_alias="ownerId",
        validation_alias="ownerId",
    )
    tenant_id: str | None = Field(
        default=None,
        serialization_alias="tenantId",
        validation_alias="tenantId",
    )
    config: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class InstanceUpdateRequest(BaseModel):
    slug: str | None = None
    name: str | None = None
    base_url: str | None = Field(
        default=None,
        serialization_alias="baseUrl",
        validation_alias="baseUrl",
    )
    visibility: str | None = None
    enabled: bool | None = None
    is_default: bool | None = Field(
        default=None,
        serialization_alias="isDefault",
        validation_alias="isDefault",
    )
    owner_id: str | None = Field(
        default=None,
        serialization_alias="ownerId",
        validation_alias="ownerId",
    )
    tenant_id: str | None = Field(
        default=None,
        serialization_alias="tenantId",
        validation_alias="tenantId",
    )
    config: dict[str, Any] | None = None
    tags: list[str] | None = None


class InstanceTestResponse(BaseModel):
    ok: bool
    status_code: int | None = Field(default=None, serialization_alias="statusCode")
    message: str


class InstanceSessionResponse(BaseModel):
    id: str
    name: str
    status: str
    model: str | None = None
    owner_id: str | None = Field(default=None, serialization_alias="ownerId")
    tenant_id: str | None = Field(default=None, serialization_alias="tenantId")
    archived_at: str | None = Field(default=None, serialization_alias="archivedAt")


class InstanceCatalogEntryResponse(BaseModel):
    kind: str
    label: str
    rune: str
    summary: str
    detail: str
    registerable: bool
    filterable: bool


def _to_response(instance: RegisteredInstance) -> InstanceResponse:
    return InstanceResponse(
        id=instance.id,
        kind=instance.kind.value,
        slug=instance.slug,
        name=instance.name,
        base_url=instance.base_url,
        visibility=instance.visibility.value,
        owner_id=instance.owner_id,
        tenant_id=instance.tenant_id,
        enabled=instance.enabled,
        is_default=instance.is_default,
        config=instance.config,
        tags=instance.tags,
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


def _uses_embedded_transport(instance: RegisteredInstance) -> bool:
    return str(instance.config.get("transport", "")).strip().lower() == "embedded"


async def _probe_instance(
    instance: RegisteredInstance,
    *,
    embedded_app: ASGIApp | None = None,
) -> InstanceTestResponse:
    if _uses_embedded_transport(instance):
        if embedded_app is None:
            return InstanceTestResponse(
                ok=False,
                status_code=502,
                message="Embedded Forge target is not available in this process",
            )
        transport = httpx.ASGITransport(app=embedded_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://embedded.local",
        ) as client:
            response = await client.get("/health")
        return InstanceTestResponse(
            ok=response.status_code < 400,
            status_code=response.status_code,
            message=(
                f"{instance.name} is reachable"
                if response.status_code < 400
                else f"Health probe failed for {instance.name}"
            ),
        )

    url = f"{instance.base_url}/health"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url)
        if response.status_code >= 400:
            return InstanceTestResponse(
                ok=False,
                status_code=response.status_code,
                message=f"Health probe failed for {instance.name}",
            )
        return InstanceTestResponse(
            ok=True,
            status_code=response.status_code,
            message=f"{instance.name} is reachable",
        )
    except Exception as exc:
        return InstanceTestResponse(ok=False, message=str(exc))


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


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-") or "service"


def _string_config_value(config: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _instance_deployment_labels(instance: RegisteredInstance) -> dict[str, str]:
    labels: dict[str, str] = {}
    for key in ("labels", "deploymentLabels", "kubernetesLabels"):
        raw_labels = instance.config.get(key)
        if isinstance(raw_labels, dict):
            for label_key, label_value in raw_labels.items():
                if (
                    isinstance(label_key, str)
                    and isinstance(label_value, str)
                    and label_value.strip()
                ):
                    labels[label_key] = label_value.strip()
    return labels


def _instance_cluster_name(instance: RegisteredInstance) -> str:
    labels = _instance_deployment_labels(instance)
    label_cluster = labels.get("niuu.world/cluster") or labels.get("cluster")
    if label_cluster:
        return label_cluster
    return _string_config_value(instance.config, "cluster", "environment")


def _instance_namespace(instance: RegisteredInstance) -> str:
    labels = _instance_deployment_labels(instance)
    label_namespace = labels.get("niuu.world/namespace") or labels.get("namespace")
    if label_namespace:
        return label_namespace
    return _string_config_value(instance.config, "namespace")


def _deployment_cluster_id(cluster_name: str) -> str:
    return f"cluster-{_slug(cluster_name)}"


def _ensure_deployment_cluster_node(
    nodes: list[dict[str, Any]],
    *,
    cluster_name: str,
    namespace: str = "",
) -> str:
    node_id = _deployment_cluster_id(cluster_name)
    existing = next((node for node in nodes if node["id"] == node_id), None)
    if existing is not None:
        if namespace and not existing.get("namespace"):
            existing["namespace"] = namespace
        return node_id

    nodes.append(
        {
            "id": node_id,
            "typeId": "cluster",
            "label": cluster_name,
            "parentId": None,
            "status": "unknown",
            "sourceKind": "deployment",
            "clusterName": cluster_name,
            "namespace": namespace,
            "layoutHints": {
                "mode": "pack",
                "scope": "world",
                "packGroup": "deployment-cluster",
                "order": 30,
            },
        }
    )
    return node_id


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _probe_status(probe: InstanceTestResponse) -> str:
    if probe.ok:
        return "healthy"
    if probe.status_code is None and not probe.message:
        return "unknown"
    return "failed"


def _kind_type_id(kind: InstanceKind) -> str:
    if kind is InstanceKind.VOLUNDR:
        return "volundr"
    if kind is InstanceKind.TING:
        return "ting"
    if kind is InstanceKind.BIFROST:
        return "bifrost"
    if kind is InstanceKind.MIMIR:
        return "mimir"
    return "service"


def _kind_svc_type(kind: InstanceKind) -> str:
    return kind.value


def _overlay_instance_nodes(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, str]],
    instances: list[RegisteredInstance],
    probe_by_id: dict[str, InstanceTestResponse],
) -> None:
    per_kind_index: dict[InstanceKind, int] = {}

    for instance in instances:
        probe = probe_by_id[instance.id]
        kind_index = per_kind_index.get(instance.kind, 0)
        per_kind_index[instance.kind] = kind_index + 1
        cluster_name = _instance_cluster_name(instance)
        namespace = _instance_namespace(instance)
        parent_id = (
            _ensure_deployment_cluster_node(
                nodes,
                cluster_name=cluster_name,
                namespace=namespace,
            )
            if cluster_name
            else _ensure_deployment_cluster_node(
                nodes,
                cluster_name="unknown",
                namespace=namespace,
            )
        )
        node_id = f"instance:{instance.kind.value}:{_slug(instance.slug or instance.name)}"
        type_id = _kind_type_id(instance.kind)
        svc_type = _kind_svc_type(instance.kind)
        node_payload: dict[str, Any] = {
            "id": node_id,
            "typeId": type_id,
            "label": instance.name,
            "parentId": parent_id,
            "status": _probe_status(probe),
            "svcType": svc_type,
            "slug": instance.slug,
            "baseUrl": instance.base_url,
            "visibility": instance.visibility.value,
            "default": instance.is_default,
            "enabled": instance.enabled,
            "sourceKind": svc_type,
            "sourceId": instance.id,
            "clusterName": cluster_name,
            "namespace": namespace,
            "layoutHints": {
                "mode": "pack",
                "scope": "node",
                "packGroup": svc_type,
                "order": 0 if instance.is_default else 10 + kind_index,
            },
        }
        nodes.append(node_payload)

        if instance.kind is InstanceKind.BIFROST:
            for model_index, provider in enumerate(("Anthropic", "OpenAI", "Local")):
                model_id = f"{node_id}:model:{model_index}"
                nodes.append(
                    {
                        "id": model_id,
                        "typeId": "model",
                        "label": provider.lower(),
                        "parentId": node_id,
                        "status": "healthy",
                        "provider": provider,
                        "location": "external" if provider != "Local" else "internal",
                    }
                )
                edges.append(
                    {
                        "id": f"edge:{model_id}",
                        "sourceId": node_id,
                        "targetId": model_id,
                        "kind": "soft",
                        "relationType": "uses",
                        "label": "uses",
                        "confidence": "inferred",
                        "evidence": {
                            "adapter": "rest_instances",
                            "field": "InstanceKind.BIFROST",
                        },
                    }
                )


def _ravn_wardens_url(base_url: str) -> str:
    if base_url.rstrip("/").endswith("/api/v1/ravn"):
        return build_remote_url(base_url, "", "/wardens")
    if base_url.rstrip("/").endswith("/ravn"):
        return build_remote_url(base_url, "", "/wardens")
    return build_remote_url(base_url, "/api/v1/ravn", "/wardens")


async def _load_ravn_wardens(
    instance: RegisteredInstance,
    request: Request,
) -> list[dict[str, Any]]:
    try:
        remote_url = _ravn_wardens_url(instance.base_url)
    except ValueError:
        return []

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(remote_url, headers=_forward_headers(request))
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def _warden_status(warden: dict[str, Any]) -> str:
    runtime = warden.get("runtime") if isinstance(warden.get("runtime"), dict) else {}
    supervisor = warden.get("supervisor") if isinstance(warden.get("supervisor"), dict) else {}
    observation = (
        supervisor.get("observation") if isinstance(supervisor.get("observation"), dict) else {}
    )
    observed_status = str(observation.get("status") or "").lower()
    if str(runtime.get("state") or "").lower() == "active":
        return "healthy"
    if observed_status == "running":
        return "healthy"
    if observed_status in {"degraded", "missing"}:
        return "failed"
    return "unknown"


def _overlay_ravn_warden_nodes(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, str]],
    events: list[dict[str, str]],
    *,
    ravn_instance: RegisteredInstance,
    wardens: list[dict[str, Any]],
    now: datetime,
) -> None:
    if not wardens:
        return

    ravn_slug = _slug(ravn_instance.slug or ravn_instance.name)
    ravn_node_id = f"instance:{ravn_instance.kind.value}:{ravn_slug}"
    ravn_node = next((node for node in nodes if node["id"] == ravn_node_id), None)
    parent_id = ravn_node.get("parentId") if ravn_node else "cluster-unknown"
    cluster_name = str(ravn_node.get("clusterName") or "") if ravn_node else ""
    namespace = str(ravn_node.get("namespace") or "") if ravn_node else ""
    mimir_node = next((node for node in nodes if node.get("typeId") == "mimir"), None)
    mimir_node_id = str(mimir_node.get("id")) if mimir_node else ""

    for index, warden in enumerate(wardens):
        warden_id = str(warden.get("id") or "").strip()
        if not warden_id:
            continue
        mimir = warden.get("mimir") if isinstance(warden.get("mimir"), dict) else {}
        schedules = warden.get("schedules") if isinstance(warden.get("schedules"), dict) else {}
        node_id = f"warden:{warden_id}"
        nodes.append(
            {
                "id": node_id,
                "typeId": "ravn_long",
                "label": str(warden.get("name") or warden_id),
                "parentId": parent_id,
                "status": _warden_status(warden),
                "svcType": "ravn",
                "persona": str(warden.get("persona") or ""),
                "model": str(warden.get("model") or ""),
                "sourceKind": "warden",
                "sourceId": warden_id,
                "baseUrl": ravn_instance.base_url,
                "deployment": str(warden.get("deployment") or ""),
                "clusterName": cluster_name,
                "namespace": namespace,
                "mimirMounts": list(mimir.get("mount_names") or []),
                "writeMount": str(mimir.get("write_mount") or ""),
                "dreamCycle": str(schedules.get("dream_cycle_cron_expression") or ""),
                "layoutHints": {
                    "mode": "pack",
                    "scope": "node",
                    "packGroup": "ravn",
                    "order": 20 + index,
                },
            }
        )
        edges.append(
            {
                "id": f"edge:ravn-instance:{ravn_instance.id}:warden:{warden_id}",
                "sourceId": ravn_node_id,
                "targetId": node_id,
                "kind": "soft",
                "relationType": "manages",
                "label": "manages",
                "confidence": "observed",
                "evidence": {
                    "adapter": "rest_instances",
                    "field": "/api/v1/ravn/wardens",
                },
            }
        )
        if mimir_node_id:
            edges.append(
                {
                    "id": f"edge:warden:{warden_id}:mimir",
                    "sourceId": node_id,
                    "targetId": mimir_node_id,
                    "kind": "dashed-long",
                    "relationType": "writes",
                    "label": "writes",
                    "confidence": "observed",
                    "evidence": {
                        "adapter": "rest_instances",
                        "field": "warden.mimir",
                    },
                }
            )
        events.append(
            {
                "id": f"warden:{warden_id}:discovered",
                "level": "info",
                "service": "ravn",
                "message": (
                    f"{warden.get('name') or warden_id} discovered through {ravn_instance.name}"
                ),
                "timestamp": _iso(now),
            }
        )


async def _build_observatory_snapshot(
    instances: list[RegisteredInstance],
    request: Request | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    probes = await asyncio.gather(*[_probe_instance(instance) for instance in instances])
    probe_by_id = {instance.id: probe for instance, probe in zip(instances, probes, strict=False)}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    events: list[dict[str, str]] = []

    _overlay_instance_nodes(nodes, edges, instances, probe_by_id)

    if request is not None:
        ravn_instances = [instance for instance in instances if instance.kind is InstanceKind.RAVN]
        ravn_warden_groups = await asyncio.gather(
            *[_load_ravn_wardens(instance, request) for instance in ravn_instances]
        )
        for ravn_instance, wardens in zip(ravn_instances, ravn_warden_groups, strict=False):
            _overlay_ravn_warden_nodes(
                nodes,
                edges,
                events,
                ravn_instance=ravn_instance,
                wardens=wardens,
                now=now,
            )

    for instance in instances:
        probe = probe_by_id[instance.id]
        events.append(
            {
                "id": f"instance:{instance.id}:{'up' if probe.ok else 'down'}",
                "level": "info" if probe.ok else "warning",
                "service": instance.kind.value,
                "message": probe.message,
                "timestamp": _iso(now),
            }
        )

    return {
        "timestamp": _iso(now),
        "layoutHints": {
            "mode": "pack",
            "scope": "world",
        },
        "nodes": nodes,
        "edges": edges,
        "events": events,
    }


async def _load_remote_sessions(
    instance: RegisteredInstance,
    request: Request,
    *,
    status_filter: str | None = None,
    embedded_app: ASGIApp | None = None,
) -> list[dict[str, Any]]:
    if _uses_embedded_transport(instance):
        if embedded_app is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Embedded Forge target is not available in this process",
            )
        params = {"status": status_filter} if status_filter else None
        transport = httpx.ASGITransport(app=embedded_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://embedded.local",
        ) as client:
            response = await client.get(
                "/api/v1/forge/sessions",
                headers=_forward_headers(request),
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, list) else []

    try:
        remote_url = build_remote_url(instance.base_url, "/api/v1/forge", "/sessions")
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    params = {"status": status_filter} if status_filter else None
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(
            remote_url,
            headers=_forward_headers(request),
            params=params,
        )
        response.raise_for_status()
        payload = response.json()
    return payload if isinstance(payload, list) else []


def create_instances_router(
    service: InstanceService,
    *,
    embedded_forge_app: ASGIApp | None = None,
    agent_directory: AgentDirectoryAggregationService | None = None,
    fragment_inbox: ObservatoryFragmentInboxService | None = None,
) -> APIRouter:
    """Create the shared instance registry router."""
    router = APIRouter(prefix="/api/v1/niuu", tags=["Shared"])

    @router.get("/instances", response_model=list[InstanceResponse])
    async def list_instances(
        kind: str | None = Query(default=None),
        enabled_only: bool = Query(default=False, alias="enabledOnly"),
        principal: Principal = Depends(extract_principal),
    ) -> list[InstanceResponse]:
        instances = await service.list_visible(
            principal,
            kind=InstanceKind(kind) if kind else None,
            enabled_only=enabled_only,
        )
        return [_to_response(instance) for instance in instances]

    @router.get("/instances/catalog", response_model=list[InstanceCatalogEntryResponse])
    async def get_instance_catalog(request: Request) -> list[InstanceCatalogEntryResponse]:
        settings = getattr(request.app.state, "settings", None)
        catalog = getattr(getattr(settings, "niuu", None), "catalog", []) if settings else []
        return [
            InstanceCatalogEntryResponse(
                kind=entry.kind.value,
                label=entry.label or entry.kind.value.title(),
                rune=entry.rune,
                summary=entry.summary,
                detail=entry.detail,
                registerable=entry.registerable,
                filterable=entry.filterable,
            )
            for entry in catalog
        ]

    @router.post("/instances", response_model=InstanceResponse, status_code=status.HTTP_201_CREATED)
    async def create_instance(
        body: InstanceCreateRequest,
        principal: Principal = Depends(extract_principal),
    ) -> InstanceResponse:
        try:
            instance = await service.create_instance(
                principal,
                kind=InstanceKind(body.kind),
                slug=body.slug,
                name=body.name,
                base_url=body.base_url,
                visibility=InstanceVisibility(body.visibility),
                enabled=body.enabled,
                is_default=body.is_default,
                config=body.config,
                owner_id=body.owner_id,
                tenant_id=body.tenant_id,
                tags=body.tags,
            )
        except (InstanceAccessError, InstanceValidationError) as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return _to_response(instance)

    @router.patch("/instances/{instance_id}", response_model=InstanceResponse)
    async def update_instance(
        body: InstanceUpdateRequest,
        instance_id: str = Path(description="Registered instance UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> InstanceResponse:
        try:
            instance = await service.update_instance(
                principal,
                instance_id,
                slug=body.slug,
                name=body.name,
                base_url=body.base_url,
                visibility=InstanceVisibility(body.visibility) if body.visibility else None,
                enabled=body.enabled,
                is_default=body.is_default,
                config=body.config,
                owner_id=body.owner_id,
                tenant_id=body.tenant_id,
                tags=body.tags,
            )
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (InstanceAccessError, InstanceValidationError) as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return _to_response(instance)

    @router.delete("/instances/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_instance(
        instance_id: str = Path(description="Registered instance UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> None:
        try:
            await service.delete_instance(principal, instance_id)
        except InstanceAccessError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @router.post("/instances/{instance_id}/test", response_model=InstanceTestResponse)
    async def test_instance(
        instance_id: str = Path(description="Registered instance UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> InstanceTestResponse:
        instance = await service.get_visible(principal, instance_id)
        if instance is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=instance_id)
        return await _probe_instance(instance, embedded_app=embedded_forge_app)

    @router.get("/instances/{instance_id}/sessions", response_model=list[InstanceSessionResponse])
    async def list_instance_sessions(
        request: Request,
        instance_id: str = Path(description="Registered instance UUID"),
        session_status: str | None = Query(default=None, alias="status"),
        principal: Principal = Depends(extract_principal),
    ) -> list[InstanceSessionResponse]:
        instance = await service.get_visible(principal, instance_id)
        if instance is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=instance_id)
        try:
            payload = await _load_remote_sessions(
                instance,
                request,
                status_filter=session_status,
                embedded_app=embedded_forge_app,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        return [
            InstanceSessionResponse(
                id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                status=str(item.get("status") or ""),
                model=item.get("model"),
                owner_id=item.get("owner_id"),
                tenant_id=item.get("tenant_id"),
                archived_at=item.get("archived_at"),
            )
            for item in payload
        ]

    @router.get("/targets/volundr", response_model=list[InstanceResponse])
    async def list_volundr_targets(
        principal: Principal = Depends(extract_principal),
    ) -> list[InstanceResponse]:
        instances = await service.list_visible(
            principal,
            kind=InstanceKind.VOLUNDR,
            enabled_only=True,
        )
        return [_to_response(instance) for instance in instances]

    @router.get("/observatory/snapshot")
    async def get_observatory_snapshot(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instances = await service.list_visible(principal, enabled_only=True)
        return await _build_observatory_snapshot(instances, request=request)

    @router.put(
        "/observatory/fragments/{source_id}",
        response_model=TopologySourceHealth,
        response_model_by_alias=True,
        summary="Publish this source's view of the topology",
    )
    async def put_observatory_fragment(
        source_id: str,
        fragment: ObservatoryFragment,
        principal: Principal = Depends(extract_principal),
        _scope: None = Depends(require_build_scope(TOPOLOGY_PUSH_SCOPE)),
    ) -> TopologySourceHealth:
        """Accept a fragment from a source the aggregator cannot reach.

        PUT rather than POST because a heartbeat is "this is my current state":
        publishing twice is publishing once, so there is no dedupe to get wrong
        and no way for a retry to double a source's nodes.

        Scoped to `observatory:topology:push`, so a resident on a bare-metal
        host can appear on the graph without holding a credential that can do
        anything else.
        """
        del principal
        if fragment_inbox is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Topology fragment inbox is not configured",
            )
        if fragment.meta and fragment.meta.source_id and fragment.meta.source_id != source_id:
            # The path is the key the fragment is stored under. A payload
            # claiming a different identity would silently overwrite, or
            # masquerade as, another source.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Fragment meta.sourceId '{fragment.meta.source_id}' does not match "
                    f"path source id '{source_id}'"
                ),
            )
        await fragment_inbox.accept(source_id, fragment)
        health = {source.source_id: source for _stored, source in await fragment_inbox.current()}
        return health[source_id]

    @router.delete(
        "/observatory/fragments/{source_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Forget a decommissioned topology source",
    )
    async def delete_observatory_fragment(
        source_id: str,
        principal: Principal = Depends(extract_principal),
        _scope: None = Depends(require_build_scope(TOPOLOGY_PUSH_SCOPE)),
    ) -> None:
        del principal
        if fragment_inbox is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Topology fragment inbox is not configured",
            )
        if not await fragment_inbox.forget(source_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No fragment published by source '{source_id}'",
            )

    @router.get(
        "/observatory/agents",
        response_model=AgentDirectoryPage,
        summary="Aggregate principal-visible A2A agents across Observatory instances",
    )
    async def list_observatory_agents(
        request: Request,
        skill: list[str] | None = Query(default=None),
        tag: list[str] | None = Query(default=None),
        kind: list[str] | None = Query(default=None),
        observed_status: list[str] | None = Query(default=None, alias="status"),
        environment_id: list[str] | None = Query(default=None, alias="environmentId"),
        cluster: list[str] | None = Query(default=None),
        instance: list[str] | None = Query(default=None),
        principal: Principal = Depends(extract_principal),
    ) -> AgentDirectoryPage:
        if agent_directory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent Directory aggregation is not configured",
            )
        instances = await service.list_visible(
            principal,
            kind=InstanceKind.OBSERVATORY,
            enabled_only=True,
        )
        return await agent_directory.list_agents(
            instances,
            principal,
            headers=_forward_headers(request),
            filters=AgentDirectoryFilters.from_values(
                skills=skill,
                tags=tag,
                kinds=kind,
                statuses=observed_status,
                environment_ids=environment_id,
                cluster_ids=cluster,
                instance_ids=instance,
            ),
        )

    @router.get(
        "/observatory/agents/{agent_id}",
        response_model=AgentDirectoryEntry,
        summary="Get one principal-visible aggregate A2A agent",
    )
    async def get_observatory_agent(
        request: Request,
        agent_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> AgentDirectoryEntry:
        if agent_directory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent Directory aggregation is not configured",
            )
        instances = await service.list_visible(
            principal,
            kind=InstanceKind.OBSERVATORY,
            enabled_only=True,
        )
        entry = await agent_directory.get_agent(
            agent_id,
            instances,
            principal,
            headers=_forward_headers(request),
        )
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        return entry

    return router

"""Shared instance registry REST endpoints — /api/v1/niuu/instances."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field
from starlette.types import ASGIApp

from identity.adapters.http_auth import authorization_http_errors
from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.remote_urls import (
    build_remote_url,
)
from niuu.adapters.inbound.remote_urls import (
    forward_identity_headers as _forward_headers,
)
from niuu.adapters.inbound.remote_urls import (
    forward_local_identity_headers as _forward_local_headers,
)
from niuu.adapters.inbound.rest_knowledge_deployments import create_knowledge_deployments_router
from niuu.adapters.outbound.guild_transport import (
    GuildTransportError,
    build_guild_httpx_client,
)
from niuu.domain.agent_directory import (
    AgentDirectoryEntry,
    AgentDirectoryFilters,
    AgentDirectoryPage,
)
from niuu.domain.models import (
    InstanceHealthStatus,
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.domain.observatory import (
    ObservatoryFragment,
    TopologySnapshot,
    TopologySourceHealth,
)
from niuu.domain.services.agent_directory import AgentDirectoryAggregationService
from niuu.domain.services.instance_health import InstanceHealthChecker
from niuu.domain.services.instances import (
    InstanceAccessError,
    InstanceService,
    InstanceTransportSecurityError,
    InstanceValidationError,
)
from niuu.domain.services.observatory_fragments import ObservatoryFragmentInboxService
from niuu.domain.services.observatory_topology import (
    ObservatoryTopologyAggregationService,
)
from niuu.domain.services.token_scope import TOPOLOGY_PUSH_SCOPE, require_scope

logger = logging.getLogger(__name__)


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
    health: str = Field(default=InstanceHealthStatus.UNKNOWN.value)
    last_seen_at: datetime | None = Field(default=None, serialization_alias="lastSeenAt")
    last_checked_at: datetime | None = Field(default=None, serialization_alias="lastCheckedAt")
    last_error: str | None = Field(default=None, serialization_alias="lastError")


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
        health=instance.health.value,
        last_seen_at=instance.last_seen_at,
        last_checked_at=instance.last_checked_at,
        last_error=instance.last_error,
    )


def _uses_embedded_transport(instance: RegisteredInstance) -> bool:
    return str(instance.config.get("transport", "")).strip().lower() == "embedded"


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
                headers=_forward_local_headers(request),
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
    try:
        client = await build_guild_httpx_client(
            instance, dial_url=instance.base_url, timeout_seconds=20.0, follow_redirects=True
        )
    except GuildTransportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    async with client:
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
    health_checker: InstanceHealthChecker,
    embedded_forge_app: ASGIApp | None = None,
    agent_directory: AgentDirectoryAggregationService | None = None,
    fragment_inbox: ObservatoryFragmentInboxService | None = None,
    topology: ObservatoryTopologyAggregationService | None = None,
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
        except InstanceTransportSecurityError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        except (InstanceAccessError, InstanceValidationError) as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        # Registering an instance that happens to be offline (e.g. a Spark not
        # yet powered on) is a legitimate operator action, so a failed probe
        # does not reject the registration — but the instance is recorded as
        # unreachable immediately, never left looking merely idle.
        #
        # The registration itself already succeeded and is durably saved by
        # this point; a crash in the immediate follow-up health check (e.g.
        # a database hiccup writing the health row — check_instance already
        # turns a crashing *probe* into a plain UNREACHABLE result) must not
        # turn a successful create into a 500. The periodic loop will pick
        # the instance up and record its health on the next sweep.
        try:
            checked = await health_checker.check_instance(instance)
        except Exception:
            logger.exception(
                "Health check crashed immediately after registering instance %s", instance.id
            )
            return _to_response(instance)
        instance = replace(
            instance,
            health=checked.health,
            last_seen_at=checked.last_seen_at,
            last_checked_at=checked.checked_at,
            last_error=checked.last_error,
        )
        return _to_response(instance)

    @router.patch("/instances/{instance_id}", response_model=InstanceResponse)
    async def update_instance(
        body: InstanceUpdateRequest,
        instance_id: str = Path(description="Registered instance UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> InstanceResponse:
        before = await service.get_visible(principal, instance_id)
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
        except InstanceTransportSecurityError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        except (InstanceAccessError, InstanceValidationError) as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if before is not None and (
            before.base_url != instance.base_url
            or _uses_embedded_transport(before) != _uses_embedded_transport(instance)
        ):
            # The endpoint this instance actually resolves to changed —
            # health.record for the old endpoint says nothing about the new
            # one, so re-probe immediately instead of waiting for the next
            # periodic sweep (up to niuu.health.interval_seconds stale).
            try:
                checked = await health_checker.check_instance(instance)
            except Exception:
                logger.exception("Health check crashed after re-pointing instance %s", instance.id)
                return _to_response(instance)
            instance = replace(
                instance,
                health=checked.health,
                last_seen_at=checked.last_seen_at,
                last_checked_at=checked.checked_at,
                last_error=checked.last_error,
            )
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
        checked = await health_checker.check_instance(instance)
        return InstanceTestResponse(
            ok=checked.probe.ok,
            status_code=checked.probe.status_code,
            message=checked.probe.message,
        )

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

    @router.get(
        "/observatory/snapshot",
        response_model=TopologySnapshot,
        response_model_by_alias=True,
        summary="Merged topology across every reporting source",
    )
    async def get_observatory_snapshot(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> TopologySnapshot:
        if topology is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Topology aggregation is not configured",
            )
        instances = await service.list_visible(
            principal,
            kind=InstanceKind.OBSERVATORY,
            enabled_only=True,
        )
        return await topology.get_snapshot(
            instances, headers=_forward_headers(request), principal=principal
        )

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
        _scope: None = Depends(require_scope(TOPOLOGY_PUSH_SCOPE)),
    ) -> TopologySourceHealth:
        """Accept a fragment from a source the aggregator cannot reach.

        PUT rather than POST because a heartbeat is "this is my current state":
        publishing twice is publishing once, so there is no dedupe to get wrong
        and no way for a retry to double a source's nodes.

        Scoped to `observatory:topology:push`, so a resident on a bare-metal
        host can appear on the graph without holding a credential that can do
        anything else.
        """
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
        with authorization_http_errors():
            await fragment_inbox.accept(source_id, fragment, principal=principal)
        health = {
            source.source_id: source
            for _stored, source in await fragment_inbox.current(principal=principal)
        }
        return health[source_id]

    @router.delete(
        "/observatory/fragments/{source_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Forget a decommissioned topology source",
    )
    async def delete_observatory_fragment(
        source_id: str,
        principal: Principal = Depends(extract_principal),
        _scope: None = Depends(require_scope(TOPOLOGY_PUSH_SCOPE)),
    ) -> None:
        if fragment_inbox is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Topology fragment inbox is not configured",
            )
        with authorization_http_errors():
            deleted = await fragment_inbox.forget(source_id, principal=principal)
        if not deleted:
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

    router.include_router(create_knowledge_deployments_router(service))
    return router

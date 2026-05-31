"""Shared instance registry REST endpoints — /api/v1/niuu/instances."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.remote_urls import build_remote_url
from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.domain.services.instances import (
    InstanceAccessError,
    InstanceService,
    InstanceValidationError,
)


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
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


async def _probe_instance(instance: RegisteredInstance) -> InstanceTestResponse:
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


def _base_demo_nodes() -> list[dict[str, Any]]:
    return [
        {
            "id": "service:guild",
            "typeId": "service",
            "label": "Guild",
            "parentId": None,
            "status": "healthy",
            "svcType": "niuu",
            "purpose": "shared instance registry and discovery surface",
            "sourceKind": "guild",
            "sourceId": "guild",
            "layoutHints": {
                "mode": "manual",
                "scope": "node",
                "anchor": {"x": 0, "y": 0, "pinned": True},
                "note": "Guild anchor until Valkyrie or operator hints provide world placement.",
            },
        },
        {
            "id": "realm-asgard",
            "typeId": "realm",
            "label": "asgard",
            "parentId": None,
            "status": "healthy",
            "zone": "asgard",
            "vlan": 90,
            "dns": "asgard.niuu.world",
            "purpose": "AI / compute / dev",
        },
        {
            "id": "realm-svartalfheim",
            "typeId": "realm",
            "label": "svartalfheim",
            "parentId": None,
            "status": "healthy",
            "zone": "svartalfheim",
            "vlan": 40,
            "dns": "svartalfheim.niuu.world",
            "purpose": "platform / operations / services",
        },
        {
            "id": "realm-vanaheim",
            "typeId": "realm",
            "label": "vanaheim",
            "parentId": None,
            "status": "healthy",
            "zone": "vanaheim",
            "vlan": 80,
            "dns": "vanaheim.niuu.world",
            "purpose": "single machines / labs / edge hosts",
        },
        {
            "id": "cluster-valaskjalf",
            "typeId": "cluster",
            "label": "valaskjálf",
            "parentId": "realm-asgard",
            "status": "healthy",
            "zone": "asgard",
            "purpose": "AI cluster",
        },
        {
            "id": "cluster-himinbjorg",
            "typeId": "cluster",
            "label": "himinbjörg",
            "parentId": "realm-asgard",
            "status": "healthy",
            "zone": "asgard",
            "purpose": "inference edge",
        },
        {
            "id": "cluster-bilskirnir",
            "typeId": "cluster",
            "label": "bilskírnir",
            "parentId": "realm-asgard",
            "status": "healthy",
            "zone": "asgard",
            "purpose": "build and release",
        },
        {
            "id": "cluster-glitnir",
            "typeId": "cluster",
            "label": "glitnir",
            "parentId": "realm-svartalfheim",
            "status": "healthy",
            "zone": "svartalfheim",
            "purpose": "operations cluster",
        },
        {
            "id": "cluster-nidavellir",
            "typeId": "cluster",
            "label": "niðavellir",
            "parentId": "realm-svartalfheim",
            "status": "healthy",
            "zone": "svartalfheim",
            "purpose": "data and shared services",
        },
        {
            "id": "host-mjolnir",
            "typeId": "host",
            "label": "mjölnir",
            "parentId": "realm-asgard",
            "status": "healthy",
            "zone": "asgard",
            "hw": "DGX Spark",
            "os": "Ubuntu 24",
            "cores": 144,
            "ram": "1 TiB",
            "gpu": "GH200",
        },
        {
            "id": "host-brokkr",
            "typeId": "host",
            "label": "brokkr",
            "parentId": "realm-svartalfheim",
            "status": "healthy",
            "zone": "svartalfheim",
            "hw": "EPYC",
            "os": "Ubuntu 24",
            "cores": 64,
            "ram": "256 GiB",
            "gpu": None,
        },
        {
            "id": "host-lif",
            "typeId": "host",
            "label": "líf",
            "parentId": "realm-vanaheim",
            "status": "healthy",
            "zone": "vanaheim",
            "hw": "NUC 13 Pro",
            "os": "Ubuntu 24",
            "cores": 16,
            "ram": "64 GiB",
            "gpu": None,
        },
        {
            "id": "host-lifthrasir",
            "typeId": "host",
            "label": "lífþrasir",
            "parentId": "realm-vanaheim",
            "status": "healthy",
            "zone": "vanaheim",
            "hw": "Mac mini M4",
            "os": "macOS 15",
            "cores": 14,
            "ram": "48 GiB",
            "gpu": "integrated",
        },
        {
            "id": "host-folkvangr",
            "typeId": "host",
            "label": "fólkvangr",
            "parentId": "realm-vanaheim",
            "status": "idle",
            "zone": "vanaheim",
            "hw": "Threadripper",
            "os": "Ubuntu 24",
            "cores": 48,
            "ram": "192 GiB",
            "gpu": "RTX 6000 Ada",
        },
        {
            "id": "valkyrie-valaskjalf",
            "typeId": "valkyrie",
            "label": "valkyrie/asgard",
            "parentId": "cluster-valaskjalf",
            "status": "healthy",
            "zone": "asgard",
            "autonomy": "notify",
            "specialty": "cluster scout",
        },
        {
            "id": "valkyrie-himinbjorg",
            "typeId": "valkyrie",
            "label": "valkyrie/inference",
            "parentId": "cluster-himinbjorg",
            "status": "healthy",
            "zone": "asgard",
            "autonomy": "notify",
            "specialty": "cluster scout",
        },
        {
            "id": "valkyrie-bilskirnir",
            "typeId": "valkyrie",
            "label": "valkyrie/build",
            "parentId": "cluster-bilskirnir",
            "status": "healthy",
            "zone": "asgard",
            "autonomy": "notify",
            "specialty": "cluster scout",
        },
        {
            "id": "valkyrie-glitnir",
            "typeId": "valkyrie",
            "label": "valkyrie/svartalfheim",
            "parentId": "cluster-glitnir",
            "status": "healthy",
            "zone": "svartalfheim",
            "autonomy": "notify",
            "specialty": "cluster scout",
        },
        {
            "id": "valkyrie-nidavellir",
            "typeId": "valkyrie",
            "label": "valkyrie/shared",
            "parentId": "cluster-nidavellir",
            "status": "healthy",
            "zone": "svartalfheim",
            "autonomy": "notify",
            "specialty": "cluster scout",
        },
        {
            "id": "run-audit",
            "typeId": "run",
            "label": "run-audit",
            "parentId": "cluster-glitnir",
            "status": "observing",
            "zone": "svartalfheim",
            "state": "working",
            "purpose": "audit cerbos policies",
            "flockId": "workflow-audit",
        },
        {
            "id": "run-refactor",
            "typeId": "run",
            "label": "run-refactor",
            "parentId": "cluster-valaskjalf",
            "status": "observing",
            "zone": "asgard",
            "state": "forming",
            "purpose": "refactor bifrost routing",
            "flockId": "workflow-refactor",
        },
        {
            "id": "run-migrate",
            "typeId": "run",
            "label": "run-migrate",
            "parentId": "cluster-bilskirnir",
            "status": "observing",
            "zone": "asgard",
            "state": "working",
            "purpose": "migrate skuld workspace storage",
            "flockId": "workflow-migrate",
        },
        {
            "id": "run-latency",
            "typeId": "run",
            "label": "run-latency",
            "parentId": "cluster-himinbjorg",
            "status": "observing",
            "zone": "asgard",
            "state": "reviewing",
            "purpose": "benchmark edge inference latency",
            "flockId": "workflow-latency",
        },
        {
            "id": "run-curate",
            "typeId": "run",
            "label": "run-curate",
            "parentId": "cluster-glitnir",
            "status": "observing",
            "zone": "svartalfheim",
            "state": "curating",
            "purpose": "refresh a scratch Mimir shard for policy memory",
            "flockId": "workflow-curate",
        },
        {
            "id": "run-audit-trigger",
            "typeId": "trigger",
            "label": "policy changed",
            "parentId": "run-audit",
            "status": "healthy",
            "flockId": "workflow-audit",
            "layoutHints": {"order": 0, "packGroup": "entry"},
        },
        {
            "id": "run-audit-policy",
            "typeId": "resource",
            "label": "cerbos bundle",
            "parentId": "run-audit",
            "status": "healthy",
            "flockId": "workflow-audit",
            "layoutHints": {"order": 1, "packGroup": "resource"},
        },
        {
            "id": "run-audit-scan",
            "typeId": "stage",
            "label": "scan",
            "parentId": "run-audit",
            "status": "healthy",
            "flockId": "workflow-audit",
            "layoutHints": {"order": 2, "packGroup": "main"},
        },
        {
            "id": "run-audit-review",
            "typeId": "gate",
            "label": "review",
            "parentId": "run-audit",
            "status": "healthy",
            "flockId": "workflow-audit",
            "layoutHints": {"order": 3, "packGroup": "decision"},
        },
        {
            "id": "run-audit-remediate",
            "typeId": "stage",
            "label": "remediate",
            "parentId": "run-audit",
            "status": "healthy",
            "flockId": "workflow-audit",
            "layoutHints": {"order": 4, "packGroup": "main"},
        },
        {
            "id": "run-audit-end",
            "typeId": "end",
            "label": "closed",
            "parentId": "run-audit",
            "status": "healthy",
            "flockId": "workflow-audit",
            "layoutHints": {"order": 5, "packGroup": "exit"},
        },
        {
            "id": "run-refactor-trigger",
            "typeId": "trigger",
            "label": "code requested",
            "parentId": "run-refactor",
            "status": "healthy",
            "flockId": "workflow-refactor",
            "layoutHints": {"order": 0, "packGroup": "entry"},
        },
        {
            "id": "run-refactor-context",
            "typeId": "resource",
            "label": "context pack",
            "parentId": "run-refactor",
            "status": "healthy",
            "flockId": "workflow-refactor",
            "layoutHints": {"order": 1, "packGroup": "resource"},
        },
        {
            "id": "run-refactor-analyze",
            "typeId": "stage",
            "label": "analyze",
            "parentId": "run-refactor",
            "status": "healthy",
            "flockId": "workflow-refactor",
            "layoutHints": {"order": 2, "packGroup": "main"},
        },
        {
            "id": "run-refactor-review",
            "typeId": "gate",
            "label": "review",
            "parentId": "run-refactor",
            "status": "healthy",
            "flockId": "workflow-refactor",
            "layoutHints": {"order": 3, "packGroup": "decision"},
        },
        {
            "id": "run-refactor-patch",
            "typeId": "stage",
            "label": "patch",
            "parentId": "run-refactor",
            "status": "healthy",
            "flockId": "workflow-refactor",
            "layoutHints": {"order": 4, "packGroup": "main"},
        },
        {
            "id": "run-refactor-validate",
            "typeId": "stage",
            "label": "validate",
            "parentId": "run-refactor",
            "status": "healthy",
            "flockId": "workflow-refactor",
            "layoutHints": {"order": 5, "packGroup": "main"},
        },
        {
            "id": "run-refactor-end",
            "typeId": "end",
            "label": "merge ready",
            "parentId": "run-refactor",
            "status": "healthy",
            "flockId": "workflow-refactor",
            "layoutHints": {"order": 6, "packGroup": "exit"},
        },
        {
            "id": "run-migrate-trigger",
            "typeId": "trigger",
            "label": "migration requested",
            "parentId": "run-migrate",
            "status": "healthy",
            "flockId": "workflow-migrate",
            "layoutHints": {"order": 0, "packGroup": "entry"},
        },
        {
            "id": "run-migrate-state",
            "typeId": "resource",
            "label": "workspace state",
            "parentId": "run-migrate",
            "status": "healthy",
            "flockId": "workflow-migrate",
            "layoutHints": {"order": 1, "packGroup": "resource"},
        },
        {
            "id": "run-migrate-plan",
            "typeId": "stage",
            "label": "plan",
            "parentId": "run-migrate",
            "status": "healthy",
            "flockId": "workflow-migrate",
            "layoutHints": {"order": 2, "packGroup": "main"},
        },
        {
            "id": "run-migrate-canary",
            "typeId": "cond",
            "label": "canary",
            "parentId": "run-migrate",
            "status": "healthy",
            "flockId": "workflow-migrate",
            "layoutHints": {"order": 3, "packGroup": "decision"},
        },
        {
            "id": "run-migrate-rollout",
            "typeId": "stage",
            "label": "rollout",
            "parentId": "run-migrate",
            "status": "healthy",
            "flockId": "workflow-migrate",
            "layoutHints": {"order": 4, "packGroup": "main"},
        },
        {
            "id": "run-migrate-rollback",
            "typeId": "stage",
            "label": "rollback",
            "parentId": "run-migrate",
            "status": "idle",
            "flockId": "workflow-migrate",
            "layoutHints": {"order": 5, "packGroup": "main"},
        },
        {
            "id": "run-migrate-end",
            "typeId": "end",
            "label": "steady",
            "parentId": "run-migrate",
            "status": "healthy",
            "flockId": "workflow-migrate",
            "layoutHints": {"order": 6, "packGroup": "exit"},
        },
        {
            "id": "run-latency-trigger",
            "typeId": "trigger",
            "label": "benchmark requested",
            "parentId": "run-latency",
            "status": "healthy",
            "flockId": "workflow-latency",
            "layoutHints": {"order": 0, "packGroup": "entry"},
        },
        {
            "id": "run-latency-dataset",
            "typeId": "resource",
            "label": "trace set",
            "parentId": "run-latency",
            "status": "healthy",
            "flockId": "workflow-latency",
            "layoutHints": {"order": 1, "packGroup": "resource"},
        },
        {
            "id": "run-latency-profile",
            "typeId": "stage",
            "label": "profile",
            "parentId": "run-latency",
            "status": "healthy",
            "flockId": "workflow-latency",
            "layoutHints": {"order": 2, "packGroup": "main"},
        },
        {
            "id": "run-latency-compare",
            "typeId": "stage",
            "label": "compare",
            "parentId": "run-latency",
            "status": "healthy",
            "flockId": "workflow-latency",
            "layoutHints": {"order": 3, "packGroup": "main"},
        },
        {
            "id": "run-latency-judge",
            "typeId": "gate",
            "label": "judge",
            "parentId": "run-latency",
            "status": "healthy",
            "flockId": "workflow-latency",
            "layoutHints": {"order": 4, "packGroup": "decision"},
        },
        {
            "id": "run-latency-end",
            "typeId": "end",
            "label": "report",
            "parentId": "run-latency",
            "status": "healthy",
            "flockId": "workflow-latency",
            "layoutHints": {"order": 5, "packGroup": "exit"},
        },
        {
            "id": "run-curate-trigger",
            "typeId": "trigger",
            "label": "refresh requested",
            "parentId": "run-curate",
            "status": "healthy",
            "flockId": "workflow-curate",
            "layoutHints": {"order": 0, "packGroup": "entry"},
        },
        {
            "id": "run-curate-corpus",
            "typeId": "resource",
            "label": "policy shard",
            "parentId": "run-curate",
            "status": "healthy",
            "flockId": "workflow-curate",
            "layoutHints": {"order": 1, "packGroup": "resource"},
        },
        {
            "id": "run-curate-mimir",
            "typeId": "mimir",
            "label": "scratch",
            "parentId": "run-curate",
            "status": "observing",
            "flockId": "workflow-curate",
            "pages": 84,
            "writes": 6,
            "mountCount": 1,
            "mounts": ["mimir://scratch/policies"],
            "layoutHints": {"order": 2, "packGroup": "resource"},
        },
        {
            "id": "run-curate-embed",
            "typeId": "stage",
            "label": "embed",
            "parentId": "run-curate",
            "status": "healthy",
            "flockId": "workflow-curate",
            "layoutHints": {"order": 3, "packGroup": "main"},
        },
        {
            "id": "run-curate-judge",
            "typeId": "gate",
            "label": "curate",
            "parentId": "run-curate",
            "status": "healthy",
            "flockId": "workflow-curate",
            "layoutHints": {"order": 4, "packGroup": "decision"},
        },
        {
            "id": "run-curate-end",
            "typeId": "end",
            "label": "published",
            "parentId": "run-curate",
            "status": "healthy",
            "flockId": "workflow-curate",
            "layoutHints": {"order": 5, "packGroup": "exit"},
        },
        {
            "id": "ravn-huginn",
            "typeId": "ravn_long",
            "label": "huginn",
            "parentId": "host-mjolnir",
            "status": "healthy",
            "hostId": "host-mjolnir",
            "persona": "thought",
            "specialty": "architecture",
            "tokens": 42800,
        },
        {
            "id": "ravn-muninn",
            "typeId": "ravn_long",
            "label": "muninn",
            "parentId": "host-brokkr",
            "status": "idle",
            "hostId": "host-brokkr",
            "persona": "memory",
            "specialty": "context",
            "tokens": 18200,
        },
        {
            "id": "ravn-verdandi",
            "typeId": "ravn_long",
            "label": "verdandi",
            "parentId": "host-lif",
            "status": "healthy",
            "hostId": "host-lif",
            "persona": "routing",
            "specialty": "edge networking",
            "tokens": 14200,
        },
        {
            "id": "ravn-skuld",
            "typeId": "ravn_long",
            "label": "skuld",
            "parentId": "host-lifthrasir",
            "status": "idle",
            "hostId": "host-lifthrasir",
            "persona": "storage",
            "specialty": "durability",
            "tokens": 9600,
        },
        {
            "id": "service-openbao",
            "typeId": "service",
            "label": "openbao",
            "parentId": "cluster-glitnir",
            "status": "healthy",
            "svcType": "secrets",
        },
        {
            "id": "service-cerbos",
            "typeId": "service",
            "label": "cerbos",
            "parentId": "cluster-glitnir",
            "status": "healthy",
            "svcType": "authz",
        },
        {
            "id": "service-grafana",
            "typeId": "service",
            "label": "grafana",
            "parentId": "cluster-glitnir",
            "status": "healthy",
            "svcType": "dashboard",
        },
        {
            "id": "service-sleipnir",
            "typeId": "service",
            "label": "sleipnir",
            "parentId": "cluster-glitnir",
            "status": "healthy",
            "svcType": "rabbitmq",
        },
        {
            "id": "service-ting",
            "typeId": "ting",
            "label": "ting",
            "parentId": "cluster-valaskjalf",
            "status": "healthy",
            "svcType": "ting",
        },
        {
            "id": "service-volundr",
            "typeId": "volundr",
            "label": "volundr",
            "parentId": "cluster-valaskjalf",
            "status": "healthy",
            "svcType": "volundr",
        },
        {
            "id": "service-ravn",
            "typeId": "service",
            "label": "ravn",
            "parentId": "cluster-himinbjorg",
            "status": "healthy",
            "svcType": "ravn",
        },
        {
            "id": "service-embeddings",
            "typeId": "service",
            "label": "embeddings",
            "parentId": "cluster-himinbjorg",
            "status": "healthy",
            "svcType": "inference",
        },
        {
            "id": "service-heimdall",
            "typeId": "service",
            "label": "heimdall",
            "parentId": "cluster-bilskirnir",
            "status": "healthy",
            "svcType": "build",
        },
        {
            "id": "service-registry",
            "typeId": "service",
            "label": "registry",
            "parentId": "cluster-bilskirnir",
            "status": "healthy",
            "svcType": "artifact",
        },
        {
            "id": "beacon-hall",
            "typeId": "beacon",
            "label": "hall beacon",
            "parentId": "realm-svartalfheim",
            "status": "healthy",
        },
        {
            "id": "printer-gungnir",
            "typeId": "printer",
            "label": "gungnir",
            "parentId": "realm-svartalfheim",
            "status": "healthy",
            "model": "Saturn 4 Ultra",
        },
        {
            "id": "service-observatory",
            "typeId": "service",
            "label": "observatory",
            "parentId": "cluster-nidavellir",
            "status": "healthy",
            "svcType": "observatory",
        },
        {
            "id": "service-bifrost",
            "typeId": "bifrost",
            "label": "bifrost",
            "parentId": "cluster-nidavellir",
            "status": "healthy",
            "svcType": "bifrost",
        },
        {
            "id": "service-loki",
            "typeId": "service",
            "label": "loki",
            "parentId": "cluster-nidavellir",
            "status": "healthy",
            "svcType": "logs",
        },
    ]


def _base_demo_edges() -> list[dict[str, str]]:
    return [
        {
            "id": "edge:guild:observatory",
            "sourceId": "service:guild",
            "targetId": "service-observatory",
            "kind": "soft",
        },
        {
            "id": "edge:run:audit:trigger",
            "sourceId": "run-audit-trigger",
            "targetId": "run-audit-scan",
            "kind": "run",
            "label": "policy.changed -> policy.changed",
        },
        {
            "id": "edge:run:audit:resource",
            "sourceId": "run-audit-policy",
            "targetId": "run-audit-scan",
            "kind": "soft",
            "label": "read",
        },
        {
            "id": "edge:run:audit:gate",
            "sourceId": "run-audit-scan",
            "targetId": "run-audit-review",
            "kind": "run",
            "label": "findings.ready -> review.requested",
        },
        {
            "id": "edge:run:audit:approve",
            "sourceId": "run-audit-review",
            "targetId": "run-audit-remediate",
            "kind": "run",
            "label": "review.approved -> remediation.begin",
        },
        {
            "id": "edge:run:audit:end",
            "sourceId": "run-audit-remediate",
            "targetId": "run-audit-end",
            "kind": "run",
            "label": "policy.fixed -> policy.fixed",
        },
        {
            "id": "edge:run:refactor:trigger",
            "sourceId": "run-refactor-trigger",
            "targetId": "run-refactor-analyze",
            "kind": "run",
            "label": "code.requested -> code.requested",
        },
        {
            "id": "edge:run:refactor:resource",
            "sourceId": "run-refactor-context",
            "targetId": "run-refactor-analyze",
            "kind": "soft",
            "label": "read",
        },
        {
            "id": "edge:run:refactor:review",
            "sourceId": "run-refactor-analyze",
            "targetId": "run-refactor-review",
            "kind": "run",
            "label": "patch.proposed -> review.requested",
        },
        {
            "id": "edge:run:refactor:approve",
            "sourceId": "run-refactor-review",
            "targetId": "run-refactor-patch",
            "kind": "run",
            "label": "review.approved -> patch.apply",
        },
        {
            "id": "edge:run:refactor:validate",
            "sourceId": "run-refactor-patch",
            "targetId": "run-refactor-validate",
            "kind": "run",
            "label": "patch.applied -> validate.requested",
        },
        {
            "id": "edge:run:refactor:end",
            "sourceId": "run-refactor-validate",
            "targetId": "run-refactor-end",
            "kind": "run",
            "label": "merge.ready -> merge.ready",
        },
        {
            "id": "edge:run:migrate:trigger",
            "sourceId": "run-migrate-trigger",
            "targetId": "run-migrate-plan",
            "kind": "run",
            "label": "workspace.requested -> workspace.requested",
        },
        {
            "id": "edge:run:migrate:resource",
            "sourceId": "run-migrate-state",
            "targetId": "run-migrate-plan",
            "kind": "soft",
            "label": "read",
        },
        {
            "id": "edge:run:migrate:canary",
            "sourceId": "run-migrate-plan",
            "targetId": "run-migrate-canary",
            "kind": "run",
            "label": "canary.requested -> canary.requested",
        },
        {
            "id": "edge:run:migrate:rollout",
            "sourceId": "run-migrate-canary",
            "targetId": "run-migrate-rollout",
            "kind": "run",
            "label": "canary.green -> rollout.begin",
        },
        {
            "id": "edge:run:migrate:rollback",
            "sourceId": "run-migrate-canary",
            "targetId": "run-migrate-rollback",
            "kind": "dashed-anim",
            "label": "canary.red -> rollback.begin",
        },
        {
            "id": "edge:run:migrate:end",
            "sourceId": "run-migrate-rollout",
            "targetId": "run-migrate-end",
            "kind": "run",
            "label": "storage.ready -> storage.ready",
        },
        {
            "id": "edge:run:latency:trigger",
            "sourceId": "run-latency-trigger",
            "targetId": "run-latency-profile",
            "kind": "run",
            "label": "benchmark.requested -> benchmark.requested",
        },
        {
            "id": "edge:run:latency:resource",
            "sourceId": "run-latency-dataset",
            "targetId": "run-latency-profile",
            "kind": "soft",
            "label": "read",
        },
        {
            "id": "edge:run:latency:compare",
            "sourceId": "run-latency-profile",
            "targetId": "run-latency-compare",
            "kind": "run",
            "label": "trace.ready -> compare.requested",
        },
        {
            "id": "edge:run:latency:judge",
            "sourceId": "run-latency-compare",
            "targetId": "run-latency-judge",
            "kind": "run",
            "label": "comparison.ready -> review.requested",
        },
        {
            "id": "edge:run:latency:end",
            "sourceId": "run-latency-judge",
            "targetId": "run-latency-end",
            "kind": "run",
            "label": "report.approved -> report.approved",
        },
        {
            "id": "edge:run:curate:trigger",
            "sourceId": "run-curate-trigger",
            "targetId": "run-curate-embed",
            "kind": "run",
            "label": "refresh.requested -> refresh.requested",
        },
        {
            "id": "edge:run:curate:resource",
            "sourceId": "run-curate-corpus",
            "targetId": "run-curate-embed",
            "kind": "soft",
            "label": "read",
        },
        {
            "id": "edge:run:curate:mimir",
            "sourceId": "run-curate-mimir",
            "targetId": "run-curate-embed",
            "kind": "soft",
            "label": "context",
        },
        {
            "id": "edge:run:curate:judge",
            "sourceId": "run-curate-embed",
            "targetId": "run-curate-judge",
            "kind": "run",
            "label": "memory.ready -> review.requested",
        },
        {
            "id": "edge:run:curate:end",
            "sourceId": "run-curate-judge",
            "targetId": "run-curate-end",
            "kind": "run",
            "label": "memory.published -> memory.published",
        },
        {
            "id": "edge:valkyrie:observatory",
            "sourceId": "valkyrie-nidavellir",
            "targetId": "service-observatory",
            "kind": "soft",
        },
        {
            "id": "edge:valkyrie:ting",
            "sourceId": "valkyrie-valaskjalf",
            "targetId": "service-ting",
            "kind": "soft",
        },
        {
            "id": "edge:ravn:refactor",
            "sourceId": "ravn-huginn",
            "targetId": "run-refactor",
            "kind": "dashed-anim",
        },
        {
            "id": "edge:ravn:audit",
            "sourceId": "ravn-muninn",
            "targetId": "run-audit",
            "kind": "dashed-anim",
        },
        {
            "id": "edge:ravn:curate",
            "sourceId": "ravn-skuld",
            "targetId": "run-curate",
            "kind": "dashed-anim",
        },
        {
            "id": "edge:ravn:mimir",
            "sourceId": "ravn-huginn",
            "targetId": "mimir-well",
            "kind": "dashed-long",
        },
    ]


def _overlay_instance_demo_nodes(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, str]],
    instances: list[RegisteredInstance],
    probe_by_id: dict[str, InstanceTestResponse],
) -> None:
    cluster_cycle = (
        "cluster-valaskjalf",
        "cluster-himinbjorg",
        "cluster-bilskirnir",
        "cluster-glitnir",
        "cluster-nidavellir",
    )
    per_kind_index: dict[InstanceKind, int] = {}

    for instance in instances:
        probe = probe_by_id[instance.id]
        kind_index = per_kind_index.get(instance.kind, 0)
        per_kind_index[instance.kind] = kind_index + 1
        parent_id = cluster_cycle[kind_index % len(cluster_cycle)]
        node_id = f"instance:{instance.kind.value}:{_slug(instance.slug or instance.name)}"
        type_id = _kind_type_id(instance.kind)
        svc_type = _kind_svc_type(instance.kind)
        is_primary_mimir = instance.kind is InstanceKind.MIMIR and kind_index == 0
        resolved_node_id = "mimir-well" if is_primary_mimir else node_id
        resolved_type_id = "mimir" if is_primary_mimir else type_id
        node_payload: dict[str, Any] = {
            "id": resolved_node_id,
            "typeId": resolved_type_id,
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
            "layoutHints": {
                "mode": "pack",
                "scope": "node",
                "pack_group": svc_type,
                "order": 0 if instance.is_default else 10 + kind_index,
            },
        }
        if instance.kind is InstanceKind.MIMIR:
            node_payload.update(
                {
                    "mountCount": 12,
                    "pages": 12420,
                    "writes": 83,
                }
            )
        nodes.append(node_payload)
        edges.append(
            {
                "id": f"edge:instance:{instance.id}",
                "sourceId": "service:guild",
                "targetId": resolved_node_id,
                "kind": "soft",
            }
        )

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
                    }
                )


async def _build_observatory_snapshot(
    instances: list[RegisteredInstance],
) -> dict[str, Any]:
    now = datetime.now(UTC)
    probes = await asyncio.gather(*[_probe_instance(instance) for instance in instances])
    probe_by_id = {instance.id: probe for instance, probe in zip(instances, probes, strict=False)}

    nodes: list[dict[str, Any]] = _base_demo_nodes()
    edges: list[dict[str, str]] = _base_demo_edges()
    events: list[dict[str, str]] = []

    _overlay_instance_demo_nodes(nodes, edges, instances, probe_by_id)

    if not any(node["id"] == "mimir-well" for node in nodes):
        nodes.append(
            {
                "id": "mimir-well",
                "typeId": "mimir",
                "label": "mímir",
                "parentId": "cluster-nidavellir",
                "status": "healthy",
                "mountCount": 9,
                "pages": 11840,
                "writes": 71,
            }
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
            "mode": "hybrid",
            "scope": "world",
            "note": (
                "Registry-first baseline layout until Valkyrie and service fragments "
                "enrich the snapshot."
            ),
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
) -> list[dict[str, Any]]:
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


def create_instances_router(service: InstanceService) -> APIRouter:
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
        return await _probe_instance(instance)

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
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, Any]:
        instances = await service.list_visible(principal, enabled_only=True)
        return await _build_observatory_snapshot(instances)

    return router

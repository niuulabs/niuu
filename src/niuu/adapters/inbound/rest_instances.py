"""Shared instance registry REST endpoints — /api/v1/niuu/instances."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from niuu.adapters.inbound.auth import extract_principal
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


async def _load_remote_sessions(
    instance: RegisteredInstance,
    request: Request,
    *,
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    query = f"?status={status_filter}" if status_filter else ""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(
            f"{instance.base_url}/api/v1/forge/sessions{query}",
            headers=_forward_headers(request),
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

    return router

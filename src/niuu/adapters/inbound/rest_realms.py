"""Shared FastAPI REST adapter for realm governance.

Exposes realms, their trust grants, and their capabilities over HTTP so ravn can
read a Valkyrie's build capability, trust level, and per-Valkyrie build config
without a ravn-local database. Mounted the same way as the PAT router: each host
passes its own ``extract_principal`` auth dependency.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from identity.models import Resource
from identity.ports import AuthorizationEvaluationError
from niuu.domain.models import Capability, Principal, Realm, TrustGrant
from niuu.domain.services.realm import RealmNotFoundError, RealmService

REALM_DETAIL_STREAM_EVENT = "realm.detail"


def _realm_resource(realm: Realm) -> Resource:
    """Describe a realm for an authorization decision."""
    return Resource(
        kind="realm",
        id=str(realm.id),
        attr={"tenant_id": realm.tenant_id, "owner_id": realm.owner_id or ""},
    )


def _to_sse(payload: object, *, event: str) -> str:
    """Serialize a named realm-detail event as an SSE frame."""
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class RealmResponse(BaseModel):
    """Response model for a realm."""

    id: str
    slug: str
    name: str
    sleipnir_domain: str | None
    owner_id: str | None
    instance_id: str | None
    autonomy_profile: str
    tenant_id: str
    project_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, realm: Realm) -> RealmResponse:
        return cls(
            id=str(realm.id),
            slug=realm.slug,
            name=realm.name,
            sleipnir_domain=realm.sleipnir_domain,
            owner_id=realm.owner_id,
            instance_id=realm.instance_id,
            autonomy_profile=realm.autonomy_profile,
            tenant_id=realm.tenant_id,
            project_id=str(realm.project_id) if realm.project_id else None,
            created_at=realm.created_at,
            updated_at=realm.updated_at,
        )


class CreateRealmRequest(BaseModel):
    """Request model for creating a realm."""

    slug: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description="URL-safe unique identifier for the realm",
    )
    name: str = Field(min_length=1, max_length=200)
    sleipnir_domain: str | None = None
    owner_id: str | None = None
    instance_id: str | None = None
    autonomy_profile: str = "balanced"
    project_id: UUID | None = None


class SetRealmProjectRequest(BaseModel):
    """Request model for moving a realm into a project, or out of one with null."""

    project_id: UUID | None


class UpsertRealmRequest(BaseModel):
    """Request model for syncing a realm onto this instance at a known id.

    For a remote instance to host a resident bound to a realm that lives on
    a different instance's database (e.g. Guild aggregating a realm created
    against ymir's database, deploying the resident on noatun/valhalla) —
    the same realm identity (id + slug) must exist here too, or the
    resident's realm_id foreign key has nothing to point at.
    """

    slug: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description="URL-safe unique identifier for the realm",
    )
    name: str = Field(min_length=1, max_length=200)
    sleipnir_domain: str | None = None
    owner_id: str | None = None
    instance_id: str | None = None
    autonomy_profile: str = "balanced"
    project_id: UUID | None = None


class TrustGrantResponse(BaseModel):
    """Response model for a trust grant."""

    id: str
    realm_id: str
    action_class: str
    target: str
    level: int
    limits: dict
    granted_by: str | None
    granted_at: datetime

    @classmethod
    def from_domain(cls, grant: TrustGrant) -> TrustGrantResponse:
        return cls(
            id=str(grant.id),
            realm_id=str(grant.realm_id),
            action_class=grant.action_class,
            target=grant.target,
            level=grant.level,
            limits=grant.limits,
            granted_by=grant.granted_by,
            granted_at=grant.granted_at,
        )


class CreateTrustGrantRequest(BaseModel):
    """Request model for granting trust to a realm's Valkyrie."""

    action_class: str = Field(min_length=1, max_length=50)
    target: str = "*"
    level: int = 0
    limits: dict = Field(default_factory=dict)
    granted_by: str | None = None


class CapabilityResponse(BaseModel):
    """Response model for a capability."""

    id: str
    realm_id: str
    name: str
    kind: str
    status: str
    trust_level: int
    mimir_page_path: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, capability: Capability) -> CapabilityResponse:
        return cls(
            id=str(capability.id),
            realm_id=str(capability.realm_id),
            name=capability.name,
            kind=capability.kind,
            status=capability.status,
            trust_level=capability.trust_level,
            mimir_page_path=capability.mimir_page_path,
            notes=capability.notes,
            created_at=capability.created_at,
            updated_at=capability.updated_at,
        )


class RecordCapabilityRequest(BaseModel):
    """Request model for recording a capability."""

    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=50)
    status: str = "gap"
    trust_level: int = 0
    mimir_page_path: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_realms_router(
    extract_principal: Callable[..., Awaitable[object]],
    prefix: str = "/api/v1/realms",
    *,
    stream_interval_seconds: float,
) -> APIRouter:
    """Create the realm governance router.

    Parameters
    ----------
    extract_principal:
        FastAPI-compatible dependency that returns a ``Principal``. Applied to
        every route so the caller is authenticated.
    prefix:
        URL prefix for the router (default ``/api/v1/realms``).
    stream_interval_seconds:
        Configured polling and keepalive interval for realm-detail streams.
    """
    router = APIRouter(
        prefix=prefix,
        tags=["Realms"],
        dependencies=[Depends(extract_principal)],
    )

    async def _realm_detail(
        service: RealmService, slug: str, tenant_id: str
    ) -> dict[str, object] | None:
        realm = await service.get_realm(slug, tenant_id)
        if realm is None:
            return None
        grants = await service.list_trust_grants(realm.id)
        capabilities = await service.list_capabilities(realm.id)
        return {
            "realm": RealmResponse.from_domain(realm).model_dump(mode="json"),
            "trust_grants": [
                TrustGrantResponse.from_domain(grant).model_dump(mode="json") for grant in grants
            ],
            "capabilities": [
                CapabilityResponse.from_domain(capability).model_dump(mode="json")
                for capability in capabilities
            ],
        }

    async def _realm_detail_stream(
        service: RealmService, slug: str, tenant_id: str
    ) -> AsyncGenerator[str, None]:
        """Emit complete realm-detail snapshots when its persisted state changes."""
        previous: str | None = None
        while True:
            detail = await _realm_detail(service, slug, tenant_id)
            if detail is None:
                return
            marker = json.dumps(detail, sort_keys=True, default=str)
            if marker != previous:
                previous = marker
                yield _to_sse(detail, event=REALM_DETAIL_STREAM_EVENT)
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(stream_interval_seconds)

    def _service(request: Request) -> RealmService:
        service: RealmService | None = getattr(request.app.state, "realm_service", None)
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Realm service is not configured",
            )
        return service

    def _tenant_id(principal: Principal) -> str:
        """Return the caller's tenant; a caller without one has no realms."""
        if not principal.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Realms require a tenant",
            )
        return principal.tenant_id

    async def _allowed_resources(
        request: Request, principal: Principal, action: str, resources: list[Resource]
    ) -> list[Resource]:
        authorization = getattr(request.app.state, "authorization", None)
        if authorization is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authorization unavailable",
            )
        try:
            return await authorization.filter_allowed(principal, action, resources)
        except AuthorizationEvaluationError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authorization unavailable",
            ) from exc

    async def _authorize(
        request: Request, principal: Principal, action: str, resource: Resource
    ) -> None:
        if not await _allowed_resources(request, principal, action, [resource]):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    async def _require_realm(
        request: Request, principal: Principal, slug: str, action: str
    ) -> Realm:
        realm = await _service(request).get_realm(slug, _tenant_id(principal))
        if realm is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Realm not found: {slug}",
            )
        await _authorize(request, principal, action, _realm_resource(realm))
        return realm

    async def _optional_realm(request: Request, principal: Principal, slug: str) -> Realm | None:
        """Return the realm or None — for list endpoints of an absent parent.

        Listing a sub-collection (trust grants, capabilities) of a realm that
        does not exist is an empty list, not an error: the dashboard queries
        these for many environments and only some have a realm, so a 404 per
        realm-less environment is noise. Reading or mutating a specific realm
        stays a real 404 via :func:`_require_realm`.
        """
        realm = await _service(request).get_realm(slug, _tenant_id(principal))
        if realm is None:
            return None
        await _authorize(request, principal, "read", _realm_resource(realm))
        return realm

    @router.get("", response_model=list[RealmResponse])
    async def list_realms(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> list[RealmResponse]:
        """List the caller's tenant's realms."""
        realms = await _service(request).list_realms(_tenant_id(principal))
        allowed = await _allowed_resources(
            request, principal, "list", [_realm_resource(realm) for realm in realms]
        )
        allowed_ids = {resource.id for resource in allowed}
        return [
            RealmResponse.from_domain(realm) for realm in realms if str(realm.id) in allowed_ids
        ]

    @router.post("", response_model=RealmResponse, status_code=status.HTTP_201_CREATED)
    async def create_realm(
        request: Request,
        body: CreateRealmRequest,
        principal: Principal = Depends(extract_principal),
    ) -> RealmResponse:
        """Create a new realm in the caller's tenant, owned by the caller by default."""
        tenant_id = _tenant_id(principal)
        owner_id = body.owner_id or principal.user_id
        await _authorize(
            request,
            principal,
            "create",
            Resource(
                kind="realm",
                id=body.slug,
                attr={"tenant_id": tenant_id, "owner_id": owner_id},
            ),
        )
        realm = await _service(request).create_realm(
            slug=body.slug,
            name=body.name,
            tenant_id=tenant_id,
            sleipnir_domain=body.sleipnir_domain,
            owner_id=owner_id,
            instance_id=body.instance_id,
            autonomy_profile=body.autonomy_profile,
            project_id=body.project_id,
        )
        return RealmResponse.from_domain(realm)

    @router.put("/by-id/{realm_id}", response_model=RealmResponse)
    async def upsert_realm(
        request: Request,
        realm_id: UUID,
        body: UpsertRealmRequest,
        principal: Principal = Depends(extract_principal),
    ) -> RealmResponse:
        """Create or replace a realm at a caller-supplied id.

        Used to sync a realm's identity onto this instance from another
        instance's database (see UpsertRealmRequest). The realm always lands
        in the caller's tenant. Replacing one is restricted to its owner or an
        admin, and a known id cannot reach a realm in another tenant.
        """
        tenant_id = _tenant_id(principal)
        existing = await _service(request).get_realm(realm_id, tenant_id)
        owner_id = body.owner_id or (existing.owner_id if existing else None) or principal.user_id
        if existing is None:
            await _authorize(
                request,
                principal,
                "create",
                Resource(
                    kind="realm",
                    id=str(realm_id),
                    attr={"tenant_id": tenant_id, "owner_id": owner_id},
                ),
            )
        else:
            await _authorize(request, principal, "update", _realm_resource(existing))
        try:
            realm = await _service(request).upsert_realm(
                realm_id,
                slug=body.slug,
                name=body.name,
                tenant_id=tenant_id,
                sleipnir_domain=body.sleipnir_domain,
                owner_id=owner_id,
                instance_id=body.instance_id,
                autonomy_profile=body.autonomy_profile,
                project_id=body.project_id,
            )
        except RealmNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return RealmResponse.from_domain(realm)

    @router.patch("/{slug}/project", response_model=RealmResponse)
    async def set_realm_project(
        request: Request,
        slug: str,
        body: SetRealmProjectRequest,
        principal: Principal = Depends(extract_principal),
    ) -> RealmResponse:
        """Move a realm into a project, or out of one."""
        await _require_realm(request, principal, slug, "update")
        realm = await _service(request).set_project(slug, _tenant_id(principal), body.project_id)
        return RealmResponse.from_domain(realm)

    @router.get("/{slug}", response_model=RealmResponse)
    async def get_realm(
        request: Request,
        slug: str,
        principal: Principal = Depends(extract_principal),
    ) -> RealmResponse:
        """Get a realm by slug."""
        realm = await _require_realm(request, principal, slug, "read")
        return RealmResponse.from_domain(realm)

    @router.get("/{slug}/stream", summary="Stream live realm detail")
    async def stream_realm_detail(
        request: Request,
        slug: str,
        principal: Principal = Depends(extract_principal),
    ) -> StreamingResponse:
        """Stream the realm, trust grants, and capabilities as ``realm.detail`` events."""
        realm = await _require_realm(request, principal, slug, "read")
        return StreamingResponse(
            _realm_detail_stream(_service(request), slug, realm.tenant_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_realm(
        request: Request,
        slug: str,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        """Delete a realm with its trust grants and capabilities.

        The realm's resident is a Ravn fleet object and is removed through
        ``DELETE /api/v1/ravn/ravens/{id}``; callers tear the resident down
        first so nothing keeps acting for a realm that no longer exists.
        """
        realm = await _require_realm(request, principal, slug, "delete")
        await _service(request).delete_realm(realm.id, realm.tenant_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{slug}/trust-grants", response_model=list[TrustGrantResponse])
    async def list_trust_grants(
        request: Request,
        slug: str,
        principal: Principal = Depends(extract_principal),
    ) -> list[TrustGrantResponse]:
        """List all trust grants for a realm (empty list when the realm is absent)."""
        realm = await _optional_realm(request, principal, slug)
        if realm is None:
            return []
        grants = await _service(request).list_trust_grants(realm.id)
        return [TrustGrantResponse.from_domain(grant) for grant in grants]

    @router.post(
        "/{slug}/trust-grants",
        response_model=TrustGrantResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def grant_trust(
        request: Request,
        slug: str,
        body: CreateTrustGrantRequest,
        principal: Principal = Depends(extract_principal),
    ) -> TrustGrantResponse:
        """Grant trust to a realm's Valkyrie for an action class."""
        realm = await _require_realm(request, principal, slug, "update")
        grant = await _service(request).grant_trust(
            realm.id,
            body.action_class,
            target=body.target,
            level=body.level,
            limits=body.limits,
            granted_by=body.granted_by,
        )
        return TrustGrantResponse.from_domain(grant)

    @router.get("/{slug}/capabilities", response_model=list[CapabilityResponse])
    async def list_capabilities(
        request: Request,
        slug: str,
        principal: Principal = Depends(extract_principal),
    ) -> list[CapabilityResponse]:
        """List all capabilities for a realm (empty list when the realm is absent)."""
        realm = await _optional_realm(request, principal, slug)
        if realm is None:
            return []
        capabilities = await _service(request).list_capabilities(realm.id)
        return [CapabilityResponse.from_domain(cap) for cap in capabilities]

    @router.post(
        "/{slug}/capabilities",
        response_model=CapabilityResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def record_capability(
        request: Request,
        slug: str,
        body: RecordCapabilityRequest,
        principal: Principal = Depends(extract_principal),
    ) -> CapabilityResponse:
        """Record (upsert) a capability for a realm.

        Residents report here as themselves rather than as the realm's owner,
        so this is its own action, separate from changing the realm.
        """
        realm = await _require_realm(request, principal, slug, "record_capability")
        capability = await _service(request).record_capability(
            realm.id,
            body.name,
            body.kind,
            status=body.status,
            trust_level=body.trust_level,
            mimir_page_path=body.mimir_page_path,
            notes=body.notes,
        )
        return CapabilityResponse.from_domain(capability)

    return router


__all__ = [
    "CapabilityResponse",
    "CreateRealmRequest",
    "CreateTrustGrantRequest",
    "REALM_DETAIL_STREAM_EVENT",
    "RealmResponse",
    "RecordCapabilityRequest",
    "TrustGrantResponse",
    "UpsertRealmRequest",
    "create_realms_router",
]

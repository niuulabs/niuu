"""Shared FastAPI REST adapter for realm governance.

Exposes realms, their trust grants, and their capabilities over HTTP so ravn can
read a Valkyrie's build capability, trust level, and per-Valkyrie build config
without a ravn-local database. Mounted the same way as the PAT router: each host
passes its own ``extract_principal`` auth dependency.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from niuu.domain.models import Capability, Realm, TrustGrant
from niuu.domain.services.realm import RealmService

logger = logging.getLogger(__name__)


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
) -> APIRouter:
    """Create the realm governance router.

    Parameters
    ----------
    extract_principal:
        FastAPI-compatible dependency that returns a ``Principal``. Applied to
        every route so the caller is authenticated.
    prefix:
        URL prefix for the router (default ``/api/v1/realms``).
    """
    router = APIRouter(
        prefix=prefix,
        tags=["Realms"],
        dependencies=[Depends(extract_principal)],
    )

    def _service(request: Request) -> RealmService:
        service: RealmService | None = getattr(request.app.state, "realm_service", None)
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Realm service is not configured",
            )
        return service

    async def _require_realm(request: Request, slug: str) -> Realm:
        realm = await _optional_realm(request, slug)
        if realm is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Realm not found: {slug}",
            )
        return realm

    async def _optional_realm(request: Request, slug: str) -> Realm | None:
        """Return the realm or None — for list endpoints of an absent parent.

        Listing a sub-collection (trust grants, capabilities) of a realm that
        does not exist is an empty list, not an error: the dashboard queries
        these for many environments and only some have a realm, so a 404 per
        realm-less environment is noise. Reading or mutating a specific realm
        stays a real 404 via :func:`_require_realm`.
        """
        return await _service(request).get_realm(slug)

    @router.get("", response_model=list[RealmResponse])
    async def list_realms(request: Request) -> list[RealmResponse]:
        """List all realms."""
        realms = await _service(request).list_realms()
        return [RealmResponse.from_domain(realm) for realm in realms]

    @router.post("", response_model=RealmResponse, status_code=status.HTTP_201_CREATED)
    async def create_realm(request: Request, body: CreateRealmRequest) -> RealmResponse:
        """Create a new realm."""
        realm = await _service(request).create_realm(
            slug=body.slug,
            name=body.name,
            sleipnir_domain=body.sleipnir_domain,
            owner_id=body.owner_id,
            instance_id=body.instance_id,
            autonomy_profile=body.autonomy_profile,
        )
        return RealmResponse.from_domain(realm)

    @router.get("/{slug}", response_model=RealmResponse)
    async def get_realm(request: Request, slug: str) -> RealmResponse:
        """Get a realm by slug."""
        realm = await _require_realm(request, slug)
        return RealmResponse.from_domain(realm)

    @router.get("/{slug}/trust-grants", response_model=list[TrustGrantResponse])
    async def list_trust_grants(request: Request, slug: str) -> list[TrustGrantResponse]:
        """List all trust grants for a realm (empty list when the realm is absent)."""
        realm = await _optional_realm(request, slug)
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
        request: Request, slug: str, body: CreateTrustGrantRequest
    ) -> TrustGrantResponse:
        """Grant trust to a realm's Valkyrie for an action class."""
        realm = await _require_realm(request, slug)
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
    async def list_capabilities(request: Request, slug: str) -> list[CapabilityResponse]:
        """List all capabilities for a realm (empty list when the realm is absent)."""
        realm = await _optional_realm(request, slug)
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
        request: Request, slug: str, body: RecordCapabilityRequest
    ) -> CapabilityResponse:
        """Record (upsert) a capability for a realm."""
        realm = await _require_realm(request, slug)
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
    "RealmResponse",
    "RecordCapabilityRequest",
    "TrustGrantResponse",
    "create_realms_router",
]

"""Shared FastAPI REST adapter for personal access token management.

Both Ting and Volundr mount this router, each passing their own
``extract_principal`` auth dependency.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_serializer

from niuu.domain.models import Principal
from niuu.domain.services.pat import PATService
from niuu.domain.services.workload_identity import (
    WorkloadIdentityError,
    WorkloadIdentityService,
)
from niuu.http_compat import LegacyRouteNotice, warn_on_legacy_route
from volundr.domain.ports import IdentityPort, UserProvisioningError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CreatePATRequest(BaseModel):
    """Request model for creating a personal access token."""

    name: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9 _-]*$",
        description="Human-readable label for the token",
    )


class PATResponse(BaseModel):
    """Response model for a personal access token (no raw token)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    created_at: datetime
    last_used_at: datetime | None

    @model_serializer(mode="wrap")
    def serialize_with_aliases(self, handler):
        payload = handler(self)
        payload["createdAt"] = payload.get("created_at")
        payload["lastUsedAt"] = payload.get("last_used_at")
        return payload


class CreatePATResponse(BaseModel):
    """Response model returned once on creation (includes raw token)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    token: str
    created_at: datetime

    @model_serializer(mode="wrap")
    def serialize_with_aliases(self, handler):
        payload = handler(self)
        payload["createdAt"] = payload.get("created_at")
        return payload


class WorkloadExchangeRequest(BaseModel):
    """Request model for exchanging a workload identity proof."""

    token: str = Field(min_length=1, description="Projected workload identity JWT.")
    audience: str | None = Field(
        default=None,
        description="Optional single target service audience for the exchanged token.",
    )
    audiences: list[str] = Field(
        default_factory=list,
        description="Optional target service audiences for the exchanged token.",
    )
    scopes: list[str] = Field(
        default_factory=list,
        description=(
            "Optional least-privilege build scopes. When present, the exchanged "
            "token is minted as a Valkyrie build credential "
            "(token_use=valkyrie_build) bounded to the known build scopes."
        ),
    )


class WorkloadPrincipalResponse(BaseModel):
    """Principal represented by an exchanged workload token."""

    user_id: str = Field(serialization_alias="userId")
    tenant_id: str = Field(serialization_alias="tenantId")
    email: str = ""
    roles: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class WorkloadExchangeResponse(BaseModel):
    """Response model for workload identity token exchange."""

    token: str
    expires_at: int = Field(serialization_alias="expiresAt")
    token_type: str = Field(default="Bearer", serialization_alias="tokenType")
    principal: WorkloadPrincipalResponse
    workload_subject: str = Field(serialization_alias="workloadSubject")
    workload_name: str = Field(serialization_alias="workloadName")

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_pats_router(
    extract_principal: Callable[..., Awaitable[Principal]],
    prefix: str = "/api/v1/tokens",
    *,
    deprecated: bool = False,
    canonical_prefix: str | None = None,
) -> APIRouter:
    """Create the personal access tokens router.

    Parameters
    ----------
    extract_principal:
        FastAPI-compatible dependency that returns a ``Principal``.
    prefix:
        URL prefix for the router (default ``/api/v1/tokens``).
    """
    router = APIRouter(
        prefix=prefix,
        tags=["Personal Access Tokens"],
    )

    async def ensure_user(request: Request, principal: Principal) -> None:
        identity: IdentityPort | None = getattr(request.app.state, "identity", None)
        if identity is None:
            return
        try:
            await identity.get_or_provision_user(principal)
        except UserProvisioningError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="User provisioning in progress, retry later",
                headers={"Retry-After": "5"},
            ) from exc

    def workload_identity_service(request: Request) -> WorkloadIdentityService:
        service: WorkloadIdentityService | None = getattr(
            request.app.state,
            "workload_identity_service",
            None,
        )
        if service is None or not service.enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Workload identity exchange is not configured",
            )
        return service

    @router.get("/workload/jwks", include_in_schema=False)
    async def workload_jwks(request: Request) -> dict:
        """Return the public JWKS for exchanged workload tokens."""
        service = workload_identity_service(request)
        return service.jwks()

    @router.post(
        "/workload/exchange",
        response_model=WorkloadExchangeResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def exchange_workload_token(
        body: WorkloadExchangeRequest,
        request: Request,
    ) -> WorkloadExchangeResponse:
        """Exchange a validated workload proof for a short-lived Volundr JWT."""
        service = workload_identity_service(request)
        try:
            result = await service.exchange(
                body.token,
                audiences=_requested_workload_audiences(body),
                scopes=list(body.scopes),
            )
        except WorkloadIdentityError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        return WorkloadExchangeResponse(
            token=result.token,
            expires_at=result.expires_at,
            principal=WorkloadPrincipalResponse(
                user_id=result.principal.user_id,
                tenant_id=result.principal.tenant_id,
                email=result.principal.email,
                roles=result.principal.roles,
            ),
            workload_subject=result.workload_subject,
            workload_name=result.workload_name,
        )

    def _requested_workload_audiences(body: WorkloadExchangeRequest) -> list[str] | None:
        audiences = [str(item).strip() for item in body.audiences if str(item).strip()]
        if body.audience and body.audience.strip():
            audiences.append(body.audience.strip())
        if not audiences:
            return None
        return list(dict.fromkeys(audiences))

    @router.post(
        "",
        response_model=CreatePATResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_token(
        request: Request,
        response: Response,
        body: CreatePATRequest,
        principal: Principal = Depends(extract_principal),
    ) -> CreatePATResponse:
        """Create a new personal access token. The raw token is shown once."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=prefix,
                    canonical_path=canonical_prefix,
                ),
            )
        await ensure_user(request, principal)
        service: PATService = request.app.state.pat_service
        # Extract the user's current access token for IDP token exchange
        auth_header = request.headers.get("authorization", "")
        subject_token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        pat, raw_token = await service.create(
            principal.user_id, body.name, subject_token=subject_token
        )
        return CreatePATResponse(
            id=str(pat.id),
            name=pat.name,
            token=raw_token,
            created_at=pat.created_at,
        )

    @router.get(
        "",
        response_model=list[PATResponse],
    )
    async def list_tokens(
        request: Request,
        response: Response,
        principal: Principal = Depends(extract_principal),
    ) -> list[PATResponse]:
        """List all personal access tokens for the authenticated user."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=prefix,
                    canonical_path=canonical_prefix,
                ),
            )
        await ensure_user(request, principal)
        service: PATService = request.app.state.pat_service
        pats = await service.list(principal.user_id)
        return [
            PATResponse(
                id=str(pat.id),
                name=pat.name,
                created_at=pat.created_at,
                last_used_at=pat.last_used_at,
            )
            for pat in pats
        ]

    @router.delete(
        "/{pat_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def revoke_token(
        request: Request,
        response: Response,
        pat_id: str,
        principal: Principal = Depends(extract_principal),
    ) -> None:
        """Revoke a personal access token by ID."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/{pat_id}",
                    canonical_path=f"{canonical_prefix}/{pat_id}",
                ),
            )
        await ensure_user(request, principal)
        service: PATService = request.app.state.pat_service
        try:
            parsed_id = UUID(pat_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"PAT not found: {pat_id}",
            )
        deleted = await service.revoke(parsed_id, principal.user_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"PAT not found: {pat_id}",
            )

    return router


def create_workload_identity_jwks_router(
    prefix: str = "/api/v1/tokens",
) -> APIRouter:
    """Create a JWKS-only router for services that validate workload JWTs."""

    router = APIRouter(prefix=prefix, tags=["Workload Identity"])

    @router.get("/workload/jwks", include_in_schema=False)
    async def workload_jwks(request: Request) -> dict:
        service: WorkloadIdentityService | None = getattr(
            request.app.state,
            "workload_identity_service",
            None,
        )
        if service is None or not service.enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Workload identity exchange is not configured",
            )
        return service.jwks()

    return router

"""FastAPI REST adapter for tenant and user management."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_serializer

from niuu.settings_schema import (
    SettingsFieldSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
    SettingsTokensResourceSchema,
)
from volundr.adapters.inbound.auth import extract_principal, require_role
from volundr.domain.models import Principal, TenantRole, TenantTier
from volundr.domain.services.identity import (
    IdentityService,
    TenantCreateCommand,
    TenantUpdateCommand,
)
from volundr.domain.services.tenant import (
    TenantAlreadyExistsError,
    TenantNotFoundError,
    TenantService,
)

logger = logging.getLogger(__name__)


def _inject_aliases(payload: dict, aliases: dict[str, str]) -> dict:
    """Add camelCase compatibility keys while preserving the existing payload."""
    data = dict(payload)
    for source, alias in aliases.items():
        data[alias] = payload.get(source)
    return data


class TenantCreate(BaseModel):
    """Request model for creating a tenant."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable tenant name",
    )
    tenant_id: str | None = Field(
        default=None,
        max_length=100,
        description="Custom tenant ID (auto-generated if omitted)",
        validation_alias=AliasChoices("tenant_id", "tenantId"),
    )
    parent_id: str | None = Field(
        default=None,
        max_length=100,
        description="Parent tenant ID for hierarchy",
        validation_alias=AliasChoices("parent_id", "parentId"),
    )
    tier: str = Field(
        default="developer",
        description="Tenant tier (developer, team, enterprise)",
    )
    max_sessions: int = Field(
        default=5,
        ge=1,
        description="Maximum concurrent sessions allowed",
        validation_alias=AliasChoices("max_sessions", "maxSessions"),
    )
    max_storage_gb: int = Field(
        default=50,
        ge=1,
        description="Maximum storage quota in GB",
        validation_alias=AliasChoices("max_storage_gb", "maxStorageGb"),
    )


class TenantResponse(BaseModel):
    """Response model for a tenant."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(description="Unique tenant identifier")
    path: str = Field(description="Materialized tenant hierarchy path")
    name: str = Field(description="Tenant display name")
    parent_id: str | None = Field(description="Parent tenant ID")
    tier: str = Field(description="Tenant tier classification")
    max_sessions: int = Field(description="Maximum concurrent sessions")
    max_storage_gb: int = Field(description="Maximum storage quota in GB")
    created_at: str | None = Field(description="ISO 8601 creation timestamp")

    @model_serializer(mode="wrap")
    def serialize_with_aliases(self, handler):
        return _inject_aliases(
            handler(self),
            {
                "parent_id": "parentId",
                "max_sessions": "maxSessions",
                "max_storage_gb": "maxStorageGb",
                "created_at": "createdAt",
            },
        )

    @classmethod
    def from_tenant(cls, t) -> TenantResponse:
        """Create a TenantResponse from a Tenant domain model."""
        return cls(
            id=t.id,
            path=t.path,
            name=t.name,
            parent_id=t.parent_id,
            tier=t.tier.value,
            max_sessions=t.max_sessions,
            max_storage_gb=t.max_storage_gb,
            created_at=t.created_at.isoformat() if t.created_at else None,
        )


class TenantUpdate(BaseModel):
    """Request model for updating tenant settings."""

    model_config = ConfigDict(populate_by_name=True)

    max_sessions: int | None = Field(
        default=None,
        description="New maximum concurrent sessions",
        validation_alias=AliasChoices("max_sessions", "maxSessions"),
    )
    max_storage_gb: int | None = Field(
        default=None,
        description="New maximum storage quota in GB",
        validation_alias=AliasChoices("max_storage_gb", "maxStorageGb"),
    )
    tier: str | None = Field(
        default=None,
        description="New tenant tier classification",
    )


class MemberCreate(BaseModel):
    """Request model for adding a tenant member."""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(
        ...,
        description="ID of the user to add",
        validation_alias=AliasChoices("user_id", "userId"),
    )
    role: str = Field(
        default="volundr:developer",
        description="Role to assign (e.g. volundr:admin)",
    )


class MemberResponse(BaseModel):
    """Response model for a tenant member."""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(description="User identifier")
    tenant_id: str = Field(description="Tenant identifier")
    role: str = Field(description="Assigned role")
    granted_at: str | None = Field(
        description="ISO 8601 timestamp of role grant",
    )

    @model_serializer(mode="wrap")
    def serialize_with_aliases(self, handler):
        return _inject_aliases(
            handler(self),
            {
                "user_id": "userId",
                "tenant_id": "tenantId",
                "granted_at": "grantedAt",
            },
        )


class UserResponse(BaseModel):
    """Response model for a user."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(description="Unique user identifier")
    email: str = Field(description="User email address")
    display_name: str = Field(
        description="Human-readable display name",
    )
    status: str = Field(description="Account status")
    home_pvc: str | None = Field(
        default=None,
        description="Kubernetes PVC name for home storage",
    )
    created_at: str | None = Field(
        description="ISO 8601 creation timestamp",
    )

    @model_serializer(mode="wrap")
    def serialize_with_aliases(self, handler):
        return _inject_aliases(
            handler(self),
            {
                "display_name": "displayName",
                "home_pvc": "homePvc",
                "created_at": "createdAt",
            },
        )


class MeResponse(BaseModel):
    """Response model for the current user."""

    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(description="Current user identifier")
    email: str = Field(description="Current user email")
    tenant_id: str = Field(
        description="Tenant the user belongs to",
    )
    roles: list[str] = Field(
        description="Roles assigned to the user",
    )
    display_name: str = Field(
        description="Human-readable display name",
    )
    status: str = Field(description="Account status")

    @model_serializer(mode="wrap")
    def serialize_with_aliases(self, handler):
        return _inject_aliases(
            handler(self),
            {
                "user_id": "userId",
                "tenant_id": "tenantId",
                "display_name": "displayName",
            },
        )


def _build_me_response(principal: Principal) -> MeResponse:
    return MeResponse(
        user_id=principal.user_id,
        email=principal.email,
        tenant_id=principal.tenant_id,
        roles=principal.roles,
        display_name=principal.email.split("@")[0],
        status="active",
    )


def _user_to_response(user) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status.value,
        home_pvc=user.home_pvc,
        created_at=user.created_at.isoformat() if user.created_at else None,
    )


def _tenant_to_response(tenant) -> TenantResponse:
    return TenantResponse(
        id=tenant.id,
        path=tenant.path,
        name=tenant.name,
        parent_id=tenant.parent_id,
        tier=tenant.tier.value,
        max_sessions=tenant.max_sessions,
        max_storage_gb=tenant.max_storage_gb,
        created_at=tenant.created_at.isoformat() if tenant.created_at else None,
    )


def _membership_to_response(membership) -> MemberResponse:
    return MemberResponse(
        user_id=membership.user_id,
        tenant_id=membership.tenant_id,
        role=membership.role.value,
        granted_at=membership.granted_at.isoformat() if membership.granted_at else None,
    )


def _provisioning_result_to_payload(result) -> dict[str, object]:
    return {
        "success": result.success,
        "user_id": result.user_id,
        "home_pvc": result.home_pvc,
        "userId": result.user_id,
        "homePvc": result.home_pvc,
        "errors": result.errors,
    }


def _storage_from_request(request: Request):
    return getattr(request.app.state, "storage", None)


def _register_identity_routes(
    router: APIRouter,
    service: IdentityService,
) -> APIRouter:
    async def get_auth_config(request: Request) -> dict:
        """Return public auth discovery metadata for CLI and external clients."""
        settings = request.app.state.settings

        issuer = settings.auth_discovery.issuer
        if not issuer:
            issuer = settings.gateway.kwargs.get("issuer_url", "")

        if not issuer:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Auth discovery not configured",
            )

        return {
            "issuer": issuer,
            "client_id": settings.auth_discovery.cli_client_id,
            "scopes": settings.auth_discovery.scopes,
            "device_authorization_supported": True,
        }

    router.add_api_route(
        "/auth/config",
        get_auth_config,
        methods=["GET"],
        tags=["Identity"],
    )

    async def get_me(
        principal: Principal = Depends(extract_principal),
    ):
        """Get the current authenticated user's identity."""
        return _build_me_response(await service.current_principal(principal))

    router.add_api_route(
        "/me",
        get_me,
        methods=["GET"],
        response_model=MeResponse,
        tags=["Identity"],
    )

    async def get_identity_settings(
        principal: Principal = Depends(extract_principal),
    ) -> SettingsProviderSchema:
        current = await service.current_principal(principal)
        me = _build_me_response(current)
        return SettingsProviderSchema(
            title="You",
            subtitle="personal settings",
            scope="user",
            sections=[
                SettingsSectionSchema(
                    id="profile",
                    label="Profile",
                    description="Current mounted identity profile and access context.",
                    fields=[
                        SettingsFieldSchema(
                            key="email",
                            label="Email",
                            type="text",
                            value=me.email,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="display_name",
                            label="Display Name",
                            type="text",
                            value=me.display_name,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="tenant_id",
                            label="Tenant",
                            type="text",
                            value=me.tenant_id,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="status",
                            label="Status",
                            type="text",
                            value=me.status,
                            read_only=True,
                        ),
                        SettingsFieldSchema(
                            key="roles",
                            label="Roles",
                            type="textarea",
                            value="\n".join(me.roles),
                            read_only=True,
                        ),
                    ],
                ),
                SettingsSectionSchema(
                    id="tokens",
                    label="Personal access tokens",
                    description=(
                        "Create and revoke personal access tokens for scripts, "
                        "local tools, and automation."
                    ),
                    fields=[],
                    resources=[
                        SettingsTokensResourceSchema(
                            id="personal_access_tokens",
                            label="Personal access tokens",
                            description=(
                                "Tokens are shown once when created. "
                                "Revoke anything you no longer use."
                            ),
                            list_path="/api/v1/tokens",
                            create_path="/api/v1/tokens",
                            delete_path="/api/v1/tokens/{id}",
                        )
                    ],
                ),
            ],
        )

    router.add_api_route(
        "/settings",
        get_identity_settings,
        methods=["GET"],
        response_model=SettingsProviderSchema,
        tags=["Identity"],
    )

    async def list_users(
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """List all users (admin only)."""
        users = await service.list_users()
        return [_user_to_response(u) for u in users]

    router.add_api_route(
        "/users",
        list_users,
        methods=["GET"],
        response_model=list[UserResponse],
        tags=["Users"],
    )

    async def reprovision_user(
        user_id: str,
        request: Request,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Re-provision storage for a user (admin only)."""
        result = await service.reprovision_user(user_id, storage=_storage_from_request(request))
        return _provisioning_result_to_payload(result)

    router.add_api_route(
        "/users/{user_id}/reprovision",
        reprovision_user,
        methods=["POST"],
        status_code=status.HTTP_202_ACCEPTED,
    )

    async def list_tenants(
        parent_id: str | None = Query(default=None, description="Filter by parent tenant ID"),
        _: Principal = Depends(extract_principal),
    ):
        """List tenants."""
        tenants = await service.list_tenants(parent_id)
        return [_tenant_to_response(t) for t in tenants]

    router.add_api_route(
        "/tenants",
        list_tenants,
        methods=["GET"],
        response_model=list[TenantResponse],
    )

    async def create_tenant(
        body: TenantCreate,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Create a new tenant (admin only)."""
        try:
            tenant = await service.create_tenant(
                TenantCreateCommand(
                    name=body.name,
                    parent_id=body.parent_id,
                    tenant_id=body.tenant_id,
                    tier=TenantTier(body.tier),
                    max_sessions=body.max_sessions,
                    max_storage_gb=body.max_storage_gb,
                )
            )
        except TenantAlreadyExistsError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
        except TenantNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        return _tenant_to_response(tenant)

    router.add_api_route(
        "/tenants",
        create_tenant,
        methods=["POST"],
        response_model=TenantResponse,
        status_code=status.HTTP_201_CREATED,
    )

    async def get_tenant(
        tenant_id: str,
        _: Principal = Depends(extract_principal),
    ):
        """Get a tenant by ID."""
        try:
            tenant = await service.get_tenant(tenant_id)
        except TenantNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        return _tenant_to_response(tenant)

    router.add_api_route(
        "/tenants/{tenant_id}",
        get_tenant,
        methods=["GET"],
        response_model=TenantResponse,
    )

    async def update_tenant(
        tenant_id: str,
        body: TenantUpdate,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Update tenant settings (admin only)."""
        try:
            tenant = await service.update_tenant(
                tenant_id,
                TenantUpdateCommand(
                    max_sessions=body.max_sessions,
                    max_storage_gb=body.max_storage_gb,
                    tier=body.tier,
                ),
            )
        except TenantNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        return TenantResponse.from_tenant(tenant)

    router.add_api_route(
        "/tenants/{tenant_id}",
        update_tenant,
        methods=["PATCH"],
        response_model=TenantResponse,
    )

    async def delete_tenant(
        tenant_id: str,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Delete a tenant (admin only)."""
        deleted = await service.delete_tenant(tenant_id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    router.add_api_route(
        "/tenants/{tenant_id}",
        delete_tenant,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
    )

    async def list_members(
        tenant_id: str,
        _: Principal = Depends(extract_principal),
    ):
        """List members of a tenant."""
        members = await service.list_members(tenant_id)
        return [_membership_to_response(m) for m in members]

    router.add_api_route(
        "/tenants/{tenant_id}/members",
        list_members,
        methods=["GET"],
        response_model=list[MemberResponse],
    )

    async def add_member(
        tenant_id: str,
        body: MemberCreate,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Add a member to a tenant (admin only)."""
        try:
            membership = await service.add_member(
                tenant_id,
                user_id=body.user_id,
                role=TenantRole(body.role),
            )
        except TenantNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        return _membership_to_response(membership)

    router.add_api_route(
        "/tenants/{tenant_id}/members",
        add_member,
        methods=["POST"],
        response_model=MemberResponse,
        status_code=status.HTTP_201_CREATED,
    )

    async def remove_member(
        tenant_id: str,
        user_id: str,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Remove a member from a tenant (admin only)."""
        removed = await service.remove_member(tenant_id, user_id)
        if not removed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Membership not found",
            )

    router.add_api_route(
        "/tenants/{tenant_id}/members/{user_id}",
        remove_member,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
    )

    async def reprovision_tenant(
        tenant_id: str,
        request: Request,
        _: Principal = Depends(require_role("volundr:admin")),
    ):
        """Re-provision storage for all users in a tenant (admin only)."""
        results = await service.reprovision_tenant(
            tenant_id,
            storage=_storage_from_request(request),
        )
        return [_provisioning_result_to_payload(r) for r in results]

    router.add_api_route(
        "/tenants/{tenant_id}/reprovision",
        reprovision_tenant,
        methods=["POST"],
        status_code=status.HTTP_202_ACCEPTED,
    )

    return router


def create_tenants_router(tenant_service: TenantService) -> APIRouter:
    """Create the identity/tenant router."""
    router = APIRouter(prefix="/api/v1/identity", tags=["Identity"])
    return _register_identity_routes(router, IdentityService(tenant_service))


def create_identity_router(tenant_service: TenantService) -> APIRouter:
    """Create the canonical identity router."""
    router = APIRouter(prefix="/api/v1/identity", tags=["Identity"])
    return _register_identity_routes(router, IdentityService(tenant_service))

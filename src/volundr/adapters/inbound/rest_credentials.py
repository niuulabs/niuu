"""FastAPI REST adapter for credential management."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from niuu.http_compat import LegacyRouteNotice, warn_on_legacy_route
from volundr.adapters.inbound.auth import extract_principal, require_role
from volundr.domain.models import Principal, SecretType
from volundr.domain.services.credential import CredentialService, CredentialValidationError

logger = logging.getLogger(__name__)


class CredentialCreate(BaseModel):
    """Request model for storing a credential."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9_-]+$",
        description="Credential name (lowercase alphanumeric, hyphens, underscores)",
        examples=["my-api-key"],
    )
    secret_type: str = Field(
        default="generic",
        description="Secret type (api_key, oauth_token, git_credential, etc.)",
        examples=["api_key"],
    )
    data: dict[str, str] = Field(
        description="Key-value pairs of secret data",
        examples=[{"token": "sk-abc123"}],
    )
    metadata: dict[str, str] | None = Field(
        default=None,
        description="Optional metadata labels for the credential",
        examples=[{"environment": "production"}],
    )


class CredentialResponse(BaseModel):
    """Response model for a credential (metadata only, NEVER values)."""

    id: str = Field(description="Unique credential identifier", examples=["a1b2c3d4"])
    name: str = Field(description="Credential name", examples=["my-api-key"])
    secret_type: str = Field(description="Secret type classification", examples=["api_key"])
    keys: list[str] = Field(
        description="List of secret data key names", examples=[["token", "secret"]]
    )
    metadata: dict = Field(
        description="Credential metadata labels", examples=[{"environment": "production"}]
    )
    created_at: str = Field(
        description="ISO 8601 creation timestamp", examples=["2025-01-15T10:30:00Z"]
    )
    updated_at: str = Field(
        description="ISO 8601 last update timestamp", examples=["2025-01-15T10:30:00Z"]
    )


class CredentialListResponse(BaseModel):
    """Response model for listing credentials."""

    credentials: list[CredentialResponse] = Field(
        description="List of credential metadata entries (values are never included)",
    )


class SecretTypeInfoResponse(BaseModel):
    """Response model for a secret type definition."""

    type: str = Field(description="Secret type identifier", examples=["api_key"])
    label: str = Field(description="Human-readable label", examples=["API Key"])
    description: str = Field(
        description="Description of the secret type", examples=["Token-based API authentication"]
    )
    fields: list[dict] = Field(
        description="Required fields for this secret type",
        examples=[[{"name": "token", "required": True}]],
    )
    default_mount_type: str = Field(
        description="Default mount type (env_file, file, template)",
        examples=["env_file"],
    )


class LegacyStoreCredentialCreate(BaseModel):
    """Compatibility request model for plugin-volundr secret-store routes."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9_-]+$",
    )
    secret_type: str = Field(
        default="generic",
        validation_alias=AliasChoices("secretType", "secret_type"),
        serialization_alias="secretType",
    )
    data: dict[str, str]
    metadata: dict[str, str] | None = None


class LegacyStoreCredentialResponse(BaseModel):
    """Compatibility response shape expected by older Volundr-scoped adapters."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    secret_type: str = Field(serialization_alias="secretType")
    keys: list[str]
    metadata: dict
    created_at: str = Field(serialization_alias="createdAt")
    updated_at: str = Field(serialization_alias="updatedAt")


class LegacySecretTypeInfoResponse(BaseModel):
    """Compatibility response shape for secret type metadata."""

    model_config = ConfigDict(populate_by_name=True)

    type: str
    label: str
    description: str
    fields: list[dict]
    default_mount_type: str = Field(serialization_alias="defaultMountType")


class LegacyCredentialSummaryResponse(BaseModel):
    """Minimal compatibility shape expected by plugin-volundr credential lists."""

    name: str
    keys: list[str]


def _build_credentials_router(
    credential_service: CredentialService,
    *,
    prefix: str,
    deprecated: bool = False,
    canonical_prefix: str | None = None,
    compatibility_summary_lists: bool = False,
) -> APIRouter:
    """Create the credentials router."""
    router = APIRouter(
        prefix=prefix,
        tags=["Credentials"],
    )

    def _cred_to_response(cred) -> CredentialResponse:
        return CredentialResponse(
            id=cred.id,
            name=cred.name,
            secret_type=cred.secret_type.value,
            keys=list(cred.keys),
            metadata=cred.metadata,
            created_at=cred.created_at.isoformat(),
            updated_at=cred.updated_at.isoformat(),
        )

    def _cred_to_legacy_store_response(cred) -> LegacyStoreCredentialResponse:
        return LegacyStoreCredentialResponse(
            id=cred.id,
            name=cred.name,
            secret_type=cred.secret_type.value,
            keys=list(cred.keys),
            metadata=cred.metadata,
            created_at=cred.created_at.isoformat(),
            updated_at=cred.updated_at.isoformat(),
        )

    def _cred_to_legacy_summary_response(cred) -> LegacyCredentialSummaryResponse:
        return LegacyCredentialSummaryResponse(
            name=cred.name,
            keys=list(cred.keys),
        )

    # ------------------------------------------------------------------
    # Type info
    # ------------------------------------------------------------------

    @router.get(
        "/types",
        response_model=list[SecretTypeInfoResponse],
    )
    async def list_credential_types(
        request: Request,
        response: Response,
    ):
        """List available credential types with field info."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/types",
                    canonical_path=f"{canonical_prefix}/types",
                ),
            )
        return credential_service.get_types()

    @router.get(
        "/secrets/types",
        response_model=list[LegacySecretTypeInfoResponse],
    )
    async def list_legacy_secret_types(
        request: Request,
        response: Response,
    ):
        """List credential types via the older Volundr-scoped secret-store route."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/secrets/types",
                    canonical_path=f"{canonical_prefix}/types",
                ),
            )
        return [
            LegacySecretTypeInfoResponse(
                type=entry["type"],
                label=entry["label"],
                description=entry["description"],
                fields=entry["fields"],
                default_mount_type=entry["default_mount_type"],
            )
            for entry in credential_service.get_types()
        ]

    # ------------------------------------------------------------------
    # User credential endpoints
    # ------------------------------------------------------------------

    user_list_response_model = (
        list[LegacyCredentialSummaryResponse]
        if compatibility_summary_lists
        else CredentialListResponse
    )

    @router.get("/user", response_model=user_list_response_model)
    async def list_credentials(
        request: Request,
        response: Response,
        secret_type: str | None = Query(
            None,
            description="Filter by secret type (api_key, oauth_token, etc.)",
        ),
        principal: Principal = Depends(extract_principal),
    ) -> Any:
        """List the current user's credentials (metadata only)."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=prefix,
                    canonical_path=f"{canonical_prefix}/user",
                ),
            )
        st = SecretType(secret_type) if secret_type else None
        creds = await credential_service.list("user", principal.user_id, st)
        if compatibility_summary_lists:
            return [_cred_to_legacy_summary_response(c) for c in creds]
        return CredentialListResponse(
            credentials=[_cred_to_response(c) for c in creds],
        )

    if compatibility_summary_lists:
        router.add_api_route(
            "",
            list_credentials,
            methods=["GET"],
            response_model=user_list_response_model,
        )

    @router.get("/secrets/store", response_model=list[LegacyStoreCredentialResponse])
    async def list_legacy_store_credentials(
        request: Request,
        response: Response,
        type: str | None = Query(
            None,
            description="Compatibility alias for filtering by secret type.",
        ),
        principal: Principal = Depends(extract_principal),
    ):
        """List user credentials via the legacy Volundr secret-store route."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/secrets/store",
                    canonical_path=f"{canonical_prefix}/user",
                ),
            )
        st = SecretType(type) if type else None
        creds = await credential_service.list("user", principal.user_id, st)
        return [_cred_to_legacy_store_response(c) for c in creds]

    @router.get("/user/{name}", response_model=CredentialResponse)
    async def get_credential(
        request: Request,
        response: Response,
        name: str = Path(description="Credential name to retrieve"),
        principal: Principal = Depends(extract_principal),
    ):
        """Get a credential's metadata by name."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/{name}",
                    canonical_path=f"{canonical_prefix}/user/{name}",
                ),
            )
        cred = await credential_service.get("user", principal.user_id, name)
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Credential not found",
            )
        return _cred_to_response(cred)

    if compatibility_summary_lists:
        router.add_api_route(
            "/{name}",
            get_credential,
            methods=["GET"],
            response_model=CredentialResponse,
        )

    @router.get("/secrets/store/{name}", response_model=LegacyStoreCredentialResponse)
    async def get_legacy_store_credential(
        request: Request,
        response: Response,
        name: str = Path(description="Credential name to retrieve"),
        principal: Principal = Depends(extract_principal),
    ):
        """Get user credential metadata via the legacy Volundr secret-store route."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/secrets/store/{name}",
                    canonical_path=f"{canonical_prefix}/user/{name}",
                ),
            )
        cred = await credential_service.get("user", principal.user_id, name)
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Credential not found",
            )
        return _cred_to_legacy_store_response(cred)

    @router.post("/user", response_model=CredentialResponse, status_code=status.HTTP_201_CREATED)
    async def create_credential(
        request: Request,
        response: Response,
        body: CredentialCreate,
        principal: Principal = Depends(extract_principal),
    ):
        """Create a credential for the current user."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=prefix,
                    canonical_path=f"{canonical_prefix}/user",
                ),
            )
        try:
            st = SecretType(body.secret_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid secret_type: {body.secret_type}",
            )

        try:
            cred = await credential_service.create(
                owner_type="user",
                owner_id=principal.user_id,
                name=body.name,
                secret_type=st,
                data=body.data,
                metadata=body.metadata,
            )
        except CredentialValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"errors": e.errors},
            )

        return _cred_to_response(cred)

    if compatibility_summary_lists:
        router.add_api_route(
            "",
            create_credential,
            methods=["POST"],
            response_model=CredentialResponse,
            status_code=status.HTTP_201_CREATED,
        )

    @router.post(
        "/secrets/store",
        response_model=LegacyStoreCredentialResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_legacy_store_credential(
        request: Request,
        response: Response,
        body: LegacyStoreCredentialCreate,
        principal: Principal = Depends(extract_principal),
    ):
        """Create a user credential via the legacy Volundr secret-store route."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/secrets/store",
                    canonical_path=f"{canonical_prefix}/user",
                ),
            )
        try:
            st = SecretType(body.secret_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid secret_type: {body.secret_type}",
            )

        try:
            cred = await credential_service.create(
                owner_type="user",
                owner_id=principal.user_id,
                name=body.name,
                secret_type=st,
                data=body.data,
                metadata=body.metadata,
            )
        except CredentialValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"errors": e.errors},
            )

        return _cred_to_legacy_store_response(cred)

    @router.delete("/user/{name}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_credential(
        request: Request,
        response: Response,
        name: str = Path(description="Credential name to delete"),
        principal: Principal = Depends(extract_principal),
    ):
        """Delete a credential for the current user."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/{name}",
                    canonical_path=f"{canonical_prefix}/user/{name}",
                ),
            )
        cred = await credential_service.get("user", principal.user_id, name)
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Credential not found",
            )
        await credential_service.delete("user", principal.user_id, name)

    if compatibility_summary_lists:
        router.add_api_route(
            "/{name}",
            delete_credential,
            methods=["DELETE"],
            status_code=status.HTTP_204_NO_CONTENT,
        )

    @router.delete("/secrets/store/{name}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_legacy_store_credential(
        request: Request,
        response: Response,
        name: str = Path(description="Credential name to delete"),
        principal: Principal = Depends(extract_principal),
    ):
        """Delete a user credential via the legacy Volundr secret-store route."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/secrets/store/{name}",
                    canonical_path=f"{canonical_prefix}/user/{name}",
                ),
            )
        cred = await credential_service.get("user", principal.user_id, name)
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Credential not found",
            )
        await credential_service.delete("user", principal.user_id, name)

    # ------------------------------------------------------------------
    # Tenant credential endpoints (admin only)
    # ------------------------------------------------------------------

    tenant_list_response_model = (
        list[LegacyCredentialSummaryResponse]
        if compatibility_summary_lists
        else CredentialListResponse
    )

    @router.get("/tenant/list", response_model=tenant_list_response_model)
    @router.get("/tenant", response_model=tenant_list_response_model)
    async def list_tenant_credentials(
        request: Request,
        response: Response,
        secret_type: str | None = Query(
            None,
            description="Filter by secret type (api_key, oauth_token, etc.)",
        ),
        principal: Principal = Depends(
            require_role("volundr:admin"),
        ),
    ) -> Any:
        """List tenant shared credentials (admin only)."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/tenant/list",
                    canonical_path=f"{canonical_prefix}/tenant",
                ),
            )
        st = SecretType(secret_type) if secret_type else None
        creds = await credential_service.list("tenant", principal.tenant_id, st)
        if compatibility_summary_lists:
            return [_cred_to_legacy_summary_response(c) for c in creds]
        return CredentialListResponse(
            credentials=[_cred_to_response(c) for c in creds],
        )

    @router.post(
        "/tenant",
        response_model=CredentialResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_tenant_credential(
        request: Request,
        response: Response,
        body: CredentialCreate,
        principal: Principal = Depends(
            require_role("volundr:admin"),
        ),
    ):
        """Create a tenant shared credential (admin only)."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/tenant",
                    canonical_path=f"{canonical_prefix}/tenant",
                ),
            )
        try:
            st = SecretType(body.secret_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid secret_type: {body.secret_type}",
            )

        try:
            cred = await credential_service.create(
                owner_type="tenant",
                owner_id=principal.tenant_id,
                name=body.name,
                secret_type=st,
                data=body.data,
                metadata=body.metadata,
            )
        except CredentialValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"errors": e.errors},
            )

        return _cred_to_response(cred)

    @router.delete(
        "/tenant/{name}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_tenant_credential(
        request: Request,
        response: Response,
        name: str = Path(description="Tenant credential name to delete"),
        principal: Principal = Depends(
            require_role("volundr:admin"),
        ),
    ):
        """Delete a tenant shared credential (admin only)."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/tenant/{name}",
                    canonical_path=f"{canonical_prefix}/tenant/{name}",
                ),
            )
        cred = await credential_service.get("tenant", principal.tenant_id, name)
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Credential not found",
            )
        await credential_service.delete("tenant", principal.tenant_id, name)

    return router


def create_credentials_router(
    credential_service: CredentialService,
) -> APIRouter:
    """Create the legacy Volundr credentials router."""
    return _build_credentials_router(
        credential_service,
        prefix="/api/v1/volundr/credentials",
        deprecated=True,
        canonical_prefix="/api/v1/credentials",
        compatibility_summary_lists=True,
    )


def create_canonical_credentials_router(
    credential_service: CredentialService,
) -> APIRouter:
    """Create the canonical shared credentials router."""
    return _build_credentials_router(
        credential_service,
        prefix="/api/v1/credentials",
    )


def create_legacy_secret_store_router(
    credential_service: CredentialService,
) -> APIRouter:
    """Create root-level Volundr secret-store compatibility routes."""
    router = APIRouter(prefix="/api/v1/volundr", tags=["Credentials"])

    def _cred_to_legacy_store_response(cred) -> LegacyStoreCredentialResponse:
        return LegacyStoreCredentialResponse(
            id=cred.id,
            name=cred.name,
            secret_type=cred.secret_type.value,
            keys=list(cred.keys),
            metadata=cred.metadata,
            created_at=cred.created_at.isoformat(),
            updated_at=cred.updated_at.isoformat(),
        )

    @router.get("/secrets/types", response_model=list[LegacySecretTypeInfoResponse])
    async def list_secret_types(
        request: Request,
        response: Response,
    ):
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path="/api/v1/volundr/secrets/types",
                canonical_path="/api/v1/credentials/types",
            ),
        )
        return [
            LegacySecretTypeInfoResponse(
                type=entry["type"],
                label=entry["label"],
                description=entry["description"],
                fields=entry["fields"],
                default_mount_type=entry["default_mount_type"],
            )
            for entry in credential_service.get_types()
        ]

    @router.get("/secrets/store", response_model=list[LegacyStoreCredentialResponse])
    async def list_store_credentials(
        request: Request,
        response: Response,
        type: str | None = Query(None),
        principal: Principal = Depends(extract_principal),
    ):
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path="/api/v1/volundr/secrets/store",
                canonical_path="/api/v1/credentials/user",
            ),
        )
        st = SecretType(type) if type else None
        creds = await credential_service.list("user", principal.user_id, st)
        return [_cred_to_legacy_store_response(c) for c in creds]

    @router.get("/secrets/store/{name}", response_model=LegacyStoreCredentialResponse)
    async def get_store_credential(
        request: Request,
        response: Response,
        name: str,
        principal: Principal = Depends(extract_principal),
    ):
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path=f"/api/v1/volundr/secrets/store/{name}",
                canonical_path=f"/api/v1/credentials/user/{name}",
            ),
        )
        cred = await credential_service.get("user", principal.user_id, name)
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Credential not found",
            )
        return _cred_to_legacy_store_response(cred)

    @router.post(
        "/secrets/store",
        response_model=LegacyStoreCredentialResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_store_credential(
        request: Request,
        response: Response,
        body: LegacyStoreCredentialCreate,
        principal: Principal = Depends(extract_principal),
    ):
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path="/api/v1/volundr/secrets/store",
                canonical_path="/api/v1/credentials/user",
            ),
        )
        try:
            st = SecretType(body.secret_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid secret_type: {body.secret_type}",
            )

        try:
            cred = await credential_service.create(
                owner_type="user",
                owner_id=principal.user_id,
                name=body.name,
                secret_type=st,
                data=body.data,
                metadata=body.metadata,
            )
        except CredentialValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"errors": e.errors},
            )

        return _cred_to_legacy_store_response(cred)

    @router.delete("/secrets/store/{name}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_store_credential(
        request: Request,
        response: Response,
        name: str,
        principal: Principal = Depends(extract_principal),
    ):
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path=f"/api/v1/volundr/secrets/store/{name}",
                canonical_path=f"/api/v1/credentials/user/{name}",
            ),
        )
        cred = await credential_service.get("user", principal.user_id, name)
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Credential not found",
            )
        await credential_service.delete("user", principal.user_id, name)

    return router

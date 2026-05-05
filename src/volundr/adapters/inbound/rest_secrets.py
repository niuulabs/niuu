"""FastAPI REST adapter for MCP servers and Kubernetes secrets."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path, Request, Response, status
from pydantic import BaseModel, Field

from niuu.http_compat import LegacyRouteNotice, warn_on_legacy_route
from volundr.domain.models import MCPServerConfig, SecretInfo
from volundr.domain.ports import (
    MCPServerProvider,
    SecretAlreadyExistsError,
    SecretManager,
    SecretValidationError,
)

logger = logging.getLogger(__name__)


# --- Response/Request models ---


class MCPServerResponse(BaseModel):
    """Response model for an available MCP server configuration."""

    name: str = Field(description="MCP server name", examples=["linear-mcp"])
    type: str = Field(description="Server type (stdio or sse)", examples=["stdio"])
    command: str | None = Field(
        default=None,
        description="Command to launch (stdio servers)",
        examples=["npx"],
    )
    url: str | None = Field(
        default=None,
        description="Server URL (SSE servers)",
        examples=["http://localhost:3000/sse"],
    )
    args: list[str] = Field(
        default_factory=list,
        description="Command-line arguments",
        examples=[["@linear/mcp-server"]],
    )
    description: str = Field(
        default="",
        description="Server description",
        examples=["Linear issue tracker MCP server"],
    )

    @classmethod
    def from_config(cls, cfg: MCPServerConfig) -> MCPServerResponse:
        """Create response from domain model."""
        return cls(
            name=cfg.name,
            type=cfg.type,
            command=cfg.command,
            url=cfg.url,
            args=list(cfg.args),
            description=cfg.description,
        )


class SecretResponse(BaseModel):
    """Response model for a Kubernetes secret (metadata only, no values)."""

    name: str = Field(description="Kubernetes secret name", examples=["my-api-secret"])
    keys: list[str] = Field(
        description="List of data key names in the secret", examples=[["token", "secret"]]
    )

    @classmethod
    def from_info(cls, info: SecretInfo) -> SecretResponse:
        """Create response from domain model."""
        return cls(name=info.name, keys=list(info.keys))


class SecretCreateRequest(BaseModel):
    """Request model for creating a Kubernetes secret."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=253,
        description="Kubernetes secret name (DNS-compatible)",
        examples=["my-api-secret"],
    )
    data: dict[str, str] = Field(
        ...,
        min_length=1,
        description="Key-value pairs of secret data",
        examples=[{"token": "sk-abc123"}],
    )


class ErrorResponse(BaseModel):
    """Response model for errors."""

    detail: str = Field(
        description="Human-readable error message", examples=["Secret not found: my-secret"]
    )


# --- Router factory ---


def _build_secrets_router(
    mcp_provider: MCPServerProvider,
    secret_manager: SecretManager,
    *,
    prefix: str,
    deprecated: bool = False,
    canonical_prefix: str | None = None,
) -> APIRouter:
    """Create FastAPI router for MCP server and secret endpoints."""
    router = APIRouter(prefix=prefix)

    # --- MCP server endpoints ---

    @router.get(
        "/mcp-servers",
        response_model=list[MCPServerResponse],
        tags=["MCP Servers"],
    )
    async def list_mcp_servers(
        request: Request,
        response: Response,
    ) -> list[MCPServerResponse]:
        """List available MCP server configurations."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/mcp-servers",
                    canonical_path=f"{canonical_prefix}/mcp-servers",
                ),
            )
        servers = mcp_provider.list()
        return [MCPServerResponse.from_config(s) for s in servers]

    @router.get(
        "/mcp-servers/{server_name}",
        response_model=MCPServerResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["MCP Servers"],
    )
    async def get_mcp_server(
        request: Request,
        response: Response,
        server_name: str = Path(description="MCP server name to retrieve"),
    ) -> MCPServerResponse:
        """Get an MCP server configuration by name."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/mcp-servers/{server_name}",
                    canonical_path=f"{canonical_prefix}/mcp-servers/{server_name}",
                ),
            )
        server = mcp_provider.get(server_name)
        if server is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"MCP server not found: {server_name}",
            )
        return MCPServerResponse.from_config(server)

    # --- Secret endpoints ---

    @router.get(
        "/secrets",
        response_model=list[SecretResponse],
        tags=["Secrets"],
    )
    async def list_secrets(
        request: Request,
        response: Response,
    ) -> list[SecretResponse]:
        """List available Kubernetes secrets (metadata only, no values)."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/secrets",
                    canonical_path=f"{canonical_prefix}/secrets",
                ),
            )
        secrets = await secret_manager.list()
        return [SecretResponse.from_info(s) for s in secrets]

    @router.get(
        "/secrets/{secret_name}",
        response_model=SecretResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Secrets"],
    )
    async def get_secret(
        request: Request,
        response: Response,
        secret_name: str = Path(description="Kubernetes secret name to retrieve"),
    ) -> SecretResponse:
        """Get a Kubernetes secret's metadata by name."""
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/secrets/{secret_name}",
                    canonical_path=f"{canonical_prefix}/secrets/{secret_name}",
                ),
            )
        secret = await secret_manager.get(secret_name)
        if secret is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Secret not found: {secret_name}",
            )
        return SecretResponse.from_info(secret)

    @router.post(
        "/secrets",
        response_model=SecretResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            400: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
        tags=["Secrets"],
    )
    async def create_secret(
        request: Request,
        response: Response,
        req: SecretCreateRequest,
    ) -> SecretResponse:
        """Create a new Kubernetes secret.

        The secret is automatically labeled for discovery by the secrets list endpoint.
        Secret values are never returned in responses.
        """
        if deprecated and canonical_prefix is not None:
            warn_on_legacy_route(
                request=request,
                response=response,
                notice=LegacyRouteNotice(
                    legacy_path=f"{prefix}/secrets",
                    canonical_path=f"{canonical_prefix}/secrets",
                ),
            )
        try:
            info = await secret_manager.create(req.name, req.data)
            return SecretResponse.from_info(info)
        except SecretValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )
        except SecretAlreadyExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            )

    return router


def _build_legacy_secrets_router(
    mcp_provider: MCPServerProvider,
    secret_manager: SecretManager,
) -> APIRouter:
    """Create the legacy Volundr secrets router with old adapter response shapes."""
    prefix = "/api/v1/volundr"
    canonical_prefix = "/api/v1/credentials"
    router = APIRouter(prefix=prefix)

    @router.get(
        "/mcp-servers",
        response_model=list[MCPServerResponse],
        tags=["MCP Servers"],
    )
    async def list_mcp_servers(
        request: Request,
        response: Response,
    ) -> list[MCPServerResponse]:
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path=f"{prefix}/mcp-servers",
                canonical_path=f"{canonical_prefix}/mcp-servers",
            ),
        )
        servers = mcp_provider.list()
        return [MCPServerResponse.from_config(s) for s in servers]

    @router.get(
        "/mcp-servers/{server_name}",
        response_model=MCPServerResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["MCP Servers"],
    )
    async def get_mcp_server(
        request: Request,
        response: Response,
        server_name: str = Path(description="MCP server name to retrieve"),
    ) -> MCPServerResponse:
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path=f"{prefix}/mcp-servers/{server_name}",
                canonical_path=f"{canonical_prefix}/mcp-servers/{server_name}",
            ),
        )
        server = mcp_provider.get(server_name)
        if server is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"MCP server not found: {server_name}",
            )
        return MCPServerResponse.from_config(server)

    @router.get(
        "/secrets",
        response_model=list[str],
        tags=["Secrets"],
    )
    async def list_secrets(
        request: Request,
        response: Response,
    ) -> list[str]:
        """Legacy Volundr adapter expects a bare list of secret names."""
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path=f"{prefix}/secrets",
                canonical_path=f"{canonical_prefix}/secrets",
            ),
        )
        secrets = await secret_manager.list()
        return [secret.name for secret in secrets]

    @router.get(
        "/secrets/{secret_name}",
        response_model=SecretResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Secrets"],
    )
    async def get_secret(
        request: Request,
        response: Response,
        secret_name: str = Path(description="Kubernetes secret name to retrieve"),
    ) -> SecretResponse:
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path=f"{prefix}/secrets/{secret_name}",
                canonical_path=f"{canonical_prefix}/secrets/{secret_name}",
            ),
        )
        secret = await secret_manager.get(secret_name)
        if secret is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Secret not found: {secret_name}",
            )
        return SecretResponse.from_info(secret)

    @router.post(
        "/secrets",
        response_model=SecretResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            400: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
        tags=["Secrets"],
    )
    async def create_secret(
        request: Request,
        response: Response,
        req: SecretCreateRequest,
    ) -> SecretResponse:
        warn_on_legacy_route(
            request=request,
            response=response,
            notice=LegacyRouteNotice(
                legacy_path=f"{prefix}/secrets",
                canonical_path=f"{canonical_prefix}/secrets",
            ),
        )
        try:
            info = await secret_manager.create(req.name, req.data)
            return SecretResponse.from_info(info)
        except SecretValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except SecretAlreadyExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

    return router


def create_secrets_router(
    mcp_provider: MCPServerProvider,
    secret_manager: SecretManager,
) -> APIRouter:
    """Create the legacy Volundr secrets router."""
    return _build_legacy_secrets_router(mcp_provider, secret_manager)


def create_canonical_secrets_router(
    mcp_provider: MCPServerProvider,
    secret_manager: SecretManager,
) -> APIRouter:
    """Create the canonical shared credential metadata router."""
    return _build_secrets_router(
        mcp_provider,
        secret_manager,
        prefix="/api/v1/credentials",
    )

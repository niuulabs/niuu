"""FastAPI REST adapter for forge profiles and workspace templates."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from volundr.config import SessionDefinitionConfig
from volundr.domain.models import (
    ForgeProfile,
    WorkspaceTemplate,
)
from volundr.domain.services.profile import (
    ForgeProfileService,
    ProfileNotFoundError,
    ProfileReadOnlyError,
    ProfileValidationError,
)
from volundr.domain.services.template import WorkspaceTemplateService

logger = logging.getLogger(__name__)


# --- Request/Response models ---


class ProfileResponse(BaseModel):
    """Response model for a forge profile."""

    name: str = Field(description="Profile name")
    description: str = Field(description="Profile description")
    workload_type: str = Field(description="Workload type")
    model: str | None = Field(description="Default LLM model identifier")
    system_prompt: str | None = Field(description="System prompt")
    resource_config: dict = Field(description="Resource allocation config")
    mcp_servers: list[dict] = Field(description="MCP server configurations")
    env_vars: dict[str, str] = Field(description="Environment variables")
    env_secret_refs: list[str] = Field(description="K8s secret references")
    workload_config: dict = Field(description="Workload-specific config")
    is_default: bool = Field(description="Whether this is the default")

    @classmethod
    def from_profile(cls, profile: ForgeProfile) -> ProfileResponse:
        """Create response from domain model."""
        return cls(
            name=profile.name,
            description=profile.description,
            workload_type=profile.workload_type,
            model=profile.model,
            system_prompt=profile.system_prompt,
            resource_config=profile.resource_config,
            mcp_servers=profile.mcp_servers,
            env_vars=profile.env_vars,
            env_secret_refs=profile.env_secret_refs,
            workload_config=profile.workload_config,
            is_default=profile.is_default,
        )


class ProfileCreateRequest(BaseModel):
    """Request model for creating a forge profile."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Profile name",
    )
    description: str = Field(default="", description="Profile description")
    workload_type: str = Field(
        default="session",
        description="Workload type",
    )
    model: str | None = Field(
        default=None,
        max_length=100,
        description="Default LLM model identifier",
    )
    system_prompt: str | None = Field(
        default=None,
        description="System prompt for the LLM",
    )
    resource_config: dict = Field(
        default_factory=dict,
        description="Resource allocation (cpu, memory, gpu)",
    )
    mcp_servers: list[dict] = Field(
        default_factory=list,
        description="MCP server configurations",
    )
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables for the session pod",
    )
    env_secret_refs: list[str] = Field(
        default_factory=list,
        description="K8s secret names to mount as env vars",
    )
    workload_config: dict = Field(
        default_factory=dict,
        description="Additional workload-specific config",
    )
    is_default: bool = Field(
        default=False,
        description="Whether this is the default profile",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "gpu-heavy",
                "description": "Profile for GPU-intensive workloads",
                "workload_type": "session",
                "model": "claude-sonnet-4-6",
                "resource_config": {"cpu": "4", "memory": "16Gi", "gpu": "1"},
                "is_default": False,
            },
        },
    }


class ProfileUpdateRequest(BaseModel):
    """Request model for updating a forge profile."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New profile name",
    )
    description: str | None = Field(
        default=None,
        description="New description",
    )
    workload_type: str | None = Field(
        default=None,
        description="New workload type",
    )
    model: str | None = Field(
        default=None,
        max_length=100,
        description="New LLM model identifier",
    )
    system_prompt: str | None = Field(
        default=None,
        description="New system prompt",
    )
    resource_config: dict | None = Field(
        default=None,
        description="New resource config",
    )
    mcp_servers: list[dict] | None = Field(
        default=None,
        description="New MCP server list",
    )
    env_vars: dict[str, str] | None = Field(
        default=None,
        description="New environment variables",
    )
    env_secret_refs: list[str] | None = Field(
        default=None,
        description="New K8s secret references",
    )
    workload_config: dict | None = Field(
        default=None,
        description="New workload config",
    )
    is_default: bool | None = Field(
        default=None,
        description="New default flag",
    )


class TemplateResponse(BaseModel):
    """Response model for a workspace template (unified blueprint)."""

    name: str = Field(description="Template name")
    description: str = Field(description="Template description")
    repos: list[dict] = Field(description="Git repos to clone")
    setup_scripts: list[str] = Field(description="Setup shell scripts")
    workspace_layout: dict = Field(description="Directory layout config")
    is_default: bool = Field(description="Whether this is the default")
    # Runtime config (merged from profile)
    workload_type: str = Field(description="Workload type")
    model: str | None = Field(description="Default LLM model identifier")
    system_prompt: str | None = Field(description="System prompt")
    resource_config: dict = Field(description="Resource allocation config")
    mcp_servers: list[dict] = Field(description="MCP server configurations")
    env_vars: dict[str, str] = Field(description="Environment variables")
    env_secret_refs: list[str] = Field(description="K8s secret references")
    workload_config: dict = Field(description="Workload-specific config")
    session_definition: str | None = Field(
        default=None,
        description="Skuld session definition CRD name",
    )

    @classmethod
    def from_template(cls, template: WorkspaceTemplate) -> TemplateResponse:
        """Create response from domain model."""
        return cls(
            name=template.name,
            description=template.description,
            repos=template.repos,
            setup_scripts=template.setup_scripts,
            workspace_layout=template.workspace_layout,
            is_default=template.is_default,
            workload_type=template.workload_type,
            model=template.model,
            system_prompt=template.system_prompt,
            resource_config=template.resource_config,
            mcp_servers=template.mcp_servers,
            env_vars=template.env_vars,
            env_secret_refs=template.env_secret_refs,
            workload_config=template.workload_config,
            session_definition=template.session_definition,
        )


class SessionDefinitionResponse(BaseModel):
    """Response model for a session definition (read-only, config-driven)."""

    key: str = Field(description="Unique definition key (e.g. skuldClaude)")
    display_name: str = Field(description="Human-readable name")
    description: str = Field(description="Short description")
    labels: list[str] = Field(description="Routing labels")
    default_model: str = Field(description="Default model for this definition")

    @classmethod
    def from_config(cls, key: str, defn: SessionDefinitionConfig) -> SessionDefinitionResponse:
        return cls(
            key=key,
            display_name=defn.display_name or key,
            description=defn.description,
            labels=defn.labels,
            default_model=defn.default_model,
        )


class ErrorResponse(BaseModel):
    """Response model for errors."""

    detail: str = Field(description="Human-readable error message")


# --- Router factory ---


def create_profiles_router(
    profile_service: ForgeProfileService,
    template_service: WorkspaceTemplateService,
    session_definitions: dict[str, SessionDefinitionConfig] | None = None,
) -> APIRouter:
    """Create FastAPI router for profile and template endpoints."""
    router = APIRouter(prefix="/api/v1/volundr")

    # --- Profile endpoints ---

    @router.get(
        "/profiles",
        response_model=list[ProfileResponse],
        tags=["Profiles"],
    )
    async def list_profiles(
        workload_type: str | None = Query(
            default=None,
            description="Filter by workload type (e.g. session)",
        ),
    ) -> list[ProfileResponse]:
        """List all forge profiles."""
        profiles = profile_service.list_profiles(workload_type=workload_type)
        return [ProfileResponse.from_profile(p) for p in profiles]

    @router.get(
        "/profiles/{profile_name}",
        response_model=ProfileResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Profiles"],
    )
    async def get_profile(
        profile_name: str = Path(description="Profile name to retrieve"),
    ) -> ProfileResponse:
        """Get a forge profile by name."""
        profile = profile_service.get_profile(profile_name)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile not found: {profile_name}",
            )
        return ProfileResponse.from_profile(profile)

    @router.post(
        "/profiles",
        response_model=ProfileResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            400: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
        tags=["Profiles"],
    )
    async def create_profile(data: ProfileCreateRequest) -> ProfileResponse:
        """Create a new forge profile."""
        profile = ForgeProfile(
            name=data.name,
            description=data.description,
            workload_type=data.workload_type,
            model=data.model,
            system_prompt=data.system_prompt,
            resource_config=data.resource_config,
            mcp_servers=data.mcp_servers,
            env_vars=data.env_vars,
            env_secret_refs=data.env_secret_refs,
            workload_config=data.workload_config,
            is_default=data.is_default,
        )
        try:
            created = await profile_service.create_profile(profile)
            return ProfileResponse.from_profile(created)
        except ProfileValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )
        except ProfileReadOnlyError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            )

    @router.put(
        "/profiles/{profile_name}",
        response_model=ProfileResponse,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
        tags=["Profiles"],
    )
    async def update_profile(
        profile_name: str = Path(description="Profile name to update"),
        data: ProfileUpdateRequest = ...,
    ) -> ProfileResponse:
        """Update an existing forge profile."""
        existing = profile_service.get_profile(profile_name)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile not found: {profile_name}",
            )

        updated = ForgeProfile(
            name=data.name if data.name is not None else existing.name,
            description=(
                data.description if data.description is not None else existing.description
            ),
            workload_type=(
                data.workload_type if data.workload_type is not None else existing.workload_type
            ),
            model=data.model if data.model is not None else existing.model,
            system_prompt=(
                data.system_prompt if data.system_prompt is not None else existing.system_prompt
            ),
            resource_config=(
                data.resource_config
                if data.resource_config is not None
                else existing.resource_config
            ),
            mcp_servers=(
                data.mcp_servers if data.mcp_servers is not None else existing.mcp_servers
            ),
            env_vars=(data.env_vars if data.env_vars is not None else existing.env_vars),
            env_secret_refs=(
                data.env_secret_refs
                if data.env_secret_refs is not None
                else existing.env_secret_refs
            ),
            workload_config=(
                data.workload_config
                if data.workload_config is not None
                else existing.workload_config
            ),
            is_default=(data.is_default if data.is_default is not None else existing.is_default),
        )

        try:
            result = await profile_service.update_profile(profile_name, updated)
            return ProfileResponse.from_profile(result)
        except ProfileValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )
        except ProfileReadOnlyError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )
        except ProfileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile not found: {profile_name}",
            )

    @router.delete(
        "/profiles/{profile_name}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
        tags=["Profiles"],
    )
    async def delete_profile(
        profile_name: str = Path(description="Profile name to delete"),
    ) -> None:
        """Delete a forge profile."""
        try:
            await profile_service.delete_profile(profile_name)
        except ProfileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Profile not found: {profile_name}",
            )
        except ProfileReadOnlyError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )

    # --- Template endpoints (read-only, config-driven) ---

    @router.get(
        "/templates",
        response_model=list[TemplateResponse],
        tags=["Templates"],
    )
    async def list_templates(
        workload_type: str | None = Query(
            default=None,
            description="Filter by workload type (e.g. session)",
        ),
    ) -> list[TemplateResponse]:
        """List all workspace templates (loaded from configuration)."""
        templates = template_service.list_templates(workload_type=workload_type)
        return [TemplateResponse.from_template(t) for t in templates]

    @router.get(
        "/templates/{template_name}",
        response_model=TemplateResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Templates"],
    )
    async def get_template(
        template_name: str = Path(description="Template name to retrieve"),
    ) -> TemplateResponse:
        """Get a workspace template by name (loaded from configuration)."""
        template = template_service.get_template(template_name)
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Template not found: {template_name}",
            )
        return TemplateResponse.from_template(template)

    # --- Session definition endpoints (read-only, config-driven) ---

    _definitions = session_definitions or {}

    @router.get(
        "/session-definitions",
        response_model=list[SessionDefinitionResponse],
        tags=["Session Definitions"],
    )
    async def list_session_definitions() -> list[SessionDefinitionResponse]:
        """List available session definitions (loaded from configuration)."""
        return [
            SessionDefinitionResponse.from_config(key, defn)
            for key, defn in _definitions.items()
            if defn.enabled
        ]

    return router

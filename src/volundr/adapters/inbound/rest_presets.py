"""FastAPI REST adapter for presets."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from volundr.domain.models import Preset
from volundr.domain.services.preset import (
    PresetDuplicateNameError,
    PresetNotFoundError,
    PresetService,
)

logger = logging.getLogger(__name__)


# --- Request/Response models ---


class PresetCreate(BaseModel):
    """Request model for creating a preset."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Human-readable preset name",
    )
    description: str = Field(
        default="",
        description="Description of the preset purpose",
    )
    is_default: bool = Field(
        default=False,
        description="Whether this is the default preset for its CLI tool",
    )
    cli_tool: str = Field(
        default="",
        description="CLI tool this preset targets (e.g. claude, aider)",
    )
    workload_type: str = Field(
        default="session",
        description="Workload type (e.g. session)",
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
        description="MCP server configurations to attach",
    )
    terminal_sidecar: dict = Field(
        default_factory=dict,
        description="Terminal sidecar container config",
    )
    skills: list[dict] = Field(
        default_factory=list,
        description="Skill definitions for the session",
    )
    rules: list[dict] = Field(
        default_factory=list,
        description="Rule definitions for session behavior",
    )
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables for the session pod",
    )
    env_secret_refs: list[str] = Field(
        default_factory=list,
        description="K8s secret names to mount as env vars",
    )
    source: dict | None = Field(
        default=None,
        description="Workspace source (git or local_mount) as JSON",
    )
    integration_ids: list[str] = Field(
        default_factory=list,
        description="Integration connection IDs to attach",
    )
    setup_scripts: list[str] = Field(
        default_factory=list,
        description="Shell scripts for workspace setup",
    )
    workload_config: dict = Field(
        default_factory=dict,
        description="Additional workload-specific configuration",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "claude-heavy",
                "description": "High-resource preset for complex tasks",
                "cli_tool": "claude",
                "workload_type": "session",
                "model": "claude-sonnet-4-6",
                "resource_config": {"cpu": "4", "memory": "8Gi"},
                "is_default": False,
            },
        },
    }


class PresetUpdate(BaseModel):
    """Request model for updating a preset (all fields optional)."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New preset name",
    )
    description: str | None = Field(
        default=None,
        description="New description",
    )
    is_default: bool | None = Field(
        default=None,
        description="New default flag",
    )
    cli_tool: str | None = Field(
        default=None,
        description="New CLI tool target",
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
    terminal_sidecar: dict | None = Field(
        default=None,
        description="New terminal sidecar config",
    )
    skills: list[dict] | None = Field(
        default=None,
        description="New skill definitions",
    )
    rules: list[dict] | None = Field(
        default=None,
        description="New rule definitions",
    )
    env_vars: dict[str, str] | None = Field(
        default=None,
        description="New environment variables",
    )
    env_secret_refs: list[str] | None = Field(
        default=None,
        description="New K8s secret references",
    )
    source: dict | None = Field(
        default=None,
        description="New workspace source",
    )
    integration_ids: list[str] | None = Field(
        default=None,
        description="New integration connection IDs",
    )
    setup_scripts: list[str] | None = Field(
        default=None,
        description="New setup scripts",
    )
    workload_config: dict | None = Field(
        default=None,
        description="New workload config",
    )


class PresetResponse(BaseModel):
    """Response model for a preset."""

    id: UUID = Field(description="Unique preset identifier")
    name: str = Field(description="Preset name")
    description: str = Field(description="Preset description")
    is_default: bool = Field(description="Whether this is the default preset")
    cli_tool: str = Field(description="CLI tool target")
    workload_type: str = Field(description="Workload type")
    model: str | None = Field(description="LLM model identifier")
    system_prompt: str | None = Field(description="System prompt")
    resource_config: dict = Field(description="Resource allocation config")
    mcp_servers: list[dict] = Field(description="MCP server configurations")
    terminal_sidecar: dict = Field(description="Terminal sidecar config")
    skills: list[dict] = Field(description="Skill definitions")
    rules: list[dict] = Field(description="Rule definitions")
    env_vars: dict[str, str] = Field(description="Environment variables")
    env_secret_refs: list[str] = Field(description="K8s secret references")
    source: dict | None = Field(description="Workspace source configuration")
    integration_ids: list[str] = Field(description="Integration connection IDs")
    setup_scripts: list[str] = Field(description="Setup shell scripts")
    workload_config: dict = Field(description="Workload-specific config")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last update timestamp")

    @classmethod
    def from_preset(cls, preset: Preset) -> PresetResponse:
        """Create response from domain model."""
        return cls(
            id=preset.id,
            name=preset.name,
            description=preset.description,
            is_default=preset.is_default,
            cli_tool=preset.cli_tool,
            workload_type=preset.workload_type,
            model=preset.model,
            system_prompt=preset.system_prompt,
            resource_config=preset.resource_config,
            mcp_servers=preset.mcp_servers,
            terminal_sidecar=preset.terminal_sidecar,
            skills=preset.skills,
            rules=preset.rules,
            env_vars=preset.env_vars,
            env_secret_refs=preset.env_secret_refs,
            source=preset.source.model_dump() if preset.source else None,
            integration_ids=preset.integration_ids,
            setup_scripts=preset.setup_scripts,
            workload_config=preset.workload_config,
            created_at=preset.created_at.isoformat(),
            updated_at=preset.updated_at.isoformat(),
        )


class ErrorResponse(BaseModel):
    """Response model for errors."""

    detail: str = Field(description="Human-readable error message")


# --- Router factory ---


def create_presets_router(
    preset_service: PresetService,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    """Create FastAPI router for preset endpoints."""
    router = APIRouter(prefix=prefix)

    @router.get("/presets", response_model=list[PresetResponse], tags=["Presets"])
    async def list_presets(
        cli_tool: str | None = Query(
            default=None,
            description="Filter by CLI tool (e.g. claude, aider)",
        ),
        is_default: bool | None = Query(
            default=None,
            description="Filter by default flag",
        ),
    ) -> list[PresetResponse]:
        """List presets with optional filters."""
        presets = await preset_service.list_presets(cli_tool=cli_tool, is_default=is_default)
        return [PresetResponse.from_preset(p) for p in presets]

    @router.get(
        "/presets/{preset_id}",
        response_model=PresetResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Presets"],
    )
    async def get_preset(
        preset_id: UUID = Path(description="Unique preset identifier"),
    ) -> PresetResponse:
        """Get a preset by ID."""
        try:
            preset = await preset_service.get_preset(preset_id)
            return PresetResponse.from_preset(preset)
        except PresetNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Preset not found: {preset_id}",
            )

    @router.post(
        "/presets",
        response_model=PresetResponse,
        status_code=status.HTTP_201_CREATED,
        responses={409: {"model": ErrorResponse}},
        tags=["Presets"],
    )
    async def create_preset(data: PresetCreate) -> PresetResponse:
        """Create a new preset."""
        try:
            preset = await preset_service.create_preset(
                name=data.name,
                description=data.description,
                is_default=data.is_default,
                cli_tool=data.cli_tool,
                workload_type=data.workload_type,
                model=data.model,
                system_prompt=data.system_prompt,
                resource_config=data.resource_config,
                mcp_servers=data.mcp_servers,
                terminal_sidecar=data.terminal_sidecar,
                skills=data.skills,
                rules=data.rules,
                env_vars=data.env_vars,
                env_secret_refs=data.env_secret_refs,
                source=data.source,
                integration_ids=data.integration_ids,
                setup_scripts=data.setup_scripts,
                workload_config=data.workload_config,
            )
            return PresetResponse.from_preset(preset)
        except PresetDuplicateNameError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Preset with name already exists: {data.name}",
            )

    @router.put(
        "/presets/{preset_id}",
        response_model=PresetResponse,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["Presets"],
    )
    async def update_preset(
        preset_id: UUID = Path(description="Unique preset identifier"), data: PresetUpdate = ...
    ) -> PresetResponse:
        """Update a preset."""
        try:
            updates = data.model_dump(exclude_unset=True)
            preset = await preset_service.update_preset(preset_id, updates)
            return PresetResponse.from_preset(preset)
        except PresetNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Preset not found: {preset_id}",
            )
        except PresetDuplicateNameError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Preset with name already exists: {data.name}",
            )

    @router.delete(
        "/presets/{preset_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"model": ErrorResponse}},
        tags=["Presets"],
    )
    async def delete_preset(preset_id: UUID = Path(description="Unique preset identifier")) -> None:
        """Delete a preset."""
        try:
            await preset_service.delete_preset(preset_id)
        except PresetNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Preset not found: {preset_id}",
            )

    return router

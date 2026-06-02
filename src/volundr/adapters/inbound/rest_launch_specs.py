"""FastAPI REST adapter for launch specs and session definitions.

Replaces rest_profiles.py + rest_presets.py. Serves the unified launch-spec
surface: read system-scope specs, CRUD user-scope specs, and read the
config-driven session definitions (runtime types).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from volundr.config import SessionDefinitionConfig
from volundr.domain.models import LaunchScope, LaunchSpec
from volundr.domain.services.launch_spec import (
    LaunchSpecDuplicateNameError,
    LaunchSpecNotFoundError,
    LaunchSpecService,
)

logger = logging.getLogger(__name__)

# Serialize/accept camelCase over the wire (web-next convention).
_CAMEL = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class LaunchSpecCreate(BaseModel):
    """Request body for creating a user-scope launch spec."""

    model_config = _CAMEL

    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    is_default: bool = False
    cli_tool: str = ""
    workload_type: str = "session"
    session_definition: str | None = None
    model: str | None = Field(default=None, max_length=100)
    system_prompt: str | None = None
    resource_config: dict = Field(default_factory=dict)
    mcp_servers: list[dict] = Field(default_factory=list)
    terminal_sidecar: dict = Field(default_factory=dict)
    skills: list[dict] = Field(default_factory=list)
    rules: list[dict] = Field(default_factory=list)
    env_vars: dict[str, str] = Field(default_factory=dict)
    env_secret_refs: list[str] = Field(default_factory=list)
    source: dict | None = None
    integration_ids: list[str] = Field(default_factory=list)
    repos: list[dict] = Field(default_factory=list)
    setup_scripts: list[str] = Field(default_factory=list)
    workspace_layout: dict = Field(default_factory=dict)
    workload_config: dict = Field(default_factory=dict)


class LaunchSpecUpdate(BaseModel):
    """Request body for updating a user-scope launch spec (all optional)."""

    model_config = _CAMEL

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    is_default: bool | None = None
    cli_tool: str | None = None
    workload_type: str | None = None
    session_definition: str | None = None
    model: str | None = Field(default=None, max_length=100)
    system_prompt: str | None = None
    resource_config: dict | None = None
    mcp_servers: list[dict] | None = None
    terminal_sidecar: dict | None = None
    skills: list[dict] | None = None
    rules: list[dict] | None = None
    env_vars: dict[str, str] | None = None
    env_secret_refs: list[str] | None = None
    source: dict | None = None
    integration_ids: list[str] | None = None
    repos: list[dict] | None = None
    setup_scripts: list[str] | None = None
    workspace_layout: dict | None = None
    workload_config: dict | None = None


class SessionDefinitionResponse(BaseModel):
    """Response model for a session definition (runtime type, read-only)."""

    model_config = _CAMEL

    key: str
    display_name: str
    description: str
    labels: list[str]
    default_model: str
    compatible_providers: list[str]

    @classmethod
    def from_config(cls, key: str, defn: SessionDefinitionConfig) -> SessionDefinitionResponse:
        return cls(
            key=key,
            display_name=defn.display_name or key,
            description=defn.description,
            labels=defn.labels,
            default_model=defn.default_model,
            compatible_providers=defn.compatible_providers,
        )


class ErrorResponse(BaseModel):
    detail: str


def create_launch_specs_router(
    service: LaunchSpecService,
    session_definitions: dict[str, SessionDefinitionConfig] | None = None,
    *,
    prefix: str = "/api/v1/volundr",
) -> APIRouter:
    """Create the launch-spec + session-definition router."""
    router = APIRouter(prefix=prefix)
    _definitions = session_definitions or {}

    @router.get("/launch-specs", response_model=list[LaunchSpec], tags=["Launch Specs"])
    async def list_launch_specs(
        scope: LaunchScope | None = Query(default=None),
        workload_type: str | None = Query(default=None),
        cli_tool: str | None = Query(default=None),
        is_default: bool | None = Query(default=None),
    ) -> list[LaunchSpec]:
        return await service.list_all(
            scope=scope,
            workload_type=workload_type,
            cli_tool=cli_tool,
            is_default=is_default,
        )

    @router.get(
        "/launch-specs/{ref}",
        response_model=LaunchSpec,
        responses={404: {"model": ErrorResponse}},
        tags=["Launch Specs"],
    )
    async def get_launch_spec(
        ref: str = Path(description="Launch spec id (user) or name (system)"),
    ):
        try:
            spec_id = UUID(ref)
        except ValueError:
            spec_id = None
        if spec_id is not None:
            try:
                return await service.get_user(spec_id)
            except LaunchSpecNotFoundError:
                pass
        system = service.get_system(ref)
        if system is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Launch spec not found: {ref}")
        return system

    @router.post(
        "/launch-specs",
        response_model=LaunchSpec,
        status_code=status.HTTP_201_CREATED,
        responses={409: {"model": ErrorResponse}},
        tags=["Launch Specs"],
    )
    async def create_launch_spec(data: LaunchSpecCreate) -> LaunchSpec:
        spec = LaunchSpec(scope=LaunchScope.USER, **data.model_dump())
        try:
            return await service.create(spec)
        except LaunchSpecDuplicateNameError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.put(
        "/launch-specs/{spec_id}",
        response_model=LaunchSpec,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["Launch Specs"],
    )
    async def update_launch_spec(
        spec_id: UUID = Path(description="User launch spec id"),
        data: LaunchSpecUpdate = ...,
    ) -> LaunchSpec:
        updates: dict[str, Any] = data.model_dump(exclude_unset=True)
        try:
            return await service.update(spec_id, updates)
        except LaunchSpecNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except LaunchSpecDuplicateNameError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.delete(
        "/launch-specs/{spec_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={404: {"model": ErrorResponse}},
        tags=["Launch Specs"],
    )
    async def delete_launch_spec(spec_id: UUID = Path(description="User launch spec id")) -> None:
        try:
            await service.delete(spec_id)
        except LaunchSpecNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get(
        "/session-definitions",
        response_model=list[SessionDefinitionResponse],
        tags=["Session Definitions"],
    )
    async def list_session_definitions() -> list[SessionDefinitionResponse]:
        return [
            SessionDefinitionResponse.from_config(key, defn)
            for key, defn in _definitions.items()
            if defn.enabled
        ]

    return router

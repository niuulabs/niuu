"""FastAPI REST adapter for Ravn persona CRUD."""

from __future__ import annotations

import logging
from dataclasses import replace as _dc_replace

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, Field

from ravn.adapters.personas.loader import FilesystemPersonaAdapter, PersonaConfig
from ravn.ports.persona import PersonaRegistryPort

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class PersonaLLMResponse(BaseModel):
    """LLM configuration embedded in a persona response."""

    primary_alias: str = Field(description="LLM alias (e.g. 'balanced', 'powerful')")
    thinking_enabled: bool = Field(description="Whether extended thinking is enabled")
    max_tokens: int = Field(description="Max tokens override (0 = use settings default)")


class PersonaProducesResponse(BaseModel):
    """Output event schema for a persona."""

    event_type: str = Field(description="Event type produced on completion")
    schema_def: dict = Field(alias="schema", description="Output schema field definitions")

    model_config = {"populate_by_name": True}


class PersonaConsumesResponse(BaseModel):
    """Input event configuration for a persona."""

    event_types: list[str] = Field(description="Event types this persona consumes")
    injects: list[str] = Field(description="Context fields injected from consumed events")


class PersonaFanInResponse(BaseModel):
    """Fan-in strategy configuration for a persona."""

    strategy: str = Field(description="Fan-in strategy (all_must_pass, any_pass, majority, merge)")
    contributes_to: str = Field(description="Parent event type this persona contributes to")


class PersonaSummary(BaseModel):
    """Summary view of a persona for list responses."""

    name: str = Field(description="Unique persona name")
    permission_mode: str = Field(
        description="Permission mode (e.g. 'read-only', 'workspace-write')",
    )
    allowed_tools: list[str] = Field(description="Explicitly allowed tool groups")
    iteration_budget: int = Field(description="Maximum agent iterations (0 = unlimited)")
    is_builtin: bool = Field(description="Whether this is a built-in persona")
    has_override: bool = Field(description="Whether a user file overrides the built-in")
    produces_event: str = Field(description="Event type produced on completion (empty if none)")
    consumes_events: list[str] = Field(description="Event types this persona consumes")

    @classmethod
    def from_persona(cls, config: PersonaConfig, loader: PersonaRegistryPort) -> PersonaSummary:
        """Build summary from a PersonaConfig."""
        name = config.name
        is_builtin = loader.is_builtin(name)
        source = loader.source(name)
        has_override = is_builtin and source != "[built-in]"
        return cls(
            name=name,
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            iteration_budget=config.iteration_budget,
            is_builtin=is_builtin,
            has_override=has_override,
            produces_event=config.produces.event_type,
            consumes_events=config.consumes.event_types,
        )


class PersonaDetail(PersonaSummary):
    """Full detail view of a persona."""

    system_prompt_template: str = Field(
        description="System prompt template (may include outcome injection)",
    )
    forbidden_tools: list[str] = Field(description="Explicitly forbidden tool groups")
    llm: PersonaLLMResponse = Field(description="LLM configuration")
    produces: PersonaProducesResponse = Field(description="Output event schema")
    consumes: PersonaConsumesResponse = Field(description="Input event configuration")
    fan_in: PersonaFanInResponse = Field(description="Fan-in strategy configuration")
    yaml_source: str = Field(description="File path providing this persona, or '[built-in]'")

    @classmethod
    def from_persona(  # type: ignore[override]
        cls, config: PersonaConfig, loader: PersonaRegistryPort
    ) -> PersonaDetail:
        """Build detail from a PersonaConfig."""
        name = config.name
        is_builtin = loader.is_builtin(name)
        source = loader.source(name)
        has_override = is_builtin and source != "[built-in]"

        # Serialize produces schema — OutcomeField dataclasses → plain dicts
        produces_schema: dict = {}
        for fname, f in config.produces.schema.items():
            fd: dict = {"type": f.type, "description": f.description}
            if f.enum_values:
                fd["values"] = list(f.enum_values)
            if not f.required:
                fd["required"] = False
            produces_schema[fname] = fd

        return cls(
            name=name,
            permission_mode=config.permission_mode,
            allowed_tools=config.allowed_tools,
            iteration_budget=config.iteration_budget,
            is_builtin=is_builtin,
            has_override=has_override,
            produces_event=config.produces.event_type,
            consumes_events=config.consumes.event_types,
            system_prompt_template=config.system_prompt_template,
            forbidden_tools=config.forbidden_tools,
            llm=PersonaLLMResponse(
                primary_alias=config.llm.primary_alias,
                thinking_enabled=config.llm.thinking_enabled,
                max_tokens=config.llm.max_tokens,
            ),
            produces=PersonaProducesResponse(
                event_type=config.produces.event_type,
                schema=produces_schema,
            ),  # type: ignore[call-arg]
            consumes=PersonaConsumesResponse(
                event_types=config.consumes.event_types,
                injects=config.consumes.injects,
            ),
            fan_in=PersonaFanInResponse(
                strategy=config.fan_in.strategy,
                contributes_to=config.fan_in.contributes_to,
            ),
            yaml_source=source or "[unknown]",
        )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

_NAME_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"


class PersonaCreate(BaseModel):
    """Request body for creating a new persona."""

    name: str = Field(
        min_length=1,
        max_length=128,
        pattern=_NAME_PATTERN,
        description="Unique persona name (alphanumeric, hyphens, underscores)",
    )
    system_prompt_template: str = Field(default="", description="System prompt template text")
    allowed_tools: list[str] = Field(default_factory=list, description="Allowed tool groups")
    forbidden_tools: list[str] = Field(default_factory=list, description="Forbidden tool groups")
    permission_mode: str = Field(default="", description="Permission mode")
    iteration_budget: int = Field(default=0, ge=0, description="Max iterations (0 = unlimited)")
    llm_primary_alias: str = Field(default="", description="LLM alias")
    llm_thinking_enabled: bool = Field(default=False, description="Enable extended thinking")
    llm_max_tokens: int = Field(default=0, ge=0, description="Max tokens override")
    produces_event_type: str = Field(default="", description="Event type produced on completion")
    consumes_event_types: list[str] = Field(
        default_factory=list,
        description="Consumed event types",
    )
    consumes_injects: list[str] = Field(default_factory=list, description="Injected context fields")
    fan_in_strategy: str = Field(default="merge", description="Fan-in strategy")
    fan_in_contributes_to: str = Field(default="", description="Parent event type")

    def to_persona_config(self) -> PersonaConfig:
        """Build a PersonaConfig from this request."""
        from ravn.adapters.personas.loader import (
            PersonaConsumes,
            PersonaFanIn,
            PersonaLLMConfig,
            PersonaProduces,
        )

        return PersonaConfig(
            name=self.name,
            system_prompt_template=self.system_prompt_template,
            allowed_tools=self.allowed_tools,
            forbidden_tools=self.forbidden_tools,
            permission_mode=self.permission_mode,
            iteration_budget=self.iteration_budget,
            llm=PersonaLLMConfig(
                primary_alias=self.llm_primary_alias,
                thinking_enabled=self.llm_thinking_enabled,
                max_tokens=self.llm_max_tokens,
            ),
            produces=PersonaProduces(event_type=self.produces_event_type),
            consumes=PersonaConsumes(
                event_types=self.consumes_event_types,
                injects=self.consumes_injects,
            ),
            fan_in=PersonaFanIn(
                strategy=self.fan_in_strategy,  # type: ignore[arg-type]
                contributes_to=self.fan_in_contributes_to,
            ),
        )


class PersonaForkRequest(BaseModel):
    """Request body for forking a persona to a new name."""

    new_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=_NAME_PATTERN,
        description="Name for the forked persona (alphanumeric, hyphens, underscores)",
    )


class PersonaValidateRequest(PersonaCreate):
    """Request body for validating a persona without saving."""


class PersonaValidateResponse(BaseModel):
    """Response model for persona validation."""

    valid: bool = Field(description="Whether the persona is valid")
    errors: list[str] = Field(default_factory=list, description="Validation error messages")


class ErrorResponse(BaseModel):
    """Response model for errors."""

    detail: str = Field(description="Human-readable error message")


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

_VALID_FAN_IN_STRATEGIES = {"all_must_pass", "any_pass", "majority", "merge"}


def create_personas_router(loader: PersonaRegistryPort) -> APIRouter:
    """Create FastAPI router for Ravn persona endpoints."""
    router = APIRouter(prefix="/api/v1/ravn")

    @router.get(
        "/personas",
        response_model=list[PersonaSummary],
        tags=["Personas"],
    )
    def list_personas(
        source: str | None = Query(
            default=None,
            description="Filter by source: 'builtin', 'custom', or 'all' (default: all)",
        ),
    ) -> list[PersonaSummary]:
        """List all available personas with summary info."""
        names = loader.list_names()
        result: list[PersonaSummary] = []

        for name in names:
            is_builtin = loader.is_builtin(name)
            persona_source = loader.source(name)
            has_file = persona_source != "[built-in]" and bool(persona_source)

            if source == "builtin" and not is_builtin:
                continue
            if source == "custom" and (is_builtin and not has_file):
                continue

            config = loader.load(name)
            if config is None:
                continue
            result.append(PersonaSummary.from_persona(config, loader))

        return result

    @router.post(
        "/personas/validate",
        response_model=PersonaValidateResponse,
        tags=["Personas"],
    )
    def validate_persona(data: PersonaValidateRequest) -> PersonaValidateResponse:
        """Validate a persona definition without saving it."""
        errors: list[str] = []

        if data.fan_in_strategy not in _VALID_FAN_IN_STRATEGIES:
            errors.append(
                f"Invalid fan_in_strategy '{data.fan_in_strategy}'. "
                f"Must be one of: {', '.join(sorted(_VALID_FAN_IN_STRATEGIES))}"
            )

        return PersonaValidateResponse(valid=not errors, errors=errors)

    @router.get(
        "/personas/{name}",
        response_model=PersonaDetail,
        responses={404: {"model": ErrorResponse}},
        tags=["Personas"],
    )
    def get_persona(
        name: str = Path(description="Persona name"),
    ) -> PersonaDetail:
        """Get full detail for a named persona."""
        config = loader.load(name)
        if config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Persona not found: {name}",
            )
        return PersonaDetail.from_persona(config, loader)

    @router.get(
        "/personas/{name}/yaml",
        response_class=Response,
        responses={
            200: {"content": {"text/yaml": {}}, "description": "Raw YAML source"},
            404: {"model": ErrorResponse},
        },
        tags=["Personas"],
    )
    def get_persona_yaml(
        name: str = Path(description="Persona name"),
    ) -> Response:
        """Return the raw YAML text for a named persona."""
        config = loader.load(name)
        if config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Persona not found: {name}",
            )
        # to_yaml() is a static serialization utility that operates on any
        # PersonaConfig — it does not perform any filesystem I/O and is not an
        # adapter-specific operation. It lives on FilesystemPersonaAdapter as
        # the canonical YAML serializer for PersonaConfig objects.
        yaml_text = FilesystemPersonaAdapter.to_yaml(config)
        return Response(content=yaml_text, media_type="text/yaml")

    @router.post(
        "/personas",
        response_model=PersonaDetail,
        status_code=status.HTTP_201_CREATED,
        responses={409: {"model": ErrorResponse}},
        tags=["Personas"],
    )
    def create_persona(data: PersonaCreate) -> PersonaDetail:
        """Create a new custom persona. Returns 409 if name already exists as a custom file."""
        existing_source = loader.source(data.name)
        if existing_source and existing_source != "[built-in]":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Persona already exists as custom: {data.name}",
            )
        if loader.is_builtin(data.name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Persona already exists as built-in: {data.name}",
            )
        config = data.to_persona_config()
        loader.save(config)
        saved = loader.load(data.name)
        if saved is None:
            saved = config
        return PersonaDetail.from_persona(saved, loader)

    @router.put(
        "/personas/{name}",
        response_model=PersonaDetail,
        responses={404: {"model": ErrorResponse}},
        tags=["Personas"],
    )
    def replace_persona(
        name: str = Path(description="Persona name"),
        data: PersonaCreate = ...,
    ) -> PersonaDetail:
        """Full replace a persona. Creates an override file for built-ins."""
        if not loader.load(name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Persona not found: {name}",
            )
        config = _dc_replace(data.to_persona_config(), name=name)
        loader.save(config)
        saved = loader.load(name)
        if saved is None:
            saved = config
        return PersonaDetail.from_persona(saved, loader)

    @router.delete(
        "/personas/{name}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
        tags=["Personas"],
    )
    def delete_persona(
        name: str = Path(description="Persona name"),
    ) -> None:
        """Delete a custom persona file. Returns 400 for built-ins without an override."""
        if not loader.load(name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Persona not found: {name}",
            )
        deleted = loader.delete(name)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete built-in persona without an override file: {name}",
            )

    @router.post(
        "/personas/{name}/fork",
        response_model=PersonaDetail,
        status_code=status.HTTP_201_CREATED,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
        tags=["Personas"],
    )
    def fork_persona(
        name: str = Path(description="Source persona name"),
        body: PersonaForkRequest = ...,
    ) -> PersonaDetail:
        """Fork a persona to a new name. Returns 409 if new_name already exists as custom."""
        source_config = loader.load(name)
        if source_config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Persona not found: {name}",
            )
        new_source = loader.source(body.new_name)
        if new_source and new_source != "[built-in]":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Persona already exists as custom: {body.new_name}",
            )
        if loader.is_builtin(body.new_name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Persona already exists as built-in: {body.new_name}",
            )
        forked = _dc_replace(source_config, name=body.new_name)
        loader.save(forked)
        saved = loader.load(body.new_name)
        if saved is None:
            saved = forked
        return PersonaDetail.from_persona(saved, loader)

    return router

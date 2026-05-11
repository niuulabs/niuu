"""Volundr-hosted Ravn persona registry routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
from ravn.adapters.personas.postgres_registry import PersonaView, PostgresPersonaRegistry
from volundr.adapters.inbound.auth import extract_principal, get_current_user
from volundr.domain.models import User

_NAME_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
_VALID_FAN_IN_STRATEGIES = {
    "all_must_pass",
    "any_passes",
    "quorum",
    "merge",
    "first_wins",
    "weighted_score",
}


class ErrorResponse(BaseModel):
    """Response model for errors."""

    detail: str = Field(description="Human-readable error message")


class PersonaSummaryResponse(BaseModel):
    """Summary view for the persona list."""

    name: str
    role: str
    letter: str
    color: str
    summary: str
    permission_mode: str
    allowed_tools: list[str]
    iteration_budget: int
    is_builtin: bool
    has_override: bool
    produces_event: str
    consumes_events: list[str]


class PersonaLLMResponse(BaseModel):
    """LLM configuration embedded in persona detail responses."""

    thinking_enabled: bool
    max_tokens: int
    temperature: float | None = None


class PersonaProducesResponse(BaseModel):
    """Produced event configuration."""

    event_type: str
    schema_def: dict[str, str]


class PersonaConsumesEventResponse(BaseModel):
    """Single consumed event configuration."""

    name: str
    injects: list[str] | None = None
    trust: float | None = None


class PersonaConsumesResponse(BaseModel):
    """Consumed event configuration."""

    events: list[PersonaConsumesEventResponse]
    schema_def: dict[str, str] = Field(default_factory=dict)


class PersonaFanInResponse(BaseModel):
    """Fan-in configuration."""

    strategy: str
    params: dict[str, Any]


class PersonaDetailResponse(PersonaSummaryResponse):
    """Full persona detail view."""

    description: str
    system_prompt_template: str
    forbidden_tools: list[str]
    llm: PersonaLLMResponse
    produces: PersonaProducesResponse
    consumes: PersonaConsumesResponse
    fan_in: PersonaFanInResponse | None = None
    mimir_write_routing: str | None = None
    yaml_source: str
    override_source: str | None = None


class PersonaConsumesEventRequest(BaseModel):
    """Single consumed event request payload."""

    name: str = Field(min_length=1)
    injects: list[str] = Field(default_factory=list)
    trust: float | None = Field(default=None, ge=0, le=1)


class PersonaCreateRequest(BaseModel):
    """Create or replace a persona."""

    name: str = Field(min_length=1, max_length=128, pattern=_NAME_PATTERN)
    role: str = Field(default="build")
    letter: str = Field(default="", max_length=1)
    color: str = Field(default="")
    summary: str = Field(default="")
    description: str = Field(default="")
    system_prompt_template: str = Field(default="")
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    permission_mode: str = Field(default="default")
    iteration_budget: int = Field(default=0, ge=0)
    llm_thinking_enabled: bool = Field(default=False)
    llm_max_tokens: int = Field(default=0, ge=0)
    llm_temperature: float | None = Field(default=None, ge=0, le=2)
    produces_event_type: str = Field(default="")
    produces_schema: dict[str, str] = Field(default_factory=dict)
    consumes_events: list[PersonaConsumesEventRequest] = Field(default_factory=list)
    consumes_schema: dict[str, str] = Field(default_factory=dict)
    fan_in_strategy: str | None = Field(default=None)
    fan_in_params: dict[str, Any] = Field(default_factory=dict)
    mimir_write_routing: str | None = Field(default=None)

    def to_payload(self, *, name: str | None = None) -> dict[str, Any]:
        """Serialize to the stored JSON payload shape."""
        return {
            "name": name or self.name,
            "role": self.role,
            "letter": self.letter,
            "color": self.color,
            "summary": self.summary,
            "description": self.description,
            "system_prompt_template": self.system_prompt_template,
            "allowed_tools": list(self.allowed_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "permission_mode": self.permission_mode,
            "executor": None,
            "iteration_budget": self.iteration_budget,
            "llm_primary_alias": "",
            "llm_thinking_enabled": self.llm_thinking_enabled,
            "llm_max_tokens": self.llm_max_tokens,
            "llm_temperature": self.llm_temperature,
            "produces_event_type": self.produces_event_type,
            "produces_schema": dict(self.produces_schema),
            "consumes_events": [event.model_dump() for event in self.consumes_events],
            "consumes_schema": dict(self.consumes_schema),
            "fan_in_strategy": self.fan_in_strategy,
            "fan_in_params": dict(self.fan_in_params),
            "mimir_write_routing": self.mimir_write_routing,
        }


class PersonaForkRequest(BaseModel):
    """Fork a persona to a new name."""

    new_name: str = Field(min_length=1, max_length=128, pattern=_NAME_PATTERN)


class PersonaValidateResponse(BaseModel):
    """Validation response."""

    valid: bool
    errors: list[str] = Field(default_factory=list)


def create_ravn_personas_router(registry: PostgresPersonaRegistry | None = None) -> APIRouter:
    """Create persona registry routes."""
    router = APIRouter(prefix="/api/v1", tags=["Personas"])

    def _get_registry(request: Request) -> PostgresPersonaRegistry:
        if registry is not None:
            return registry
        active = getattr(request.app.state, "persona_registry", None)
        if isinstance(active, PostgresPersonaRegistry):
            return active
        raise RuntimeError("Persona registry is not configured on this app.")

    @router.get("/personas", response_model=list[PersonaSummaryResponse])
    @router.get(
        "/ravn/personas", response_model=list[PersonaSummaryResponse], include_in_schema=False
    )
    async def list_personas(
        source: str = Query(default="all"),
        principal: Principal = Depends(extract_principal),
        persona_registry: PostgresPersonaRegistry = Depends(_get_registry),
    ) -> list[PersonaSummaryResponse]:
        views = await persona_registry.list_personas(principal.user_id, source=source)
        return [_to_summary(view) for view in views]

    @router.post("/personas/validate", response_model=PersonaValidateResponse)
    @router.post(
        "/ravn/personas/validate",
        response_model=PersonaValidateResponse,
        include_in_schema=False,
    )
    async def validate_persona(data: PersonaCreateRequest) -> PersonaValidateResponse:
        errors: list[str] = []
        overlap = sorted(set(data.allowed_tools) & set(data.forbidden_tools))
        if overlap:
            errors.append(f"allowed_tools and forbidden_tools overlap: {', '.join(overlap)}")
        if data.fan_in_strategy and data.fan_in_strategy not in _VALID_FAN_IN_STRATEGIES:
            errors.append(
                f"Invalid fan_in_strategy '{data.fan_in_strategy}'. "
                f"Must be one of: {', '.join(sorted(_VALID_FAN_IN_STRATEGIES))}"
            )
        return PersonaValidateResponse(valid=not errors, errors=errors)

    @router.get(
        "/personas/{name}",
        response_model=PersonaDetailResponse,
        responses={404: {"model": ErrorResponse}},
    )
    @router.get(
        "/ravn/personas/{name}",
        response_model=PersonaDetailResponse,
        responses={404: {"model": ErrorResponse}},
        include_in_schema=False,
    )
    async def get_persona(
        name: str = Path(description="Persona name"),
        principal: Principal = Depends(extract_principal),
        persona_registry: PostgresPersonaRegistry = Depends(_get_registry),
    ) -> PersonaDetailResponse:
        view = await persona_registry.get_persona(principal.user_id, name)
        if view is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Persona not found: {name}",
            )
        return _to_detail(view)

    @router.get(
        "/personas/{name}/yaml", response_class=Response, responses={404: {"model": ErrorResponse}}
    )
    @router.get(
        "/ravn/personas/{name}/yaml",
        response_class=Response,
        responses={404: {"model": ErrorResponse}},
        include_in_schema=False,
    )
    async def get_persona_yaml(
        name: str = Path(description="Persona name"),
        principal: Principal = Depends(extract_principal),
        persona_registry: PostgresPersonaRegistry = Depends(_get_registry),
    ) -> Response:
        yaml_text = await persona_registry.get_persona_yaml(principal.user_id, name)
        if yaml_text is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Persona not found: {name}",
            )
        return Response(content=yaml_text, media_type="text/yaml")

    @router.post(
        "/personas",
        response_model=PersonaDetailResponse,
        status_code=status.HTTP_201_CREATED,
        responses={409: {"model": ErrorResponse}},
    )
    @router.post(
        "/ravn/personas",
        response_model=PersonaDetailResponse,
        status_code=status.HTTP_201_CREATED,
        responses={409: {"model": ErrorResponse}},
        include_in_schema=False,
    )
    async def create_persona(
        data: PersonaCreateRequest,
        principal: Principal = Depends(extract_principal),
        user: User = Depends(get_current_user),
        persona_registry: PostgresPersonaRegistry = Depends(_get_registry),
    ) -> PersonaDetailResponse:
        del user
        existing = await persona_registry.get_persona(principal.user_id, data.name)
        if existing is not None and existing.has_override:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Persona already exists as custom: {data.name}",
            )
        if persona_registry.is_builtin(data.name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Persona already exists as built-in: {data.name}",
            )
        await persona_registry.save_persona(principal.user_id, data.to_payload())
        saved = await persona_registry.get_persona(principal.user_id, data.name)
        assert saved is not None
        return _to_detail(saved)

    @router.put(
        "/personas/{name}",
        response_model=PersonaDetailResponse,
        responses={404: {"model": ErrorResponse}},
    )
    @router.put(
        "/ravn/personas/{name}",
        response_model=PersonaDetailResponse,
        responses={404: {"model": ErrorResponse}},
        include_in_schema=False,
    )
    async def replace_persona(
        data: PersonaCreateRequest,
        name: str = Path(description="Persona name"),
        principal: Principal = Depends(extract_principal),
        user: User = Depends(get_current_user),
        persona_registry: PostgresPersonaRegistry = Depends(_get_registry),
    ) -> PersonaDetailResponse:
        del user
        existing = await persona_registry.get_persona(principal.user_id, name)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Persona not found: {name}",
            )
        await persona_registry.save_persona(principal.user_id, data.to_payload(name=name))
        saved = await persona_registry.get_persona(principal.user_id, name)
        assert saved is not None
        return _to_detail(saved)

    @router.delete(
        "/personas/{name}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    @router.delete(
        "/ravn/personas/{name}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
        include_in_schema=False,
    )
    async def delete_persona(
        name: str = Path(description="Persona name"),
        principal: Principal = Depends(extract_principal),
        user: User = Depends(get_current_user),
        persona_registry: PostgresPersonaRegistry = Depends(_get_registry),
    ) -> None:
        del user
        existing = await persona_registry.get_persona(principal.user_id, name)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Persona not found: {name}",
            )
        deleted = await persona_registry.delete_persona(principal.user_id, name)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot delete built-in persona without a user override: {name}",
            )

    @router.post(
        "/personas/{name}/fork",
        response_model=PersonaDetailResponse,
        status_code=status.HTTP_201_CREATED,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    @router.post(
        "/ravn/personas/{name}/fork",
        response_model=PersonaDetailResponse,
        status_code=status.HTTP_201_CREATED,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        include_in_schema=False,
    )
    async def fork_persona(
        body: PersonaForkRequest,
        name: str = Path(description="Source persona name"),
        principal: Principal = Depends(extract_principal),
        user: User = Depends(get_current_user),
        persona_registry: PostgresPersonaRegistry = Depends(_get_registry),
    ) -> PersonaDetailResponse:
        del user
        source_view = await persona_registry.get_persona(principal.user_id, name)
        if source_view is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Persona not found: {name}",
            )
        existing = await persona_registry.get_persona(principal.user_id, body.new_name)
        if existing is not None and existing.has_override:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Persona already exists as custom: {body.new_name}",
            )
        if persona_registry.is_builtin(body.new_name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Persona already exists as built-in: {body.new_name}",
            )

        payload = dict(source_view.payload)
        payload["name"] = body.new_name
        await persona_registry.save_persona(principal.user_id, payload)
        saved = await persona_registry.get_persona(principal.user_id, body.new_name)
        assert saved is not None
        return _to_detail(saved)

    return router


def _to_summary(view: PersonaView) -> PersonaSummaryResponse:
    payload = view.payload
    return PersonaSummaryResponse(
        name=str(payload["name"]),
        role=str(payload["role"]),
        letter=str(payload["letter"]),
        color=str(payload["color"]),
        summary=str(payload["summary"]),
        permission_mode=str(payload["permission_mode"]),
        allowed_tools=list(payload["allowed_tools"]),
        iteration_budget=int(payload["iteration_budget"]),
        is_builtin=view.is_builtin,
        has_override=view.has_override,
        produces_event=str(payload["produces_event_type"]),
        consumes_events=[
            str(event["name"]) for event in payload["consumes_events"] if str(event.get("name", ""))
        ],
    )


def _to_detail(view: PersonaView) -> PersonaDetailResponse:
    payload = view.payload
    fan_in_strategy = payload.get("fan_in_strategy")
    fan_in = None
    if fan_in_strategy:
        fan_in = PersonaFanInResponse(
            strategy=str(fan_in_strategy),
            params=dict(payload.get("fan_in_params") or {}),
        )

    return PersonaDetailResponse(
        **_to_summary(view).model_dump(),
        description=str(payload["description"]),
        system_prompt_template=str(payload["system_prompt_template"]),
        forbidden_tools=list(payload["forbidden_tools"]),
        llm=PersonaLLMResponse(
            thinking_enabled=bool(payload["llm_thinking_enabled"]),
            max_tokens=int(payload["llm_max_tokens"]),
            temperature=(
                float(payload["llm_temperature"])
                if payload.get("llm_temperature") is not None
                else None
            ),
        ),
        produces=PersonaProducesResponse(
            event_type=str(payload["produces_event_type"]),
            schema_def=dict(payload["produces_schema"]),
        ),
        consumes=PersonaConsumesResponse(
            events=[
                PersonaConsumesEventResponse(
                    name=str(event["name"]),
                    injects=list(event.get("injects", [])) or None,
                    trust=(float(event["trust"]) if event.get("trust") is not None else None),
                )
                for event in payload["consumes_events"]
            ],
            schema_def=dict(payload.get("consumes_schema") or {}),
        ),
        fan_in=fan_in,
        mimir_write_routing=(
            str(payload["mimir_write_routing"]) if payload.get("mimir_write_routing") else None
        ),
        yaml_source=view.yaml_source,
        override_source=view.override_source,
    )

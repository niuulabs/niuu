"""REST API for flock flow configuration — CRUD for reusable persona compositions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
from tyr.adapters.inbound.auth import extract_principal
from tyr.domain.flock_flow import FlockFlowConfig, FlockPersonaOverride
from tyr.ports.flock_flow import FlockFlowProvider

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class PersonaOverrideBody(BaseModel):
    name: str
    llm: dict | None = None
    system_prompt_extra: str = ""
    iteration_budget: int = 0
    max_concurrent_tasks: int = 0


class FlockFlowBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    personas: list[PersonaOverrideBody] = Field(default_factory=list)
    mesh_transport: str = "nng"
    mimir: dict = Field(default_factory=dict)
    mimir_hosted_url: str = ""
    sleipnir_publish_urls: list[str] = Field(default_factory=list)
    max_concurrent_tasks: int = Field(default=3, ge=1, le=100)


class FlockFlowResponse(BaseModel):
    name: str
    description: str
    personas: list[PersonaOverrideBody]
    mesh_transport: str
    mimir: dict
    mimir_hosted_url: str
    sleipnir_publish_urls: list[str]
    max_concurrent_tasks: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_response(flow: FlockFlowConfig) -> FlockFlowResponse:
    return FlockFlowResponse(
        name=flow.name,
        description=flow.description,
        personas=[
            PersonaOverrideBody(
                name=p.name,
                llm=p.llm,
                system_prompt_extra=p.system_prompt_extra,
                iteration_budget=p.iteration_budget,
                max_concurrent_tasks=p.max_concurrent_tasks,
            )
            for p in flow.personas
        ],
        mesh_transport=flow.mesh_transport,
        mimir=dict(flow.mimir),
        mimir_hosted_url=flow.mimir_hosted_url,
        sleipnir_publish_urls=flow.sleipnir_publish_urls,
        max_concurrent_tasks=flow.max_concurrent_tasks,
    )


def _body_to_domain(body: FlockFlowBody) -> FlockFlowConfig:
    return FlockFlowConfig(
        name=body.name,
        description=body.description,
        personas=[
            FlockPersonaOverride(
                name=p.name,
                llm=p.llm,
                system_prompt_extra=p.system_prompt_extra,
                iteration_budget=p.iteration_budget,
                max_concurrent_tasks=p.max_concurrent_tasks,
            )
            for p in body.personas
        ],
        mesh_transport=body.mesh_transport,
        mimir=dict(body.mimir),
        mimir_hosted_url=body.mimir_hosted_url,
        sleipnir_publish_urls=body.sleipnir_publish_urls,
        max_concurrent_tasks=body.max_concurrent_tasks,
    )


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

_persona_source = None


async def resolve_flow_provider() -> FlockFlowProvider:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Flock flow provider not configured",
    )


async def resolve_persona_names() -> set[str]:
    """Return the set of known persona names from the configured persona source.

    Overridden at startup when a persona source is wired.
    """
    return set()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def create_flock_flows_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr/flock_flows", tags=["Flock Flows"])

    @router.get("", response_model=list[FlockFlowResponse])
    async def list_flows(
        _principal: Principal = Depends(extract_principal),
        provider: FlockFlowProvider = Depends(resolve_flow_provider),
    ) -> list[FlockFlowResponse]:
        """List all flock flow configurations."""
        return [_to_response(f) for f in provider.list()]

    @router.get("/{name}", response_model=FlockFlowResponse)
    async def get_flow(
        name: str,
        _principal: Principal = Depends(extract_principal),
        provider: FlockFlowProvider = Depends(resolve_flow_provider),
    ) -> FlockFlowResponse:
        """Get a single flock flow by name."""
        flow = provider.get(name)
        if flow is None:
            raise HTTPException(status_code=404, detail=f"Flow '{name}' not found")
        return _to_response(flow)

    @router.post("", response_model=FlockFlowResponse, status_code=201)
    async def create_flow(
        body: FlockFlowBody,
        _principal: Principal = Depends(extract_principal),
        provider: FlockFlowProvider = Depends(resolve_flow_provider),
        known_personas: set[str] = Depends(resolve_persona_names),
    ) -> FlockFlowResponse:
        """Create a new flock flow configuration."""
        if provider.get(body.name) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Flow '{body.name}' already exists",
            )
        _validate_persona_names(body, known_personas)
        flow = _body_to_domain(body)
        provider.save(flow)
        return _to_response(flow)

    @router.put("/{name}", response_model=FlockFlowResponse)
    async def update_flow(
        name: str,
        body: FlockFlowBody,
        _principal: Principal = Depends(extract_principal),
        provider: FlockFlowProvider = Depends(resolve_flow_provider),
        known_personas: set[str] = Depends(resolve_persona_names),
    ) -> FlockFlowResponse:
        """Update an existing flock flow configuration."""
        if provider.get(name) is None:
            raise HTTPException(status_code=404, detail=f"Flow '{name}' not found")
        _validate_persona_names(body, known_personas)
        flow = _body_to_domain(body)
        provider.save(flow)
        return _to_response(flow)

    @router.delete("/{name}", status_code=204)
    async def delete_flow(
        name: str,
        _principal: Principal = Depends(extract_principal),
        provider: FlockFlowProvider = Depends(resolve_flow_provider),
    ) -> None:
        """Delete a flock flow configuration."""
        if not provider.delete(name):
            raise HTTPException(status_code=404, detail=f"Flow '{name}' not found")

    return router


def _validate_persona_names(body: FlockFlowBody, known_personas: set[str]) -> None:
    """Validate that all persona names in the body resolve against the known set.

    When the known set is empty (no persona source configured), validation is
    skipped — the provider may be running without a persona source in dev mode.
    """
    if not known_personas:
        return
    requested = {p.name for p in body.personas}
    missing = requested - known_personas
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown persona name(s): {sorted(missing)}",
        )

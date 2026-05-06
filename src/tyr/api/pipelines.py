"""REST API for dynamic pipeline creation.

Allows clients to POST a YAML pipeline definition and start execution
immediately.  Supports:

- UI "Launch" button sending a pipeline definition
- A Ravn coordinator persona generating a YAML and posting it
- Event triggers creating pipelines from stored templates (existing behaviour)

Endpoint::

    POST /api/v1/tyr/pipelines
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
from tyr.adapters.inbound.auth import extract_principal
from tyr.domain.pipeline_executor import TemplateAwarePipelineExecutor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreatePipelineRequest(BaseModel):
    """Request body for POST /api/v1/tyr/pipelines."""

    definition: str = Field(
        min_length=1,
        description="YAML pipeline definition string.",
    )
    context: dict = Field(
        default_factory=dict,
        description="Key-value context substituted as {event.*} placeholders.",
    )
    auto_start: bool = Field(
        default=True,
        description="When True, dispatch Phase 1 raids immediately after creation.",
    )


class CreatePipelineResponse(BaseModel):
    """Response body for POST /api/v1/tyr/pipelines."""

    saga_id: str
    slug: str
    name: str
    phase_count: int
    auto_started: bool


# ---------------------------------------------------------------------------
# Dependency stubs (overridden by main.py)
# ---------------------------------------------------------------------------


async def resolve_pipeline_executor() -> TemplateAwarePipelineExecutor:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Pipeline executor not configured",
    )


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_pipelines_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr/pipelines", tags=["Pipelines"])

    @router.post("", response_model=CreatePipelineResponse, status_code=201)
    async def create_pipeline(
        body: CreatePipelineRequest,
        principal: Principal = Depends(extract_principal),
        executor: TemplateAwarePipelineExecutor = Depends(resolve_pipeline_executor),
    ) -> CreatePipelineResponse:
        """Create and optionally start a pipeline from an inline YAML definition.

        Parses the YAML, creates a Saga with Phases and Raids in the database,
        and (when ``auto_start`` is True) immediately dispatches Phase 1 raids
        to Volundr.

        Returns 422 when the YAML definition fails validation.
        Returns 503 when no Volundr adapter is available.
        """
        try:
            saga = await executor.create_from_yaml(
                body.definition,
                context=body.context,
                auto_start=body.auto_start,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid pipeline definition: {exc}",
            )
        except Exception as exc:
            logger.error("Failed to create pipeline: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Pipeline creation failed: {exc}",
            )

        phases = await executor.get_phases(saga.id)

        return CreatePipelineResponse(
            saga_id=str(saga.id),
            slug=saga.slug,
            name=saga.name,
            phase_count=len(phases),
            auto_started=body.auto_start,
        )

    return router

"""Lightweight saga preview endpoints for Tyr compatibility."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
from tyr.adapters.inbound.auth import extract_principal
from tyr.api.sagas import SagaListItem, resolve_llm
from tyr.ports.llm import LLMPort

logger = logging.getLogger(__name__)


class CreateSagaRequest(BaseModel):
    spec: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    model: str = Field(default="")


def _slugify_preview(value: str) -> str:
    words = [part for part in value.lower().replace("/", " ").replace("_", " ").split() if part]
    if not words:
        return "saga-preview"
    return "-".join(words[:6])[:48]


def create_saga_previews_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr/sagas", tags=["Sagas"])

    @router.post("", response_model=SagaListItem, status_code=status.HTTP_201_CREATED)
    async def create_saga(
        body: CreateSagaRequest,
        request: Request,
        _principal: Principal = Depends(extract_principal),
        llm: LLMPort = Depends(resolve_llm),
    ) -> SagaListItem:
        """Return a decomposition-backed saga preview for web-next compatibility."""
        model = body.model or request.app.state.settings.llm.default_model
        try:
            structure = await llm.decompose_spec(body.spec, body.repo, model=model)
        except Exception as exc:
            logger.error("Saga preview failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"LLM decomposition failed: {exc}",
            ) from exc

        slug = _slugify_preview(structure.name or body.spec)
        return SagaListItem(
            id=f"preview-{slug}",
            tracker_id="",
            tracker_type="preview",
            slug=slug,
            name=structure.name or body.spec,
            repos=[body.repo],
            feature_branch=f"feat/{slug}",
            status="active",
            progress=0.0,
            milestone_count=len(structure.phases),
            issue_count=sum(len(phase.raids) for phase in structure.phases),
            url="",
            base_branch="main",
            confidence=0.0,
            created_at="",
        )

    return router

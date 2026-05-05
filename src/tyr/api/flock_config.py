"""REST API for flock runtime configuration.

Updates are applied in-memory and take effect immediately. They are not
persisted to disk; the YAML config remains authoritative across restarts.

This route family owns workflow/runtime flock execution settings and is kept
separate from operator settings under ``/api/v1/tyr/settings/flock``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from niuu.domain.models import Principal
from tyr.adapters.inbound.auth import extract_principal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class FlockPersonaResponse(BaseModel):
    name: str
    llm: dict = {}


class FlockConfigResponse(BaseModel):
    flock_enabled: bool
    flock_default_personas: list[FlockPersonaResponse]
    flock_llm_config: dict
    flock_sleipnir_publish_urls: list[str]


class PatchFlockConfigRequest(BaseModel):
    flock_enabled: bool | None = None
    flock_default_personas: list[str] | None = None
    flock_llm_config: dict | None = None
    flock_sleipnir_publish_urls: list[str] | None = None


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


def create_flock_config_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr/flock", tags=["Flock"])

    @router.get("/config", response_model=FlockConfigResponse)
    async def get_flock_config(
        request: Request,
        _principal: Principal = Depends(extract_principal),
    ) -> FlockConfigResponse:
        """Return the current flock configuration."""
        flock = request.app.state.settings.dispatch.flock
        return FlockConfigResponse(
            flock_enabled=flock.enabled,
            flock_default_personas=[
                FlockPersonaResponse(name=p.name, llm=dict(p.llm)) for p in flock.default_personas
            ],
            flock_llm_config=dict(flock.llm_config),
            flock_sleipnir_publish_urls=list(flock.sleipnir_publish_urls),
        )

    @router.patch("/config", response_model=FlockConfigResponse)
    async def patch_flock_config(
        request: Request,
        body: PatchFlockConfigRequest,
        _principal: Principal = Depends(extract_principal),
    ) -> FlockConfigResponse:
        """Update flock configuration in-memory.

        Changes take effect immediately but are not persisted to disk.
        """
        flock = request.app.state.settings.dispatch.flock

        if body.flock_enabled is not None:
            flock.enabled = body.flock_enabled
            logger.info("Flock enabled set to %s", flock.enabled)

        if body.flock_llm_config is not None:
            flock.llm_config = body.flock_llm_config
            logger.info("Flock llm_config updated")

        if body.flock_sleipnir_publish_urls is not None:
            flock.sleipnir_publish_urls = body.flock_sleipnir_publish_urls
            n = len(flock.sleipnir_publish_urls)
            logger.info("Flock sleipnir_publish_urls updated: %d URLs", n)

        if body.flock_default_personas is not None:
            from tyr.config import PersonaOverride

            flock.default_personas = [
                PersonaOverride(name=name) for name in body.flock_default_personas
            ]
            logger.info("Flock default_personas updated: %s", body.flock_default_personas)

        return FlockConfigResponse(
            flock_enabled=flock.enabled,
            flock_default_personas=[
                FlockPersonaResponse(name=p.name, llm=dict(p.llm)) for p in flock.default_personas
            ],
            flock_llm_config=dict(flock.llm_config),
            flock_sleipnir_publish_urls=list(flock.sleipnir_publish_urls),
        )

    return router

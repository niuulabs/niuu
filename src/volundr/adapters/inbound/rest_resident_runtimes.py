"""Authenticated resident runtime read and deployment-profile API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import (
    Principal,
    ResidentBackend,
    ResidentCapability,
    ResidentDeploymentProfile,
    ResidentEngine,
    ResidentRuntime,
)
from volundr.domain.services.resident_runtime import (
    ResidentRuntimeNotFoundError,
    ResidentRuntimeService,
)

_CAMEL = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ResidentProfileResponse(BaseModel):
    """Public, non-secret portion of a configured resident profile."""

    model_config = _CAMEL

    id: str
    display_name: str
    description: str
    backend: ResidentBackend
    engine: ResidentEngine
    capabilities: list[ResidentCapability]
    default_model: str
    allowed_models: list[str]
    labels: list[str]

    @classmethod
    def from_profile(cls, profile: ResidentDeploymentProfile) -> ResidentProfileResponse:
        return cls.model_validate(profile.model_dump())


def create_resident_runtimes_router(service: ResidentRuntimeService) -> APIRouter:
    """Create target-local resident runtime routes for Ravn and Guild."""
    router = APIRouter(prefix="/api/v1/forge", tags=["Resident Runtimes"])

    @router.get("/resident-profiles", response_model=list[ResidentProfileResponse])
    async def list_resident_profiles(
        _: Principal = Depends(extract_principal),
    ) -> list[ResidentProfileResponse]:
        return [
            ResidentProfileResponse.from_profile(profile) for profile in service.list_profiles()
        ]

    @router.get("/resident-runtimes", response_model=list[ResidentRuntime])
    async def list_resident_runtimes(
        principal: Principal = Depends(extract_principal),
    ) -> list[ResidentRuntime]:
        return await service.list(principal)

    @router.get(
        "/resident-runtimes/{runtime_id}",
        response_model=ResidentRuntime,
        responses={status.HTTP_404_NOT_FOUND: {"description": "Resident runtime not found"}},
    )
    async def get_resident_runtime(
        runtime_id: UUID = Path(description="Resident runtime UUID"),
        principal: Principal = Depends(extract_principal),
    ) -> ResidentRuntime:
        try:
            return await service.get(principal, runtime_id)
        except ResidentRuntimeNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return router

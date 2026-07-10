"""Authenticated resident runtime read and deployment-profile API."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.models import (
    Principal,
    ResidentBackend,
    ResidentCapability,
    ResidentDeploymentProfile,
    ResidentDesiredState,
    ResidentEngine,
    ResidentRuntime,
)
from volundr.domain.services.resident_runtime import (
    ResidentProfileNotFoundError,
    ResidentRuntimeAccessError,
    ResidentRuntimeConflictError,
    ResidentRuntimeDeploymentError,
    ResidentRuntimeNotFoundError,
    ResidentRuntimeService,
    ResidentRuntimeValidationError,
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


class CreateResidentRuntimeRequest(BaseModel):
    """Product-level input for one resident deployment."""

    model_config = _CAMEL

    name: str = Field(min_length=1, max_length=255)
    profile_id: str = Field(min_length=1, max_length=100)
    persona_name: str = Field(default="", max_length=255)
    model: str = Field(default="", max_length=255)


def _resident_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ResidentRuntimeAccessError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ResidentRuntimeNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ResidentRuntimeConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (ResidentProfileNotFoundError, ResidentRuntimeValidationError)):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, ResidentRuntimeDeploymentError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    raise exc


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

    @router.post(
        "/resident-runtimes",
        response_model=ResidentRuntime,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_resident_runtime(
        body: CreateResidentRuntimeRequest,
        principal: Principal = Depends(extract_principal),
    ) -> ResidentRuntime:
        try:
            return await service.create(
                principal,
                name=body.name,
                profile_id=body.profile_id,
                persona_name=body.persona_name,
                model=body.model,
            )
        except Exception as exc:
            raise _resident_error(exc) from exc

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
        except Exception as exc:
            raise _resident_error(exc) from exc

    @router.post("/resident-runtimes/{runtime_id}/restart", response_model=ResidentRuntime)
    async def restart_resident_runtime(
        runtime_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> ResidentRuntime:
        try:
            return await service.restart(principal, runtime_id)
        except Exception as exc:
            raise _resident_error(exc) from exc

    @router.post("/resident-runtimes/{runtime_id}/suspend", response_model=ResidentRuntime)
    async def suspend_resident_runtime(
        runtime_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> ResidentRuntime:
        try:
            return await service.set_desired_state(
                principal,
                runtime_id,
                ResidentDesiredState.SUSPENDED,
            )
        except Exception as exc:
            raise _resident_error(exc) from exc

    @router.post("/resident-runtimes/{runtime_id}/resume", response_model=ResidentRuntime)
    async def resume_resident_runtime(
        runtime_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> ResidentRuntime:
        try:
            return await service.set_desired_state(
                principal,
                runtime_id,
                ResidentDesiredState.RUNNING,
            )
        except Exception as exc:
            raise _resident_error(exc) from exc

    @router.delete(
        "/resident-runtimes/{runtime_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_resident_runtime(
        runtime_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        try:
            await service.delete(principal, runtime_id)
        except ResidentRuntimeNotFoundError:
            pass
        except Exception as exc:
            raise _resident_error(exc) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router

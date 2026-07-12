"""Authenticated resident runtime read and deployment-profile API."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
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
    ResidentLogPage,
    ResidentRuntime,
    ResidentSession,
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
    model_prefix: str
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


class ResidentUsageRequest(BaseModel):
    """One real token/cost report emitted by a resident engine."""

    tokens: int = Field(gt=0)
    cost: float = Field(default=0, ge=0)
    message_count: int = Field(default=1, ge=0)
    provider: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=255)


class CreateResidentSessionRequest(BaseModel):
    """Input for one native session inside a resident engine."""

    model_config = _CAMEL

    title: str = Field(min_length=1, max_length=255)
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

    @router.get("/resident-runtimes/{runtime_id}/logs", response_model=ResidentLogPage)
    async def get_resident_runtime_logs(
        runtime_id: UUID,
        lines: int = Query(default=200, ge=1, le=5000),
        source: list[str] = Query(default_factory=list),
        min_level: str = Query(default="", max_length=32),
        principal: Principal = Depends(extract_principal),
    ) -> ResidentLogPage:
        try:
            return await service.logs(
                principal,
                runtime_id,
                lines=lines,
                sources=tuple(source),
                min_level=min_level,
            )
        except Exception as exc:
            raise _resident_error(exc) from exc

    @router.post("/resident-runtimes/{runtime_id}/usage", response_model=ResidentRuntime)
    async def record_resident_runtime_usage(
        runtime_id: UUID,
        body: ResidentUsageRequest,
        principal: Principal = Depends(extract_principal),
    ) -> ResidentRuntime:
        try:
            return await service.record_usage(
                principal,
                runtime_id,
                tokens=body.tokens,
                cost=body.cost,
                message_count=body.message_count,
            )
        except Exception as exc:
            raise _resident_error(exc) from exc

    @router.get(
        "/resident-runtimes/{runtime_id}/sessions",
        response_model=list[ResidentSession],
    )
    async def list_resident_sessions(
        runtime_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> list[ResidentSession]:
        try:
            return await service.list_sessions(principal, runtime_id)
        except Exception as exc:
            raise _resident_error(exc) from exc

    @router.post(
        "/resident-runtimes/{runtime_id}/sessions",
        response_model=ResidentSession,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_resident_session(
        runtime_id: UUID,
        body: CreateResidentSessionRequest,
        principal: Principal = Depends(extract_principal),
    ) -> ResidentSession:
        try:
            return await service.create_session(
                principal,
                runtime_id,
                title=body.title,
                model=body.model,
            )
        except Exception as exc:
            raise _resident_error(exc) from exc

    @router.delete(
        "/resident-runtimes/{runtime_id}/sessions/{session_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_resident_session(
        runtime_id: UUID,
        session_id: UUID,
        principal: Principal = Depends(extract_principal),
    ) -> Response:
        try:
            await service.delete_session(principal, runtime_id, session_id)
        except Exception as exc:
            raise _resident_error(exc) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.websocket("/resident-runtimes/{runtime_id}/sessions/{session_id}/chat")
    async def resident_session_chat(
        websocket: WebSocket,
        runtime_id: UUID,
        session_id: UUID,
    ) -> None:
        from niuu.app import _proxy_ws_identity

        user_id, tenant_id, roles = _proxy_ws_identity(websocket)
        if not user_id:
            await websocket.close(code=1008, reason="Not authorized for this resident")
            return
        principal = Principal(
            user_id=user_id,
            email="",
            tenant_id=tenant_id or "",
            roles=list(roles),
        )
        try:
            connection = await service.connect_chat(principal, runtime_id, session_id)
        except Exception:
            await websocket.close(code=1008, reason="Resident session is unavailable")
            return
        await websocket.accept()

        async def browser_to_engine() -> None:
            while True:
                await connection.send(await websocket.receive_json())

        async def engine_to_browser() -> None:
            while True:
                await websocket.send_json(await connection.receive())

        tasks = [
            asyncio.create_task(browser_to_engine()),
            asyncio.create_task(engine_to_browser()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(Exception, asyncio.CancelledError):
                    await task
            await connection.close()
            with suppress(Exception):
                await websocket.close()

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

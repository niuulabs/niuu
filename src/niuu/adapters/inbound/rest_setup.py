"""REST API for the first-launch setup wizard (progress, host facts, checks)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from niuu.adapters.inbound.auth import extract_principal
from niuu.domain.models import Principal
from niuu.domain.services.setup import SetupService
from niuu.domain.setup import KNOWN_SETUP_STEPS, SetupState, SystemReport

ADMIN_ROLE = "volundr:admin"


class SetupStepResponse(BaseModel):
    step: str
    completed_at: datetime = Field(serialization_alias="completedAt")
    data: dict[str, Any] = Field(default_factory=dict)


class SetupStateResponse(BaseModel):
    enabled: bool
    mode: str
    completed: bool
    completed_at: datetime | None = Field(default=None, serialization_alias="completedAt")
    steps: list[str] = Field(description="All step ids in presentation order")
    completed_steps: list[SetupStepResponse] = Field(serialization_alias="completedSteps")


class SetupStepRequest(BaseModel):
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-secret choices to remember for this step (never credentials).",
    )


class SystemCheckResponse(BaseModel):
    name: str
    passed: bool
    warn_only: bool = Field(serialization_alias="warnOnly")
    message: str


class SystemReportResponse(BaseModel):
    host: dict[str, Any] | None
    checks: list[SystemCheckResponse]
    healthy: bool


def _state_response(service: SetupService, state: SetupState) -> SetupStateResponse:
    return SetupStateResponse(
        enabled=service.enabled,
        mode=service.mode,
        completed=state.completed,
        completed_at=state.completed_at,
        steps=list(KNOWN_SETUP_STEPS),
        completed_steps=[
            SetupStepResponse(step=r.step, completed_at=r.completed_at, data=r.data)
            for r in state.steps.values()
        ],
    )


def _system_response(report: SystemReport) -> SystemReportResponse:
    return SystemReportResponse(
        host=report.host,
        checks=[
            SystemCheckResponse(
                name=c.name, passed=c.passed, warn_only=c.warn_only, message=c.message
            )
            for c in report.checks
        ],
        healthy=report.healthy,
    )


def _require_admin(principal: Principal) -> None:
    if ADMIN_ROLE not in principal.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Setup changes require the volundr:admin role.",
        )


def create_setup_router(
    service: SetupService,
    prefix: str = "/api/v1/niuu/setup",
) -> APIRouter:
    """Routes under ``/api/v1/niuu/setup``."""
    router = APIRouter(prefix=prefix.rstrip("/"), tags=["Setup"])

    @router.get("", response_model=SetupStateResponse, response_model_by_alias=True)
    async def get_state(
        principal: Principal = Depends(extract_principal),
    ) -> SetupStateResponse:
        del principal
        return _state_response(service, await service.state())

    @router.get("/system", response_model=SystemReportResponse, response_model_by_alias=True)
    async def get_system(
        principal: Principal = Depends(extract_principal),
    ) -> SystemReportResponse:
        del principal
        return _system_response(await service.system())

    @router.put(
        "/steps/{step}",
        response_model=SetupStateResponse,
        response_model_by_alias=True,
    )
    async def complete_step(
        step: str,
        body: SetupStepRequest,
        principal: Principal = Depends(extract_principal),
    ) -> SetupStateResponse:
        _require_admin(principal)
        try:
            state = await service.complete_step(step, body.data)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return _state_response(service, state)

    @router.post("/complete", response_model=SetupStateResponse, response_model_by_alias=True)
    async def complete(
        principal: Principal = Depends(extract_principal),
    ) -> SetupStateResponse:
        _require_admin(principal)
        return _state_response(service, await service.complete())

    @router.post("/reset", response_model=SetupStateResponse, response_model_by_alias=True)
    async def reset(
        principal: Principal = Depends(extract_principal),
    ) -> SetupStateResponse:
        _require_admin(principal)
        return _state_response(service, await service.reset())

    return router

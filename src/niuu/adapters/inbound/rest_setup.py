"""REST API for the first-launch setup wizard (progress, host facts, checks)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from niuu.adapters.inbound.auth import extract_principal
from niuu.domain.models import Principal
from niuu.domain.services.setup import SetupService
from niuu.domain.setup import KNOWN_SETUP_STEPS, SetupState, SystemReport
from niuu.domain.stack import ApplyStatus, StackView
from niuu.ports.stack_control import StackControlPort
from niuu.settings_schema import (
    SettingsFieldSchema,
    SettingsProviderSchema,
    SettingsSectionSchema,
)

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


class VllmSettingsResponse(BaseModel):
    enabled: bool
    model: str
    image: str
    max_model_len: int = Field(serialization_alias="maxModelLen")
    gpu_memory_utilization: float = Field(serialization_alias="gpuMemoryUtilization")


class StackSettingsResponse(BaseModel):
    bind_host: str = Field(serialization_alias="bindHost")
    external_host: str = Field(serialization_alias="externalHost")
    port: int
    project_name: str = Field(serialization_alias="projectName")
    skuld_image: str = Field(serialization_alias="skuldImage")
    vllm: VllmSettingsResponse
    max_sessions: int = Field(serialization_alias="maxSessions")
    access_urls: list[str] = Field(serialization_alias="accessUrls")


class ModelOptionResponse(BaseModel):
    id: str
    model: str
    name: str
    description: str
    weight_gib: int = Field(serialization_alias="weightGib")
    recommended: bool
    fits: bool | None
    memory_needed_gib: int = Field(serialization_alias="memoryNeededGib")


class StackViewResponse(BaseModel):
    current: StackSettingsResponse
    staged: dict[str, Any]
    effective: StackSettingsResponse
    models: list[ModelOptionResponse]
    accelerator_memory_gib: int = Field(serialization_alias="acceleratorMemoryGib")
    has_staged_changes: bool = Field(serialization_alias="hasStagedChanges")


class StackChangesRequest(BaseModel):
    changes: dict[str, Any] = Field(
        description=(
            "Wizard-level keys: bind_host, max_sessions, vllm_enabled, vllm_model, "
            "vllm_max_model_len, vllm_gpu_memory_utilization."
        )
    )


class SessionsSettingsUpdate(BaseModel):
    """The Settings → Runtime → Sessions form: how many sessions may run at once."""

    model_config = ConfigDict(populate_by_name=True)

    max_sessions: int = Field(
        ge=1,
        validation_alias=AliasChoices("max_sessions", "maxSessions"),
        description="Sessions that may run at once on this host.",
    )


class SessionsSettingsResponse(BaseModel):
    max_sessions: int = Field(serialization_alias="maxSessions")
    apply_state: str = Field(
        serialization_alias="applyState",
        description="State of the apply the save started; the platform restarts during it.",
    )


class VllmStatusResponse(BaseModel):
    state: str
    detail: str


class ApplyStatusResponse(BaseModel):
    state: str
    started_at: str = Field(serialization_alias="startedAt")
    detail: str
    changes: dict[str, Any]
    vllm: VllmStatusResponse | None


def _stack_settings_response(settings: Any) -> StackSettingsResponse:
    return StackSettingsResponse(
        bind_host=settings.bind_host,
        external_host=settings.external_host,
        port=settings.port,
        project_name=settings.project_name,
        skuld_image=settings.skuld_image,
        vllm=VllmSettingsResponse(
            enabled=settings.vllm.enabled,
            model=settings.vllm.model,
            image=settings.vllm.image,
            max_model_len=settings.vllm.max_model_len,
            gpu_memory_utilization=settings.vllm.gpu_memory_utilization,
        ),
        max_sessions=settings.max_sessions,
        access_urls=settings.access_urls,
    )


def _stack_view_response(view: StackView) -> StackViewResponse:
    return StackViewResponse(
        current=_stack_settings_response(view.current),
        staged=view.staged,
        effective=_stack_settings_response(view.effective),
        models=[
            ModelOptionResponse(
                id=m.id,
                model=m.model,
                name=m.name,
                description=m.description,
                weight_gib=m.weight_gib,
                recommended=m.recommended,
                fits=m.fits,
                memory_needed_gib=m.memory_needed_gib,
            )
            for m in view.models
        ],
        accelerator_memory_gib=view.accelerator_memory_gib,
        has_staged_changes=view.has_staged_changes,
    )


def _apply_status_response(status: ApplyStatus) -> ApplyStatusResponse:
    return ApplyStatusResponse(
        state=status.state,
        started_at=status.started_at,
        detail=status.detail,
        changes=status.changes,
        vllm=(
            VllmStatusResponse(state=status.vllm.state, detail=status.vllm.detail)
            if status.vllm is not None
            else None
        ),
    )


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
    stack: StackControlPort | None = None,
) -> APIRouter:
    """Routes under ``/api/v1/niuu/setup``."""
    router = APIRouter(prefix=prefix.rstrip("/"), tags=["Setup"])

    def _require_stack() -> StackControlPort:
        if stack is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Stack changes are not available on this install; start it with "
                    "`niuu up` (docker mode) to enable them."
                ),
            )
        return stack

    def _stack_error(exc: Exception) -> HTTPException:
        if isinstance(exc, ValueError):
            return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
        if isinstance(exc, FileNotFoundError):
            return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
        raise exc

    @router.get("/stack", response_model=StackViewResponse, response_model_by_alias=True)
    async def get_stack(
        principal: Principal = Depends(extract_principal),
    ) -> StackViewResponse:
        del principal
        control = _require_stack()
        try:
            return _stack_view_response(await control.view())
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc

    def _runtime_settings_schema(view: StackView | None) -> SettingsProviderSchema:
        """The Settings → Runtime page: the host stack the wizard also edits."""
        if view is None:
            field = SettingsFieldSchema(
                key="maxSessions",
                label="Sessions at once",
                type="number",
                value=None,
                read_only=True,
                description=(
                    "Not changeable from here: this install was not started with `niuu up` "
                    "(docker mode). Set pod_manager.max_concurrent in its config.yaml and "
                    "restart the platform."
                ),
            )
        else:
            field = SettingsFieldSchema(
                key="maxSessions",
                label="Sessions at once",
                type="number",
                value=view.effective.max_sessions,
                description=(
                    "A launch is refused once this many sessions are running; each one is a "
                    "container with its own agent process. Raise it when the host has the "
                    "memory and your provider plans allow the parallel work. Saving applies "
                    "the change and restarts the platform, which takes about a minute."
                ),
            )
        return SettingsProviderSchema(
            title="Runtime",
            subtitle="this host: sessions at once, access, local model",
            scope="admin",
            sections=[
                SettingsSectionSchema(
                    id="sessions",
                    label="Sessions",
                    description="How many sessions may run at once on this host.",
                    path="/settings/sessions",
                    save_label="Save and restart the platform",
                    fields=[field],
                )
            ],
        )

    @router.get("/settings", response_model=SettingsProviderSchema, response_model_by_alias=True)
    async def get_runtime_settings(
        principal: Principal = Depends(extract_principal),
    ) -> SettingsProviderSchema:
        """Schema for the Settings → Runtime page."""
        del principal
        if stack is None:
            return _runtime_settings_schema(None)
        try:
            return _runtime_settings_schema(await stack.view())
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc

    @router.patch(
        "/settings/sessions",
        response_model=SessionsSettingsResponse,
        response_model_by_alias=True,
    )
    async def update_sessions_settings(
        body: SessionsSettingsUpdate,
        principal: Principal = Depends(extract_principal),
    ) -> SessionsSettingsResponse:
        """Stage the new session limit and apply it: the platform restarts with it."""
        _require_admin(principal)
        control = _require_stack()
        try:
            await control.stage({"max_sessions": body.max_sessions})
            applied = await control.apply()
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc
        return SessionsSettingsResponse(max_sessions=body.max_sessions, apply_state=applied.state)

    @router.put("/stack", response_model=StackViewResponse, response_model_by_alias=True)
    async def stage_stack(
        body: StackChangesRequest,
        principal: Principal = Depends(extract_principal),
    ) -> StackViewResponse:
        _require_admin(principal)
        control = _require_stack()
        try:
            return _stack_view_response(await control.stage(body.changes))
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc

    @router.delete("/stack", response_model=StackViewResponse, response_model_by_alias=True)
    async def discard_stack(
        principal: Principal = Depends(extract_principal),
    ) -> StackViewResponse:
        _require_admin(principal)
        control = _require_stack()
        try:
            return _stack_view_response(await control.discard())
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc

    @router.post("/stack/apply", response_model=ApplyStatusResponse, response_model_by_alias=True)
    async def apply_stack(
        principal: Principal = Depends(extract_principal),
    ) -> ApplyStatusResponse:
        _require_admin(principal)
        control = _require_stack()
        try:
            return _apply_status_response(await control.apply())
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc

    @router.get("/stack/status", response_model=ApplyStatusResponse, response_model_by_alias=True)
    async def stack_status(
        principal: Principal = Depends(extract_principal),
    ) -> ApplyStatusResponse:
        del principal
        control = _require_stack()
        try:
            return _apply_status_response(await control.status())
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc

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

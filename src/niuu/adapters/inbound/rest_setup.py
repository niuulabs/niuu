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
from niuu.domain.stack import ApplyStatus, ModelTestResult, Progress, StackView
from niuu.ports.stack_control import StackControlPort
from niuu.settings_schema import (
    SettingsExternalIntegrationsResourceSchema,
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


class ModelServerSettingsResponse(BaseModel):
    enabled: bool
    base_url: str = Field(serialization_alias="baseUrl")
    models: list[str]
    has_api_key: bool = Field(serialization_alias="hasApiKey")


class StackSettingsResponse(BaseModel):
    bind_host: str = Field(serialization_alias="bindHost")
    external_host: str = Field(serialization_alias="externalHost")
    port: int
    project_name: str = Field(serialization_alias="projectName")
    skuld_image: str = Field(serialization_alias="skuldImage")
    vllm: VllmSettingsResponse
    max_sessions: int = Field(serialization_alias="maxSessions")
    model_server: ModelServerSettingsResponse = Field(serialization_alias="modelServer")
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
            "vllm_max_model_len, vllm_gpu_memory_utilization, model_server_enabled, "
            "model_server_url, model_server_models, model_server_api_key."
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


class ModelServerSettingsUpdate(BaseModel):
    """The Settings → Runtime → Model server form."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(
        validation_alias=AliasChoices("enabled", "modelServerEnabled"),
        description="Route sessions at a model server you already run.",
    )
    url: str = Field(
        default="",
        validation_alias=AliasChoices("url", "modelServerUrl"),
        description="OpenAI-compatible base URL as reachable from the platform container.",
    )
    models: str | list[str] = Field(
        default="",
        validation_alias=AliasChoices("models", "modelServerModels"),
        description="Model ids the server serves: a list, or one string separated by commas.",
    )
    api_key: str = Field(
        default="",
        validation_alias=AliasChoices("api_key", "modelServerApiKey"),
        description="Bearer token for the server; blank keeps the stored one.",
    )


class ModelServerSettingsResponseWithApply(ModelServerSettingsResponse):
    apply_state: str = Field(serialization_alias="applyState")


class ProgressResponse(BaseModel):
    phase: str
    detail: str
    completed_bytes: int = Field(serialization_alias="completedBytes")
    total_bytes: int = Field(serialization_alias="totalBytes")


class VllmStatusResponse(BaseModel):
    state: str
    detail: str
    progress: ProgressResponse | None = None


class ApplyStatusResponse(BaseModel):
    state: str
    started_at: str = Field(serialization_alias="startedAt")
    detail: str
    changes: dict[str, Any]
    vllm: VllmStatusResponse | None
    progress: ProgressResponse | None = None


class ModelTestResponse(BaseModel):
    ok: bool
    model: str
    reply: str
    latency_ms: int = Field(serialization_alias="latencyMs")
    detail: str


class ExternalIntegrationPackageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_dir: str = Field(validation_alias=AliasChoices("source_dir", "sourceDir"))
    definition_files: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("definition_files", "definitionFiles"),
    )
    manifest_file: str = Field(
        default="",
        validation_alias=AliasChoices("manifest_file", "manifestFile"),
    )


class ExternalIntegrationDefinitionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    slug: str
    name: str
    integration_type: str = Field(serialization_alias="integrationType")
    adapter: str


class ExternalModuleComponentResponse(BaseModel):
    kind: str
    name: str
    adapter: str


class ExternalIntegrationValidationResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    source_dir: str = Field(serialization_alias="sourceDir")
    definition_files: list[str] = Field(serialization_alias="definitionFiles")
    manifest_file: str = Field(serialization_alias="manifestFile")
    module_id: str = Field(serialization_alias="moduleId")
    definitions: list[ExternalIntegrationDefinitionResponse]
    components: list[ExternalModuleComponentResponse]
    errors: list[str]


class ExternalIntegrationPackageResponse(ExternalIntegrationValidationResponse):
    id: str


class ExternalIntegrationPackagesResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    managed_root: str = Field(serialization_alias="managedRoot")
    items: list[ExternalIntegrationPackageResponse]


class ExternalIntegrationMutationResponse(ExternalIntegrationPackageResponse):
    apply_state: str = Field(serialization_alias="applyState")


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
        model_server=ModelServerSettingsResponse(
            enabled=settings.model_server.enabled,
            base_url=settings.model_server.base_url,
            models=list(settings.model_server.models),
            has_api_key=settings.model_server.has_api_key,
        ),
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


def _progress_response(progress: Progress | None) -> ProgressResponse | None:
    if progress is None:
        return None
    return ProgressResponse(
        phase=progress.phase,
        detail=progress.detail,
        completed_bytes=progress.completed_bytes,
        total_bytes=progress.total_bytes,
    )


def _apply_status_response(status: ApplyStatus) -> ApplyStatusResponse:
    return ApplyStatusResponse(
        state=status.state,
        started_at=status.started_at,
        detail=status.detail,
        changes=status.changes,
        vllm=(
            VllmStatusResponse(
                state=status.vllm.state,
                detail=status.vllm.detail,
                progress=_progress_response(status.vllm.progress),
            )
            if status.vllm is not None
            else None
        ),
        progress=_progress_response(status.progress),
    )


def _model_test_response(result: ModelTestResult) -> ModelTestResponse:
    return ModelTestResponse(
        ok=result.ok,
        model=result.model,
        reply=result.reply,
        latency_ms=result.latency_ms,
        detail=result.detail,
    )


def _external_validation_response(
    result: Any,
    *,
    package_id: str | None = None,
) -> ExternalIntegrationValidationResponse | ExternalIntegrationPackageResponse:
    values = {
        "ok": result.ok,
        "source_dir": result.source_dir,
        "definition_files": list(result.definition_files),
        "manifest_file": result.manifest_file,
        "module_id": result.module_id,
        "definitions": [
            ExternalIntegrationDefinitionResponse(
                slug=definition.slug,
                name=definition.name,
                integration_type=definition.integration_type,
                adapter=definition.adapter,
            )
            for definition in result.definitions
        ],
        "components": [
            ExternalModuleComponentResponse(
                kind=component.kind,
                name=component.name,
                adapter=component.adapter,
            )
            for component in result.components
        ],
        "errors": list(result.errors),
    }
    if package_id is None:
        return ExternalIntegrationValidationResponse(**values)
    return ExternalIntegrationPackageResponse(id=package_id, **values)


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

    def _model_server_section(view: StackView | None) -> SettingsSectionSchema:
        """Settings → Runtime → Model server: a server you already run, via the gateway."""
        server = view.effective.model_server if view is not None else None
        read_only = view is None
        fields = [
            SettingsFieldSchema(
                key="modelServerEnabled",
                label="Use my model server",
                type="boolean",
                value=server.enabled if server else False,
                read_only=read_only,
                description=(
                    "Register a model server you run yourself (vLLM, sparkrun, Ollama, "
                    "anything OpenAI-compatible) with the platform's model gateway. Its "
                    "models then appear under the Model server provider in the launch "
                    "dialog, for Claude Code, Codex and Ravn alike."
                ),
            ),
            SettingsFieldSchema(
                key="modelServerUrl",
                label="Server URL",
                type="text",
                value=server.base_url if server else "",
                read_only=read_only,
                placeholder="http://host.docker.internal:8000",
                description=(
                    "Base URL without /v1, as reachable from the platform container. A "
                    "server on this host is http://host.docker.internal:<port>."
                ),
            ),
            SettingsFieldSchema(
                key="modelServerModels",
                label="Models",
                type="text",
                value=", ".join(server.models) if server else "",
                read_only=read_only,
                placeholder="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16",
                description=(
                    "Model ids the server serves, comma-separated; the first is the default."
                ),
            ),
            SettingsFieldSchema(
                key="modelServerApiKey",
                label="API key",
                type="text",
                value=None,
                read_only=read_only,
                secret=True,
                description=(
                    "Only if the server checks a bearer token. Leave blank to keep the stored "
                    f"key{' (one is stored)' if server and server.has_api_key else ''}."
                ),
            ),
        ]
        return SettingsSectionSchema(
            id="model-server",
            label="Model server",
            description=(
                "A model you serve yourself, made available to every engine through the "
                "model gateway."
                if view is not None
                else "Not changeable from here: this install was not started with `niuu up`."
            ),
            path="/settings/model-server",
            save_label="Save and restart the platform",
            fields=fields,
        )

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
                ),
                _model_server_section(view),
                SettingsSectionSchema(
                    id="external-integrations",
                    label="External integrations",
                    description=(
                        "Load deployment-owned integration definitions and adapter code without "
                        "putting private packages in the Niuu image or repository."
                        if view is not None
                        else "External packages are deployment-managed on this install."
                    ),
                    fields=[],
                    resources=[
                        SettingsExternalIntegrationsResourceSchema(
                            id="external-integration-packages",
                            label="Integration packages",
                            description=(
                                "Package files stay on this machine. Adding or removing a "
                                "package updates the persisted stack configuration and restarts "
                                "the platform."
                            ),
                            writable=view is not None,
                            list_path=f"{prefix.rstrip('/')}/settings/external-integrations",
                            create_path=f"{prefix.rstrip('/')}/settings/external-integrations",
                            delete_path=(
                                f"{prefix.rstrip('/')}/settings/external-integrations/{{id}}"
                            ),
                            validate_path=(
                                f"{prefix.rstrip('/')}/settings/external-integrations/validate"
                            ),
                        )
                    ],
                ),
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

    @router.patch(
        "/settings/model-server",
        response_model=ModelServerSettingsResponseWithApply,
        response_model_by_alias=True,
    )
    async def update_model_server_settings(
        body: ModelServerSettingsUpdate,
        principal: Principal = Depends(extract_principal),
    ) -> ModelServerSettingsResponseWithApply:
        """Stage the model server and apply it: the platform restarts with the gateway
        provider and the Model server AI provider pointing at it."""
        _require_admin(principal)
        control = _require_stack()
        changes: dict[str, Any] = {"model_server_enabled": body.enabled}
        if body.enabled:
            changes["model_server_url"] = body.url
            changes["model_server_models"] = body.models
        if body.api_key.strip():
            changes["model_server_api_key"] = body.api_key.strip()
        try:
            staged = await control.stage(changes)
            applied = await control.apply()
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc
        server = staged.effective.model_server
        return ModelServerSettingsResponseWithApply(
            enabled=server.enabled,
            base_url=server.base_url,
            models=list(server.models),
            has_api_key=server.has_api_key,
            apply_state=applied.state,
        )

    @router.get(
        "/settings/external-integrations",
        response_model=ExternalIntegrationPackagesResponse,
        response_model_by_alias=True,
    )
    async def list_external_integrations(
        principal: Principal = Depends(extract_principal),
    ) -> ExternalIntegrationPackagesResponse:
        _require_admin(principal)
        control = _require_stack()
        try:
            view = await control.view()
            root = await control.external_integrations_root()
            items = []
            for index, package in enumerate(view.effective.external_integrations):
                validation = await control.describe_external_integration(
                    package.source_dir,
                    list(package.definition_files),
                    package.manifest_file,
                )
                items.append(_external_validation_response(validation, package_id=str(index)))
            return ExternalIntegrationPackagesResponse(managed_root=root, items=items)
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc

    @router.post(
        "/settings/external-integrations/validate",
        response_model=ExternalIntegrationValidationResponse,
        response_model_by_alias=True,
    )
    async def validate_external_integration(
        body: ExternalIntegrationPackageRequest,
        principal: Principal = Depends(extract_principal),
    ) -> ExternalIntegrationValidationResponse:
        _require_admin(principal)
        control = _require_stack()
        try:
            result = await control.validate_external_integration(
                body.source_dir,
                body.definition_files,
                body.manifest_file,
            )
            return _external_validation_response(result)
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc

    @router.post(
        "/settings/external-integrations",
        response_model=ExternalIntegrationMutationResponse,
        response_model_by_alias=True,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_external_integration(
        body: ExternalIntegrationPackageRequest,
        principal: Principal = Depends(extract_principal),
    ) -> ExternalIntegrationMutationResponse:
        _require_admin(principal)
        control = _require_stack()
        try:
            validation = await control.validate_external_integration(
                body.source_dir,
                body.definition_files,
                body.manifest_file,
            )
            if not validation.ok:
                raise ValueError("; ".join(validation.errors))
            view = await control.view()
            packages = [
                {
                    "source_dir": package.source_dir,
                    "definition_files": list(package.definition_files),
                    "manifest_file": package.manifest_file,
                }
                for package in view.effective.external_integrations
            ]
            if any(package["source_dir"] == validation.source_dir for package in packages):
                raise ValueError(
                    f"External integration package already exists: {validation.source_dir}"
                )

            existing_slugs: set[str] = set()
            existing_module_ids: set[str] = set()
            existing_components: set[str] = set()
            for package in view.effective.external_integrations:
                current = await control.describe_external_integration(
                    package.source_dir,
                    list(package.definition_files),
                    package.manifest_file,
                )
                existing_slugs.update(definition.slug for definition in current.definitions)
                if current.module_id:
                    existing_module_ids.add(current.module_id)
                existing_components.update(component.name for component in current.components)
            duplicate_slugs = sorted(
                definition.slug
                for definition in validation.definitions
                if definition.slug in existing_slugs
            )
            if duplicate_slugs:
                raise ValueError(
                    "Integration slugs already supplied by another external package: "
                    + ", ".join(duplicate_slugs)
                )
            if validation.module_id and validation.module_id in existing_module_ids:
                raise ValueError(
                    f"External module id already supplied by another package: "
                    f"{validation.module_id}"
                )
            duplicate_components = sorted(
                f"{component.kind}:{component.name}"
                for component in validation.components
                if component.name in existing_components
            )
            if duplicate_components:
                raise ValueError(
                    "External component names already supplied by another package: "
                    + ", ".join(duplicate_components)
                )

            packages.append(
                {
                    "source_dir": validation.source_dir,
                    "definition_files": list(validation.definition_files),
                    "manifest_file": validation.manifest_file,
                }
            )
            await control.stage({"external_integrations": packages})
            applied = await control.apply()
            package = _external_validation_response(validation, package_id=str(len(packages) - 1))
            return ExternalIntegrationMutationResponse(
                **package.model_dump(),
                apply_state=applied.state,
            )
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc

    @router.delete(
        "/settings/external-integrations/{package_id}",
        response_model=ApplyStatusResponse,
        response_model_by_alias=True,
    )
    async def remove_external_integration(
        package_id: int,
        principal: Principal = Depends(extract_principal),
    ) -> ApplyStatusResponse:
        """Unregister a package without deleting its machine-local files."""
        _require_admin(principal)
        control = _require_stack()
        try:
            view = await control.view()
            packages = [
                {
                    "source_dir": package.source_dir,
                    "definition_files": list(package.definition_files),
                    "manifest_file": package.manifest_file,
                }
                for package in view.effective.external_integrations
            ]
            if package_id < 0 or package_id >= len(packages):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="External integration package was not found.",
                )
            packages.pop(package_id)
            await control.stage({"external_integrations": packages})
            return _apply_status_response(await control.apply())
        except HTTPException:
            raise
        except (ValueError, FileNotFoundError) as exc:
            raise _stack_error(exc) from exc

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

    @router.post(
        "/stack/test-model", response_model=ModelTestResponse, response_model_by_alias=True
    )
    async def test_model(
        principal: Principal = Depends(extract_principal),
    ) -> ModelTestResponse:
        """Send one short completion to the local model and report what came back."""
        _require_admin(principal)
        control = _require_stack()
        try:
            return _model_test_response(await control.test_model())
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

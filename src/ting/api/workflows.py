"""REST API for persisted Ting workflow catalogs and direct workflow launches."""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import require_scope
from niuu.domain.session_endpoint import public_session_endpoint
from ting.adapters.inbound.auth import (
    extract_bearer_token,
    extract_principal,
    is_platform_admin,
    platform_tenant_id,
)
from ting.api.dispatch import resolve_volundr_factory
from ting.api.workflow_bindings import binding_errors
from ting.api.workflow_personas import authoring_persona_source
from ting.domain.exceptions import (
    WorkflowConflictError,
    WorkflowReadOnlyError,
    WorkflowStepsHaveNoRunnerError,
)
from ting.domain.models import (
    PersonaDependency,
    WorkflowCampaign,
    WorkflowCampaignStatus,
    WorkflowDefinition,
    WorkflowDependency,
    WorkflowScope,
    WorkflowVersionSummary,
)
from ting.domain.services.dispatch_service import (
    HostTagsUnreadableError,
    TargetSelectionError,
    select_adapter_by_tags,
)
from ting.domain.workflow_document import (
    WorkflowPlacement,
    document_from_workflow,
    dump_workflow_document,
    load_workflow_document,
    validate_resolved_workflow_graph,
    validate_workflow_graph,
    workflow_document_payload,
    workflow_document_revision,
)
from ting.domain.workflow_execution import validate_json_schema
from ting.domain.workflow_includes import (
    graph_has_include_nodes,
    include_resolver_from_workflow_definitions,
    resolve_workflow_includes,
)
from ting.domain.workflow_snapshot import (
    build_workflow_snapshot,
    pin_workflow_personas,
    workflow_personas_from_snapshot,
)
from ting.domain.workflow_versioning import WorkflowVersionBump
from ting.ports.volundr import (
    SessionMembership,
    VolundrFactory,
    VolundrPort,
    VolundrSession,
)
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_repository import WorkflowRepository

_DIRECT_LAUNCH_SURFACE = "ting.workflow-launch"
logger = logging.getLogger(__name__)


class WorkflowBody(BaseModel):
    schema_version: Literal[1, 2] = 1
    workflow_dependencies: dict[str, WorkflowDependency] = Field(default_factory=dict)
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    version: str = Field(default="draft", min_length=1, max_length=64)
    scope: Literal["system", "user"] = "user"
    graph: dict[str, Any] | None = None
    persona_dependencies: dict[str, PersonaDependency] = Field(default_factory=dict)
    expected_revision: str | None = None
    base_revision: str | None = None
    bump: Literal["patch", "minor", "major"] = "patch"
    copy_from: UUID | None = None
    refresh_personas: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    resource_bindings: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="resourceBindings",
        serialization_alias="resourceBindings",
    )
    artifact_paths: list[str] = Field(
        default_factory=list,
        alias="artifactPaths",
        serialization_alias="artifactPaths",
        description=(
            "Durable Mimir paths returned as workflow artifacts. "
            "The {slug} placeholder resolves to the launch slug."
        ),
    )
    model_config = {"populate_by_name": True}


class WorkflowResponse(BaseModel):
    schema_version: int = 1
    workflow_dependencies: dict[str, WorkflowDependency] = Field(default_factory=dict)
    id: str
    name: str
    description: str
    version: str
    scope: Literal["system", "user"]
    owner_id: str | None
    graph: dict[str, Any] = Field(default_factory=dict)
    persona_dependencies: dict[str, PersonaDependency] = Field(default_factory=dict)
    revision: str | None = None
    document_revision: str
    is_head: bool = True
    origin: Literal["authored", "bundled"] = "authored"
    based_on_revision: str | None = None
    read_only: bool = False
    can_edit: bool = False
    canonical_yaml: str = ""
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    resource_bindings: list[dict[str, Any]] = Field(
        default_factory=list,
        serialization_alias="resourceBindings",
    )
    artifact_paths: list[str] = Field(
        default_factory=list,
        serialization_alias="artifactPaths",
    )
    created_at: datetime
    updated_at: datetime


class WorkflowLaunchBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    session_name: str | None = Field(default=None, max_length=63, alias="sessionName")
    repo: str = Field(default="", max_length=500)
    branch: str = Field(default="", max_length=255)
    base_branch: str = Field(default="", max_length=255, alias="baseBranch")
    connection_id: str | None = Field(default=None, max_length=255, alias="connectionId")
    model: str = Field(default="", max_length=255)
    workflow_version: str | None = Field(default=None, alias="workflowVersion", max_length=64)
    definition: str | None = Field(default=None, max_length=255)
    context: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    inherited_result_schema: dict[str, Any] = Field(
        default_factory=dict,
        alias="inheritedResultSchema",
        description=("Pinned result contract inherited from the parent subworkflow allocation."),
    )
    gate_auto_forward_after: str | None = Field(
        default=None,
        max_length=32,
        alias="gateAutoForwardAfter",
        description=(
            "Override every gate node's autoForwardAfter for this launch "
            "(e.g. '24h'). An empty string disables auto-forward entirely — "
            "chat-driven approvals must wait for the human instead of "
            "sailing past them. Omit to keep the definition's values."
        ),
    )

    model_config = {"populate_by_name": True}

    @field_validator("inherited_result_schema")
    @classmethod
    def _validate_inherited_result_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value:
            validate_json_schema(value, field="inheritedResultSchema")
        return value


class WorkflowLaunchResponse(BaseModel):
    workflow_id: str = Field(serialization_alias="workflowId")
    workflow_name: str = Field(serialization_alias="workflowName")
    slug: str
    session_id: str = Field(serialization_alias="sessionId")
    session_name: str = Field(serialization_alias="sessionName")
    status: str
    cluster_name: str = Field(default="", serialization_alias="clusterName")
    chat_endpoint: str | None = Field(default=None, serialization_alias="chatEndpoint")
    workflow_version: str = Field(serialization_alias="workflowVersion")
    document_revision: str = Field(serialization_alias="documentRevision")

    model_config = {"populate_by_name": True}


class WorkflowVersionSummaryResponse(BaseModel):
    version: str
    document_revision: str
    created_at: datetime
    is_head: bool
    based_on_revision: str | None = None
    origin: Literal["authored", "bundled"] = "authored"


@dataclass(frozen=True)
class WorkflowLaunchExecution:
    workflow: WorkflowDefinition
    workflow_snapshot: dict[str, Any]
    slug: str
    session: VolundrSession
    adapter: VolundrPort
    # Canonical id of the Volundr connection the session was created on;
    # campaigns persist this so later reads target the same instance.
    connection_id: str | None = None


async def resolve_workflow_repo() -> WorkflowRepository:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Workflow repository not configured",
    )


async def resolve_workflow_launch_campaign_repo() -> WorkflowCampaignRepository:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Workflow launch history is not configured",
    )


def create_workflows_router() -> APIRouter:
    from ting.api.workflow_sharing import create_workflow_sharing_router

    router = APIRouter(prefix="/api/v1/ting/workflows", tags=["Workflows"])
    router.include_router(create_workflow_sharing_router())

    @router.get("", response_model=list[WorkflowResponse])
    async def list_workflows(
        request: Request,
        scope: Literal["all", "system", "user"] = Query(default="all"),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> list[WorkflowResponse]:
        scope_filter = _coerce_scope_filter(scope)
        workflows = await repo.list_workflows(
            owner_id=principal.user_id,
            scope=scope_filter,
        )
        return [
            _to_response(workflow, request=request, principal=principal) for workflow in workflows
        ]

    @router.get(
        "/{workflow_id}/versions",
        response_model=list[WorkflowVersionSummaryResponse],
    )
    async def list_workflow_versions(
        workflow_id: UUID = Path(description="Workflow UUID"),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> list[WorkflowVersionSummaryResponse]:
        workflow = await repo.get_workflow(workflow_id)
        if workflow is None or not _can_view_workflow(workflow, principal):
            raise HTTPException(status_code=404, detail="Workflow not found")
        versions = await repo.list_workflow_versions(workflow_id)
        return [_to_version_summary(item) for item in versions]

    @router.get(
        "/{workflow_id}/versions/{version}",
        response_model=WorkflowResponse,
    )
    async def get_workflow_version(
        version: str,
        request: Request,
        workflow_id: UUID = Path(description="Workflow UUID"),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> WorkflowResponse:
        head = await repo.get_workflow(workflow_id)
        if head is None or not _can_view_workflow(head, principal):
            raise HTTPException(status_code=404, detail="Workflow not found")
        workflow = await repo.get_workflow_version(workflow_id, version=version)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow version not found")
        return _to_response(workflow, request=request, principal=principal)

    @router.get("/{workflow_id}", response_model=WorkflowResponse)
    async def get_workflow(
        request: Request,
        workflow_id: UUID = Path(description="Workflow UUID"),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> WorkflowResponse:
        workflow = await repo.get_workflow(workflow_id)
        if workflow is None or not _can_view_workflow(workflow, principal):
            raise HTTPException(status_code=404, detail="Workflow not found")
        return _to_response(workflow, request=request, principal=principal)

    @router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
    async def create_workflow(
        body: WorkflowBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> WorkflowResponse:
        scope = WorkflowScope(body.scope)
        _assert_can_manage_scope(scope, principal, request)
        graph = _body_to_graph(body)

        original = None
        if body.copy_from is not None:
            original = await repo.get_workflow(body.copy_from)
            if original is None or not _can_view_workflow(original, principal):
                raise HTTPException(status_code=404, detail="Source workflow not found")

        now = datetime.now(UTC)
        workflow = WorkflowDefinition(
            tenant_id=principal.tenant_id,
            id=uuid4(),
            name=body.name,
            description=body.description,
            version=body.version,
            scope=scope,
            owner_id=_owner_id_for_scope(scope, principal),
            graph=graph,
            created_at=now,
            updated_at=now,
            persona_dependencies=(
                body.persona_dependencies
                if "persona_dependencies" in body.model_fields_set or original is None
                else original.persona_dependencies
            ),
            persona_definitions=original.persona_definitions if original else {},
            schema_version=(
                body.schema_version
                if "schema_version" in body.model_fields_set or original is None
                else original.schema_version
            ),
            workflow_dependencies=(
                body.workflow_dependencies
                if "workflow_dependencies" in body.model_fields_set or original is None
                else original.workflow_dependencies
            ),
            workflow_definitions=original.workflow_definitions if original else {},
            requirements=original.requirements if original else [],
        )
        saved = await _save_workflow(
            repo,
            workflow,
            request=request,
            principal=principal,
            refresh_personas=body.refresh_personas,
        )
        return _to_response(saved, request=request, principal=principal)

    @router.put("/{workflow_id}", response_model=WorkflowResponse)
    async def update_workflow(
        body: WorkflowBody,
        request: Request,
        workflow_id: UUID = Path(description="Workflow UUID"),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> WorkflowResponse:
        existing = await repo.get_workflow(workflow_id)
        if existing is None or not _can_view_workflow(existing, principal):
            raise HTTPException(status_code=404, detail="Workflow not found")

        scope = WorkflowScope(body.scope)
        _assert_can_manage_existing(existing, principal, request)
        _assert_can_manage_scope(scope, principal, request)
        if scope != existing.scope:
            raise HTTPException(
                status_code=422,
                detail="Workflow scope cannot change across versions",
            )
        _assert_revision(existing, body.expected_revision)
        graph = _body_to_graph(body)

        base_revision = (
            body.base_revision or existing.document_revision or workflow_document_revision(existing)
        )
        base = existing
        if base_revision != (existing.document_revision or workflow_document_revision(existing)):
            base = await repo.get_workflow_version(
                workflow_id,
                document_revision=base_revision,
            )
            if base is None:
                raise HTTPException(status_code=404, detail="Workflow base revision not found")

        saved = await _save_workflow(
            repo,
            WorkflowDefinition(
                tenant_id=existing.tenant_id,
                id=existing.id,
                name=body.name,
                description=body.description,
                version=existing.version,
                scope=existing.scope,
                owner_id=existing.owner_id,
                graph=graph,
                created_at=existing.created_at,
                updated_at=datetime.now(UTC),
                persona_dependencies=(
                    body.persona_dependencies
                    if "persona_dependencies" in body.model_fields_set
                    else base.persona_dependencies
                ),
                persona_definitions=base.persona_definitions,
                schema_version=(
                    body.schema_version
                    if "schema_version" in body.model_fields_set
                    else base.schema_version
                ),
                workflow_dependencies=(
                    body.workflow_dependencies
                    if "workflow_dependencies" in body.model_fields_set
                    else base.workflow_dependencies
                ),
                workflow_definitions=base.workflow_definitions,
                requirements=base.requirements,
                revision=existing.revision,
                origin="authored",
                based_on_revision=base_revision,
            ),
            request=request,
            principal=principal,
            refresh_personas=body.refresh_personas,
            versioned=True,
            expected_revision=body.expected_revision,
            base_revision=base_revision,
            bump=body.bump,
        )
        return _to_response(saved, request=request, principal=principal)

    @router.post(
        "/{workflow_id}/versions",
        response_model=WorkflowResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_workflow_version(
        body: WorkflowBody,
        request: Request,
        workflow_id: UUID = Path(description="Workflow UUID"),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> WorkflowResponse:
        if body.base_revision is None:
            raise HTTPException(status_code=422, detail="base_revision is required")
        return await update_workflow(
            body=body,
            request=request,
            workflow_id=workflow_id,
            principal=principal,
            repo=repo,
        )

    @router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_workflow(
        request: Request,
        workflow_id: UUID = Path(description="Workflow UUID"),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> None:
        existing = await repo.get_workflow(workflow_id)
        if existing is None or not _can_view_workflow(existing, principal):
            raise HTTPException(status_code=404, detail="Workflow not found")

        if existing.read_only:
            raise HTTPException(status_code=409, detail="Bundled workflows cannot be deleted")
        _assert_can_manage_existing(existing, principal, request)
        try:
            deleted = await repo.delete_workflow(workflow_id)
        except (WorkflowConflictError, WorkflowReadOnlyError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Workflow not found")

    @router.post(
        "/{workflow_id}/launch",
        response_model=WorkflowLaunchResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def launch_workflow(
        body: WorkflowLaunchBody,
        request: Request,
        workflow_id: UUID = Path(description="Workflow UUID"),
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
        campaign_repo: WorkflowCampaignRepository = Depends(resolve_workflow_launch_campaign_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
        _build_scope: None = Depends(require_scope("ting:workflow:launch")),
    ) -> WorkflowLaunchResponse:
        workflow = (
            await repo.get_workflow_version(workflow_id, version=body.workflow_version)
            if body.workflow_version
            else await repo.get_workflow(workflow_id)
        )
        if workflow is None or not _can_view_workflow(workflow, principal):
            raise HTTPException(status_code=404, detail="Workflow not found")

        execution = await launch_workflow_execution(
            request=request,
            workflow=workflow,
            launch=body,
            volundr_factory=volundr_factory,
            principal=principal,
            bearer_token=bearer_token,
        )
        now = datetime.now(UTC)
        campaign_id = uuid4()
        campaign = WorkflowCampaign(
            id=campaign_id,
            tenant_id=principal.tenant_id,
            slug=f"{execution.slug}-{campaign_id.hex[:12]}",
            name=execution.session.name or execution.workflow.name,
            owner_id=principal.user_id,
            workflow_id=execution.workflow.id,
            workflow_version=execution.workflow.version,
            workflow_name=execution.workflow.name,
            workflow_snapshot=execution.workflow_snapshot,
            session_id=execution.session.id,
            session_name=execution.session.name,
            status=_campaign_status_from_launch(execution.session.status),
            active_stage_id=None,
            stage_state=[],
            metadata={
                "surface": _DIRECT_LAUNCH_SURFACE,
                "launch_slug": execution.slug,
                # What the person asked for, so the run's review page can say what it is about.
                "prompt": body.prompt,
                **({"repo": body.repo} if body.repo else {}),
                **({"branch": body.branch} if body.branch else {}),
                "document_revision": (
                    execution.workflow.document_revision
                    or workflow_document_revision(execution.workflow)
                ),
                # The workload that launched this for its owner (attribution only).
                **({"actor_id": principal.actor} if principal.actor else {}),
            },
            created_at=now,
            updated_at=now,
            last_activity_at=now,
            completed_at=(
                now
                if _campaign_status_from_launch(execution.session.status)
                in {WorkflowCampaignStatus.COMPLETED, WorkflowCampaignStatus.FAILED}
                else None
            ),
            connection_id=execution.connection_id,
        )
        try:
            await campaign_repo.save_campaign(campaign)
        except Exception as exc:
            stopped = False
            try:
                await execution.adapter.stop_session(
                    execution.session.id,
                    auth_token=bearer_token,
                    principal=principal,
                )
                stopped = True
            except Exception:
                logger.exception(
                    "Workflow launch history failed and session compensation failed for %s",
                    execution.session.id,
                )
            logger.exception(
                "Workflow session %s launched but its durable Work record failed",
                execution.session.id,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "launchRecordFailed",
                    "message": (
                        "The workflow session launched but its Work record could not be saved."
                    ),
                    "sessionId": execution.session.id,
                    "sessionName": execution.session.name,
                    "clusterName": execution.session.cluster_name,
                    "sessionStopped": stopped,
                    "reconciliationRequired": not stopped,
                },
            ) from exc
        return _launch_response(
            execution.workflow,
            execution.slug,
            execution.session,
            public_host=request.url.hostname,
        )

    return router


def _campaign_status_from_launch(status_value: str) -> WorkflowCampaignStatus:
    normalized = status_value.strip().lower()
    if normalized in {"completed", "complete", "succeeded"}:
        return WorkflowCampaignStatus.COMPLETED
    if normalized in {"failed", "cancelled", "canceled"}:
        return WorkflowCampaignStatus.FAILED
    if normalized in {"pending", "queued", "created"}:
        return WorkflowCampaignStatus.PENDING
    if normalized == "stopped":
        return WorkflowCampaignStatus.BLOCKED
    return WorkflowCampaignStatus.RUNNING


def _coerce_scope_filter(scope: Literal["all", "system", "user"]) -> WorkflowScope | None:
    if scope == "all":
        return None
    return WorkflowScope(scope)


def _body_to_graph(body: WorkflowBody) -> dict[str, Any]:
    graph = copy.deepcopy(body.graph or {})
    fields = {
        "tags": body.tags,
        "nodes": body.nodes,
        "edges": body.edges,
        "resourceBindings": body.resource_bindings,
        "artifactPaths": body.artifact_paths,
    }
    field_names = {"resourceBindings": "resource_bindings", "artifactPaths": "artifact_paths"}
    for key, value in fields.items():
        if body.graph is None or field_names.get(key, key) in body.model_fields_set:
            graph[key] = value
    return graph


def _to_response(
    workflow: WorkflowDefinition,
    *,
    request: Request,
    principal: Principal | None = None,
) -> WorkflowResponse:
    graph = workflow.graph or {}
    document_revision = workflow.document_revision or workflow_document_revision(workflow)
    return WorkflowResponse(
        id=str(workflow.id),
        name=workflow.name,
        description=workflow.description,
        version=workflow.version,
        scope=workflow.scope.value,
        owner_id=workflow.owner_id,
        graph=graph,
        persona_dependencies=workflow.persona_dependencies,
        schema_version=workflow.schema_version,
        workflow_dependencies=workflow.workflow_dependencies,
        revision=workflow.revision,
        document_revision=document_revision,
        is_head=workflow.is_head,
        origin="bundled" if workflow.origin == "bundled" else "authored",
        based_on_revision=workflow.based_on_revision,
        read_only=workflow.read_only,
        can_edit=principal is not None
        and _can_edit_workflow(workflow, principal, platform_tenant=platform_tenant_id(request)),
        canonical_yaml=dump_workflow_document(workflow),
        requirements=workflow.requirements,
        tags=[str(tag).strip() for tag in list(graph.get("tags") or []) if str(tag).strip()],
        nodes=list(graph.get("nodes") or []),
        edges=list(graph.get("edges") or []),
        resource_bindings=list(
            graph.get("resourceBindings") or graph.get("resource_bindings") or []
        ),
        artifact_paths=[
            str(path)
            for path in list(graph.get("artifactPaths") or graph.get("artifact_paths") or [])
            if str(path).strip()
        ],
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _to_version_summary(item: WorkflowVersionSummary) -> WorkflowVersionSummaryResponse:
    return WorkflowVersionSummaryResponse(
        version=item.version,
        document_revision=item.document_revision,
        created_at=item.created_at,
        is_head=item.is_head,
        based_on_revision=item.based_on_revision,
        origin="bundled" if item.origin == "bundled" else "authored",
    )


def _launch_response(
    workflow: WorkflowDefinition,
    slug: str,
    session: VolundrSession,
    *,
    public_host: str | None = None,
) -> WorkflowLaunchResponse:
    return WorkflowLaunchResponse(
        workflow_id=str(workflow.id),
        workflow_name=workflow.name,
        slug=slug,
        session_id=session.id,
        session_name=session.name,
        status=session.status,
        cluster_name=session.cluster_name,
        chat_endpoint=public_session_endpoint(session.chat_endpoint, public_host=public_host),
        workflow_version=workflow.version,
        document_revision=workflow.document_revision or workflow_document_revision(workflow),
    )


async def launch_workflow_execution(
    *,
    request: Request,
    workflow: WorkflowDefinition,
    launch: WorkflowLaunchBody,
    volundr_factory: VolundrFactory,
    principal: Principal,
    bearer_token: str | None = None,
    pinned_workflow_snapshot: dict[str, Any] | None = None,
    trusted_workflow_execution: bool = False,
    launch_slug: str | None = None,
    session_membership: Callable[[VolundrPort], Awaitable[SessionMembership | None]] | None = None,
) -> WorkflowLaunchExecution:
    """Check a workflow launch and start it.

    Authorization and the workflow's own validity are checked first, so a caller
    who may not launch, or whose workflow is malformed, still gets that answer.

    :raises WorkflowStepsHaveNoRunnerError: For every launch that passes those
        checks. Workflow steps have no runner until they run on harness sessions
        (NIU-1198); the HTTP layer maps this to 501.
    """
    raw_execution_context = launch.provenance.get("workflow_execution")
    if raw_execution_context and not trusted_workflow_execution:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workflow_execution provenance is reserved for verified platform launches",
        )
    if isinstance(raw_execution_context, dict) and any(
        str(raw_execution_context.get(key) or "").strip()
        for key in ("auth_token", "auth_token_file")
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="workflow_execution provenance must not contain credentials",
        )
    authorization = getattr(request.app.state, "authorization", None)
    if authorization is None:
        raise HTTPException(status_code=503, detail="Authorization is not configured")
    from identity.adapters.http_auth import authorization_http_errors
    from identity.models import Resource

    with authorization_http_errors():
        allowed = await authorization.is_allowed(
            principal,
            "launch",
            Resource(
                "workflow",
                str(workflow.id),
                {
                    "owner_id": workflow.owner_id,
                    "tenant_id": workflow.tenant_id,
                    "scope": workflow.scope.value,
                },
            ),
        )
    if not allowed:
        raise HTTPException(status_code=403, detail="Workflow launch denied")
    if pinned_workflow_snapshot is None:
        try:
            build_workflow_snapshot(
                workflow, persona_source=getattr(request.app.state, "persona_source", None)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise WorkflowStepsHaveNoRunnerError()


async def _resolve_target_adapter(
    *,
    volundr_factory: VolundrFactory,
    principal: Principal,
    connection_id: str | None = None,
    placement: WorkflowPlacement | None = None,
    auth_token: str | None = None,
):
    adapters = await volundr_factory.for_principal(principal)
    if not adapters:
        return None
    normalized_connection_id = str(connection_id or "").strip()
    if normalized_connection_id and placement is not None:
        return await _resolve_pinned_connection_against_placement(
            adapters,
            normalized_connection_id,
            placement,
            principal=principal,
            auth_token=auth_token,
        )
    if normalized_connection_id:
        for adapter in adapters:
            if normalized_connection_id in {adapter.target_id, adapter.name}:
                return adapter
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Volundr target not found: {normalized_connection_id}",
        )
    if placement is not None:
        return await _resolve_placement_target(
            adapters, placement, principal=principal, auth_token=auth_token
        )
    return await select_adapter_by_tags(adapters)


async def _resolve_pinned_connection_against_placement(
    adapters: list[VolundrPort],
    connection_id: str,
    placement: WorkflowPlacement,
    *,
    principal: Principal,
    auth_token: str | None,
) -> VolundrPort:
    """An explicit connectionId must satisfy the workflow's pinned placement.

    Without this check, any caller naming a connection — a human, an A2A
    ``connectionId`` metadata field, or a Ravn resident's configured
    ``a2a_default_connection_id`` applied to every launch it starts — could
    silently strip placement from a workflow that asked to run on specific
    hardware. A pin that is compatible with the placement still resolves
    directly (no re-balancing among other eligible targets); a pin that
    conflicts is rejected loudly, naming both.
    """
    resolved = next(
        (adapter for adapter in adapters if connection_id in {adapter.target_id, adapter.name}),
        None,
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Volundr target not found: {connection_id}",
        )
    if placement.instance:
        if placement.instance in {resolved.target_id, resolved.name}:
            return resolved
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"connectionId {connection_id!r} conflicts with workflow placement "
                f"instance={placement.instance!r}"
            ),
        )
    try:
        # The pinned target satisfies the tags itself or through one of its hosts.
        return await select_adapter_by_tags(
            [resolved],
            list(placement.tags),
            placement.match,
            auth_token=auth_token,
            principal=principal,
        )
    except HostTagsUnreadableError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except TargetSelectionError:
        pass
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            f"connectionId {connection_id!r} (tags={sorted(resolved.tags)}) does not satisfy "
            f"workflow placement (tags={sorted(placement.tags)} match={placement.match!r})"
        ),
    )


async def _resolve_placement_target(
    adapters: list[VolundrPort],
    placement: WorkflowPlacement,
    *,
    principal: Principal,
    auth_token: str | None,
) -> VolundrPort:
    """Resolve the workflow's pinned graph.placement against visible Guild targets.

    ``instance`` is an exact pin — id or name — with no fallback. ``tags``
    reuses the same eligibility and balancing rule as the untargeted launch
    path. Either way, no match never falls back to the default instance: it
    is a rejected launch, loud, with the requested placement and every
    visible target's tags in the message. Callers always pass a non-empty
    ``adapters``: an empty registry is a 503 before this function runs.
    """
    if placement.instance:
        for adapter in adapters:
            if placement.instance in {adapter.target_id, adapter.name}:
                return adapter
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_placement_rejection_detail(placement, adapters),
        )
    try:
        return await select_adapter_by_tags(
            adapters,
            list(placement.tags),
            placement.match,
            auth_token=auth_token,
            principal=principal,
        )
    except HostTagsUnreadableError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except TargetSelectionError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_placement_rejection_detail(placement, adapters),
        ) from None


def _placement_rejection_detail(
    placement: WorkflowPlacement,
    adapters: list[VolundrPort],
) -> str:
    requested = (
        f"instance={placement.instance!r}"
        if placement.instance
        else f"tags={sorted(placement.tags)} match={placement.match!r}"
    )
    visible = ", ".join(
        f"{adapter.name} ({adapter.target_id}) tags={sorted(adapter.tags)}" for adapter in adapters
    )
    return f"No Guild target satisfies workflow placement ({requested}); visible targets: {visible}"


def _owner_id_for_scope(scope: WorkflowScope, principal: Principal) -> str | None:
    if scope == WorkflowScope.SYSTEM:
        return None
    return principal.user_id


def _assert_can_manage_scope(scope: WorkflowScope, principal: Principal, request: Request) -> None:
    if scope != WorkflowScope.SYSTEM:
        return

    if is_platform_admin(principal, platform_tenant_id=platform_tenant_id(request)):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="System workflows require elevated permissions",
    )


def _can_view_workflow(workflow: WorkflowDefinition, principal: Principal) -> bool:
    if workflow.scope == WorkflowScope.SYSTEM:
        return True

    return workflow.owner_id == principal.user_id


def _can_edit_workflow(
    workflow: WorkflowDefinition, principal: Principal, *, platform_tenant: str
) -> bool:
    if workflow.scope == WorkflowScope.SYSTEM:
        return is_platform_admin(principal, platform_tenant_id=platform_tenant)
    return workflow.owner_id == principal.user_id


def _assert_can_manage_existing(
    workflow: WorkflowDefinition, principal: Principal, request: Request
) -> None:
    if workflow.scope == WorkflowScope.SYSTEM:
        if is_platform_admin(principal, platform_tenant_id=platform_tenant_id(request)):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System workflows require elevated permissions",
        )

    if workflow.owner_id == principal.user_id:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Workflow is not owned by caller",
    )


def _assert_revision(workflow: WorkflowDefinition, expected: str | None) -> None:
    if workflow.revision is not None and workflow.revision != expected:
        raise HTTPException(
            status_code=409,
            detail="Workflow changed since it was loaded; reload before saving",
        )


async def _save_workflow(
    repo: WorkflowRepository,
    workflow: WorkflowDefinition,
    *,
    request: Request | None = None,
    principal: Principal | None = None,
    refresh_personas: list[str] | None = None,
    versioned: bool = False,
    expected_revision: str | None = None,
    base_revision: str | None = None,
    bump: WorkflowVersionBump = "patch",
) -> WorkflowDefinition:
    try:
        if request is not None:
            workflow = _resolve_edited_requirements(workflow, request)
            if principal is None:
                raise ValueError("Workflow authoring requires an authenticated principal")
            aliases = {
                item["name"] for item in workflow_personas_from_snapshot({"graph": workflow.graph})
            }
            needed = set()
            for alias in aliases:
                pin = workflow.persona_dependencies.get(alias)
                if alias not in workflow.persona_definitions or alias in (refresh_personas or []):
                    needed.add(pin.id if pin else alias)
            source = await authoring_persona_source(request, principal, needed)
            if refresh_personas:
                workflow = _refresh_persona_pins(workflow, refresh_personas, source)
            workflow = pin_workflow_personas(workflow, source)
            workflow = await _resolve_workflow_dependencies(workflow, repo, principal)
        document = load_workflow_document(dump_workflow_document(workflow))
        if graph_has_include_nodes(document.graph):
            # workflow.workflow_definitions is already the validated pinned
            # closure by this point — populated above for an authored save,
            # or supplied by the import plan for a bundle apply — so an
            # include resolves against it instead of a second catalog call.
            resolved_graph = resolve_workflow_includes(
                document.graph,
                persona_dependencies=document.persona_dependencies,
                workflow_dependencies=document.workflow_dependencies,
                resolve_alias=include_resolver_from_workflow_definitions(
                    workflow.workflow_definitions
                ),
            )
            validate_resolved_workflow_graph(
                resolved_graph,
                schema_version=document.schema_version,
                persona_dependencies=document.persona_dependencies,
                workflow_dependencies=document.workflow_dependencies,
                persona_definitions=workflow.persona_definitions,
            )
        else:
            # A saved workflow must be one the step engine can run to an end.
            validate_workflow_graph(
                document.graph, persona_definitions=workflow.persona_definitions
            )
        if versioned:
            if base_revision is None:
                raise ValueError("base_revision is required for a workflow successor")
            return await repo.save_workflow_version(
                workflow,
                expected_revision=expected_revision,
                base_revision=base_revision,
                bump=bump,
            )
        return await repo.save_workflow(workflow)
    except (WorkflowConflictError, WorkflowReadOnlyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _resolve_edited_requirements(
    workflow: WorkflowDefinition, request: Request
) -> WorkflowDefinition:
    graph = copy.deepcopy(workflow.graph)
    nodes = {node.get("id"): node for node in graph.get("nodes", [])}
    requirements = []
    for requirement in workflow.requirements:
        if "/" in requirement["id"]:
            # Child bindings belong to that immutable imported scope. Editing
            # the parent graph cannot resolve or discard a child requirement.
            requirements.append(requirement)
            continue
        if requirement.get("kind") != "mimir":
            requirements.append(requirement)
            continue
        node = nodes.get(requirement["id"])
        if node is None:
            continue
        binding = node.get("registryEntryId") or node.get("seedFromRegistryId")
        if not binding or (
            not requirement.get("resolved") and binding == requirement.get("source_binding")
        ):
            requirements.append({**requirement, "resolved": False})
            continue
        errors = binding_errors(
            {requirement["id"]: binding},
            registry_path=request.app.state.settings.dispatch.mimir.registry_path,
            tenant_id=workflow.tenant_id,
        )
        if errors:
            raise ValueError("; ".join(errors))
        for key in ("path", "url", "adapter", "kwargs", "secretKwargsEnv", "authRef", "auth_ref"):
            node.pop(key, None)
        requirements.append({**requirement, "resolved": True, "binding": binding})
    return replace(workflow, graph=graph, requirements=requirements)


async def _resolve_workflow_dependencies(workflow, repo, principal, ancestors=()):
    """Capture authorized child definitions before an editable document is saved."""
    if workflow.id in ancestors or len(ancestors) >= 32:
        raise ValueError("Cyclic or excessively nested workflow dependencies")
    definitions = {}
    for alias, pin in workflow.workflow_dependencies.items():
        aggregate = workflow.workflow_definitions.get(alias)
        child = None
        document = None
        if aggregate:
            embedded_document = load_workflow_document(json.dumps(aggregate["document"]))
            embedded_revision = workflow_document_revision(embedded_document)
            if (
                embedded_document.id == pin.id
                and embedded_revision == pin.revision
                and embedded_revision == pin.digest
            ):
                document = embedded_document
                child = document.to_workflow(
                    scope=WorkflowScope.USER,
                    owner_id=principal.user_id,
                    persona_definitions=aggregate.get("persona_definitions", {}),
                    workflow_definitions=aggregate.get("workflow_definitions", {}),
                )
        if child is None:
            child = await repo.get_workflow_version(
                pin.id,
                document_revision=pin.digest,
            )
            if child is None or not _can_view_workflow(child, principal):
                raise ValueError(f"Pinned workflow dependency unavailable: {alias}")
            document = document_from_workflow(child)
        assert document is not None
        revision = workflow_document_revision(document)
        if child.id != pin.id or revision != pin.revision or revision != pin.digest:
            raise ValueError(f"Workflow dependency pin mismatch: {alias}")
        child = await _resolve_workflow_dependencies(
            child, repo, principal, (*ancestors, workflow.id)
        )
        definitions[alias] = {
            "document": workflow_document_payload(document),
            "persona_definitions": child.persona_definitions,
            "workflow_definitions": child.workflow_definitions,
        }
    return replace(workflow, workflow_definitions=definitions)


def _refresh_persona_pins(
    workflow: WorkflowDefinition, aliases: list[str], source
) -> WorkflowDefinition:
    if source is None:
        raise ValueError("No authoritative persona source is configured")
    dependencies = dict(workflow.persona_dependencies)
    definitions = dict(workflow.persona_definitions)
    for alias in aliases:
        pin = dependencies.get(alias)
        if pin is None:
            raise ValueError(f"Unknown persona dependency alias: {alias}")
        current = source.load_current_portable(pin.id)
        if current is None:
            raise ValueError(f"Current persona definition is unavailable: {pin.id}")
        dependencies[alias] = current.dependency
        definitions[alias] = current.to_dict()
    return replace(workflow, persona_dependencies=dependencies, persona_definitions=definitions)

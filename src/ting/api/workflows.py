"""REST API for persisted Ting workflow catalogs and direct workflow launches."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field, field_validator

from mimir.connections import normalize_mimir_workload_config, resolve_mimir_registry_refs
from niuu.domain.models import Principal
from niuu.domain.services.token_scope import require_scope
from niuu.domain.session_endpoint import public_session_endpoint
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.a2a_identity import local_agent_card_url
from ting.api.dispatch import resolve_volundr_factory
from ting.api.workflow_bindings import binding_errors
from ting.api.workflow_personas import authoring_persona_source
from ting.domain.delivery_execution import validate_json_schema
from ting.domain.exceptions import WorkflowConflictError, WorkflowReadOnlyError
from ting.domain.models import (
    PersonaDependency,
    WorkflowDefinition,
    WorkflowDependency,
    WorkflowScope,
)
from ting.domain.services.dispatch_service import (
    _resolve_workflow_execution,
    select_adapter_by_tags,
)
from ting.domain.utils import _session_name, _slugify
from ting.domain.workflow_document import (
    document_from_workflow,
    dump_workflow_document,
    load_workflow_document,
    workflow_document_payload,
    workflow_document_revision,
)
from ting.domain.workflow_snapshot import (
    build_workflow_snapshot,
    pin_workflow_personas,
    workflow_mimir_from_snapshot,
    workflow_personas_from_snapshot,
)
from ting.ports.volundr import SpawnRequest, VolundrFactory, VolundrPort, VolundrSession
from ting.ports.workflow_repository import WorkflowRepository

_DEFAULT_WORKFLOW_LAUNCH_DEFINITION = "skuldCodex"


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
    read_only: bool = False
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
    connection_id: str | None = Field(default=None, max_length=255, alias="connectionId")
    model: str = Field(default="", max_length=255)
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

    model_config = {"populate_by_name": True}


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


def create_workflows_router() -> APIRouter:
    from ting.api.workflow_sharing import create_workflow_sharing_router

    router = APIRouter(prefix="/api/v1/ting/workflows", tags=["Workflows"])
    router.include_router(create_workflow_sharing_router())

    @router.get("", response_model=list[WorkflowResponse])
    async def list_workflows(
        scope: Literal["all", "system", "user"] = Query(default="all"),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> list[WorkflowResponse]:
        scope_filter = _coerce_scope_filter(scope)
        workflows = await repo.list_workflows(
            owner_id=principal.user_id,
            scope=scope_filter,
        )
        return [_to_response(workflow) for workflow in workflows]

    @router.get("/{workflow_id}", response_model=WorkflowResponse)
    async def get_workflow(
        workflow_id: UUID = Path(description="Workflow UUID"),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> WorkflowResponse:
        workflow = await repo.get_workflow(workflow_id)
        if workflow is None or not _can_view_workflow(workflow, principal):
            raise HTTPException(status_code=404, detail="Workflow not found")
        return _to_response(workflow)

    @router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
    async def create_workflow(
        body: WorkflowBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> WorkflowResponse:
        scope = WorkflowScope(body.scope)
        _assert_can_manage_scope(scope, principal)
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
        return _to_response(saved)

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
        _assert_can_manage_existing(existing, principal)
        _assert_can_manage_scope(scope, principal)
        _assert_revision(existing, body.expected_revision)
        graph = _body_to_graph(body)

        saved = await _save_workflow(
            repo,
            WorkflowDefinition(
                tenant_id=existing.tenant_id,
                id=existing.id,
                name=body.name,
                description=body.description,
                version=body.version,
                scope=scope,
                owner_id=_owner_id_for_scope(scope, principal),
                graph=graph,
                created_at=existing.created_at,
                updated_at=datetime.now(UTC),
                persona_dependencies=(
                    body.persona_dependencies
                    if "persona_dependencies" in body.model_fields_set
                    else existing.persona_dependencies
                ),
                persona_definitions=existing.persona_definitions,
                schema_version=(
                    body.schema_version
                    if "schema_version" in body.model_fields_set
                    else existing.schema_version
                ),
                workflow_dependencies=(
                    body.workflow_dependencies
                    if "workflow_dependencies" in body.model_fields_set
                    else existing.workflow_dependencies
                ),
                workflow_definitions=existing.workflow_definitions,
                requirements=existing.requirements,
                revision=existing.revision,
            ),
            request=request,
            principal=principal,
            refresh_personas=body.refresh_personas,
        )
        return _to_response(saved)

    @router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_workflow(
        workflow_id: UUID = Path(description="Workflow UUID"),
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> None:
        existing = await repo.get_workflow(workflow_id)
        if existing is None or not _can_view_workflow(existing, principal):
            raise HTTPException(status_code=404, detail="Workflow not found")

        _assert_can_manage_existing(existing, principal)
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
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
        _build_scope: None = Depends(require_scope("ting:workflow:launch")),
    ) -> WorkflowLaunchResponse:
        workflow = await repo.get_workflow(workflow_id)
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
        return _launch_response(
            execution.workflow,
            execution.slug,
            execution.session,
            public_host=request.url.hostname,
        )

    return router


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


def _to_response(workflow: WorkflowDefinition) -> WorkflowResponse:
    graph = workflow.graph or {}
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
        read_only=workflow.read_only,
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
    )


# A duration far longer than any session lifetime — the way to say "never
# auto-forward" without teaching every downstream duration parser a keyword.
# Popping the key does NOT disable auto-forward: the Skuld gate consumer falls
# back to a "30m" default for an absent value, so an empty override would
# silently become 30 minutes — the opposite of the intended contract.
_GATE_NEVER_AUTO_FORWARD = "87600h"


def _apply_gate_auto_forward_override(
    snapshot: dict[str, Any],
    value: str,
) -> dict[str, Any]:
    """Return a snapshot copy with every gate node's autoForwardAfter overridden.

    The snapshot shares the stored definition's graph object, so patching
    happens on a deep copy — a per-launch override must never mutate the
    workflow definition. An empty *value* means "never auto-forward" (the gate
    waits for the human), encoded as a very large duration rather than by
    removing the key (which the consumer would treat as its 30m default).
    """
    effective = value or _GATE_NEVER_AUTO_FORWARD
    patched = copy.deepcopy(snapshot)
    graph = patched.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    for node in nodes or []:
        if not isinstance(node, dict) or node.get("kind") != "gate":
            continue
        node["autoForwardAfter"] = effective
    return patched


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
) -> WorkflowLaunchExecution:
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
    try:
        workflow_snapshot = (
            copy.deepcopy(pinned_workflow_snapshot)
            if pinned_workflow_snapshot is not None
            else build_workflow_snapshot(
                workflow, persona_source=getattr(request.app.state, "persona_source", None)
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if launch.gate_auto_forward_after is not None:
        workflow_snapshot = _apply_gate_auto_forward_override(
            workflow_snapshot,
            launch.gate_auto_forward_after.strip(),
        )
    launch_slug = _resolve_launch_slug(launch, workflow)
    session_name = _resolve_launch_session_name(launch, workflow, launch_slug)
    settings = request.app.state.settings

    try:
        resolved_model, resolved_definition, workflow_personas = _resolve_workflow_execution(
            workflow_snapshot,
            fallback_model=launch.model or settings.dispatch.default_model,
            requested_definition=launch.definition or _DEFAULT_WORKFLOW_LAUNCH_DEFINITION,
            session_definitions=settings.session_definitions,
            configured_models=list(settings.bifrost.models),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    initiative_context = _build_workflow_initiative_context(
        workflow=workflow,
        launch=launch,
        slug=launch_slug,
    )
    workflow_mimir = resolve_mimir_registry_refs(
        normalize_mimir_workload_config(
            workflow_mimir_from_snapshot(workflow_snapshot),
            hosted_url=settings.dispatch.flock.mimir_hosted_url,
        ),
        registry_path=settings.dispatch.flock.mimir_registry_path,
    )
    target_adapter = await _resolve_target_adapter(
        volundr_factory=volundr_factory,
        principal=principal,
        connection_id=launch.connection_id,
    )
    if target_adapter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No Volundr connection is available for this user",
        )

    integration_ids = await target_adapter.list_integration_ids(
        auth_token=bearer_token,
        principal=principal,
    )
    workflow_execution_context = launch.provenance.get("workflow_execution")
    if not isinstance(workflow_execution_context, dict):
        workflow_execution_context = {}
    ravn_runtime_config = dict(settings.dispatch.flock.ravn_config or {})
    configured_workflow_execution = ravn_runtime_config.get("workflow_execution")
    if isinstance(configured_workflow_execution, dict):
        configured_workflow_execution = dict(configured_workflow_execution)
        static_token = str(configured_workflow_execution.pop("auth_token", "") or "").strip()
        configured_token_file = str(
            configured_workflow_execution.pop("auth_token_file", "") or ""
        ).strip()
        if workflow_execution_context and static_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Static workflow_execution.auth_token configuration is unsupported; "
                    "enable Forge developer execution credential rotation"
                ),
            )
        if workflow_execution_context and configured_token_file:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Ting workflow_execution.auth_token_file configuration is unsupported; "
                    "Forge owns the per-session credential projection"
                ),
            )
        ravn_runtime_config["workflow_execution"] = configured_workflow_execution
    if workflow_execution_context:
        ravn_runtime_config["workflow_execution"] = {
            **workflow_execution_context,
            "enabled": True,
        }
        ravn_runtime_config = _configure_child_task_a2a_runtime(
            ravn_runtime_config,
            card_url=local_agent_card_url(
                public_base_url=settings.a2a.public_base_url,
                request_base_url=str(request.base_url),
            ),
        )
    session = await target_adapter.spawn_session(
        SpawnRequest(
            name=session_name,
            repo=launch.repo,
            branch=launch.branch,
            base_branch="",
            model=resolved_model,
            tracker_issue_id=f"workflow:{launch_slug}",
            tracker_issue_url="",
            system_prompt="",
            initial_prompt=initiative_context,
            workload_type="ravn_flock",
            workload_config={
                "personas": workflow_personas,
                "initiative_context": initiative_context,
                "workflow": workflow_snapshot,
                **(
                    {"workflow_result_schema": copy.deepcopy(launch.inherited_result_schema)}
                    if launch.inherited_result_schema
                    else {}
                ),
                **({"provenance": dict(launch.provenance)} if launch.provenance else {}),
                **({"mimir": workflow_mimir} if workflow_mimir else {}),
                **(
                    {"sleipnir_publish_urls": list(settings.dispatch.flock.sleipnir_publish_urls)}
                    if settings.dispatch.flock.sleipnir_publish_urls
                    else {}
                ),
                **(
                    {"daily_budget_usd": settings.dispatch.flock.daily_budget_usd}
                    if settings.dispatch.flock.daily_budget_usd > 0
                    else {}
                ),
                **(
                    {"llm_config": settings.dispatch.flock.llm_config}
                    if settings.dispatch.flock.llm_config
                    else {}
                ),
                **({"ravn_config": ravn_runtime_config} if ravn_runtime_config else {}),
                **(
                    {"observability": settings.dispatch.flock.observability}
                    if settings.dispatch.flock.observability
                    else {}
                ),
            },
            credential_names=_mimir_auth_credential_names(workflow_mimir),
            integration_ids=integration_ids,
            definition=resolved_definition,
        ),
        auth_token=bearer_token,
        principal=principal,
    )
    return WorkflowLaunchExecution(
        workflow=workflow,
        workflow_snapshot=workflow_snapshot,
        slug=launch_slug,
        session=session,
        adapter=target_adapter,
        connection_id=getattr(target_adapter, "target_id", None) or None,
    )


def _configure_child_task_a2a_runtime(
    ravn_config: dict[str, Any],
    *,
    card_url: str,
) -> dict[str, Any]:
    """Make Ting's local workflow facade addressable through Ravn's directory."""
    parsed = urlsplit(card_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Local A2A Agent Card URL must use http or https")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    configured = copy.deepcopy(ravn_config)
    gateway = configured.setdefault("gateway", {})
    if not isinstance(gateway, dict):
        raise ValueError("Ravn gateway config must be an object")
    platform = gateway.setdefault("platform", {})
    if not isinstance(platform, dict):
        raise ValueError("Ravn gateway.platform config must be an object")
    card_urls = platform.get("a2a_agent_card_urls", [])
    trusted_origins = platform.get("a2a_trusted_origins", [])
    if not isinstance(card_urls, list) or not isinstance(trusted_origins, list):
        raise ValueError("Ravn A2A Agent Card URLs and trusted origins must be lists")
    platform["enabled"] = True
    platform.setdefault("base_url", origin)
    platform["a2a_agent_card_urls"] = list(dict.fromkeys([*card_urls, card_url]))
    platform["a2a_trusted_origins"] = list(dict.fromkeys([*trusted_origins, origin]))
    return configured


def _resolve_launch_slug(body: WorkflowLaunchBody, workflow: WorkflowDefinition) -> str:
    if body.session_name:
        return _slugify(body.session_name)[:96] or "workflow"
    return _slugify(body.prompt)[:96] or _slugify(workflow.name)[:96] or "workflow"


def _resolve_launch_session_name(
    body: WorkflowLaunchBody,
    workflow: WorkflowDefinition,
    slug: str,
) -> str:
    if body.session_name:
        return _session_name(body.session_name)
    return _session_name(f"{workflow.name}-{slug}")


async def _resolve_target_adapter(
    *,
    volundr_factory: VolundrFactory,
    principal: Principal,
    connection_id: str | None = None,
):
    adapters = await volundr_factory.for_principal(principal)
    if not adapters:
        return None
    normalized_connection_id = str(connection_id or "").strip()
    if normalized_connection_id:
        for adapter in adapters:
            if normalized_connection_id in {adapter.target_id, adapter.name}:
                return adapter
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Volundr target not found: {normalized_connection_id}",
        )
    return select_adapter_by_tags(adapters)


def _build_workflow_initiative_context(
    *,
    workflow: WorkflowDefinition,
    launch: WorkflowLaunchBody,
    slug: str,
) -> str:
    lines = [
        "# Workflow Launch",
        "",
        f"Workflow: {workflow.name}",
        f"Slug: {slug}",
    ]
    lines.extend(
        [
            "",
            "## Prompt",
            launch.prompt.strip(),
        ]
    )
    if launch.context:
        lines.extend(
            [
                "",
                "## Launch Context",
                json.dumps(launch.context, indent=2, sort_keys=True),
            ]
        )
    lines.extend(
        [
            "",
            "## Expectations",
            (
                "- Follow the workflow graph and respect the personas, "
                "resources, and event flow it defines."
            ),
            (
                "- Use workflow artifacts to keep the work legible while "
                "still adapting to the task at hand."
            ),
            (
                "- Preserve uncertainty honestly and leave durable outputs "
                "that help the next pass continue well."
            ),
        ]
    )
    return "\n".join(lines)


def _mimir_auth_credential_names(mimir_config: dict[str, Any]) -> list[str]:
    """Return credential names needed by workflow-backed Mimir resources."""
    names: list[str] = []
    seen: set[str] = set()
    for raw_ref in list(mimir_config.get("registry_refs") or []):
        if not isinstance(raw_ref, dict):
            continue
        auth_ref = str(raw_ref.get("auth_ref") or raw_ref.get("authRef") or "").strip()
        if (
            not auth_ref
            or auth_ref == "workload:mimir"
            or auth_ref.startswith("integration:")
            or auth_ref in seen
        ):
            continue
        seen.add(auth_ref)
        names.append(auth_ref)
    return names


def _owner_id_for_scope(scope: WorkflowScope, principal: Principal) -> str | None:
    if scope == WorkflowScope.SYSTEM:
        return None
    return principal.user_id


def _can_manage_system_workflows(principal: Principal) -> bool:
    allowed_roles = {"admin", "ting:admin", "volundr:admin", "volundr:developer"}
    normalized_roles = {role.strip() for role in principal.roles if role.strip()}
    return bool(normalized_roles & allowed_roles)


def _assert_can_manage_scope(scope: WorkflowScope, principal: Principal) -> None:
    if scope != WorkflowScope.SYSTEM:
        return

    if _can_manage_system_workflows(principal):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="System workflows require elevated permissions",
    )


def _can_view_workflow(workflow: WorkflowDefinition, principal: Principal) -> bool:
    if workflow.scope == WorkflowScope.SYSTEM:
        return True

    return workflow.owner_id == principal.user_id


def _assert_can_manage_existing(workflow: WorkflowDefinition, principal: Principal) -> None:
    if workflow.read_only:
        raise HTTPException(
            status_code=409, detail="Bundled workflows are read-only; create a copy"
        )
    if workflow.scope == WorkflowScope.SYSTEM:
        if _can_manage_system_workflows(principal):
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
        load_workflow_document(dump_workflow_document(workflow))
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
            registry_path=request.app.state.settings.dispatch.flock.mimir_registry_path,
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
        if aggregate:
            document = load_workflow_document(json.dumps(aggregate["document"]))
            child = document.to_workflow(
                scope=WorkflowScope.USER,
                owner_id=principal.user_id,
                persona_definitions=aggregate.get("persona_definitions", {}),
                workflow_definitions=aggregate.get("workflow_definitions", {}),
            )
        else:
            child = await repo.get_workflow(pin.id)
            if child is None or not _can_view_workflow(child, principal):
                raise ValueError(f"Pinned workflow dependency unavailable: {alias}")
            document = document_from_workflow(child)
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

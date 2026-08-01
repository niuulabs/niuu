"""REST API for persisted Ting workflow catalogs and direct workflow launches."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import require_scope
from niuu.domain.session_endpoint import public_session_endpoint
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.dispatch import resolve_volundr_factory
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.services.dispatch_service import (
    _normalize_mimir_workload_config,
    _resolve_mimir_registry_refs,
    _resolve_workflow_execution,
)
from ting.domain.utils import _session_name, _slugify
from ting.domain.workflow_snapshot import build_workflow_snapshot, workflow_mimir_from_snapshot
from ting.ports.volundr import SpawnRequest, VolundrFactory, VolundrPort, VolundrSession
from ting.ports.workflow_repository import WorkflowRepository

_DEFAULT_WORKFLOW_LAUNCH_DEFINITION = "skuldCodex"


class WorkflowBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    version: str = Field(default="draft", min_length=1, max_length=64)
    scope: Literal["system", "user"] = "user"
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
    id: str
    name: str
    description: str
    version: str
    scope: Literal["system", "user"]
    owner_id: str | None
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
    router = APIRouter(prefix="/api/v1/ting/workflows", tags=["Workflows"])

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
        principal: Principal = Depends(extract_principal),
        repo: WorkflowRepository = Depends(resolve_workflow_repo),
    ) -> WorkflowResponse:
        scope = WorkflowScope(body.scope)
        _assert_can_manage_scope(scope, principal)
        graph = _body_to_graph(body)

        now = datetime.now(UTC)
        workflow = WorkflowDefinition(
            id=uuid4(),
            name=body.name,
            description=body.description,
            version=body.version,
            scope=scope,
            owner_id=_owner_id_for_scope(scope, principal),
            graph=graph,
            created_at=now,
            updated_at=now,
        )
        saved = await repo.save_workflow(workflow)
        return _to_response(saved)

    @router.put("/{workflow_id}", response_model=WorkflowResponse)
    async def update_workflow(
        body: WorkflowBody,
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
        graph = _body_to_graph(body)

        saved = await repo.save_workflow(
            WorkflowDefinition(
                id=existing.id,
                name=body.name,
                description=body.description,
                version=body.version,
                scope=scope,
                owner_id=_owner_id_for_scope(scope, principal),
                graph=graph,
                created_at=existing.created_at,
                updated_at=datetime.now(UTC),
            )
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
        if not await repo.delete_workflow(workflow_id):
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
    return {
        "tags": body.tags,
        "nodes": body.nodes,
        "edges": body.edges,
        "resourceBindings": body.resource_bindings,
        "artifactPaths": body.artifact_paths,
    }


def _to_response(workflow: WorkflowDefinition) -> WorkflowResponse:
    graph = workflow.graph or {}
    return WorkflowResponse(
        id=str(workflow.id),
        name=workflow.name,
        description=workflow.description,
        version=workflow.version,
        scope=workflow.scope.value,
        owner_id=workflow.owner_id,
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
) -> WorkflowLaunchExecution:
    workflow_snapshot = build_workflow_snapshot(workflow)
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
    workflow_mimir = _resolve_mimir_registry_refs(
        _normalize_mimir_workload_config(
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
                **(
                    {"ravn_config": settings.dispatch.flock.ravn_config}
                    if settings.dispatch.flock.ravn_config
                    else {}
                ),
                **(
                    {"observability": settings.dispatch.flock.observability}
                    if settings.dispatch.flock.observability
                    else {}
                ),
            },
            credential_names=_mimir_auth_credential_names(workflow_mimir),
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
    return adapters[0]


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
        if not auth_ref or auth_ref.startswith("integration:") or auth_ref in seen:
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

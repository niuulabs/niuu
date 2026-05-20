"""REST API for persisted Ting workflow catalogs and direct workflow launches."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
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
from ting.ports.volundr import SpawnRequest, VolundrFactory, VolundrSession
from ting.ports.workflow_repository import WorkflowRepository

_DEFAULT_WORKFLOW_LAUNCH_DEFINITION = "skuldCodexExec"


class WorkflowBody(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    version: str = Field(default="draft", min_length=1, max_length=64)
    scope: Literal["system", "user"] = "user"
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    resource_bindings: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="resourceBindings",
        serialization_alias="resourceBindings",
    )
    model_config = {"populate_by_name": True}


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: str
    version: str
    scope: Literal["system", "user"]
    owner_id: str | None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    resource_bindings: list[dict[str, Any]] = Field(
        default_factory=list,
        serialization_alias="resourceBindings",
    )
    created_at: datetime
    updated_at: datetime


class WorkflowLaunchBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    slug: str | None = Field(default=None, max_length=120)
    session_name: str | None = Field(default=None, max_length=63, alias="sessionName")
    repo: str = Field(default="", max_length=500)
    branch: str = Field(default="main", max_length=255)
    base_branch: str = Field(default="", max_length=255, alias="baseBranch")
    model: str = Field(default="", max_length=100)
    definition: str | None = Field(default=None, max_length=100)
    profile_name: str | None = Field(default=None, max_length=255, alias="profileName")
    integration_ids: list[str] = Field(default_factory=list, alias="integrationIds")
    connection_id: str | None = Field(default=None, max_length=255, alias="connectionId")
    mimir_path: str | None = Field(default=None, max_length=2048, alias="mimirPath")
    context: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class WorkflowLaunchResponse(BaseModel):
    workflow_id: str = Field(serialization_alias="workflowId")
    workflow_name: str = Field(serialization_alias="workflowName")
    slug: str
    session_id: str = Field(serialization_alias="sessionId")
    session_name: str = Field(serialization_alias="sessionName")
    status: str
    cluster_name: str = Field(default="", serialization_alias="clusterName")

    model_config = {"populate_by_name": True}


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
    ) -> WorkflowLaunchResponse:
        workflow = await repo.get_workflow(workflow_id)
        if workflow is None or not _can_view_workflow(workflow, principal):
            raise HTTPException(status_code=404, detail="Workflow not found")

        workflow_snapshot = build_workflow_snapshot(workflow)
        workflow_snapshot = _apply_mimir_path_override(workflow_snapshot, body.mimir_path)
        launch_slug = _resolve_launch_slug(body, workflow)
        session_name = _resolve_launch_session_name(body, workflow, launch_slug)
        settings = request.app.state.settings

        try:
            resolved_model, resolved_definition, workflow_personas = _resolve_workflow_execution(
                workflow_snapshot,
                fallback_model=body.model or settings.dispatch.default_model,
                requested_definition=body.definition or _DEFAULT_WORKFLOW_LAUNCH_DEFINITION,
                session_definitions=settings.session_definitions,
                configured_models=list(settings.bifrost.models),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        initiative_context = _build_workflow_initiative_context(
            workflow=workflow,
            launch=body,
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
            connection_id=body.connection_id,
        )
        if target_adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No Volundr connection is available for this user",
            )

        session = await target_adapter.spawn_session(
            SpawnRequest(
                name=session_name,
                repo=body.repo,
                branch=body.branch,
                base_branch=body.base_branch,
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
                    **({"mimir": workflow_mimir} if workflow_mimir else {}),
                    **(
                        {
                            "sleipnir_publish_urls": list(
                                settings.dispatch.flock.sleipnir_publish_urls
                            )
                        }
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
                },
                definition=resolved_definition,
                profile=body.profile_name,
                integration_ids=body.integration_ids,
            ),
            auth_token=bearer_token,
            principal=principal,
        )
        return _launch_response(workflow, launch_slug, session)

    return router


def _coerce_scope_filter(scope: Literal["all", "system", "user"]) -> WorkflowScope | None:
    if scope == "all":
        return None
    return WorkflowScope(scope)


def _body_to_graph(body: WorkflowBody) -> dict[str, Any]:
    return {
        "nodes": body.nodes,
        "edges": body.edges,
        "resourceBindings": body.resource_bindings,
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
        nodes=list(graph.get("nodes") or []),
        edges=list(graph.get("edges") or []),
        resource_bindings=list(
            graph.get("resourceBindings") or graph.get("resource_bindings") or []
        ),
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _launch_response(
    workflow: WorkflowDefinition,
    slug: str,
    session: VolundrSession,
) -> WorkflowLaunchResponse:
    return WorkflowLaunchResponse(
        workflow_id=str(workflow.id),
        workflow_name=workflow.name,
        slug=slug,
        session_id=session.id,
        session_name=session.name,
        status=session.status,
        cluster_name=session.cluster_name,
    )


def _resolve_launch_slug(body: WorkflowLaunchBody, workflow: WorkflowDefinition) -> str:
    if body.slug:
        return _slugify(body.slug)[:96] or "workflow"
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
    connection_id: str | None,
):
    adapters = await volundr_factory.for_principal(principal)
    if not adapters:
        return None
    if connection_id:
        for adapter in adapters:
            if getattr(adapter, "target_id", "") == connection_id:
                return adapter
        raise HTTPException(status_code=404, detail="Requested Volundr connection not found")
    return adapters[0]


def _apply_mimir_path_override(
    snapshot: dict[str, Any],
    mimir_path: str | None,
) -> dict[str, Any]:
    path = str(mimir_path or "").strip()
    if not path:
        return snapshot

    mutated = copy.deepcopy(snapshot)
    mutated.pop("mimir", None)
    graph = mutated.get("graph")
    if not isinstance(graph, dict):
        return mutated
    nodes = list(graph.get("nodes") or [])
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if str(node.get("kind") or "") != "resource":
            continue
        if str(node.get("resourceType") or "") != "mimir":
            continue
        if str(node.get("bindingMode") or "registry") != "registry":
            continue
        node["path"] = path
    for node in list(mutated.get("resource_nodes") or []):
        if not isinstance(node, dict):
            continue
        if str(node.get("bindingMode") or "registry") != "registry":
            continue
        node["path"] = path
    return mutated


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
        lines.extend(_format_launch_context_sections(launch.context))
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


def _format_launch_context_sections(context: dict[str, Any]) -> list[str]:
    lines: list[str] = ["", "## Launch Context"]

    scalar_labels = (
        ("mode", "Mode"),
        ("audience", "Audience"),
        ("deliverable", "Deliverable"),
    )
    consumed_keys: set[str] = set()
    for key, label in scalar_labels:
        value = str(context.get(key, "") or "").strip()
        if not value:
            continue
        lines.append(f"{label}: {value}")
        consumed_keys.add(key)

    list_labels = (
        ("constraints", "Constraints"),
        ("success_criteria", "Success Criteria"),
        ("seed_urls", "Seed URLs"),
    )
    for key, label in list_labels:
        value = context.get(key)
        if not isinstance(value, list):
            continue
        items = [str(item).strip() for item in value if str(item).strip()]
        if not items:
            continue
        lines.extend(["", f"### {label}"])
        lines.extend(f"- {item}" for item in items)
        consumed_keys.add(key)

    remaining = {key: value for key, value in context.items() if key not in consumed_keys}
    if remaining:
        lines.extend(
            ["", "### Additional Context", json.dumps(remaining, indent=2, sort_keys=True)]
        )
    return lines


def _owner_id_for_scope(scope: WorkflowScope, principal: Principal) -> str | None:
    if scope == WorkflowScope.SYSTEM:
        return None
    return principal.user_id


def _can_manage_system_workflows(principal: Principal) -> bool:
    allowed_roles = {"ting:admin", "volundr:developer"}
    return bool(set(principal.roles) & allowed_roles)


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

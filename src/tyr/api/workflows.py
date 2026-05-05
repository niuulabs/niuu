"""REST API for persisted Tyr workflow catalogs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from niuu.domain.models import Principal
from tyr.adapters.inbound.auth import extract_principal
from tyr.domain.models import WorkflowDefinition, WorkflowScope
from tyr.domain.workflow_compiler import compile_workflow_graph
from tyr.ports.workflow_repository import WorkflowRepository


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
    definition_yaml: str | None = None
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
    definition_yaml: str | None
    compile_errors: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class WorkflowCompileResponse(BaseModel):
    definition_yaml: str | None
    compile_errors: list[str]


async def resolve_workflow_repo() -> WorkflowRepository:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Workflow repository not configured",
    )


def create_workflows_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/tyr/workflows", tags=["Workflows"])

    @router.post("/compile", response_model=WorkflowCompileResponse)
    async def compile_workflow(
        body: WorkflowBody,
        _principal: Principal = Depends(extract_principal),
    ) -> WorkflowCompileResponse:
        compiled = compile_workflow_graph(
            body.name,
            _body_to_graph(body),
        )
        return WorkflowCompileResponse(
            definition_yaml=compiled.definition_yaml,
            compile_errors=compiled.errors,
        )

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
        compiled = compile_workflow_graph(body.name, graph)

        now = datetime.now(UTC)
        workflow = WorkflowDefinition(
            id=uuid4(),
            name=body.name,
            description=body.description,
            version=body.version,
            scope=scope,
            owner_id=_owner_id_for_scope(scope, principal),
            definition_yaml=compiled.definition_yaml or body.definition_yaml,
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
        compiled = compile_workflow_graph(body.name, graph)

        saved = await repo.save_workflow(
            WorkflowDefinition(
                id=existing.id,
                name=body.name,
                description=body.description,
                version=body.version,
                scope=scope,
                owner_id=_owner_id_for_scope(scope, principal),
                definition_yaml=compiled.definition_yaml or body.definition_yaml,
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
    compiled = compile_workflow_graph(workflow.name, graph)
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
        definition_yaml=workflow.definition_yaml,
        compile_errors=compiled.errors,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _owner_id_for_scope(scope: WorkflowScope, principal: Principal) -> str | None:
    if scope == WorkflowScope.SYSTEM:
        return None
    return principal.user_id


def _can_manage_system_workflows(principal: Principal) -> bool:
    allowed_roles = {"tyr:admin", "volundr:developer"}
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

"""REST API for the domain-neutral durable workflow parent/child executions.

Fan-out and join, durable waits, and session continuation serve any workflow
that expands into a bounded child DAG. A workflow that needs something more
specific (code delivery today) layers its own contract, service, and API
surface over this one instead of teaching this module its vocabulary — see
``ting.delivery``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.agent_directory import configured_agent_id
from niuu.domain.models import Principal
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.a2a_identity import local_agent_card_url
from ting.api.dispatch import resolve_volundr_factory
from ting.api.workflow_execution_auth import (
    assert_coordinator_claims,
    assert_parent_workload_claims_if_scoped,
    owned_execution,
    require_coordinate_scope,
    resolve_launch_expansion_policy,
)
from ting.api.workflows import WorkflowLaunchBody, launch_workflow_execution, resolve_workflow_repo
from ting.domain.services.workflow_execution import WorkflowExecutionService
from ting.domain.services.workflow_wait import WorkflowWaitService
from ting.domain.workflow_continuation_events import wait_node_conditions
from ting.domain.workflow_document import workflow_document_revision
from ting.domain.workflow_execution import (
    ExecutionBudget,
    ExecutionConflictError,
    WorkflowChildExecution,
    WorkflowChildProposal,
    WorkflowExecution,
    WorkflowExecutionError,
    digest_json,
)
from ting.domain.workflow_execution_trace import (
    project_trace_event,
    public_trace_graph,
    workflow_event_sources,
)
from ting.domain.workflow_snapshot import build_workflow_snapshot
from ting.ports.volundr import PublicSessionLogPage, VolundrFactory
from ting.ports.workflow_execution import WorkflowExecutionRepository
from ting.ports.workflow_repository import WorkflowRepository


class WorkflowExecutionLaunchBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    workflow_id: UUID = Field(alias="workflowId")
    parent_node_id: str = Field(alias="parentNodeId", min_length=1, max_length=255)
    prompt: str = Field(min_length=1, max_length=100_000)
    name: str | None = Field(default=None, max_length=255)
    input: dict[str, Any] = Field(default_factory=dict)
    model: str = Field(default="", max_length=255)
    connection_id: str | None = Field(default=None, alias="connectionId", max_length=255)
    budget_units: int | None = Field(default=None, alias="budgetUnits", ge=1)
    deadline: datetime | None = None


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpansionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str = ""
    parent_node_id: str = ""
    generation: int = Field(ge=1)
    plan_revision: str = Field(min_length=1, max_length=255)
    children: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class ChildMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str = ""
    parent_node_id: str = ""
    child_key: str = Field(min_length=1)
    attempt_id: UUID
    answer: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Exact outstanding input identity: requestId for a question, or gateId plus "
            "gateDecision for a gate."
        ),
    )
    message_id: str = Field(min_length=1)


class RetryChildBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: UUID


class WaitRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    node_id: str = Field(alias="nodeId", min_length=1, max_length=255)
    condition_type: str = Field(alias="conditionType", min_length=1, max_length=255)
    request: dict[str, Any] = Field(default_factory=dict)


async def resolve_workflow_execution_repo() -> WorkflowExecutionRepository:
    raise HTTPException(status_code=503, detail="Workflow execution repository not configured")


async def resolve_workflow_execution_service() -> WorkflowExecutionService:
    raise HTTPException(status_code=503, detail="Workflow execution service not configured")


async def resolve_workflow_wait_service(
    request: Request,
) -> WorkflowWaitService:
    service = getattr(request.app.state, "workflow_wait_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Workflow execution waits not configured")
    return service


def create_workflow_executions_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/ting/workflow-executions", tags=["Workflow executions"])

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def launch_execution(
        body: WorkflowExecutionLaunchBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        workflow_repo: WorkflowRepository = Depends(resolve_workflow_repo),
        execution_repo: WorkflowExecutionRepository = Depends(resolve_workflow_execution_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> dict[str, Any]:
        launch_key = request.headers.get("idempotency-key", "").strip()
        if not launch_key or len(launch_key) > 255:
            raise HTTPException(
                status_code=422,
                detail="Idempotency-Key header is required and must be at most 255 characters",
            )
        workflow = await workflow_repo.get_workflow(body.workflow_id)
        if workflow is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        if workflow.schema_version < 2:
            raise HTTPException(status_code=422, detail="Workflow requires schema v2")
        try:
            node, policy = resolve_launch_expansion_policy(workflow, body.parent_node_id)
        except WorkflowExecutionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        settings = request.app.state.settings.workflow_execution
        now = datetime.now(UTC)
        deadline = body.deadline or now + timedelta(seconds=settings.default_deadline_seconds)
        if deadline.tzinfo is None:
            raise HTTPException(status_code=422, detail="deadline must include a timezone")
        if deadline <= now:
            raise HTTPException(status_code=422, detail="deadline must be in the future")
        execution_id = uuid4()
        revision = workflow_document_revision(workflow)
        launch_digest = digest_json(
            {
                "workflowId": str(body.workflow_id),
                "workflowDigest": revision,
                "request": body.model_dump(mode="json", by_alias=True),
            }
        )
        execution = WorkflowExecution(
            id=execution_id,
            name=body.name or workflow.name,
            prompt=body.prompt,
            owner_id=principal.user_id,
            tenant_id=principal.tenant_id,
            workflow_id=workflow.id,
            workflow_revision=workflow.revision or revision,
            workflow_digest=revision,
            workflow_snapshot=build_workflow_snapshot(
                workflow, persona_source=getattr(request.app.state, "persona_source", None)
            ),
            parent_session_id="",
            parent_node_id=str(node["id"]),
            connection_id="",
            policy=policy,
            budget=ExecutionBudget(total_units=body.budget_units or settings.default_budget_units),
            deadline=deadline,
            input=body.input,
            launch_key=launch_key,
            launch_digest=launch_digest,
            suspension_reason="launching_parent",
            created_at=now,
            updated_at=now,
        )
        try:
            reserved, created = await execution_repo.reserve_parent_launch(execution)
        except ExecutionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if reserved.parent_session_id:
            return await _detail(execution_repo, reserved)

        session_key = f"workflow:execution-{reserved.id.hex}"
        target_adapter = await _target_adapter(volundr_factory, principal, body.connection_id)
        sessions = await target_adapter.list_sessions(
            auth_token=bearer_token,
            principal=principal,
        )
        recovered = next(
            (session for session in sessions if session.tracker_issue_id == session_key),
            None,
        )
        if recovered is not None:
            attached = await execution_repo.attach_parent_session(
                reserved.id,
                session_id=recovered.id,
                connection_id=getattr(target_adapter, "target_id", "") or "",
            )
            return await _detail(execution_repo, attached)
        if not created and datetime.now(UTC) - reserved.updated_at < timedelta(
            seconds=settings.lease_seconds
        ):
            return await _detail(execution_repo, reserved)

        api_base_url = request.app.state.settings.a2a.public_base_url.rstrip("/") or str(
            request.base_url
        ).rstrip("/")
        card_url = local_agent_card_url(
            public_base_url=request.app.state.settings.a2a.public_base_url,
            request_base_url=str(request.base_url),
        )
        local_agent_id = configured_agent_id(card_url)
        launch = WorkflowLaunchBody(
            prompt=body.prompt,
            sessionName=f"workflow-execution-{reserved.id.hex}",
            model=body.model,
            connectionId=body.connection_id,
            context={
                "workflow_execution": {
                    "campaign_id": str(reserved.id),
                    "execution_id": str(reserved.id),
                    "parent_node_id": reserved.parent_node_id,
                    "coordinator_id": reserved.policy.coordinator_id,
                    "deadline": reserved.deadline.isoformat(),
                    "budget": {
                        "total_units": reserved.budget.total_units,
                        "reserved_units": reserved.budget.reserved_units,
                        "spent_units": reserved.budget.spent_units,
                        "available_units": reserved.budget.available_units,
                    },
                    "current_generation": reserved.current_generation,
                    "workflow": {
                        "id": str(reserved.workflow_id),
                        "revision": reserved.workflow_revision,
                        "digest": reserved.workflow_digest,
                    },
                    "child_dependency": {
                        "alias": reserved.policy.workflow_dependency,
                        "template_id": str(reserved.policy.template_id),
                        "template_revision": reserved.policy.template_revision,
                        "template_digest": reserved.policy.template_digest,
                        "agent_id": local_agent_id,
                        "skill_id": str(reserved.policy.template_id),
                        "agent_card_url": card_url,
                    },
                }
            },
            provenance={
                "surface": "workflow_execution",
                "workflow_execution_id": str(reserved.id),
                "workflow_execution": {
                    "base_url": api_base_url,
                    "execution_id": str(reserved.id),
                    "parent_node_id": reserved.parent_node_id,
                    "parent_session_key": session_key,
                    "coordinator_id": reserved.policy.coordinator_id,
                },
            },
        )
        launched = await launch_workflow_execution(
            request=request,
            workflow=workflow,
            pinned_workflow_snapshot=reserved.workflow_snapshot,
            launch=launch,
            volundr_factory=volundr_factory,
            principal=principal,
            bearer_token=bearer_token,
            trusted_workflow_execution=True,
        )
        saved = await execution_repo.attach_parent_session(
            reserved.id,
            session_id=launched.session.id,
            connection_id=launched.connection_id or "",
        )
        return await _detail(execution_repo, saved)

    @router.get("")
    async def list_executions(
        request: Request,
        state_filter: str = Query(default="", alias="state"),
        limit: int | None = Query(default=None, ge=1, le=200),
        cursor: str = Query(default=""),
        principal: Principal = Depends(extract_principal),
        repository: WorkflowExecutionRepository = Depends(resolve_workflow_execution_repo),
    ) -> dict[str, Any]:
        page_size = limit or request.app.state.settings.workflow_execution.list_page_size
        try:
            executions, next_cursor = await repository.list(
                owner_id=principal.user_id,
                tenant_id=principal.tenant_id,
                state=state_filter,
                limit=page_size,
                cursor=cursor,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "executions": [_execution_json(item) for item in executions],
            "nextCursor": next_cursor or None,
        }

    @router.get("/{execution_id}")
    async def get_execution(
        execution_id: UUID,
        principal: Principal = Depends(extract_principal),
        repository: WorkflowExecutionRepository = Depends(resolve_workflow_execution_repo),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        return await _detail(repository, execution)

    @router.get("/{execution_id}/trace")
    async def get_execution_trace(
        execution_id: UUID,
        request: Request,
        child_id: UUID | None = Query(default=None, alias="childId"),
        after: int = Query(default=0, ge=0),
        limit: int | None = Query(default=None, ge=1, le=200),
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        repository: WorkflowExecutionRepository = Depends(resolve_workflow_execution_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> dict[str, Any]:
        """Return frozen topology and one cursor page of public runtime outcomes."""
        execution = await owned_execution(repository, execution_id, principal)
        children = await repository.list_children(execution.id)
        selected_child = None
        selected_session_id = execution.parent_session_id
        selected_connection_id = execution.connection_id
        workflow = _trace_workflow(execution, child=None)

        if child_id is not None:
            selected_child = next((child for child in children if child.id == child_id), None)
            if selected_child is None:
                raise HTTPException(status_code=404, detail="Workflow execution child not found")
            workflow = _trace_workflow(execution, child=selected_child)
            selected_session_id = ""
            campaign_repository = getattr(request.app.state, "workflow_campaign_repo", None)
            if campaign_repository is None:
                raise HTTPException(
                    status_code=503,
                    detail="Workflow campaign repository not configured",
                )
            if selected_child.task_id:
                campaign = await campaign_repository.get_campaign_by_slug(
                    selected_child.task_id,
                    owner_id=execution.owner_id,
                )
                if campaign is not None:
                    if campaign.tenant_id != execution.tenant_id:
                        raise HTTPException(
                            status_code=404,
                            detail="Workflow execution child not found",
                        )
                    selected_session_id = campaign.session_id
                    selected_connection_id = campaign.connection_id or execution.connection_id

        log_page = PublicSessionLogPage(entries=(), scanned_through=after, has_more=False)
        page_size = limit or request.app.state.settings.workflow_execution.list_page_size
        if selected_session_id:
            adapter = await _trace_adapter(
                volundr_factory,
                principal,
                selected_connection_id,
            )
            log_page = await adapter.get_public_session_log_page(
                selected_session_id,
                after=after,
                limit=page_size,
                auth_token=bearer_token,
                principal=principal,
            )

        entries = log_page.entries
        node_ids = {node["id"] for node in workflow["graph"]["nodes"]}
        event_sources = workflow_event_sources(workflow["sourceGraph"])
        events = [
            project_trace_event(
                entry,
                node_ids=node_ids,
                event_sources=event_sources,
                persona_definitions=workflow["personaDefinitions"],
            )
            for entry in entries
            if entry.kind == "room_outcome"
        ]
        node_history: dict[str, list[str]] = {}
        unattached: list[str] = []
        for event in events:
            if not event["nodeIds"]:
                unattached.append(event["eventId"])
                continue
            for node_id in event["nodeIds"]:
                node_history.setdefault(node_id, []).append(event["eventId"])

        sessions = [
            {
                "kind": "parent",
                "sessionId": execution.parent_session_id or None,
                "parentNodeId": None,
            }
        ]
        sessions.extend(
            {
                "kind": "child",
                "sessionId": (
                    selected_session_id
                    if selected_child is not None and child.id == selected_child.id
                    else None
                ),
                "parentNodeId": execution.parent_node_id,
                "childId": str(child.id),
                "childKey": child.key,
                "generation": child.generation,
                "attempt": child.attempt,
                "taskId": child.task_id or None,
                "state": child.state.value,
            }
            for child in children
        )
        return {
            "schemaVersion": 1,
            "execution": {
                "executionId": str(execution.id),
                "state": execution.state.value,
                "currentGeneration": execution.current_generation,
                "parentSessionId": execution.parent_session_id or None,
            },
            "workflow": {
                key: value
                for key, value in workflow.items()
                if key not in {"sourceGraph", "personaDefinitions"}
            },
            "sessions": sessions,
            "selectedChildId": str(selected_child.id) if selected_child is not None else None,
            "selectedSessionId": selected_session_id or None,
            "page": {
                "after": after,
                "scannedThrough": log_page.scanned_through,
                "hasMore": log_page.has_more,
            },
            "events": events,
            "nodeHistory": node_history,
            "unattachedEventIds": unattached,
        }

    @router.get("/{execution_id}/waits")
    async def list_waits(
        execution_id: UUID,
        principal: Principal = Depends(extract_principal),
        repository: WorkflowExecutionRepository = Depends(resolve_workflow_execution_repo),
        service: WorkflowWaitService = Depends(resolve_workflow_wait_service),
    ) -> list[dict[str, Any]]:
        execution = await owned_execution(repository, execution_id, principal)
        return [wait.to_dict() for wait in await service.list_waits(execution)]

    @router.post("/{execution_id}/waits")
    async def request_wait(
        execution_id: UUID,
        body: WaitRequestBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_coordinate_scope),
        repository: WorkflowExecutionRepository = Depends(resolve_workflow_execution_repo),
        service: WorkflowWaitService = Depends(resolve_workflow_wait_service),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        assert_coordinator_claims(request, bearer_token, execution)
        allowed_condition_types = _wait_node_conditions(execution, body.node_id)
        try:
            wait = await service.request_wait(
                execution,
                node_id=body.node_id,
                condition_type=body.condition_type,
                request=body.request,
                allowed_condition_types=allowed_condition_types,
            )
        except WorkflowExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return wait.to_dict()

    @router.post("/{execution_id}/cancel")
    async def cancel_execution(
        execution_id: UUID,
        _body: EmptyBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_coordinate_scope),
        repository: WorkflowExecutionRepository = Depends(resolve_workflow_execution_repo),
        service: WorkflowExecutionService = Depends(resolve_workflow_execution_service),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        assert_parent_workload_claims_if_scoped(request, bearer_token, execution)
        try:
            await service.cancel(
                execution_id,
                owner_id=principal.user_id,
                tenant_id=principal.tenant_id,
            )
        except WorkflowExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        execution = await owned_execution(repository, execution_id, principal)
        return await _detail(repository, execution)

    @router.post("/{execution_id}/expansions")
    async def expand_execution(
        execution_id: UUID,
        body: ExpansionBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_coordinate_scope),
        repository: WorkflowExecutionRepository = Depends(resolve_workflow_execution_repo),
        service: WorkflowExecutionService = Depends(resolve_workflow_execution_service),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        assert_coordinator_claims(request, bearer_token, execution)
        if body.campaign_id and body.campaign_id != str(execution_id):
            raise HTTPException(status_code=409, detail="campaign_id does not match route")
        if body.parent_node_id and body.parent_node_id != execution.parent_node_id:
            raise HTTPException(status_code=409, detail="parent_node_id does not match execution")
        plan_digest = digest_json({"planRevision": body.plan_revision})
        proposals = [
            _generic_proposal_from_payload(item, plan_digest=plan_digest) for item in body.children
        ]
        try:
            await service.expand(
                execution.id,
                owner_id=execution.owner_id,
                tenant_id=execution.tenant_id,
                coordinator_id=execution.policy.coordinator_id,
                generation=body.generation,
                plan_revision=body.plan_revision,
                children=proposals,
            )
        except (WorkflowExecutionError, ExecutionConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        execution = await owned_execution(repository, execution_id, principal)
        return await _detail(repository, execution)

    @router.post("/{execution_id}/messages")
    async def message_child(
        execution_id: UUID,
        body: ChildMessageBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_coordinate_scope),
        repository: WorkflowExecutionRepository = Depends(resolve_workflow_execution_repo),
        service: WorkflowExecutionService = Depends(resolve_workflow_execution_service),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        assert_coordinator_claims(request, bearer_token, execution)
        if body.campaign_id and body.campaign_id != str(execution_id):
            raise HTTPException(status_code=409, detail="campaign_id does not match route")
        if body.parent_node_id and body.parent_node_id != execution.parent_node_id:
            raise HTTPException(status_code=409, detail="parent_node_id does not match execution")
        try:
            await service.message(
                execution.id,
                owner_id=execution.owner_id,
                tenant_id=execution.tenant_id,
                child_key=body.child_key,
                attempt_id=body.attempt_id,
                answer=body.answer,
                metadata=body.metadata,
                message_id=body.message_id,
            )
        except (WorkflowExecutionError, ExecutionConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        execution = await owned_execution(repository, execution_id, principal)
        return await _detail(repository, execution)

    @router.post("/{execution_id}/reconcile")
    async def reconcile_execution(
        execution_id: UUID,
        _body: EmptyBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_coordinate_scope),
        repository: WorkflowExecutionRepository = Depends(resolve_workflow_execution_repo),
        service: WorkflowExecutionService = Depends(resolve_workflow_execution_service),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        assert_parent_workload_claims_if_scoped(request, bearer_token, execution)
        await service.reconcile(execution_id)
        execution = await owned_execution(repository, execution_id, principal)
        return await _detail(repository, execution)

    @router.post("/{execution_id}/children/{child_key}/retry")
    async def retry_child(
        execution_id: UUID,
        child_key: str,
        body: RetryChildBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_coordinate_scope),
        repository: WorkflowExecutionRepository = Depends(resolve_workflow_execution_repo),
        service: WorkflowExecutionService = Depends(resolve_workflow_execution_service),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        assert_parent_workload_claims_if_scoped(request, bearer_token, execution)
        try:
            await service.retry(
                execution_id,
                owner_id=principal.user_id,
                tenant_id=principal.tenant_id,
                child_key=child_key,
                attempt_id=body.attempt_id,
            )
            await service.launch_ready()
        except WorkflowExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        execution = await owned_execution(repository, execution_id, principal)
        return await _detail(repository, execution)

    return router


async def _target_adapter(factory: VolundrFactory, principal: Principal, connection_id: str | None):
    if connection_id:
        adapter = await factory.for_connection(principal.user_id, connection_id)
    else:
        adapter = await factory.primary_for_owner(principal.user_id)
    if adapter is None:
        raise HTTPException(status_code=503, detail="No Volundr connection is available")
    return adapter


async def _trace_adapter(
    factory: VolundrFactory,
    principal: Principal,
    connection_id: str | None,
):
    adapter = (
        await factory.for_connection(principal.user_id, connection_id)
        if connection_id
        else await factory.primary_for_owner(principal.user_id)
    )
    if adapter is None:
        raise HTTPException(status_code=503, detail="Execution's Volundr connection is unavailable")
    return adapter


def _trace_workflow(
    execution: WorkflowExecution,
    *,
    child: WorkflowChildExecution | None,
) -> dict[str, Any]:
    snapshot = execution.workflow_snapshot
    graph = snapshot.get("graph")
    workflow_id = str(execution.workflow_id)
    revision = execution.workflow_revision
    digest = execution.workflow_digest
    name = str(snapshot.get("name") or execution.name)
    version = str(snapshot.get("version") or "")
    persona_definitions = snapshot.get("persona_definitions")

    if child is not None:
        definitions = snapshot.get("workflow_definitions")
        dependency = (
            definitions.get(execution.policy.workflow_dependency)
            if isinstance(definitions, dict)
            else None
        )
        document = dependency.get("document") if isinstance(dependency, dict) else None
        if not isinstance(document, dict) or not isinstance(document.get("graph"), dict):
            raise HTTPException(
                status_code=409,
                detail="Frozen child workflow topology is unavailable for this execution",
            )
        graph = document["graph"]
        workflow_id = str(document.get("id") or child.template_id)
        revision = child.template_revision
        digest = child.template_digest
        name = str(document.get("name") or child.key)
        version = str(document.get("version") or "")
        persona_definitions = dependency.get("persona_definitions")

    if not isinstance(graph, dict):
        raise HTTPException(
            status_code=409,
            detail="Frozen workflow topology is unavailable for this execution",
        )
    return {
        "workflowId": workflow_id,
        "revision": revision,
        "digest": digest,
        "name": name,
        "version": version,
        "graph": public_trace_graph(graph),
        "sourceGraph": graph,
        "personaDefinitions": (
            persona_definitions if isinstance(persona_definitions, dict) else {}
        ),
    }


def _generic_proposal_from_payload(raw: object, *, plan_digest: str) -> WorkflowChildProposal:
    if not isinstance(raw, dict):
        raise WorkflowExecutionError("each child proposal must be an object")
    input_payload = dict(raw.get("input") or {})
    input_digest = digest_json(input_payload)
    supplied_input_digest = str(raw.get("inputDigest") or "")
    if supplied_input_digest and supplied_input_digest != input_digest:
        raise WorkflowExecutionError("child inputDigest does not match canonical input")
    supplied_plan_digest = str(raw.get("planDigest") or "")
    if supplied_plan_digest and supplied_plan_digest != plan_digest:
        raise WorkflowExecutionError("child planDigest does not match plan_revision")
    deadline = datetime.fromisoformat(str(raw.get("deadline") or ""))
    return WorkflowChildProposal(
        key=str(raw.get("key") or ""),
        objective=str(raw.get("objective") or ""),
        dependencies=tuple(str(item) for item in raw.get("dependencies") or []),
        input=input_payload,
        input_digest=input_digest,
        plan_digest=plan_digest,
        budget_units=int(raw.get("budgetUnits") or 0),
        deadline=deadline,
        agent_id=str(raw.get("agentId") or ""),
        skill_id=str(raw.get("skillId") or ""),
        context=dict(raw.get("context") or {}),
    )


def _wait_node_conditions(execution: WorkflowExecution, node_id: str) -> frozenset[str]:
    snapshot = execution.workflow_snapshot
    graph = snapshot.get("graph") if isinstance(snapshot, dict) else None
    if not isinstance(graph, dict):
        raise HTTPException(
            status_code=503, detail="Execution has no pinned workflow graph to resolve its waits"
        )
    try:
        return wait_node_conditions(graph, node_id)
    except WorkflowExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _detail(
    repository: WorkflowExecutionRepository, execution: WorkflowExecution
) -> dict[str, Any]:
    children = await repository.list_children(execution.id)
    join = None
    if execution.current_generation:
        join_status = await repository.join_status(execution.id, execution.current_generation)
        join = {
            "generation": join_status.generation,
            "sealed": join_status.sealed,
            "ready": join_status.ready,
            "pending": list(join_status.pending),
            "blocked": list(join_status.blocked),
            "failed": list(join_status.failed),
        }
    return {
        **_execution_json(execution),
        "join": join,
        "children": [_child_json(child) for child in children],
    }


def _execution_json(execution: WorkflowExecution) -> dict[str, Any]:
    return {
        "executionId": str(execution.id),
        "name": execution.name,
        "prompt": execution.prompt,
        "workflowId": str(execution.workflow_id),
        "input": execution.input,
        "state": execution.state.value,
        "suspensionReason": execution.suspension_reason,
        "parentStopRequestedAt": execution.parent_stop_requested_at,
        "parentStoppedAt": execution.parent_stopped_at,
        "currentGeneration": execution.current_generation,
        "planRevision": execution.plan_revision,
        "budget": {
            "totalUnits": execution.budget.total_units,
            "reservedUnits": execution.budget.reserved_units,
            "spentUnits": execution.budget.spent_units,
            "availableUnits": execution.budget.available_units,
        },
        "deadline": execution.deadline,
        "createdAt": execution.created_at,
        "updatedAt": execution.updated_at,
        "completedAt": execution.completed_at,
        "declaredChildren": _declared_children(execution),
    }


def _declared_children(execution: WorkflowExecution) -> list[dict[str, Any]] | None:
    """Surface a subworkflow node's own default children, when it declares any.

    The engine never proposes these itself — a coordinator persona still
    decides, through the ordinary expansion route, whether to submit them
    verbatim, amended, or alongside additional children it invents. This only
    lets the coordinator see what the graph already knows about its node.
    """
    snapshot = execution.workflow_snapshot
    graph = snapshot.get("graph") if isinstance(snapshot, dict) else None
    if not isinstance(graph, dict):
        return None
    node = next(
        (
            candidate
            for candidate in graph.get("nodes", [])
            if isinstance(candidate, dict)
            and str(candidate.get("id") or "") == execution.parent_node_id
        ),
        None,
    )
    if node is None:
        return None
    children = node.get("children")
    if not isinstance(children, list) or not children:
        return None
    return [dict(child) for child in children if isinstance(child, dict)]


def _child_json(child: WorkflowChildExecution) -> dict[str, Any]:
    return {
        "childId": str(child.id),
        "childKey": child.key,
        "attempt": child.attempt,
        "generation": child.generation,
        "state": child.state.value,
        "dependencies": list(child.dependencies),
        "input": child.input,
        "taskHandle": (
            {"agentId": child.agent_id, "taskId": child.task_id, "contextId": child.context_id}
            if child.task_id
            else None
        ),
        "result": child.result,
        "artifacts": list(child.artifacts),
        "resultValidation": child.gate_report,
        "resultValidatedAt": child.gate_validated_at,
        "pendingQuestions": [item.to_a2a_metadata() for item in child.pending_questions],
        "pendingGates": [item.to_a2a_metadata() for item in child.pending_gates],
        "error": (
            {
                "kind": child.failure_kind.value if child.failure_kind else None,
                "detail": child.error,
            }
            if child.error
            else None
        ),
        "deadline": child.deadline,
        "createdAt": child.created_at,
        "updatedAt": child.updated_at,
    }

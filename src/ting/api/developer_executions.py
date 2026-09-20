"""REST API for durable developer-delivery parent/child executions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.agent_directory import configured_agent_id
from niuu.domain.delivery import (
    CandidateEvidence,
    IntegrationReceipt,
    MergeRequest,
    WorkspaceAllocation,
)
from niuu.domain.models import Principal
from niuu.domain.services.token_scope import VALKYRIE_BUILD_TOKEN_USE, require_scope
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.a2a_identity import local_agent_card_url
from ting.api.dispatch import resolve_volundr_factory
from ting.api.workflows import WorkflowLaunchBody, launch_workflow_execution, resolve_workflow_repo
from ting.domain.developer_delivery_wait import DeveloperDeliveryWaitRequest
from ting.domain.developer_execution import (
    ChildExecution,
    ChildExecutionState,
    DeveloperExecution,
    DeveloperExecutionError,
    ExecutionBudget,
    ExecutionConflictError,
    ExecutionState,
    ExpansionPolicy,
    digest_json,
)
from ting.domain.developer_execution_trace import (
    project_trace_event,
    public_trace_graph,
    workflow_event_sources,
)
from ting.domain.services.developer_completion import DeveloperCompletionService
from ting.domain.services.developer_delivery_wait import DeveloperDeliveryWaitService
from ting.domain.services.developer_execution import (
    DeveloperExecutionCoordinator,
    DeveloperExecutionService,
)
from ting.domain.workflow_document import workflow_document_revision
from ting.domain.workflow_snapshot import build_workflow_snapshot
from ting.ports.developer_execution import DeveloperExecutionRepository
from ting.ports.volundr import PublicSessionLogPage, VolundrFactory
from ting.ports.workflow_repository import WorkflowRepository

_EXECUTION_CONTRACT = "developer-delivery/v1"
_ACTIVE_DELIVERY_STATES = frozenset(
    {ExecutionState.RUNNING, ExecutionState.WAITING, ExecutionState.BLOCKED}
)
_CHILD_DELIVERY_OPERATIONS = frozenset({"run_verification", "validate_evidence"})
_DELIVERY_OPERATION_STATES = {
    operation: _ACTIVE_DELIVERY_STATES
    for operation in (
        "allocate_workstream",
        "run_verification",
        "validate_evidence",
        "integrate_candidate",
        "publish_branch",
        "open_review",
        "inspect_candidate",
        "inspect_integration",
        "conditional_merge",
        "reconcile_merge",
    )
}


class DeveloperExecutionLaunchBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    workflow_id: UUID = Field(alias="workflowId")
    prompt: str = Field(min_length=1, max_length=100_000)
    repo: str = Field(min_length=1, max_length=2_000)
    base_branch: str = Field(alias="baseBranch", min_length=1, max_length=500)
    model: str = Field(default="", max_length=255)
    connection_id: str | None = Field(default=None, alias="connectionId", max_length=255)
    name: str | None = Field(default=None, max_length=255)
    budget_units: int | None = Field(default=None, alias="budgetUnits", ge=1)
    deadline: datetime | None = None


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompletionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merge: MergeRequest
    evidence: CandidateEvidence


class IntegrationCandidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integration_receipts: list[IntegrationReceipt] = Field(min_length=1)
    integration_allocation: WorkspaceAllocation


class ExpansionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str = ""
    parent_node_id: str = ""
    generation: int = Field(ge=1)
    plan_revision: str = Field(min_length=1, max_length=255)
    workstreams: list[dict[str, Any]] = Field(min_length=1, max_length=100)


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


class DeliveryAuthorizationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal[
        "allocate_workstream",
        "run_verification",
        "validate_evidence",
        "integrate_candidate",
        "publish_branch",
        "open_review",
        "inspect_candidate",
        "inspect_integration",
        "conditional_merge",
        "reconcile_merge",
    ]
    repository: str = Field(min_length=1, max_length=2_000)
    base_sha: str | None = Field(default=None, min_length=7, max_length=128)
    candidate_sha: str | None = Field(default=None, min_length=7, max_length=128)
    candidate_tree: str | None = Field(default=None, min_length=7, max_length=128)
    target_branch: str | None = Field(default=None, min_length=1, max_length=500)
    policy_id: str | None = Field(default=None, min_length=1, max_length=255)


async def resolve_developer_execution_repo() -> DeveloperExecutionRepository:
    raise HTTPException(status_code=503, detail="Developer execution repository not configured")


async def resolve_developer_execution_service() -> DeveloperExecutionService:
    raise HTTPException(status_code=503, detail="Developer execution service not configured")


async def resolve_developer_delivery_wait_service(
    request: Request,
) -> DeveloperDeliveryWaitService:
    service = getattr(request.app.state, "developer_delivery_wait_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Developer delivery waits not configured")
    return service


def create_developer_executions_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/ting/developer-executions", tags=["Developer delivery"])

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def launch_execution(
        body: DeveloperExecutionLaunchBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        workflow_repo: WorkflowRepository = Depends(resolve_workflow_repo),
        execution_repo: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
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
            raise HTTPException(status_code=404, detail="Developer workflow not found")
        if workflow.schema_version < 2:
            raise HTTPException(status_code=422, detail="Developer workflow requires schema v2")
        if str(workflow.graph.get("executionContract") or "") != _EXECUTION_CONTRACT:
            raise HTTPException(
                status_code=422,
                detail=f"Workflow does not declare executionContract={_EXECUTION_CONTRACT}",
            )
        try:
            node, policy = _expansion_policy(workflow)
        except DeveloperExecutionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        target_adapter = await _target_adapter(
            volundr_factory,
            principal,
            body.connection_id,
        )
        try:
            resolved_ref = await target_adapter.resolve_delivery_ref(
                body.repo,
                body.base_branch,
                auth_token=bearer_token,
                principal=principal,
            )
        except NotImplementedError as exc:
            raise HTTPException(
                status_code=422,
                detail="Selected Forge connection cannot resolve immutable delivery refs",
            ) from exc

        settings = request.app.state.settings.developer_execution
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
        execution = DeveloperExecution(
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
            repository=resolved_ref.repository,
            base_ref=resolved_ref.ref,
            base_sha=resolved_ref.sha,
            parent_session_id="",
            parent_node_id=str(node["id"]),
            connection_id=getattr(target_adapter, "target_id", "") or "",
            policy=policy,
            budget=ExecutionBudget(total_units=body.budget_units or settings.default_budget_units),
            deadline=deadline,
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

        session_key = f"workflow:developer-{reserved.id.hex}"
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
            sessionName=f"developer-{reserved.id.hex}",
            repo=reserved.repository,
            branch=reserved.base_ref,
            model=body.model,
            connectionId=body.connection_id,
            context={
                "developer_execution": {
                    "campaign_id": str(reserved.id),
                    "execution_id": str(reserved.id),
                    "parent_node_id": reserved.parent_node_id,
                    "coordinator_id": reserved.policy.coordinator_id,
                    "repository": reserved.repository,
                    "base_ref": reserved.base_ref,
                    "base_sha": reserved.base_sha,
                    "deadline": reserved.deadline.isoformat(),
                    "budget": {
                        "total_units": reserved.budget.total_units,
                        "reserved_units": reserved.budget.reserved_units,
                        "spent_units": reserved.budget.spent_units,
                        "available_units": reserved.budget.available_units,
                    },
                    "evidence_policy_id": settings.evidence_policy_id,
                    "integration_policy_id": settings.integration_policy_id,
                    "current_generation": reserved.current_generation,
                    "workflow": {
                        "id": str(reserved.workflow_id),
                        "revision": reserved.workflow_revision,
                        "digest": reserved.workflow_digest,
                    },
                    "workstream_dependency": {
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
                "surface": "developer_execution",
                "developer_execution_id": str(reserved.id),
                "base_sha": reserved.base_sha,
                "developer_execution": {
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
            trusted_developer_execution=True,
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
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
    ) -> dict[str, Any]:
        page_size = limit or request.app.state.settings.developer_execution.list_page_size
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
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
    ) -> dict[str, Any]:
        execution = await _owned(repository, execution_id, principal)
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
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> dict[str, Any]:
        """Return frozen topology and one cursor page of public runtime outcomes."""
        execution = await _owned(repository, execution_id, principal)
        children = await repository.list_children(execution.id)
        selected_child = None
        selected_session_id = execution.parent_session_id
        selected_connection_id = execution.connection_id
        workflow = _trace_workflow(execution, child=None)

        if child_id is not None:
            selected_child = next((child for child in children if child.id == child_id), None)
            if selected_child is None:
                raise HTTPException(status_code=404, detail="Developer execution child not found")
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
                            detail="Developer execution child not found",
                        )
                    selected_session_id = campaign.session_id
                    selected_connection_id = campaign.connection_id or execution.connection_id

        log_page = PublicSessionLogPage(entries=(), scanned_through=after, has_more=False)
        page_size = limit or request.app.state.settings.developer_execution.list_page_size
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

    @router.post("/{execution_id}/complete")
    async def complete_execution(
        execution_id: UUID,
        body: CompletionBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_scope("ting:developer:coordinate")),
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> dict[str, Any]:
        execution = await _owned(repository, execution_id, principal)
        _assert_coordinator_claims(request, bearer_token, execution)
        service = DeveloperCompletionService(
            repository=repository,
            volundr_factory=volundr_factory,
            policy_id=request.app.state.settings.developer_execution.integration_policy_id,
        )
        try:
            completed = await service.complete(
                execution,
                merge=body.merge,
                evidence=body.evidence,
                principal=principal,
                auth_token=bearer_token,
            )
        except DeveloperExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return await _detail(repository, completed)

    @router.post("/{execution_id}/integration-candidate")
    async def record_integration_candidate(
        execution_id: UUID,
        body: IntegrationCandidateBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_scope("ting:developer:coordinate")),
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> dict[str, Any]:
        execution = await _owned(repository, execution_id, principal)
        _assert_coordinator_claims(request, bearer_token, execution)
        if not execution.current_generation:
            raise HTTPException(
                status_code=409,
                detail="Integration requires a sealed successful child generation",
            )
        join = await repository.join_status(execution.id, execution.current_generation)
        if not join.ready:
            raise HTTPException(
                status_code=409,
                detail="Integration requires a sealed successful child generation",
            )
        if (
            body.integration_allocation.campaign_id != str(execution.id)
            or body.integration_allocation.repository != execution.repository
            or body.integration_allocation.base_sha != execution.base_sha
            or any(
                receipt.campaign_id != str(execution.id)
                or receipt.repository != execution.repository
                or receipt.base_sha != execution.base_sha
                or receipt.integration_allocation_id != body.integration_allocation.allocation_id
                for receipt in body.integration_receipts
            )
        ):
            raise HTTPException(
                status_code=409,
                detail="Integration receipt and allocation do not match this execution",
            )
        adapter = await _target_adapter(volundr_factory, principal, execution.connection_id)
        inspection = await adapter.inspect_delivery_integration_chain(
            body.integration_allocation,
            tuple(body.integration_receipts),
            policy_id=request.app.state.settings.developer_execution.integration_policy_id,
            auth_token=bearer_token,
            principal=principal,
        )
        expected_receipt_ids = tuple(item.receipt_id for item in body.integration_receipts)
        if (
            inspection.campaign_id != str(execution.id)
            or inspection.integration_allocation_id != body.integration_allocation.allocation_id
            or inspection.repository != execution.repository
            or inspection.base_sha != execution.base_sha
            or inspection.receipt_ids != expected_receipt_ids
        ):
            raise HTTPException(
                status_code=409,
                detail="Inspected integration chain identity does not match this execution",
            )
        children = await repository.list_children(execution.id)
        current: dict[str, ChildExecution] = {}
        for child in children:
            if child.generation != execution.current_generation:
                continue
            previous = current.get(child.key)
            if previous is None or child.attempt > previous.attempt:
                current[child.key] = child
        accepted = {
            (
                child.key,
                str(child.id),
                str((child.result or {}).get("candidateSha") or ""),
                str((child.result or {}).get("candidateTree") or ""),
            )
            for child in current.values()
            if child.state == ChildExecutionState.COMPLETED
        }
        inspected = {
            (
                item.workstream_key,
                item.attempt_id,
                item.candidate_sha,
                item.candidate_tree,
            )
            for item in inspection.integrated_candidates
        }
        if len(accepted) != len(current) or inspected != accepted:
            raise HTTPException(
                status_code=409,
                detail="Inspected integration chain does not exactly cover accepted children",
            )
        try:
            saved = await repository.record_integration_candidate(
                execution.id,
                owner_id=execution.owner_id,
                tenant_id=execution.tenant_id,
                expected_revision=execution.revision,
                allocation=body.integration_allocation.model_dump(mode="json"),
                receipts=[item.model_dump(mode="json") for item in body.integration_receipts],
                candidate=inspection.model_dump(mode="json"),
            )
        except ExecutionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return await _detail(repository, saved)

    @router.get("/{execution_id}/delivery-waits")
    async def list_delivery_waits(
        execution_id: UUID,
        principal: Principal = Depends(extract_principal),
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
        service: DeveloperDeliveryWaitService = Depends(resolve_developer_delivery_wait_service),
    ) -> list[dict[str, Any]]:
        execution = await _owned(repository, execution_id, principal)
        return [wait.to_dict() for wait in await service.list_waits(execution)]

    @router.post("/{execution_id}/delivery-waits")
    async def wait_for_delivery(
        execution_id: UUID,
        body: DeveloperDeliveryWaitRequest,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_scope("ting:developer:coordinate")),
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
        service: DeveloperDeliveryWaitService = Depends(resolve_developer_delivery_wait_service),
    ) -> dict[str, Any]:
        execution = await _owned(repository, execution_id, principal)
        _assert_coordinator_claims(request, bearer_token, execution)
        try:
            wait = await service.request_wait(execution, body)
        except DeveloperExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return wait.to_dict()

    @router.post("/{execution_id}/cancel")
    async def cancel_execution(
        execution_id: UUID,
        _body: EmptyBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_scope("ting:developer:coordinate")),
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
        service: DeveloperExecutionService = Depends(resolve_developer_execution_service),
    ) -> dict[str, Any]:
        execution = await _owned(repository, execution_id, principal)
        _assert_parent_workload_claims_if_scoped(request, bearer_token, execution)
        try:
            await service.cancel(
                execution_id,
                owner_id=principal.user_id,
                tenant_id=principal.tenant_id,
            )
        except DeveloperExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        execution = await _owned(repository, execution_id, principal)
        return await _detail(repository, execution)

    @router.post("/{execution_id}/expansions")
    async def expand_execution(
        execution_id: UUID,
        body: ExpansionBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_scope("ting:developer:coordinate")),
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
        service: DeveloperExecutionService = Depends(resolve_developer_execution_service),
    ) -> dict[str, Any]:
        execution = await _owned(repository, execution_id, principal)
        _assert_coordinator_claims(request, bearer_token, execution)
        if body.campaign_id and body.campaign_id != str(execution_id):
            raise HTTPException(status_code=409, detail="campaign_id does not match route")
        if body.parent_node_id and body.parent_node_id != execution.parent_node_id:
            raise HTTPException(status_code=409, detail="parent_node_id does not match execution")
        coordinator = DeveloperExecutionCoordinator(
            service,
            execution_id=execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            coordinator_id=execution.policy.coordinator_id,
        )
        try:
            await coordinator.expand(body.model_dump())
        except (DeveloperExecutionError, ExecutionConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        execution = await _owned(repository, execution_id, principal)
        return await _detail(repository, execution)

    @router.post("/{execution_id}/messages")
    async def message_child(
        execution_id: UUID,
        body: ChildMessageBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_scope("ting:developer:coordinate")),
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
        service: DeveloperExecutionService = Depends(resolve_developer_execution_service),
    ) -> dict[str, Any]:
        execution = await _owned(repository, execution_id, principal)
        _assert_coordinator_claims(request, bearer_token, execution)
        if body.campaign_id and body.campaign_id != str(execution_id):
            raise HTTPException(status_code=409, detail="campaign_id does not match route")
        if body.parent_node_id and body.parent_node_id != execution.parent_node_id:
            raise HTTPException(status_code=409, detail="parent_node_id does not match execution")
        coordinator = DeveloperExecutionCoordinator(
            service,
            execution_id=execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            coordinator_id=execution.policy.coordinator_id,
        )
        try:
            await coordinator.message(body.model_dump())
        except (DeveloperExecutionError, ExecutionConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        execution = await _owned(repository, execution_id, principal)
        return await _detail(repository, execution)

    @router.post(
        "/{execution_id}/delivery-authorizations",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def authorize_delivery_operation(
        execution_id: UUID,
        body: DeliveryAuthorizationBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_scope("ting:developer:coordinate")),
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
    ) -> None:
        execution = await _owned(repository, execution_id, principal)
        await _assert_delivery_claims(
            request,
            bearer_token,
            execution,
            repository,
            operation=body.operation,
        )
        allowed_states = _DELIVERY_OPERATION_STATES[body.operation]
        if body.repository != execution.repository:
            raise HTTPException(status_code=403, detail="Delivery repository is outside execution")
        if body.base_sha is not None and body.base_sha != execution.base_sha:
            raise HTTPException(status_code=403, detail="Delivery base SHA is outside execution")
        if execution.cancel_requested or execution.state not in allowed_states:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Delivery operation {body.operation} is not allowed while execution is "
                    f"{execution.state.value}"
                ),
            )
        candidate = execution.integration_candidate or {}
        exact_candidate = (
            body.candidate_sha is not None
            and candidate.get("candidate_sha") == body.candidate_sha
            and (
                body.candidate_tree is None
                or candidate.get("candidate_tree") == body.candidate_tree
            )
        )
        settings = request.app.state.settings.developer_execution
        if body.operation in {"publish_branch", "conditional_merge"}:
            if body.policy_id != settings.integration_policy_id or not exact_candidate:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Delivery operation {body.operation} requires the configured integration "
                        "policy and exact inspected candidate"
                    ),
                )
        if body.operation == "publish_branch":
            allocated_branch = str(
                (execution.integration_allocation or {}).get("branch_name") or ""
            )
            if (
                body.target_branch is None
                or body.target_branch != allocated_branch
                or body.target_branch == execution.base_ref
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Branch publication requires the exact allocated integration branch, "
                        "distinct from the execution base branch"
                    ),
                )
        if body.operation == "open_review":
            if body.target_branch != execution.base_ref or not exact_candidate:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Review publication requires the inspected candidate and execution target"
                    ),
                )
        if body.operation == "conditional_merge":
            review = execution.integration_review_receipt or {}
            join = (
                await repository.join_status(execution.id, execution.current_generation)
                if execution.current_generation
                else None
            )
            if (
                body.candidate_sha is None
                or body.candidate_tree is None
                or body.target_branch != execution.base_ref
                or candidate.get("candidate_sha") != body.candidate_sha
                or candidate.get("candidate_tree") != body.candidate_tree
                or review.get("candidate_sha") != body.candidate_sha
                or review.get("candidate_tree") != body.candidate_tree
                or review.get("verdict") != "pass"
                or join is None
                or not join.ready
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Conditional merge requires the exact inspected candidate, a trusted "
                        "passing integration review, and a ready child join"
                    ),
                )

    @router.post("/{execution_id}/reconcile")
    async def reconcile_execution(
        execution_id: UUID,
        _body: EmptyBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_scope("ting:developer:coordinate")),
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
        service: DeveloperExecutionService = Depends(resolve_developer_execution_service),
    ) -> dict[str, Any]:
        execution = await _owned(repository, execution_id, principal)
        _assert_parent_workload_claims_if_scoped(request, bearer_token, execution)
        await service.reconcile(execution_id)
        execution = await _owned(repository, execution_id, principal)
        return await _detail(repository, execution)

    @router.post("/{execution_id}/children/{child_key}/retry")
    async def retry_child(
        execution_id: UUID,
        child_key: str,
        body: RetryChildBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_scope("ting:developer:coordinate")),
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
        service: DeveloperExecutionService = Depends(resolve_developer_execution_service),
    ) -> dict[str, Any]:
        execution = await _owned(repository, execution_id, principal)
        _assert_parent_workload_claims_if_scoped(request, bearer_token, execution)
        try:
            await service.retry(
                execution_id,
                owner_id=principal.user_id,
                tenant_id=principal.tenant_id,
                child_key=child_key,
                attempt_id=body.attempt_id,
            )
            await service.launch_ready()
        except DeveloperExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        execution = await _owned(repository, execution_id, principal)
        return await _detail(repository, execution)

    @router.get("/{execution_id}/evidence")
    async def execution_evidence(
        execution_id: UUID,
        principal: Principal = Depends(extract_principal),
        repository: DeveloperExecutionRepository = Depends(resolve_developer_execution_repo),
    ) -> dict[str, Any]:
        execution = await _owned(repository, execution_id, principal)
        children = await repository.list_children(execution_id)
        current: dict[str, ChildExecution] = {}
        for child in children:
            if child.generation != execution.current_generation:
                continue
            previous = current.get(child.key)
            if previous is None or child.attempt > previous.attempt:
                current[child.key] = child
        reports = [child.evidence_report for child in current.values()]
        rejected = [
            reason
            for report in reports
            if report and not report.get("accepted")
            for reason in report.get("blocking_reasons", [])
        ]
        has_rejected = any(report is not None and not report.get("accepted") for report in reports)
        verification_status = (
            "rejected"
            if has_rejected
            else (
                "accepted"
                if reports and all(report and report.get("accepted") for report in reports)
                else "pending"
            )
        )
        return {
            "schemaVersion": 1,
            "executionId": str(execution.id),
            "workflowDigest": execution.workflow_digest,
            "repository": execution.repository,
            "baseRef": execution.base_ref,
            "baseSha": execution.base_sha,
            "state": execution.state.value,
            "mergeReceipt": execution.merge_receipt,
            "completedAt": execution.completed_at,
            "verification": {
                "status": verification_status,
                "blockingReasons": rejected,
            },
            "children": [_child_json(child) for child in children],
        }

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
    execution: DeveloperExecution,
    *,
    child: ChildExecution | None,
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


def _assert_coordinator_claims(
    request: Request,
    bearer_token: str | None,
    execution: DeveloperExecution,
) -> None:
    settings = getattr(request.app.state, "settings", None)
    if not bearer_token and settings is not None and settings.auth.allow_anonymous_dev:
        return
    try:
        claims = jwt.decode(
            bearer_token or "",
            options={"verify_signature": False, "verify_exp": False},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=403, detail="Coordinator credential is invalid") from exc
    expected_session_key = f"workflow:developer-{execution.id.hex}"
    expected = {
        "token_use": VALKYRIE_BUILD_TOKEN_USE,
        "workload_developer_execution_id": str(execution.id),
        "workload_parent_node_id": execution.parent_node_id,
        "workload_parent_session_key": expected_session_key,
        "workload_coordinator_id": execution.policy.coordinator_id,
        "workload_forge_session_id": execution.parent_session_id,
    }
    if any(claims.get(key) != value for key, value in expected.items()):
        raise HTTPException(
            status_code=403,
            detail="Coordinator credential is not bound to this execution node and session",
        )
    scopes = claims.get("scopes")
    if not isinstance(scopes, list) or "ting:developer:coordinate" not in scopes:
        raise HTTPException(status_code=403, detail="Coordinator credential scope is missing")


def _assert_parent_workload_claims_if_scoped(
    request: Request,
    bearer_token: str | None,
    execution: DeveloperExecution,
) -> None:
    """Bind workload JWTs to the parent session without changing human/PAT access."""
    try:
        claims = jwt.decode(
            bearer_token or "",
            options={"verify_signature": False, "verify_exp": False},
        )
    except jwt.InvalidTokenError:
        return
    if claims.get("token_use") != VALKYRIE_BUILD_TOKEN_USE:
        return
    _assert_coordinator_claims(request, bearer_token, execution)


async def _assert_delivery_claims(
    request: Request,
    bearer_token: str | None,
    execution: DeveloperExecution,
    repository: DeveloperExecutionRepository,
    *,
    operation: str,
) -> None:
    settings = getattr(request.app.state, "settings", None)
    if not bearer_token and settings is not None and settings.auth.allow_anonymous_dev:
        return
    try:
        claims = jwt.decode(
            bearer_token or "",
            options={"verify_signature": False, "verify_exp": False},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=403, detail="Delivery credential is invalid") from exc
    common = {
        "token_use": VALKYRIE_BUILD_TOKEN_USE,
        "workload_developer_execution_id": str(execution.id),
        "workload_parent_node_id": execution.parent_node_id,
        "workload_coordinator_id": execution.policy.coordinator_id,
    }
    scopes = claims.get("scopes")
    if any(claims.get(key) != value for key, value in common.items()) or not (
        isinstance(scopes, list) and "ting:developer:coordinate" in scopes
    ):
        raise HTTPException(status_code=403, detail="Delivery credential lineage is invalid")
    parent_key = f"workflow:developer-{execution.id.hex}"
    if claims.get("workload_parent_session_key") == parent_key:
        if (
            not execution.parent_session_id
            or claims.get("workload_forge_session_id") != execution.parent_session_id
        ):
            raise HTTPException(
                status_code=403,
                detail="Delivery credential is not bound to the parent Forge session",
            )
        return
    if operation not in _CHILD_DELIVERY_OPERATIONS:
        raise HTTPException(
            status_code=403,
            detail=f"Child credentials cannot authorize delivery operation {operation}",
        )
    raw_attempt_id = str(claims.get("workload_child_attempt_id") or "")
    try:
        attempt_id = UUID(raw_attempt_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Delivery child attempt is invalid") from exc
    child = await repository.get_child(attempt_id)
    child_session_key = str(claims.get("workload_sub") or "")
    child_task_id = str(claims.get("workload_child_task_id") or "")
    if (
        child is None
        or child.execution_id != execution.id
        or not child_session_key.startswith("workflow:a2a-")
        or claims.get("workload_parent_session_key") != child_session_key
        or not child.task_id
        or child.task_id != child_task_id
    ):
        raise HTTPException(status_code=403, detail="Delivery child lineage is invalid")
    campaign_repo = getattr(request.app.state, "workflow_campaign_repo", None)
    campaign = (
        await campaign_repo.get_campaign_by_slug(child_task_id, owner_id=execution.owner_id)
        if campaign_repo is not None
        else None
    )
    if (
        campaign is None
        or campaign.tenant_id != execution.tenant_id
        or not campaign.session_id
        or claims.get("workload_forge_session_id") != campaign.session_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Delivery credential is not bound to the child Forge session",
        )
    children = await repository.list_children(execution.id)
    current = max(
        (
            candidate
            for candidate in children
            if candidate.generation == child.generation and candidate.key == child.key
        ),
        key=lambda candidate: candidate.attempt,
        default=None,
    )
    if (
        current is None
        or current.id != child.id
        or child.generation != execution.current_generation
        or child.state
        not in {
            ChildExecutionState.LAUNCHING,
            ChildExecutionState.SUBMITTED,
            ChildExecutionState.RUNNING,
            ChildExecutionState.BLOCKED,
        }
    ):
        raise HTTPException(status_code=403, detail="Delivery child attempt is no longer active")


def _expansion_policy(workflow) -> tuple[dict[str, Any], ExpansionPolicy]:
    nodes = [
        node
        for node in workflow.graph.get("nodes", [])
        if isinstance(node, dict)
        and node.get("kind") == "subworkflow"
        and node.get("expansionRole") == "workstream"
    ]
    if len(nodes) != 1:
        raise DeveloperExecutionError(
            "Developer workflow must declare exactly one workstream expansion subworkflow"
        )
    node = nodes[0]
    dependency_alias = str(node.get("workflowDependency") or "")
    dependency = workflow.workflow_dependencies.get(dependency_alias)
    if dependency is None:
        raise DeveloperExecutionError("subworkflow references an undeclared workflow dependency")
    return node, ExpansionPolicy(
        coordinator_id=str(node.get("allowedCoordinator") or ""),
        workflow_dependency=dependency_alias,
        template_id=dependency.id,
        template_revision=dependency.revision,
        template_digest=dependency.digest,
        input_schema=dict(node.get("inputSchema") or {}),
        result_schema=dict(node.get("resultSchema") or {}),
        max_children=int(node.get("maxChildren") or 0),
        max_attempts=int(node.get("maxAttempts") or 0),
        max_active_children=int(node.get("maxActiveChildren") or node.get("maxChildren") or 0),
        join_mode=str(node.get("joinMode") or ""),
    )


async def _owned(
    repository,
    execution_id: UUID,
    principal: Principal,
) -> DeveloperExecution:
    execution = await repository.get(
        execution_id,
        owner_id=principal.user_id,
        tenant_id=principal.tenant_id,
    )
    if execution is None:
        raise HTTPException(status_code=404, detail="Developer execution not found")
    return execution


async def _detail(repository, execution: DeveloperExecution) -> dict[str, Any]:
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


def _execution_json(execution: DeveloperExecution) -> dict[str, Any]:
    return {
        "executionId": str(execution.id),
        "name": execution.name,
        "prompt": execution.prompt,
        "workflowId": str(execution.workflow_id),
        "repo": execution.repository,
        "baseBranch": execution.base_ref,
        "baseSha": execution.base_sha,
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
        "mergeReceipt": execution.merge_receipt,
        "integrationReceipts": list(execution.integration_receipts),
        "integrationAllocation": execution.integration_allocation,
        "integrationCandidate": execution.integration_candidate,
        "integrationReviewReceipt": execution.integration_review_receipt,
        "completedAt": execution.completed_at,
    }


def _child_json(child: ChildExecution) -> dict[str, Any]:
    return {
        "childId": str(child.id),
        "childKey": child.key,
        "attempt": child.attempt,
        "generation": child.generation,
        "state": child.state.value,
        "dependencies": list(child.dependencies),
        "requirementIds": list(child.requirement_ids),
        "taskHandle": (
            {"agentId": child.agent_id, "taskId": child.task_id, "contextId": child.context_id}
            if child.task_id
            else None
        ),
        "workspace": child.workspace,
        "candidate": child.result,
        "evidence": list(child.artifacts),
        "evidenceValidation": child.evidence_report,
        "evidenceValidatedAt": child.evidence_validated_at,
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

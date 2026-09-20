"""REST API for the code-delivery specialization of workflow executions.

This router layers git-bound launch, workstream expansion, integration, and
completion over the generic workflow-execution ledger exposed by
``ting.api.workflow_executions``. An execution launched here is a row in the
same generic ledger plus one delivery extension row; ``GET /{id}`` on this
router returns both, while the generic router's ``GET`` on the same id
returns only the generic fields.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.agent_directory import configured_agent_id
from niuu.domain.delivery import (
    CandidateEvidence,
    IntegrationReceipt,
    MergeRequest,
    WorkspaceAllocation,
)
from niuu.domain.models import Principal
from ting.adapters.inbound.auth import extract_bearer_token, extract_principal
from ting.api.a2a_identity import local_agent_card_url
from ting.api.dispatch import resolve_volundr_factory
from ting.api.workflow_execution_auth import (
    assert_coordinator_claims,
    owned_execution,
    require_coordinate_scope,
    resolve_launch_expansion_policy,
)
from ting.api.workflows import WorkflowLaunchBody, launch_workflow_execution, resolve_workflow_repo
from ting.delivery.auth import DELIVERY_OPERATION_STATES, assert_delivery_claims
from ting.delivery.completion import DeliveryCompletionService
from ting.delivery.domain import ChildExecution, DeliveryExecution
from ting.delivery.ports import DeliveryExecutionRepository
from ting.delivery.service import DeliveryExecutionCoordinator, DeliveryExecutionService
from ting.domain.workflow_document import workflow_document_revision
from ting.domain.workflow_execution import (
    ChildExecutionState,
    ExecutionBudget,
    ExecutionConflictError,
    WorkflowExecutionError,
    digest_json,
)
from ting.domain.workflow_snapshot import build_workflow_snapshot
from ting.ports.volundr import VolundrFactory
from ting.ports.workflow_repository import WorkflowRepository


class DeliveryExecutionLaunchBody(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    workflow_id: UUID = Field(alias="workflowId")
    parent_node_id: str = Field(alias="parentNodeId", min_length=1, max_length=255)
    prompt: str = Field(min_length=1, max_length=100_000)
    repo: str = Field(min_length=1, max_length=2_000)
    base_branch: str = Field(alias="baseBranch", min_length=1, max_length=500)
    model: str = Field(default="", max_length=255)
    connection_id: str | None = Field(default=None, alias="connectionId", max_length=255)
    name: str | None = Field(default=None, max_length=255)
    budget_units: int | None = Field(default=None, alias="budgetUnits", ge=1)
    deadline: datetime | None = None


class CompletionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merge: MergeRequest
    evidence: CandidateEvidence


class IntegrationCandidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integration_receipts: list[IntegrationReceipt] = Field(min_length=1)
    integration_allocation: WorkspaceAllocation


class WorkstreamExpansionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: str = ""
    parent_node_id: str = ""
    generation: int = Field(ge=1)
    plan_revision: str = Field(min_length=1, max_length=255)
    workstreams: list[dict[str, Any]] = Field(min_length=1, max_length=100)


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


async def resolve_delivery_execution_repo() -> DeliveryExecutionRepository:
    raise HTTPException(status_code=503, detail="Delivery execution repository not configured")


async def resolve_delivery_execution_service() -> DeliveryExecutionService:
    raise HTTPException(status_code=503, detail="Delivery execution service not configured")


def create_delivery_executions_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/ting/delivery-executions", tags=["Code delivery"])

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def launch_execution(
        body: DeliveryExecutionLaunchBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        workflow_repo: WorkflowRepository = Depends(resolve_workflow_repo),
        execution_repo: DeliveryExecutionRepository = Depends(resolve_delivery_execution_repo),
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
        try:
            node, policy = resolve_launch_expansion_policy(workflow, body.parent_node_id)
        except WorkflowExecutionError as exc:
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
        execution = DeliveryExecution(
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

        session_key = f"workflow:execution-{reserved.id.hex}"
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
                "workflow_execution": {
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
                    "evidence_policy_id": settings.delivery.evidence_policy_id,
                    "integration_policy_id": settings.delivery.integration_policy_id,
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
                "surface": "workflow_execution",
                "workflow_execution_id": str(reserved.id),
                "base_sha": reserved.base_sha,
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

    @router.get("/{execution_id}")
    async def get_execution(
        execution_id: UUID,
        principal: Principal = Depends(extract_principal),
        repository: DeliveryExecutionRepository = Depends(resolve_delivery_execution_repo),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        return await _detail(repository, execution)

    @router.post("/{execution_id}/complete")
    async def complete_execution(
        execution_id: UUID,
        body: CompletionBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_coordinate_scope),
        repository: DeliveryExecutionRepository = Depends(resolve_delivery_execution_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        assert_coordinator_claims(request, bearer_token, execution)
        service = DeliveryCompletionService(
            repository=repository,
            volundr_factory=volundr_factory,
            policy_id=request.app.state.settings.workflow_execution.delivery.integration_policy_id,
        )
        try:
            completed = await service.complete(
                execution,
                merge=body.merge,
                evidence=body.evidence,
                principal=principal,
                auth_token=bearer_token,
            )
        except WorkflowExecutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return await _detail(repository, completed)

    @router.post("/{execution_id}/integration-candidate")
    async def record_integration_candidate(
        execution_id: UUID,
        body: IntegrationCandidateBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_coordinate_scope),
        repository: DeliveryExecutionRepository = Depends(resolve_delivery_execution_repo),
        volundr_factory: VolundrFactory = Depends(resolve_volundr_factory),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        assert_coordinator_claims(request, bearer_token, execution)
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
            policy_id=request.app.state.settings.workflow_execution.delivery.integration_policy_id,
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

    @router.post("/{execution_id}/expansions")
    async def expand_execution(
        execution_id: UUID,
        body: WorkstreamExpansionBody,
        request: Request,
        principal: Principal = Depends(extract_principal),
        bearer_token: str | None = Depends(extract_bearer_token),
        _scope: None = Depends(require_coordinate_scope),
        repository: DeliveryExecutionRepository = Depends(resolve_delivery_execution_repo),
        service: DeliveryExecutionService = Depends(resolve_delivery_execution_service),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        assert_coordinator_claims(request, bearer_token, execution)
        if body.campaign_id and body.campaign_id != str(execution_id):
            raise HTTPException(status_code=409, detail="campaign_id does not match route")
        if body.parent_node_id and body.parent_node_id != execution.parent_node_id:
            raise HTTPException(status_code=409, detail="parent_node_id does not match execution")
        coordinator = DeliveryExecutionCoordinator(
            service,
            execution_id=execution.id,
            owner_id=execution.owner_id,
            tenant_id=execution.tenant_id,
            coordinator_id=execution.policy.coordinator_id,
        )
        try:
            await coordinator.expand(body.model_dump())
        except (WorkflowExecutionError, ExecutionConflictError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        execution = await owned_execution(repository, execution_id, principal)
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
        _scope: None = Depends(require_coordinate_scope),
        repository: DeliveryExecutionRepository = Depends(resolve_delivery_execution_repo),
    ) -> None:
        execution = await owned_execution(repository, execution_id, principal)
        await assert_delivery_claims(
            request,
            bearer_token,
            execution,
            repository,
            operation=body.operation,
        )
        allowed_states = DELIVERY_OPERATION_STATES[body.operation]
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
        settings = request.app.state.settings.workflow_execution
        if body.operation in {"publish_branch", "conditional_merge"}:
            if body.policy_id != settings.delivery.integration_policy_id or not exact_candidate:
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

    @router.get("/{execution_id}/evidence")
    async def execution_evidence(
        execution_id: UUID,
        principal: Principal = Depends(extract_principal),
        repository: DeliveryExecutionRepository = Depends(resolve_delivery_execution_repo),
    ) -> dict[str, Any]:
        execution = await owned_execution(repository, execution_id, principal)
        children = await repository.list_children(execution_id)
        current: dict[str, ChildExecution] = {}
        for child in children:
            if child.generation != execution.current_generation:
                continue
            previous = current.get(child.key)
            if previous is None or child.attempt > previous.attempt:
                current[child.key] = child
        reports = [child.gate_report for child in current.values()]
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


async def _detail(
    repository: DeliveryExecutionRepository, execution: DeliveryExecution
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


def _execution_json(execution: DeliveryExecution) -> dict[str, Any]:
    return {
        "executionId": str(execution.id),
        "name": execution.name,
        "prompt": execution.prompt,
        "workflowId": str(execution.workflow_id),
        "input": execution.input,
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
        "evidenceValidation": child.gate_report,
        "evidenceValidatedAt": child.gate_validated_at,
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

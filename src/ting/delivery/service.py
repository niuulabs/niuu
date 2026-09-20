"""Code delivery specialization of the generic workflow execution service."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

from niuu.ports.workload_identity import WorkloadTokenIssuer
from ting.delivery.domain import (
    ChildExecution,
    DeliveryExecution,
    WorkstreamProposal,
)
from ting.delivery.domain import make_children as make_workstream_children
from ting.delivery.domain import validate_expansion as validate_workstream_expansion
from ting.delivery.ports import DeliveryExecutionRepository, ParentWorkflowContinuation
from ting.domain.services.workflow_execution import WorkflowExecutionService
from ting.domain.workflow_execution import (
    ChildLaunchRequest,
    WorkflowExecutionError,
    digest_json,
)
from ting.domain.workflow_wait import is_wait_suspension_reason
from ting.ports.child_evidence import ChildEvidenceVerifier, ChildReviewAttestor
from ting.ports.workflow_execution import ChildWorkflowGateway


class DeliveryExecutionService(
    WorkflowExecutionService[DeliveryExecution, WorkstreamProposal, ChildExecution]
):
    """Progress the delivery ledger through A2A without embedding review judgment."""

    def __init__(
        self,
        *,
        repository: DeliveryExecutionRepository,
        gateway: ChildWorkflowGateway,
        continuation: ParentWorkflowContinuation,
        evidence_verifier: ChildEvidenceVerifier,
        review_attestor: ChildReviewAttestor,
        token_issuer: WorkloadTokenIssuer | None = None,
        admission_roles: tuple[str, ...] = ("volundr:developer",),
        worker_id: str,
        launch_claim_limit: int,
        reconcile_limit: int,
        lease_seconds: float,
        max_child_reconcile_failures: int = 5,
        max_parent_stop_failures: int = 5,
    ) -> None:
        super().__init__(
            repository=repository,
            gateway=gateway,
            continuation=continuation,
            evidence_verifier=evidence_verifier,
            review_attestor=review_attestor,
            validator=_validate_delivery_expansion,
            child_factory=_make_delivery_children,
            launch_request_builder=_launch_request,
            child_retry_reset=_reset_delivery_retry,
            token_issuer=token_issuer,
            admission_roles=admission_roles,
            worker_id=worker_id,
            launch_claim_limit=launch_claim_limit,
            reconcile_limit=reconcile_limit,
            lease_seconds=lease_seconds,
            max_child_reconcile_failures=max_child_reconcile_failures,
            max_parent_stop_failures=max_parent_stop_failures,
            message_namespace="workflow-execution",
        )

    async def expand(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
        coordinator_id: str,
        generation: int,
        plan_revision: str,
        workstreams: list[WorkstreamProposal],
    ) -> DeliveryExecution:
        return await super().expand(
            execution_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
            coordinator_id=coordinator_id,
            generation=generation,
            plan_revision=plan_revision,
            children=workstreams,
        )

    def _past_children_join(self, execution: DeliveryExecution) -> bool:
        """Also honour delivery's own post-join wait phase and its failure trail.

        The children join stays ready forever once its winning generation's
        children are all terminal, but a delivery execution moves on from
        there into remote delivery observation. Re-running the join
        projection from that later state would re-publish ``resume_parent``
        and clobber the delivery wait, so this must be checked before either.
        """
        if super()._past_children_join(execution):
            return True
        return is_wait_suspension_reason(execution.suspension_reason)


class DeliveryExecutionCoordinator:
    """Identity-bound surface injected into the Ravn coordinator runtime."""

    def __init__(
        self,
        service: DeliveryExecutionService,
        *,
        execution_id: UUID,
        owner_id: str,
        tenant_id: str,
        coordinator_id: str,
    ) -> None:
        self._service = service
        self._execution_id = execution_id
        self._owner_id = owner_id
        self._tenant_id = tenant_id
        self._coordinator_id = coordinator_id

    async def expand(self, payload: dict[str, Any]) -> dict[str, Any]:
        generation = int(payload.get("generation") or 0)
        plan_revision = str(
            payload.get("plan_revision") or payload.get("planRevision") or ""
        ).strip()
        if not plan_revision:
            raise WorkflowExecutionError("plan_revision is required")
        plan_digest = digest_json({"planRevision": plan_revision})
        raw_workstreams = payload.get("workstreams")
        if not isinstance(raw_workstreams, list):
            raise WorkflowExecutionError("workstreams must be a list")
        workstreams = [
            _proposal_from_payload(item, plan_digest=plan_digest) for item in raw_workstreams
        ]
        execution = await self._service.expand(
            self._execution_id,
            owner_id=self._owner_id,
            tenant_id=self._tenant_id,
            coordinator_id=self._coordinator_id,
            generation=generation,
            plan_revision=plan_revision,
            workstreams=workstreams,
        )
        return {
            "executionId": str(execution.id),
            "generation": execution.current_generation,
            "state": execution.state.value,
            "reservedUnits": execution.budget.reserved_units,
        }

    async def reconcile(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        return await self._service.reconcile(self._execution_id)

    async def cancel(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        execution = await self._service.cancel(
            self._execution_id,
            owner_id=self._owner_id,
            tenant_id=self._tenant_id,
        )
        return {"executionId": str(execution.id), "state": execution.state.value}

    async def message(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            attempt_id = UUID(str(payload.get("attempt_id") or payload.get("attemptId") or ""))
        except ValueError as exc:
            raise WorkflowExecutionError("attempt_id must be a UUID") from exc
        child = await self._service.message(
            self._execution_id,
            owner_id=self._owner_id,
            tenant_id=self._tenant_id,
            child_key=str(payload.get("child_key") or payload.get("childKey") or ""),
            attempt_id=attempt_id,
            answer=str(payload.get("answer") or ""),
            metadata=dict(payload.get("metadata") or {}),
            message_id=str(payload.get("message_id") or payload.get("messageId") or ""),
        )
        return {
            "executionId": str(child.execution_id),
            "childKey": child.key,
            "attempt": child.attempt,
            "state": child.state.value,
        }


def _launch_request(child: ChildExecution) -> ChildLaunchRequest:
    work_order = {
        "schemaVersion": 1,
        "executionId": str(child.execution_id),
        "childKey": child.key,
        "attemptId": str(child.id),
        "attempt": child.attempt,
        "objective": child.objective,
        "requirementIds": list(child.requirement_ids),
        "dependencies": list(child.dependencies),
        "template": {
            "id": str(child.template_id),
            "revision": child.template_revision,
            "digest": child.template_digest,
        },
        "planDigest": child.plan_digest,
        "inputDigest": child.input_digest,
        "input": child.input,
        "workspace": child.workspace,
        "repository": child.repository,
        "baseSha": child.base_sha,
        "budgetUnits": child.budget_units,
        "deadline": child.deadline.isoformat(),
    }
    return ChildLaunchRequest(
        intent_id=child.intent_id,
        message_id=child.message_id,
        agent_id=child.agent_id,
        skill_id=child.skill_id,
        work_order=work_order,
        metadata={
            "deliveryContract": "niuulabs.dev-workstream/v1",
            "executionId": str(child.execution_id),
            "childKey": child.key,
            "attemptId": str(child.id),
        },
    )


def _reset_delivery_retry(child: ChildExecution) -> ChildExecution:
    return replace(child, gate_report=None, gate_validated_at=None)


def _validate_delivery_expansion(
    execution: DeliveryExecution,
    *,
    coordinator_id: str,
    generation: int,
    children: list[WorkstreamProposal],
) -> tuple[WorkstreamProposal, ...]:
    """Adapt the delivery contract to the reusable lifecycle service."""
    return validate_workstream_expansion(
        execution,
        coordinator_id=coordinator_id,
        generation=generation,
        workstreams=children,
    )


def _make_delivery_children(
    execution: DeliveryExecution,
    generation: int,
    ordered: tuple[WorkstreamProposal, ...],
    *,
    message_namespace: str,
) -> tuple[ChildExecution, ...]:
    """Build delivery attempts while preserving the generic factory contract."""
    if message_namespace != "workflow-execution":
        raise WorkflowExecutionError("delivery child message namespace is invalid")
    return make_workstream_children(execution, generation, ordered)


def _proposal_from_payload(raw: object, *, plan_digest: str) -> WorkstreamProposal:
    if not isinstance(raw, dict):
        raise WorkflowExecutionError("each workstream must be an object")
    input_payload = dict(raw.get("input") or {})
    input_digest = digest_json(input_payload)
    supplied_input_digest = str(raw.get("inputDigest") or "")
    if supplied_input_digest and supplied_input_digest != input_digest:
        raise WorkflowExecutionError("workstream inputDigest does not match canonical input")
    supplied_plan_digest = str(raw.get("planDigest") or "")
    if supplied_plan_digest and supplied_plan_digest != plan_digest:
        raise WorkflowExecutionError("workstream planDigest does not match plan_revision")
    deadline = datetime.fromisoformat(str(raw.get("deadline") or ""))
    return WorkstreamProposal(
        key=str(raw.get("key") or ""),
        objective=str(raw.get("objective") or ""),
        requirement_ids=tuple(str(item) for item in raw.get("requirementIds") or []),
        dependencies=tuple(str(item) for item in raw.get("dependencies") or []),
        input=input_payload,
        input_digest=input_digest,
        plan_digest=plan_digest,
        repository=str(raw.get("repository") or ""),
        base_sha=str(raw.get("baseSha") or ""),
        budget_units=int(raw.get("budgetUnits") or 0),
        deadline=deadline,
        agent_id=str(raw.get("agentId") or ""),
        skill_id=str(raw.get("skillId") or ""),
        workspace=dict(raw.get("workspace") or {}),
    )

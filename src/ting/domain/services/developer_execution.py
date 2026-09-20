"""Deterministic orchestration of durable developer workflow children."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from niuu.domain.models import Principal
from niuu.ports.workload_identity import WorkloadTokenIssuer
from ting.domain.developer_execution import (
    TERMINAL_CHILD_STATES,
    ChildExecution,
    ChildExecutionState,
    ChildLaunchRequest,
    ChildMessage,
    ChildPendingGate,
    ChildPendingQuestion,
    ChildTaskHandle,
    DeveloperExecution,
    DeveloperExecutionError,
    ExecutionState,
    FailureKind,
    WorkstreamProposal,
    digest_json,
    make_children,
    validate_child_result,
    validate_expansion,
)
from ting.domain.services.workflow_execution import WorkflowExecutionLifecycle
from ting.domain.workflow_execution import make_retry as make_workflow_retry
from ting.ports.developer_evidence import ChildEvidenceVerifier, ChildReviewAttestor
from ting.ports.developer_execution import (
    ChildWorkflowGateway,
    DeveloperExecutionRepository,
    ParentWorkflowContinuation,
)

logger = logging.getLogger(__name__)


class DeveloperExecutionService:
    """Progress the ledger through A2A without embedding semantic judgment."""

    def __init__(
        self,
        *,
        repository: DeveloperExecutionRepository,
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
    ) -> None:
        if not worker_id.strip():
            raise ValueError("developer execution worker_id is required")
        if launch_claim_limit <= 0 or reconcile_limit <= 0 or lease_seconds <= 0:
            raise ValueError("developer execution worker limits must be positive")
        if not admission_roles or any(not role.strip() for role in admission_roles):
            raise ValueError("developer execution admission roles must be non-empty")
        self._repository = repository
        self._gateway = gateway
        self._continuation = continuation
        self._evidence_verifier = evidence_verifier
        self._review_attestor = review_attestor
        self._token_issuer = token_issuer
        self._admission_roles = tuple(role.strip() for role in admission_roles)
        self._worker_id = worker_id
        self._launch_claim_limit = launch_claim_limit
        self._reconcile_limit = reconcile_limit
        self._lease_seconds = lease_seconds
        self._lifecycle = WorkflowExecutionLifecycle(
            repository=repository,
            validator=_validate_developer_expansion,
            child_factory=_make_developer_children,
            message_namespace="developer-execution",
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
    ) -> DeveloperExecution:
        execution = await self._require_execution(
            execution_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        reserved = await self._lifecycle.expand(
            execution,
            coordinator_id=coordinator_id,
            generation=generation,
            plan_revision=plan_revision,
            children=workstreams,
        )
        await self.launch_ready()
        return reserved

    async def launch_ready(self) -> int:
        """Launch one leased batch; ambiguous failures remain leased for retry."""
        lease_until = datetime.now(UTC) + timedelta(seconds=self._lease_seconds)
        claimed = await self._repository.claim_launches(
            worker_id=self._worker_id,
            limit=self._launch_claim_limit,
            lease_until=lease_until,
        )
        launched = 0
        for child in claimed:
            if child.lease_token is None:
                raise DeveloperExecutionError("claimed child is missing its lease token")
            execution = await self._execution_for_child(child)
            handle = await self._gateway.launch_child(
                _launch_request(child),
                auth_token=self._gateway_token(execution, child),
            )
            await self._repository.record_handle(
                child,
                handle,
                worker_id=self._worker_id,
                lease_token=child.lease_token,
                fencing_generation=child.fencing_generation,
            )
            launched += 1
        return launched

    async def reconcile(self, execution_id: UUID | None = None) -> dict[str, int]:
        """Project remote tasks, validate results, and advance durable joins."""
        # Stop an owner-canceled parent before any child transport call. Child
        # cancellation can fail independently; that must not leave the parent
        # runtime able to continue issuing work after its durable stop intent.
        await self._stop_canceled_parents(execution_id=execution_id)
        children = await self._repository.list_reconcilable(limit=self._reconcile_limit)
        selected = [child for child in children if execution_id in {None, child.execution_id}]
        projected = 0
        execution_ids: set[UUID] = {execution_id} if execution_id is not None else set()
        for child in selected:
            execution = await self._execution_for_child(child)
            handle = ChildTaskHandle(
                agent_id=child.agent_id,
                task_id=child.task_id,
                context_id=child.context_id,
            )
            canceling = child.state == ChildExecutionState.CANCELING
            if canceling:
                observation = _normalize_observation(
                    await self._gateway.cancel_child(
                        handle,
                        auth_token=self._gateway_token(execution, child),
                    )
                )
            else:
                observation = _normalize_observation(
                    await self._gateway.get_child(
                        handle,
                        auth_token=self._gateway_token(execution, child),
                    )
                )
            if canceling and observation.state not in TERMINAL_CHILD_STATES:
                observation = replace(
                    observation,
                    state=ChildExecutionState.CANCELING,
                )
            if not canceling and observation.state == ChildExecutionState.COMPLETED:
                if observation.result is None:
                    observation = replace(
                        observation,
                        state=ChildExecutionState.BLOCKED,
                        failure_kind=FailureKind.CONTRACT_INVALID,
                        error="completed child returned no typed result",
                    )
                else:
                    try:
                        attested_result = await self._review_attestor.attest(
                            execution,
                            child,
                            observation.result,
                        )
                        validate_child_result(
                            child,
                            attested_result,
                            execution.policy.result_schema,
                        )
                    except DeveloperExecutionError as exc:
                        observation = replace(
                            observation,
                            state=ChildExecutionState.BLOCKED,
                            failure_kind=FailureKind.CONTRACT_INVALID,
                            error=str(exc),
                        )
                    else:
                        observation = replace(observation, result=attested_result)
                        report = await self._evidence_verifier.validate(
                            execution,
                            child,
                            attested_result,
                        )
                        await self._repository.record_evidence_report(
                            child.id,
                            report.model_dump(mode="json"),
                        )
                        if not report.accepted:
                            observation = replace(
                                observation,
                                state=ChildExecutionState.BLOCKED,
                                failure_kind=FailureKind.POLICY_REJECTED,
                                error="; ".join(report.blocking_reasons),
                            )
            await self._repository.record_observation(child, observation)
            execution_ids.add(child.execution_id)
            projected += 1
        for selected_execution_id in execution_ids:
            await self._project_join(selected_execution_id)
        return {"projected": projected, "executions": len(execution_ids)}

    async def cancel(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> DeveloperExecution:
        await self._repository.request_cancel(
            execution_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        await self.reconcile(execution_id)
        return await self._require_execution(
            execution_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )

    async def _stop_canceled_parents(self, *, execution_id: UUID | None = None) -> None:
        pending = await self._repository.list_parent_stop_pending(limit=self._reconcile_limit)
        for execution in pending:
            if execution_id is not None and execution.id != execution_id:
                continue
            try:
                await self._continuation.stop_parent(execution)
                await self._repository.mark_parent_stopped(execution.id)
            except Exception:
                logger.exception(
                    "Failed to stop canceled developer parent session; durable cleanup will retry",
                    extra={"developer_execution_id": str(execution.id)},
                )

    async def retry(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
        child_key: str,
        attempt_id: UUID,
    ) -> ChildExecution:
        execution = await self._require_execution(
            execution_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        children = await self._repository.list_children(execution_id)
        candidates = [
            child
            for child in children
            if child.generation == execution.current_generation and child.key == child_key
        ]
        if not candidates:
            raise DeveloperExecutionError(f"unknown child key {child_key!r}")
        child = max(candidates, key=lambda item: item.attempt)
        retry = make_workflow_retry(
            child,
            attempt_id=attempt_id,
            max_attempts=execution.policy.max_attempts,
            message_namespace="developer-execution",
        )
        if child.state == ChildExecutionState.BLOCKED:
            if not child.task_id:
                raise DeveloperExecutionError(
                    "blocked child has no A2A task handle to stop before retry"
                )
            handle = ChildTaskHandle(child.agent_id, child.task_id, child.context_id)
            try:
                remote = _normalize_observation(
                    await self._gateway.get_child(
                        handle,
                        auth_token=self._gateway_token(execution, child),
                    )
                )
                if remote.state not in TERMINAL_CHILD_STATES:
                    remote = _normalize_observation(
                        await self._gateway.cancel_child(
                            handle,
                            auth_token=self._gateway_token(execution, child),
                        )
                    )
            except DeveloperExecutionError:
                raise
            except Exception as exc:
                raise DeveloperExecutionError(
                    "blocked child could not be stopped before retry"
                ) from exc
            if remote.state not in TERMINAL_CHILD_STATES:
                await self._repository.record_observation(child, remote)
                raise DeveloperExecutionError(
                    "blocked child is still active; retry was not created"
                )
        retry = replace(
            retry,
            evidence_report=None,
            evidence_validated_at=None,
            pending_questions=(),
            pending_gates=(),
        )
        return await self._repository.create_retry(
            child,
            retry,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )

    async def message(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
        child_key: str,
        attempt_id: UUID,
        answer: str,
        metadata: dict[str, Any],
        message_id: str,
    ) -> ChildExecution:
        if not answer.strip() or not message_id.strip():
            raise DeveloperExecutionError("child answer and messageId are required")
        execution = await self._require_execution(
            execution_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        children = await self._repository.list_children(execution_id)
        candidates = [
            child
            for child in children
            if child.generation == execution.current_generation and child.key == child_key
        ]
        if not candidates:
            raise DeveloperExecutionError(f"unknown child key {child_key!r}")
        child = max(candidates, key=lambda item: item.attempt)
        if child.id != attempt_id:
            raise DeveloperExecutionError("child attempt is no longer current")
        message = await self._repository.reserve_message(
            ChildMessage(
                id=uuid4(),
                child_id=child.id,
                message_id=message_id,
                answer=answer,
                metadata=metadata,
            )
        )
        if message.state == "delivered":
            return child
        if child.state != ChildExecutionState.BLOCKED or not child.task_id:
            raise DeveloperExecutionError("child is not awaiting an A2A reply")
        _validate_reply_target(child, metadata)
        observation = _normalize_observation(
            await self._gateway.reply_child(
                ChildTaskHandle(child.agent_id, child.task_id, child.context_id),
                answer=answer,
                metadata=metadata,
                message_id=message_id,
                auth_token=self._gateway_token(
                    await self._execution_for_child(child),
                    child,
                ),
            )
        )
        await self._repository.mark_message_delivered(message.id)
        updated = await self._repository.record_observation(child, observation)
        return updated

    async def _project_join(self, execution_id: UUID) -> None:
        execution = await self._repository.get_internal(execution_id)
        if execution is None:
            raise DeveloperExecutionError("join parent execution was not found")
        if execution.state in {
            ExecutionState.CANCELED,
            ExecutionState.COMPLETED,
            ExecutionState.FAILED,
        }:
            return
        children = await self._repository.list_children(execution_id)
        if not children and execution.cancel_requested:
            await self._repository.update_execution_state(
                execution_id,
                state=ExecutionState.CANCELED.value,
                suspension_reason="canceled",
            )
            return
        if not children:
            return
        generation = max(child.generation for child in children)
        join = await self._repository.join_status(execution_id, generation)
        if execution.cancel_requested:
            if not join.pending and not join.blocked:
                await self._repository.update_execution_state(
                    execution_id,
                    state=ExecutionState.CANCELED.value,
                    suspension_reason="canceled",
                )
            return
        if join.ready:
            if execution.suspension_reason == "children_contract_valid":
                return
            current_attempts: dict[str, ChildExecution] = {}
            for child in children:
                if child.generation != generation:
                    continue
                previous = current_attempts.get(child.key)
                if previous is None or child.attempt > previous.attempt:
                    current_attempts[child.key] = child
            results = [
                {
                    "childKey": key,
                    "attemptId": str(child.id),
                    "result": child.result,
                    "artifacts": list(child.artifacts),
                }
                for key, child in sorted(current_attempts.items())
            ]
            await self._continuation.resume_parent(
                execution,
                generation=generation,
                results=results,
            )
            await self._repository.update_execution_state(
                execution_id,
                state=ExecutionState.RUNNING.value,
                suspension_reason="children_contract_valid",
            )
            return
        if join.failed:
            blocker_revision = execution.blocker_revision
            await self._repository.update_execution_state(
                execution_id,
                state=ExecutionState.BLOCKED.value,
                suspension_reason="child_failed",
            )
            execution = await self._repository.get_internal(execution_id)
            if execution is None:
                raise DeveloperExecutionError("join parent execution was not found")
            await self._notify_changed_blockers(
                execution,
                children,
                generation,
                snapshot_blocker_revision=blocker_revision,
            )
            return
        if join.blocked:
            blocker_revision = execution.blocker_revision
            await self._repository.update_execution_state(
                execution_id,
                state=ExecutionState.BLOCKED.value,
                suspension_reason="child_blocked",
            )
            execution = await self._repository.get_internal(execution_id)
            if execution is None:
                raise DeveloperExecutionError("join parent execution was not found")
            await self._notify_changed_blockers(
                execution,
                children,
                generation,
                snapshot_blocker_revision=blocker_revision,
            )
            return
        await self._repository.update_execution_state(
            execution_id,
            state=ExecutionState.WAITING.value,
            suspension_reason="awaiting_children",
        )

    async def _notify_changed_blockers(
        self,
        execution: DeveloperExecution,
        children: list[ChildExecution],
        generation: int,
        *,
        snapshot_blocker_revision: int,
    ) -> None:
        if execution.blocker_revision != snapshot_blocker_revision:
            return
        if execution.blocker_revision <= execution.blocker_notified_revision:
            return
        current_attempts: dict[str, ChildExecution] = {}
        for child in children:
            if child.generation != generation:
                continue
            previous = current_attempts.get(child.key)
            if previous is None or child.attempt > previous.attempt:
                current_attempts[child.key] = child
        blocked = [
            {
                "childKey": key,
                "attempt": child.attempt,
                "attemptId": str(child.id),
                "taskId": child.task_id,
                "state": child.state.value,
                "failureKind": child.failure_kind.value if child.failure_kind else None,
                "detail": child.error,
                "pendingQuestions": [item.to_a2a_metadata() for item in child.pending_questions],
                "pendingGates": [item.to_a2a_metadata() for item in child.pending_gates],
            }
            for key, child in sorted(current_attempts.items())
            if child.state in {ChildExecutionState.BLOCKED, ChildExecutionState.FAILED}
        ]
        await self._continuation.notify_parent(
            execution,
            event_type="developer.children.blocked",
            generation=generation,
            correlation_revision=execution.blocker_revision,
            children=blocked,
        )
        await self._repository.mark_blocker_notification(
            execution.id,
            blocker_revision=execution.blocker_revision,
        )

    async def _require_execution(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> DeveloperExecution:
        execution = await self._repository.get(
            execution_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        if execution is None:
            raise DeveloperExecutionError("developer execution was not found")
        return execution

    async def _execution_for_child(self, child: ChildExecution) -> DeveloperExecution:
        execution = await self._repository.get_internal(child.execution_id)
        if execution is None:
            raise DeveloperExecutionError("child parent execution was not found")
        return execution

    def _gateway_token(
        self,
        execution: DeveloperExecution,
        child: ChildExecution,
    ) -> str:
        if self._token_issuer is None:
            return ""
        claims = {
            "scopes": ["ting:workflow:launch"],
            "developer_execution_id": str(execution.id),
            "child_attempt_id": str(child.id),
            "child_intent_id": str(child.intent_id),
        }
        if child.task_id:
            claims["child_task_id"] = child.task_id
        issued = self._token_issuer.issue_token(
            principal=Principal(
                user_id=execution.owner_id,
                email="",
                tenant_id=execution.tenant_id,
                roles=list(self._admission_roles),
            ),
            workload_subject=f"developer-child:{child.id}",
            workload_name=execution.policy.coordinator_id,
            audiences=[],
            token_use="valkyrie_build",
            claims=claims,
        )
        return issued.token


class DeveloperExecutionCoordinator:
    """Identity-bound surface injected into the Ravn coordinator runtime."""

    def __init__(
        self,
        service: DeveloperExecutionService,
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
            raise DeveloperExecutionError("plan_revision is required")
        plan_digest = digest_json({"planRevision": plan_revision})
        raw_workstreams = payload.get("workstreams")
        if not isinstance(raw_workstreams, list):
            raise DeveloperExecutionError("workstreams must be a list")
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
            raise DeveloperExecutionError("attempt_id must be a UUID") from exc
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


def _validate_developer_expansion(
    execution: DeveloperExecution,
    *,
    coordinator_id: str,
    generation: int,
    children: list[WorkstreamProposal],
) -> tuple[WorkstreamProposal, ...]:
    """Adapt the developer contract to the reusable lifecycle service."""
    return validate_expansion(
        execution,
        coordinator_id=coordinator_id,
        generation=generation,
        workstreams=children,
    )


def _make_developer_children(
    execution: DeveloperExecution,
    generation: int,
    ordered: tuple[WorkstreamProposal, ...],
    *,
    message_namespace: str,
) -> tuple[ChildExecution, ...]:
    """Build developer attempts while preserving the generic factory contract."""
    if message_namespace != "developer-execution":
        raise DeveloperExecutionError("developer child message namespace is invalid")
    return make_children(execution, generation, ordered)


def _proposal_from_payload(raw: object, *, plan_digest: str) -> WorkstreamProposal:
    if not isinstance(raw, dict):
        raise DeveloperExecutionError("each workstream must be an object")
    input_payload = dict(raw.get("input") or {})
    input_digest = digest_json(input_payload)
    supplied_input_digest = str(raw.get("inputDigest") or "")
    if supplied_input_digest and supplied_input_digest != input_digest:
        raise DeveloperExecutionError("workstream inputDigest does not match canonical input")
    supplied_plan_digest = str(raw.get("planDigest") or "")
    if supplied_plan_digest and supplied_plan_digest != plan_digest:
        raise DeveloperExecutionError("workstream planDigest does not match plan_revision")
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


def _normalize_observation(observation):
    raw_state = (
        observation.state.value
        if isinstance(observation.state, ChildExecutionState)
        else str(observation.state).strip().lower()
    )
    aliases = {
        "task_state_submitted": ChildExecutionState.SUBMITTED,
        "task_state_working": ChildExecutionState.RUNNING,
        "task_state_input_required": ChildExecutionState.BLOCKED,
        "task_state_auth_required": ChildExecutionState.BLOCKED,
        "task_state_completed": ChildExecutionState.COMPLETED,
        "task_state_failed": ChildExecutionState.FAILED,
        "task_state_canceled": ChildExecutionState.CANCELED,
        "task_state_rejected": ChildExecutionState.FAILED,
    }
    try:
        state = aliases[raw_state] if raw_state in aliases else ChildExecutionState(raw_state)
    except ValueError as exc:
        raise DeveloperExecutionError(f"A2A child returned unknown state {raw_state!r}") from exc
    raw_failure = getattr(observation, "failure_kind", None)
    failure_kind = None
    if raw_failure:
        failure_kind = (
            raw_failure if isinstance(raw_failure, FailureKind) else FailureKind(raw_failure)
        )
    event_id = str(observation.event_id or "").strip()
    if not event_id:
        raise DeveloperExecutionError("A2A child observation is missing a stable eventId")
    return replace(
        observation,
        state=state,
        artifacts=tuple(observation.artifacts),
        failure_kind=failure_kind,
        event_id=event_id,
        pending_questions=tuple(
            _normalize_pending_question(item)
            for item in getattr(observation, "pending_questions", ())
        ),
        pending_gates=tuple(
            _normalize_pending_gate(item) for item in getattr(observation, "pending_gates", ())
        ),
    )


def _normalize_pending_question(value: object) -> ChildPendingQuestion:
    if isinstance(value, ChildPendingQuestion):
        return value
    attempted = _pending_field(value, "attempted", default=())
    return ChildPendingQuestion(
        request_id=str(
            _pending_field(value, "request_id") or _pending_field(value, "requestId") or ""
        ),
        persona=str(_pending_field(value, "persona") or ""),
        question=str(_pending_field(value, "question") or _pending_field(value, "summary") or ""),
        reason=str(_pending_field(value, "reason") or ""),
        recommendation=str(_pending_field(value, "recommendation") or ""),
        attempted=tuple(str(item) for item in attempted or ()),
    )


def _normalize_pending_gate(value: object) -> ChildPendingGate:
    if isinstance(value, ChildPendingGate):
        return value
    return ChildPendingGate(
        gate_id=str(
            _pending_field(value, "gate_id")
            or _pending_field(value, "gateId")
            or _pending_field(value, "id")
            or ""
        ),
        node_id=str(_pending_field(value, "node_id") or _pending_field(value, "nodeId") or ""),
        label=str(_pending_field(value, "label") or ""),
        condition=str(_pending_field(value, "condition") or ""),
        instructions=str(_pending_field(value, "instructions") or ""),
        summary=str(_pending_field(value, "summary") or ""),
    )


def _pending_field(value: object, key: str, *, default: object = "") -> object:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _validate_reply_target(child: ChildExecution, metadata: dict[str, Any]) -> None:
    decision = str(metadata.get("gateDecision") or "").strip().lower()
    request_id = str(metadata.get("requestId") or "").strip()
    gate_id = str(metadata.get("gateId") or "").strip()
    if decision:
        if request_id:
            raise DeveloperExecutionError(
                "reply metadata must identify either a question or a gate, not both"
            )
        if decision not in {"approve", "request_changes"}:
            raise DeveloperExecutionError(
                'metadata.gateDecision must be "approve" or "request_changes"'
            )
        if not gate_id:
            raise DeveloperExecutionError("metadata.gateId is required for a gate reply")
        matching = [item for item in child.pending_gates if item.gate_id == gate_id]
        if len(matching) != 1:
            raise DeveloperExecutionError("gateId does not identify an exact outstanding gate")
        return

    if gate_id:
        raise DeveloperExecutionError("metadata.gateDecision is required for a gate reply")
    if not request_id:
        raise DeveloperExecutionError("metadata.requestId is required for a question reply")
    matching = [item for item in child.pending_questions if item.request_id == request_id]
    if len(matching) != 1:
        raise DeveloperExecutionError("requestId does not identify an exact outstanding question")

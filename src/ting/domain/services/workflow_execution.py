"""Deterministic orchestration of a durable, dynamically expanded workflow.

Fan-out, per-child launch and reconciliation, deadline enforcement, retry,
cancellation, replies, and the join projection that resumes or blocks the
parent are all domain-neutral: they operate on the core execution/child
contracts and on ports, never on what a child actually produces. A workflow
that needs something more (verifying a child's result a particular way,
building a richer launch payload, resetting extra state on retry) supplies
that behavior through a pluggable hook rather than branching on workflow
identity here.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from niuu.domain.models import Principal
from niuu.ports.workload_identity import WorkloadTokenIssuer
from ting.domain.workflow_execution import (
    CHILDREN_JOINED_SUSPENSION_REASON,
    TERMINAL_CHILD_STATES,
    ChildExecutionState,
    ChildLaunchRequest,
    ChildMessage,
    ChildPendingGate,
    ChildPendingQuestion,
    ChildTaskHandle,
    ChildTaskObservation,
    ExecutionState,
    FailureKind,
    WorkflowChildExecution,
    WorkflowChildProposal,
    WorkflowExecution,
    WorkflowExecutionError,
    digest_json,
    make_children,
    validate_child_result,
    validate_expansion,
)
from ting.domain.workflow_execution import (
    make_retry as make_child_retry,
)
from ting.ports.child_evidence import ChildEvidenceVerifier, ChildReviewAttestor
from ting.ports.workflow_execution import (
    ChildWorkflowGateway,
    SessionContinuation,
    WorkflowExecutionRepository,
)

logger = logging.getLogger(__name__)


class ExpansionValidator[
    ExecutionT: WorkflowExecution,
    ProposalT: WorkflowChildProposal,
](Protocol):
    def __call__(
        self,
        execution: ExecutionT,
        *,
        coordinator_id: str,
        generation: int,
        children: list[ProposalT],
    ) -> tuple[ProposalT, ...]: ...


class ChildFactory[
    ExecutionT: WorkflowExecution,
    ProposalT: WorkflowChildProposal,
    ChildT: WorkflowChildExecution,
](Protocol):
    def __call__(
        self,
        execution: ExecutionT,
        generation: int,
        ordered: tuple[ProposalT, ...],
        *,
        message_namespace: str,
    ) -> tuple[ChildT, ...]: ...


class LaunchRequestBuilder[ChildT: WorkflowChildExecution](Protocol):
    def __call__(self, child: ChildT) -> ChildLaunchRequest: ...


class ChildRetryReset[ChildT: WorkflowChildExecution](Protocol):
    def __call__(self, child: ChildT) -> ChildT: ...


class WorkflowExecutionLifecycle[
    ExecutionT: WorkflowExecution,
    ProposalT: WorkflowChildProposal,
    ChildT: WorkflowChildExecution,
]:
    """Reserve validated child DAGs without knowing their domain contract."""

    def __init__(
        self,
        *,
        repository: WorkflowExecutionRepository[ExecutionT, ChildT],
        validator: ExpansionValidator[ExecutionT, ProposalT] = validate_expansion,
        child_factory: ChildFactory[ExecutionT, ProposalT, ChildT] = make_children,
        message_namespace: str = "workflow-execution",
    ) -> None:
        self._repository = repository
        self._validator = validator
        self._child_factory = child_factory
        self._message_namespace = message_namespace

    async def expand(
        self,
        execution: ExecutionT,
        *,
        coordinator_id: str,
        generation: int,
        plan_revision: str,
        children: list[ProposalT],
    ) -> ExecutionT:
        normalized_plan_revision = plan_revision.strip()
        if not normalized_plan_revision:
            raise WorkflowExecutionError("plan_revision is required")
        expected_plan_digest = digest_json({"planRevision": normalized_plan_revision})
        if any(item.plan_digest != expected_plan_digest for item in children):
            raise WorkflowExecutionError("child planDigest does not match plan_revision")
        if execution.current_generation and not execution.plan_revision:
            raise WorkflowExecutionError(
                "existing execution has no durable plan_revision and must be replanned"
            )
        if execution.plan_revision and execution.plan_revision != normalized_plan_revision:
            raise WorkflowExecutionError("plan_revision differs from the durable execution plan")
        ordered = self._validator(
            execution,
            coordinator_id=coordinator_id,
            generation=generation,
            children=children,
        )
        attempts = self._child_factory(
            execution,
            generation,
            ordered,
            message_namespace=self._message_namespace,
        )
        return await self._repository.reserve_generation(
            execution,
            attempts,
            plan_revision=normalized_plan_revision,
        )


def default_launch_request(child: WorkflowChildExecution) -> ChildLaunchRequest:
    """Build a launch request from only the generic child attempt contract."""
    work_order = {
        "schemaVersion": 1,
        "executionId": str(child.execution_id),
        "childKey": child.key,
        "attemptId": str(child.id),
        "attempt": child.attempt,
        "objective": child.objective,
        "dependencies": list(child.dependencies),
        "template": {
            "id": str(child.template_id),
            "revision": child.template_revision,
            "digest": child.template_digest,
        },
        "planDigest": child.plan_digest,
        "inputDigest": child.input_digest,
        "input": child.input,
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
            "executionId": str(child.execution_id),
            "childKey": child.key,
            "attemptId": str(child.id),
        },
    )


def default_child_retry_reset[ChildT: WorkflowChildExecution](child: ChildT) -> ChildT:
    """No extra state to clear beyond what `make_retry` already resets."""
    return child


class WorkflowExecutionService[
    ExecutionT: WorkflowExecution,
    ProposalT: WorkflowChildProposal,
    ChildT: WorkflowChildExecution,
]:
    """Progress the ledger through a child-task gateway without embedding
    judgment about what a child produces or how its result is verified."""

    def __init__(
        self,
        *,
        repository: WorkflowExecutionRepository[ExecutionT, ChildT],
        gateway: ChildWorkflowGateway,
        continuation: SessionContinuation[ExecutionT],
        evidence_verifier: ChildEvidenceVerifier,
        review_attestor: ChildReviewAttestor,
        validator: ExpansionValidator[ExecutionT, ProposalT] = validate_expansion,
        child_factory: ChildFactory[ExecutionT, ProposalT, ChildT] = make_children,
        launch_request_builder: LaunchRequestBuilder[ChildT] = default_launch_request,
        child_retry_reset: ChildRetryReset[ChildT] = default_child_retry_reset,
        token_issuer: WorkloadTokenIssuer | None = None,
        admission_roles: tuple[str, ...] = ("ting:workflow",),
        worker_id: str,
        launch_claim_limit: int,
        reconcile_limit: int,
        lease_seconds: float,
        max_child_reconcile_failures: int = 5,
        max_parent_stop_failures: int = 5,
        message_namespace: str = "workflow-execution",
    ) -> None:
        if not worker_id.strip():
            raise ValueError("workflow execution worker_id is required")
        if launch_claim_limit <= 0 or reconcile_limit <= 0 or lease_seconds <= 0:
            raise ValueError("workflow execution worker limits must be positive")
        if max_child_reconcile_failures <= 0:
            raise ValueError("workflow execution max_child_reconcile_failures must be positive")
        if max_parent_stop_failures <= 0:
            raise ValueError("workflow execution max_parent_stop_failures must be positive")
        if not admission_roles or any(not role.strip() for role in admission_roles):
            raise ValueError("workflow execution admission roles must be non-empty")
        self._repository = repository
        self._gateway = gateway
        self._continuation = continuation
        self._evidence_verifier = evidence_verifier
        self._review_attestor = review_attestor
        self._launch_request_builder = launch_request_builder
        self._child_retry_reset = child_retry_reset
        self._token_issuer = token_issuer
        self._admission_roles = tuple(role.strip() for role in admission_roles)
        self._worker_id = worker_id
        self._launch_claim_limit = launch_claim_limit
        self._reconcile_limit = reconcile_limit
        self._lease_seconds = lease_seconds
        self._max_child_reconcile_failures = max_child_reconcile_failures
        self._max_parent_stop_failures = max_parent_stop_failures
        self._message_namespace = message_namespace
        self._lifecycle = WorkflowExecutionLifecycle(
            repository=repository,
            validator=validator,
            child_factory=child_factory,
            message_namespace=message_namespace,
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
        children: list[ProposalT],
    ) -> ExecutionT:
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
            children=children,
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
                raise WorkflowExecutionError("claimed child is missing its lease token")
            execution = await self._execution_for_child(child)
            handle = await self._gateway.launch_child(
                self._launch_request_builder(child),
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
        """Project remote tasks, validate results, and advance durable joins.

        Each child and each execution's join projection is isolated: one
        item's exception is recorded durably and does not prevent the rest
        of the batch from being processed.
        """
        # Stop an owner-canceled parent before any child transport call. Child
        # cancellation can fail independently; that must not leave the parent
        # runtime able to continue issuing work after its durable stop intent.
        await self._stop_canceled_parents(execution_id=execution_id)
        deadline_execution_ids = await self._fail_expired_deadlines(execution_id=execution_id)
        children = await self._repository.list_reconcilable(limit=self._reconcile_limit)
        # A child past its deadline belongs to the deadline step alone. Polling it
        # here as well would reset its failure count on every successful poll, so
        # a remote task that cannot be canceled would never be failed.
        now = datetime.now(UTC)
        selected = [
            child
            for child in children
            if execution_id in {None, child.execution_id}
            and (child.deadline is None or child.deadline > now)
        ]
        projected = 0
        execution_ids: set[UUID] = (
            {execution_id} if execution_id is not None else set()
        ) | deadline_execution_ids
        for child in selected:
            try:
                await self._reconcile_child(child)
            except Exception as exc:
                logger.exception(
                    "Child reconciliation failed; recording durable failure",
                    extra={
                        "workflow_child_id": str(child.id),
                        "workflow_execution_id": str(child.execution_id),
                    },
                )
                await self._repository.record_reconcile_error(
                    child.id,
                    error=f"{type(exc).__name__}: {exc}",
                    failure_kind=FailureKind.TRANSIENT.value,
                    max_consecutive_failures=self._max_child_reconcile_failures,
                )
            execution_ids.add(child.execution_id)
            projected += 1
        for selected_execution_id in execution_ids:
            try:
                await self._project_join(selected_execution_id)
            except Exception:
                logger.exception(
                    "Execution join projection failed; will retry next cycle",
                    extra={"workflow_execution_id": str(selected_execution_id)},
                )
        return {"projected": projected, "executions": len(execution_ids)}

    async def _reconcile_child(self, child: ChildT) -> None:
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
                except WorkflowExecutionError as exc:
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
                    await self._repository.record_gate_report(
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

    async def cancel(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> ExecutionT:
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
        """Stop each owner-canceled parent, isolating one row's failure.

        A durable attempt/error trail (`record_parent_stop_error`) advances
        the row's position in `list_parent_stop_pending`'s ordering so a
        permanently failing stop cannot starve fresher rows, and gives up
        with a visible terminal outcome past the configured maximum instead
        of retrying forever.
        """
        pending = await self._repository.list_parent_stop_pending(limit=self._reconcile_limit)
        for execution in pending:
            if execution_id is not None and execution.id != execution_id:
                continue
            try:
                await self._continuation.stop_parent(execution)
                await self._repository.mark_parent_stopped(execution.id)
            except Exception as exc:
                logger.exception(
                    "Failed to stop canceled parent session; recording durable failure",
                    extra={"workflow_execution_id": str(execution.id)},
                )
                try:
                    await self._repository.record_parent_stop_error(
                        execution.id,
                        error=f"{type(exc).__name__}: {exc}",
                        max_attempts=self._max_parent_stop_failures,
                    )
                except Exception:
                    logger.exception(
                        "Failed to durably record parent-stop failure; will retry next cycle",
                        extra={"workflow_execution_id": str(execution.id)},
                    )

    async def _fail_expired_deadlines(self, *, execution_id: UUID | None = None) -> set[UUID]:
        """Fail children and executions whose own deadline has already elapsed.

        Deadlines were previously only a filter on launch eligibility: a
        `reserved` child past its deadline became unclaimable but never
        changed state, and a `launched`/`running` child was polled forever.
        Both left the parent join pending indefinitely. Each item is
        isolated so one failure does not block the others.
        """
        now = datetime.now(UTC)
        affected: set[UUID] = set()
        expired_children = await self._repository.list_deadline_expired_children(
            now=now, limit=self._reconcile_limit
        )
        for child in expired_children:
            if execution_id not in {None, child.execution_id}:
                continue
            try:
                await self._fail_expired_child(child, now=now)
            except Exception as exc:
                logger.exception(
                    "Failing deadline-expired child did not complete; recording durable failure",
                    extra={
                        "workflow_child_id": str(child.id),
                        "workflow_execution_id": str(child.execution_id),
                    },
                )
                await self._repository.record_reconcile_error(
                    child.id,
                    error=f"{type(exc).__name__}: {exc}",
                    failure_kind=FailureKind.DEADLINE_EXCEEDED.value,
                    max_consecutive_failures=self._max_child_reconcile_failures,
                )
            affected.add(child.execution_id)
        expired_executions = await self._repository.list_deadline_expired_executions(
            now=now, limit=self._reconcile_limit
        )
        for execution in expired_executions:
            if execution_id not in {None, execution.id}:
                continue
            try:
                await self._repository.update_execution_state(
                    execution.id,
                    state=ExecutionState.FAILED.value,
                    suspension_reason="deadline_exceeded",
                    expected_revision=execution.revision,
                )
            except Exception:
                logger.exception(
                    "Failed to fail deadline-expired execution; will retry next cycle",
                    extra={"workflow_execution_id": str(execution.id)},
                )
            affected.add(execution.id)
        return affected

    async def _fail_expired_child(self, child: ChildT, *, now: datetime) -> None:
        execution = await self._execution_for_child(child)
        handle = ChildTaskHandle(
            agent_id=child.agent_id,
            task_id=child.task_id,
            context_id=child.context_id,
        )
        if child.task_id:
            # The remote task must be stopped before the child is recorded as
            # failed. A failed cancellation raises: the caller records it and
            # retries, so Ting never reports dead a task that is still running.
            await self._gateway.cancel_child(
                handle,
                auth_token=self._gateway_token(execution, child),
            )
        observation = _normalize_observation(
            ChildTaskObservation(
                handle=handle,
                state=ChildExecutionState.FAILED,
                result=None,
                observed_at=now,
                event_id=f"deadline-exceeded:{child.id}",
                failure_kind=FailureKind.DEADLINE_EXCEEDED,
                error="Child execution deadline elapsed before completion",
            )
        )
        await self._repository.record_observation(child, observation)

    async def retry(
        self,
        execution_id: UUID,
        *,
        owner_id: str,
        tenant_id: str,
        child_key: str,
        attempt_id: UUID,
    ) -> ChildT:
        execution = await self._require_execution(
            execution_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        children = await self._repository.list_children(execution_id)
        matches = [
            child
            for child in children
            if child.generation == execution.current_generation and child.key == child_key
        ]
        if not matches:
            raise WorkflowExecutionError(f"unknown child key {child_key!r}")
        child = max(matches, key=lambda item: item.attempt)
        retry_attempt = make_child_retry(
            child,
            attempt_id=attempt_id,
            max_attempts=execution.policy.max_attempts,
            message_namespace=self._message_namespace,
        )
        if child.state == ChildExecutionState.BLOCKED:
            if not child.task_id:
                raise WorkflowExecutionError(
                    "blocked child has no task handle to stop before retry"
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
            except WorkflowExecutionError:
                raise
            except Exception as exc:
                raise WorkflowExecutionError(
                    "blocked child could not be stopped before retry"
                ) from exc
            if remote.state not in TERMINAL_CHILD_STATES:
                await self._repository.record_observation(child, remote)
                raise WorkflowExecutionError("blocked child is still active; retry was not created")
        retry_attempt = self._child_retry_reset(retry_attempt)
        return await self._repository.create_retry(
            child,
            retry_attempt,
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
    ) -> ChildT:
        if not answer.strip() or not message_id.strip():
            raise WorkflowExecutionError("child answer and messageId are required")
        execution = await self._require_execution(
            execution_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        children = await self._repository.list_children(execution_id)
        matches = [
            child
            for child in children
            if child.generation == execution.current_generation and child.key == child_key
        ]
        if not matches:
            raise WorkflowExecutionError(f"unknown child key {child_key!r}")
        child = max(matches, key=lambda item: item.attempt)
        if child.id != attempt_id:
            raise WorkflowExecutionError("child attempt is no longer current")
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
            raise WorkflowExecutionError("child is not awaiting a reply")
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

    def _past_children_join(self, execution: ExecutionT) -> bool:
        """True once the parent has already been resumed past the children join.

        A manual reconcile can race an execution that has since moved on from
        the children join to whatever phase follows it (the join stays ready
        forever once its winning generation's children are all terminal).
        Re-running the join projection from that later state would re-publish
        `resume_parent` and clobber it, so this is checked before doing either.
        """
        return execution.suspension_reason == CHILDREN_JOINED_SUSPENSION_REASON

    async def _project_join(self, execution_id: UUID) -> None:
        execution = await self._repository.get_internal(execution_id)
        if execution is None:
            raise WorkflowExecutionError("join parent execution was not found")
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
                expected_revision=execution.revision,
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
                    expected_revision=execution.revision,
                )
            return
        if join.ready:
            if self._past_children_join(execution):
                return
            current_attempts: dict[str, ChildT] = {}
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
                suspension_reason=CHILDREN_JOINED_SUSPENSION_REASON,
                expected_revision=execution.revision,
            )
            return
        if join.failed:
            blocker_revision = execution.blocker_revision
            await self._repository.update_execution_state(
                execution_id,
                state=ExecutionState.BLOCKED.value,
                suspension_reason="child_failed",
                expected_revision=execution.revision,
            )
            execution = await self._repository.get_internal(execution_id)
            if execution is None:
                raise WorkflowExecutionError("join parent execution was not found")
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
                expected_revision=execution.revision,
            )
            execution = await self._repository.get_internal(execution_id)
            if execution is None:
                raise WorkflowExecutionError("join parent execution was not found")
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
            expected_revision=execution.revision,
        )

    async def _notify_changed_blockers(
        self,
        execution: ExecutionT,
        children: list[ChildT],
        generation: int,
        *,
        snapshot_blocker_revision: int,
    ) -> None:
        if execution.blocker_revision != snapshot_blocker_revision:
            return
        if execution.blocker_revision <= execution.blocker_notified_revision:
            return
        current_attempts: dict[str, ChildT] = {}
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
                "failureKind": child.failure_kind,
                "detail": child.error,
                "pendingQuestions": [item.to_a2a_metadata() for item in child.pending_questions],
                "pendingGates": [item.to_a2a_metadata() for item in child.pending_gates],
            }
            for key, child in sorted(current_attempts.items())
            if child.state in {ChildExecutionState.BLOCKED, ChildExecutionState.FAILED}
        ]
        await self._continuation.notify_parent(
            execution,
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
    ) -> ExecutionT:
        execution = await self._repository.get(
            execution_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        if execution is None:
            raise WorkflowExecutionError("workflow execution was not found")
        return execution

    async def _execution_for_child(self, child: ChildT) -> ExecutionT:
        execution = await self._repository.get_internal(child.execution_id)
        if execution is None:
            raise WorkflowExecutionError("child parent execution was not found")
        return execution

    def _gateway_token(
        self,
        execution: ExecutionT,
        child: ChildT,
    ) -> str:
        if self._token_issuer is None:
            return ""
        claims = {
            "scopes": ["ting:workflow:launch"],
            "workflow_execution_id": str(execution.id),
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
            workload_subject=f"workflow-child:{child.id}",
            workload_name=execution.policy.coordinator_id,
            audiences=[],
            token_use="valkyrie_build",
            claims=claims,
        )
        return issued.token


def _normalize_observation(observation: ChildTaskObservation) -> ChildTaskObservation:
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
        raise WorkflowExecutionError(f"child task returned unknown state {raw_state!r}") from exc
    raw_failure = getattr(observation, "failure_kind", None)
    failure_kind = None
    if raw_failure:
        failure_kind = (
            raw_failure if isinstance(raw_failure, FailureKind) else FailureKind(raw_failure)
        )
    event_id = str(observation.event_id or "").strip()
    if not event_id:
        raise WorkflowExecutionError("child task observation is missing a stable eventId")
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


def _validate_reply_target(child: WorkflowChildExecution, metadata: dict[str, Any]) -> None:
    decision = str(metadata.get("gateDecision") or "").strip().lower()
    request_id = str(metadata.get("requestId") or "").strip()
    gate_id = str(metadata.get("gateId") or "").strip()
    if decision:
        if request_id:
            raise WorkflowExecutionError(
                "reply metadata must identify either a question or a gate, not both"
            )
        if decision not in {"approve", "request_changes"}:
            raise WorkflowExecutionError(
                'metadata.gateDecision must be "approve" or "request_changes"'
            )
        if not gate_id:
            raise WorkflowExecutionError("metadata.gateId is required for a gate reply")
        matching = [item for item in child.pending_gates if item.gate_id == gate_id]
        if len(matching) != 1:
            raise WorkflowExecutionError("gateId does not identify an exact outstanding gate")
        return

    if gate_id:
        raise WorkflowExecutionError("metadata.gateDecision is required for a gate reply")
    if not request_id:
        raise WorkflowExecutionError("metadata.requestId is required for a question reply")
    matching = [item for item in child.pending_questions if item.request_id == request_id]
    if len(matching) != 1:
        raise WorkflowExecutionError("requestId does not identify an exact outstanding question")

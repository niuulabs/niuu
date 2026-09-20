"""Application service for reserving reusable expanded-workflow generations."""

from __future__ import annotations

from typing import Protocol

from ting.domain.workflow_execution import (
    WorkflowChildExecution,
    WorkflowChildProposal,
    WorkflowExecution,
    WorkflowExecutionError,
    digest_json,
    make_children,
    validate_expansion,
)
from ting.ports.workflow_execution import WorkflowExecutionRepository


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

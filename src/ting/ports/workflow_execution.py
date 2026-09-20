"""Persistence boundary for the reusable expanded-workflow lifecycle."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from ting.domain.workflow_execution import (
    JoinStatus,
    WorkflowChildExecution,
    WorkflowExecution,
)


class WorkflowExecutionRepository[
    ExecutionT: WorkflowExecution,
    ChildT: WorkflowChildExecution,
](ABC):
    """Atomic ledger operations required by any expanded workflow domain."""

    @abstractmethod
    async def reserve_generation(
        self,
        execution: ExecutionT,
        children: tuple[ChildT, ...],
        *,
        plan_revision: str,
    ) -> ExecutionT:
        """Atomically reserve a generation and its child budgets."""

    @abstractmethod
    async def list_children(self, execution_id: UUID) -> list[ChildT]:
        """List every child attempt in stable order."""

    @abstractmethod
    async def get_child(self, child_id: UUID) -> ChildT | None:
        """Fetch one child attempt."""

    @abstractmethod
    async def seal_generation(self, execution_id: UUID, generation: int) -> None:
        """Seal the expected child set for a generation."""

    @abstractmethod
    async def join_status(self, execution_id: UUID, generation: int) -> JoinStatus:
        """Read the durable fan-in barrier."""

    @abstractmethod
    async def create_retry(
        self,
        child: ChildT,
        retry: ChildT,
        *,
        owner_id: str,
        tenant_id: str,
    ) -> ChildT:
        """Supersede the current attempt and reserve its bounded retry atomically."""

"""Trusted result checks required before a child can satisfy a workflow join."""

from abc import ABC, abstractmethod
from typing import Any

from niuu.domain.evidence import EvidenceValidationReport
from ting.domain.workflow_execution import WorkflowChildExecution, WorkflowExecution


class ChildEvidenceVerifier(ABC):
    @abstractmethod
    async def validate(
        self,
        execution: WorkflowExecution,
        child: WorkflowChildExecution,
        result: dict[str, Any],
    ) -> EvidenceValidationReport:
        """Verify current-attempt receipts against deployment-pinned acceptance policy."""


class ChildReviewAttestor(ABC):
    """Turn server-authenticated reviewer outcomes into signed review receipts."""

    @abstractmethod
    async def attest(
        self,
        execution: WorkflowExecution,
        child: WorkflowChildExecution,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a result containing only server-derived signed review receipts."""

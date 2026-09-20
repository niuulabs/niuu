"""Trusted evidence checks required before a child can satisfy a delivery join."""

from abc import ABC, abstractmethod
from typing import Any

from niuu.domain.delivery import EvidenceValidationReport
from ting.domain.delivery_execution import ChildExecution, DeliveryExecution


class ChildEvidenceVerifier(ABC):
    @abstractmethod
    async def validate(
        self,
        execution: DeliveryExecution,
        child: ChildExecution,
        result: dict[str, Any],
    ) -> EvidenceValidationReport:
        """Verify current-attempt receipts against deployment-pinned acceptance policy."""


class ChildReviewAttestor(ABC):
    """Turn server-authenticated reviewer outcomes into signed review receipts."""

    @abstractmethod
    async def attest(
        self,
        execution: DeliveryExecution,
        child: ChildExecution,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a result containing only server-derived signed review receipts."""

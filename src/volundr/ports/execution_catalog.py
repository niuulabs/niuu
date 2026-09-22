"""Port for the operator-owned compute execution catalog."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from volundr.domain.execution_catalog import ExecutionCatalogSnapshot


@runtime_checkable
class ExecutionCatalogPort(Protocol):
    """Read the complete immutable catalog from its configured source."""

    def load(self) -> ExecutionCatalogSnapshot:
        """Load and validate one internally consistent catalog snapshot."""
        ...

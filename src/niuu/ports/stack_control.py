"""Port for changing a single-host stack from inside the platform."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from niuu.domain.stack import (
    ApplyStatus,
    ExternalIntegrationValidation,
    ModelTestResult,
    StackView,
)


class StackControlPort(ABC):
    """Stage, apply and observe changes to the bundle this platform runs in."""

    @abstractmethod
    async def view(self) -> StackView:
        """Current settings, staged changes and the curated model list."""

    @abstractmethod
    async def stage(self, changes: dict[str, Any]) -> StackView:
        """Record *changes* (wizard-level keys) without applying them."""

    @abstractmethod
    async def discard(self) -> StackView:
        """Drop every staged change."""

    @abstractmethod
    async def apply(self) -> ApplyStatus:
        """Re-render the bundle with the staged changes and restart what changed."""

    @abstractmethod
    async def status(self) -> ApplyStatus:
        """Progress of the last apply plus the local model container's state."""

    @abstractmethod
    async def test_model(self) -> ModelTestResult:
        """Send one short completion to the local model; ValueError when it is not serving."""

    @abstractmethod
    async def validate_external_integration(
        self,
        source_dir: str,
        definition_files: list[str],
        manifest_file: str = "",
    ) -> ExternalIntegrationValidation:
        """Validate metadata and component classes in one machine-local package."""

    @abstractmethod
    async def external_integrations_root(self) -> str:
        """Directory in which Settings-managed external packages must live."""

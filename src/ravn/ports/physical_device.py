"""Port for resident physical-world capabilities."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.domain.physical_device import (
    PhysicalActionRequest,
    PhysicalActionResult,
    PhysicalCapability,
)


class PhysicalDevicePort(ABC):
    """Inspect, dry-run, or operate physical capabilities behind policy gates."""

    @abstractmethod
    async def list_capabilities(self) -> list[PhysicalCapability]:
        """Return capabilities available through this adapter."""
        raise NotImplementedError

    @abstractmethod
    async def read_telemetry(self, capability_id: str) -> PhysicalActionResult:
        """Read telemetry without operating a machine."""
        raise NotImplementedError

    @abstractmethod
    async def dry_run(self, request: PhysicalActionRequest) -> PhysicalActionResult:
        """Perform a configured no-side-effect simulation or validation."""
        raise NotImplementedError

    @abstractmethod
    async def execute(self, request: PhysicalActionRequest) -> PhysicalActionResult:
        """Perform a real physical operation after caller-side approval."""
        raise NotImplementedError


"""Port for probing a registered runtime instance's reachability."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from niuu.domain.models import RegisteredInstance


@dataclass(frozen=True)
class InstanceProbeResult:
    """Outcome of one reachability probe."""

    ok: bool
    status_code: int | None
    message: str


class InstanceProbePort(ABC):
    """Probes one registered instance and reports whether it answered."""

    @abstractmethod
    async def probe(self, instance: RegisteredInstance) -> InstanceProbeResult:
        """Probe *instance* and return the outcome. Never raises."""

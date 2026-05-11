"""Port for installing and controlling persisted Ravn wardens."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from ravn.warden.models import WardenSupervisor

if TYPE_CHECKING:
    from ravn.warden.models import WardenSpec


class WardenDeploymentError(RuntimeError):
    """Raised when a deployment backend cannot complete a lifecycle action."""


class WardenDeploymentResult(BaseModel):
    """Observed outcome of a deployment lifecycle action."""

    supervisor: WardenSupervisor
    runtime_state: Literal["active", "idle", "offline"] = "idle"


class WardenObservationResult(BaseModel):
    """Observed backend status for one persisted warden."""

    supervisor: WardenSupervisor


class WardenDeploymentPort(ABC):
    """Install and control one persisted warden on a deployment target."""

    @abstractmethod
    def install(
        self,
        spec: WardenSpec,
        *,
        warden_dir: Path,
        workspace_root: Path | None = None,
    ) -> WardenDeploymentResult:
        """Render and register the warden with its target runtime."""

    @abstractmethod
    def start(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        """Start an already installed warden."""

    @abstractmethod
    def stop(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        """Stop an installed warden while keeping its deployment artifacts."""

    @abstractmethod
    def uninstall(self, spec: WardenSpec, *, warden_dir: Path) -> WardenDeploymentResult:
        """Remove deployment artifacts while keeping the persisted warden spec."""

    @abstractmethod
    def observe(self, spec: WardenSpec, *, warden_dir: Path) -> WardenObservationResult:
        """Refresh live backend status without changing the desired lifecycle state."""

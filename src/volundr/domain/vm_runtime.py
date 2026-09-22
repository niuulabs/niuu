"""Guest runtime control, independent of the machine infrastructure provider."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from niuu.ports.credentials import CredentialStorePort
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.compute import ComputeLease, MachineBootstrap
from volundr.domain.models import Session, SessionSpec


class VmRuntimeUnavailableError(RuntimeError):
    """The guest SSH connection is not established; readiness may be retried."""


class VmRuntimeStageError(RuntimeError):
    """A runtime startup command failed at an explicitly non-sensitive stage."""

    def __init__(self, stage: str, diagnostic: str = ""):
        self.stage = stage
        self.diagnostic = diagnostic
        suffix = f": {diagnostic}" if diagnostic else ""
        super().__init__(f"Guest runtime startup failed at stage {stage}{suffix}")


@runtime_checkable
class CredentialAwareVmRuntime(Protocol):
    """Optional composition hook for runtimes that inject native credentials."""

    def configure_credentials(self, credential_store: CredentialStorePort) -> None:
        """Attach the configured credential port without placing values in runtime config."""


@runtime_checkable
class ProfileAwareVmRuntime(Protocol):
    """Route legacy profile-selected operations before a durable plan is available."""

    def machine_bootstrap_for(
        self, profile: str, defaults: MachineBootstrap
    ) -> MachineBootstrap: ...

    def session_bootstrap_for(
        self,
        profile: str,
        session: Session,
        spec: SessionSpec,
        machine: MachineBootstrap,
    ) -> MachineBootstrap: ...

    def bootstrap_for(
        self,
        profile: str,
        session: Session,
        spec: SessionSpec,
        defaults: MachineBootstrap,
    ) -> MachineBootstrap: ...


class VmRuntime(ABC):
    @abstractmethod
    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        """Prepare an unbound guest without any session credentials or data."""

    @abstractmethod
    def session_bootstrap(
        self, session: Session, spec: SessionSpec, machine: MachineBootstrap
    ) -> MachineBootstrap:
        """Attach session configuration while retaining the guest's pinned identity."""

    @abstractmethod
    async def warm(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        """Verify guest readiness and cache runtime dependencies without starting a session."""

    @abstractmethod
    def bootstrap(
        self, session: Session, spec: SessionSpec, defaults: MachineBootstrap
    ) -> MachineBootstrap:
        """Validate the supported session contract and prepare first-boot content."""

    @abstractmethod
    async def prepare(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        """Wait for authenticated guest control before touching session data."""

    @abstractmethod
    async def start(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        """Idempotently restore retained data and start this allocation's runtime."""

    @abstractmethod
    async def target(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> SessionProxyTarget:
        """Resolve an authenticated transport to the guest, including after restart."""

    @abstractmethod
    async def ready(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> bool: ...

    @abstractmethod
    async def stop(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        """Stop execution and durably preserve session data before machine disposal."""

    @property
    def supports_reuse(self) -> bool:
        return False

    async def recycle(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        """Remove stopped session resources after durable archival; idempotent."""
        raise NotImplementedError("This runtime does not support in-place guest reuse")

    @abstractmethod
    async def close(self) -> None:
        """Close controller connections, without deleting machines or session data."""

"""Guest runtime control, independent of the machine infrastructure provider."""

from abc import ABC, abstractmethod

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.compute import ComputeLease, MachineBootstrap
from volundr.domain.models import Session, SessionSpec


class VmRuntimeUnavailableError(RuntimeError):
    """The guest SSH connection is not established; readiness may be retried."""


class VmRuntime(ABC):
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

    @abstractmethod
    async def close(self) -> None:
        """Close controller connections, without deleting machines or session data."""

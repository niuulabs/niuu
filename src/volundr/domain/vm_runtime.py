"""Guest runtime control, independent of the machine infrastructure provider."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from niuu.ports.credentials import CredentialStorePort
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.compute import ComputeLease, MachineBootstrap, SlotLease
from volundr.domain.guest_credentials import GuestCredentialDelivery
from volundr.domain.models import Session, SessionSpec
from volundr.domain.session_runtimes import SessionGrant, SessionRuntimeKind


@dataclass(frozen=True)
class SlotLimits:
    """What one separated slot may use, and where its uids start; None is "no limit"."""

    uid_base: int
    cpu: float | None
    memory_mib: int | None
    pids: int | None


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
class CredentialDeliveryVmRuntime(Protocol):
    """Optional composition hook for runtimes that deliver guest credentials."""

    def configure_credential_delivery(self, delivery: GuestCredentialDelivery) -> None:
        """Attach the configured ``GuestCredentialDelivery``."""


#: ``(session_id, key, label)``: a guest runtime began the named step of a start.
StartupStepReport = Callable[[UUID, str, str], None]


@runtime_checkable
class StartupReportingVmRuntime(Protocol):
    """A runtime that names each step of ``start`` as it begins it."""

    def report_startup_to(self, report: StartupStepReport) -> None:
        """Call ``report`` at the start of every step of a session's ``start``."""


@runtime_checkable
class ForgeSessionVmRuntime(Protocol):
    """A runtime that writes each launch's Forge session credential into its guest.

    The credential changes on every start, so it is staged in memory for the next
    start and never stored with the allocation's bootstrap.
    """

    @property
    def delivers_forge_session_token(self) -> bool:
        """Whether this runtime writes the Forge session credential into its guest."""

    def stage_forge_session(self, session_id: UUID, credential: IssuedForgeSessionToken) -> None:
        """Hold ``credential`` for the next start of ``session_id``."""

    def discard_forge_session(self, session_id: UUID) -> None:
        """Forget a staged credential."""


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
    async def prepare(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        """Wait for authenticated guest control before touching session data."""

    @abstractmethod
    async def start(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        """Idempotently restore retained data and start this allocation's runtime."""

    @abstractmethod
    async def target(self, lease: SlotLease, bootstrap: MachineBootstrap) -> SessionProxyTarget:
        """Resolve an authenticated transport to the guest, including after restart."""

    @abstractmethod
    async def ready(self, lease: SlotLease, bootstrap: MachineBootstrap) -> bool: ...

    @abstractmethod
    async def stop(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        """Stop execution and durably preserve session data before machine disposal."""

    @property
    def supports_slots(self) -> bool:
        """Whether one machine can serve several sessions at once.

        The contract is a per-slot user, network namespace, cgroup and runtime state keyed
        by (allocation, slot). No runtime declares it yet, so a pool with
        ``slots_per_machine > 1`` is refused at startup and on an admin change.
        """
        return False

    @property
    def supports_reuse(self) -> bool:
        return False

    @property
    def slot_limits(self) -> SlotLimits | None:
        """The per-slot limits sessions run under, when the runtime separates slots."""
        return None

    @property
    def requires_root_daemon(self) -> bool:
        """Whether sessions run under a root daemon on the machine (root Docker, say).

        Such a daemon is root-equivalent for anything that reaches it, so a pool of
        registered hosts refuses the runtime: a host is not disposable like a VM.
        """
        return False

    @property
    def session_runtime(self) -> SessionRuntimeKind | None:
        """What a session runs in under this runtime, as a launch offers it.

        None means the runtime cannot be offered as a launch choice.
        """
        return None

    @property
    def session_grants(self) -> tuple[SessionGrant, ...]:
        """Extra access this runtime can give one session, enforced in placement."""
        return ()

    async def recycle(self, lease: SlotLease, bootstrap: MachineBootstrap) -> None:
        """Remove stopped session resources after durable archival; idempotent."""
        raise NotImplementedError("This runtime does not support in-place guest reuse")

    @abstractmethod
    async def close(self) -> None:
        """Close controller connections, without deleting machines or session data."""

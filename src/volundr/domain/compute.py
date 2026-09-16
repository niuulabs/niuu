"""Provider-neutral VM allocation contracts.

An allocation is infrastructure, not a Forge session. Provider credentials and
provider-specific manifests never cross this boundary.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BootstrapFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(pattern=r"^/[^\x00\n]+$")
    content: str = Field(repr=False)
    permissions: str = Field(default="0600", pattern=r"^0[0-7]{3}$")


class MachineBootstrap(BaseModel):
    """Portable first-boot content; an adapter chooses its delivery format."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    files: tuple[BootstrapFile, ...] = ()
    commands: tuple[tuple[str, ...], ...] = Field(default=(), repr=False)
    ssh_authorized_keys: tuple[str, ...] = ()


class MachineRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allocation_id: UUID
    profile: str = Field(min_length=1)
    bootstrap: MachineBootstrap = Field(default_factory=MachineBootstrap, repr=False)


class MachineState(StrEnum):
    PROVISIONING = "provisioning"
    RUNNING = "running"
    STOPPED = "stopped"
    DELETING = "deleting"
    FAILED = "failed"


class Machine(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allocation_id: UUID
    resource_id: str
    state: MachineState
    addresses: tuple[str, ...] = ()


class MachineProviderError(RuntimeError):
    """An infrastructure operation failed without changing providers."""


class MachineOwnershipError(MachineProviderError):
    """A resource does not belong to this installation/allocation."""


class MachineProvider(ABC):
    """Idempotent VM API. False from delete means cleanup is still in progress.

    Allocation IDs are durable caller-supplied operation keys. Repeating create
    with the same key and request resumes that operation; changing its request
    is an error. Inventory includes partially created owned allocations so
    reconciliation can recover a timeout before create returned.
    """

    @abstractmethod
    async def create(self, request: MachineRequest) -> Machine: ...

    @abstractmethod
    async def get(self, allocation_id: UUID) -> Machine | None: ...

    @abstractmethod
    async def list(self) -> list[Machine]: ...

    @abstractmethod
    async def delete(self, allocation_id: UUID) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...


class LeaseState(StrEnum):
    PROVISIONING = "provisioning"
    READY = "ready"
    BUSY = "busy"
    DRAINING = "draining"
    RELEASED = "released"
    FAILED = "failed"


class ComputeLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    pool_id: str
    session_id: UUID
    tenant_id: str
    owner_id: str
    profile: str
    request_fingerprint: str
    bootstrap_ref: str | None = None
    runtime_data_started: bool = False
    state: LeaseState = LeaseState.PROVISIONING
    machine: Machine | None = None
    error: str | None = None


class ComputeCapacityError(RuntimeError):
    """All allocation slots are reserved, including pending cleanup."""


class ComputeLeaseBusyError(RuntimeError):
    """Another controller is operating on this allocation; retry the same operation."""


class ComputeLeaseRepository(ABC):
    @abstractmethod
    async def reserve(self, lease: ComputeLease, limit: int) -> ComputeLease:
        """Atomically reserve pool capacity or return the same live session claim."""

    @abstractmethod
    async def get(self, lease_id: UUID) -> ComputeLease | None: ...

    @abstractmethod
    async def list(self, pool_id: str) -> list[ComputeLease]: ...

    @abstractmethod
    async def active_for_session(self, pool_id: str, session_id: UUID) -> ComputeLease | None: ...

    @abstractmethod
    async def save(self, lease: ComputeLease) -> None: ...

    @abstractmethod
    def operation(self, lease_id: UUID):
        """Async context manager holding exclusive operation ownership until exit.

        Ownership must survive long provider calls and release after process
        death. save() inside this context must use its owning connection.
        """

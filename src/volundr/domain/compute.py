"""Provider-neutral VM allocation contracts.

An allocation is infrastructure, not a Forge session. Provider credentials and
provider-specific manifests never cross this boundary.
"""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from volundr.domain.execution_catalog import ResolvedExecutionPlan


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
    status_detail: str | None = Field(default=None, max_length=512)


class MachineProviderError(RuntimeError):
    """An infrastructure operation failed without changing providers."""


class MachineOwnershipError(MachineProviderError):
    """A resource does not belong to this installation/allocation."""


class MachineProfile(BaseModel):
    """Adapter-selected public details; never include credentials or bootstrap content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    revision: str = Field(min_length=1, repr=False)
    details: dict[str, str]

    @field_validator("name", "revision")
    @classmethod
    def reject_blank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Machine profile name and revision must not be blank")
        return value


class MachineProvider(ABC):
    """Idempotent VM API. False from delete means cleanup is still in progress.

    Allocation IDs are durable caller-supplied operation keys. Repeating create
    with the same key and request resumes that operation; changing its request
    is an error. Inventory includes partially created owned allocations so
    reconciliation can recover a timeout before create returned.
    """

    @abstractmethod
    async def profiles(self) -> tuple[MachineProfile, ...]:
        """List configured choices and safe, human-readable infrastructure details."""
        ...

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
    IDLE = "idle"
    QUARANTINED = "quarantined"
    READY = "ready"
    BUSY = "busy"
    DRAINING = "draining"
    RELEASED = "released"
    FAILED = "failed"


class ComputeLease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    pool_id: str
    session_id: UUID | None
    tenant_id: str
    owner_id: str
    profile: str
    profile_revision: str = ""
    request_fingerprint: str
    provider_binding: str = ""
    provider_fingerprint: str = ""
    execution_plan: ResolvedExecutionPlan | None = None
    host_preparation_started: bool = False
    host_preparation_result: dict[str, JsonValue] | None = None
    bootstrap_ref: str | None = None
    bootstrap_owner: str | None = None
    session_bootstrap_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provision_deadline: datetime | None = None
    idle_since: datetime | None = None
    retry_after: datetime | None = None
    failures: int = 0
    runtime_data_started: bool = False
    runtime_started: bool = False
    stop_requested: bool = False
    state: LeaseState = LeaseState.PROVISIONING
    machine: Machine | None = None
    error: str | None = None


class ComputeCapacityError(RuntimeError):
    """All allocation slots are reserved, including pending cleanup."""


class ComputeLeaseBusyError(RuntimeError):
    """Another controller is operating on this allocation; retry the same operation."""


class ComputeLeaseRepository(ABC):
    @abstractmethod
    async def policy(self, pool_id: str, default: "ComputePoolPolicy") -> "ComputePoolPolicy": ...

    @abstractmethod
    async def set_policy(self, pool_id: str, policy: "ComputePoolPolicy") -> None: ...

    @abstractmethod
    async def bind(self, lease: ComputeLease) -> ComputeLease:
        """Bind an idle allocation while its operation lock is held; enforce pool admission."""

    @abstractmethod
    async def quarantine(self, lease: ComputeLease) -> None:
        """Record owned infrastructure missing from the ledger, even above capacity."""

    @abstractmethod
    async def reserve(self, lease: ComputeLease, limit: int) -> ComputeLease:
        """Atomically reserve pool capacity or return the same live session claim."""

    @abstractmethod
    async def get(self, lease_id: UUID) -> ComputeLease | None: ...

    @abstractmethod
    async def list(self, pool_id: str, *, include_released: bool = True) -> list[ComputeLease]: ...

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


class ComputePoolPolicy(BaseModel):
    """Provider-neutral, durable admission and standby policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: str = Field(min_length=1)
    max_machines: int = Field(gt=0)
    reuse_policy: Literal["replace", "reuse"] = "replace"
    warm_min: int = Field(default=0, ge=0)
    max_provisioning: int = Field(default=1, gt=0)
    idle_timeout_seconds: float = Field(default=3600, gt=0)
    provisioning_timeout_seconds: float = Field(default=600, gt=0)
    paused: bool = False
    drain: bool = False

    @model_validator(mode="after")
    def capacity_bounds(self):
        if self.warm_min > self.max_machines:
            raise ValueError("Warm minimum cannot exceed maximum machines")
        return self

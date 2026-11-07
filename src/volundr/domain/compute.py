"""Provider-neutral VM allocation contracts.

An allocation is infrastructure, not a Forge session. Provider credentials and
provider-specific manifests never cross this boundary.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol
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
    phase: str | None = Field(
        default=None,
        description="The provider's own words for where the machine is (e.g. 'Starting on hnoss')",
    )


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
    """Machine states.

    A machine is never stored ``BUSY``: one in service is ``IDLE`` with a live
    ``ComputeSlot`` binding. ``BUSY`` stays in the enum for rows written before the
    slot ledger and as a derived display state (see ``display_state``).
    """

    PROVISIONING = "provisioning"
    IDLE = "idle"
    QUARANTINED = "quarantined"
    READY = "ready"
    BUSY = "busy"
    DRAINING = "draining"
    RELEASED = "released"
    FAILED = "failed"


class ComputeLease(BaseModel):
    """A machine (allocation). Its sessions are ``ComputeSlot`` bindings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    pool_id: str
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
    slots: int = Field(default=1, ge=1, description="Session slots; fixed when reserved")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provision_deadline: datetime | None = None
    idle_since: datetime | None = Field(
        default=None, description="When the machine last became empty"
    )
    retry_after: datetime | None = None
    failures: int = 0
    state: LeaseState = LeaseState.PROVISIONING
    machine: Machine | None = None
    error: str | None = None


class SlotState(StrEnum):
    STARTING = "starting"  # bound, runtime start unconfirmed
    BUSY = "busy"  # runtime ready
    FAILED = "failed"  # start failed or its start deadline passed
    DRAINING = "draining"  # archived; this stop deletes the machine
    RETIRED = "retired"  # ended; the slot is not reused
    RELEASED = "released"  # ended and cleaned; free again


#: Slot states in which a binding still holds a session.
LIVE_SLOT_STATES = frozenset(
    {SlotState.STARTING, SlotState.BUSY, SlotState.FAILED, SlotState.DRAINING}
)


class ComputeSlot(BaseModel):
    """One session bound to one slot of a machine.

    There is no quarantine state: ``stop_requested`` on a live binding means failed or
    pending cleanup, and its machine is offered to no one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    allocation_id: UUID
    slot: int = Field(ge=0)
    pool_id: str
    session_id: UUID
    tenant_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    execution_plan: ResolvedExecutionPlan | None = None
    session_bootstrap_ref: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    start_deadline: datetime | None = None
    runtime_data_started: bool = False
    runtime_started: bool = False
    stop_requested: bool = False
    state: SlotState = SlotState.STARTING
    error: str | None = None


class AllocationRef(Protocol):
    """What guest access and host preparation need of a machine: no session."""

    @property
    def id(self) -> UUID: ...

    @property
    def pool_id(self) -> str: ...

    @property
    def profile(self) -> str: ...

    @property
    def machine(self) -> Machine | None: ...


class SlotLease(BaseModel):
    """A session's view of its machine: what runtimes and credential delivery receive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    allocation: ComputeLease
    binding: ComputeSlot

    @property
    def id(self) -> UUID:
        """The allocation id."""
        return self.allocation.id

    @property
    def pool_id(self) -> str:
        return self.allocation.pool_id

    @property
    def profile(self) -> str:
        return self.allocation.profile

    @property
    def machine(self) -> Machine | None:
        return self.allocation.machine

    @property
    def execution_plan(self) -> ResolvedExecutionPlan | None:
        """The session's plan, not the host-provisioning plan of the machine."""
        return self.binding.execution_plan

    @property
    def slot(self) -> int:
        return self.binding.slot

    @property
    def session_id(self) -> UUID:
        return self.binding.session_id

    @property
    def owner_id(self) -> str:
        return self.binding.owner_id

    @property
    def tenant_id(self) -> str:
        return self.binding.tenant_id


class ComputeCapacityError(RuntimeError):
    """All allocation slots are reserved, including pending cleanup."""


class ComputeStandbyTargetMetError(ComputeCapacityError):
    """The pool already holds its warm minimum; expected, not a failure."""


class ComputePoolFullError(ComputeCapacityError):
    """The pool is at ``max_machines`` (pending cleanup included); a cap, so it is loud."""


class ComputeProvisioningLimitError(ComputeCapacityError):
    """The pool is already provisioning its maximum number of machines.

    Unlike a full pool, this clears on its own as starts finish, so a session
    start waits for it instead of failing.
    """


class ComputeLeaseBusyError(RuntimeError):
    """Another controller is operating on this allocation; retry the same operation."""


class ComputeLeaseRepository(ABC):
    @abstractmethod
    async def policy(self, pool_id: str, default: "ComputePoolPolicy") -> "ComputePoolPolicy": ...

    @abstractmethod
    async def set_policy(self, pool_id: str, policy: "ComputePoolPolicy") -> None: ...

    @abstractmethod
    async def bind(self, machine: ComputeLease, binding: ComputeSlot) -> SlotLease:
        """Bind a session to a free slot of an idle machine while its operation lock is held.

        Re-checks pool admission, that the machine is idle with a usable slot, that the
        slot is free and that the session owns no other live binding.
        """

    @abstractmethod
    async def quarantine(self, lease: ComputeLease) -> None:
        """Record owned infrastructure missing from the ledger, even above capacity."""

    @abstractmethod
    async def reserve(
        self, lease: ComputeLease, limit: int, binding: ComputeSlot | None = None
    ) -> tuple[ComputeLease, ComputeSlot | None]:
        """Atomically reserve pool capacity, with the session's binding, or return the
        same live session claim."""

    @abstractmethod
    async def get(self, lease_id: UUID) -> ComputeLease | None: ...

    @abstractmethod
    async def active_for_session(self, pool_id: str, session_id: UUID) -> SlotLease | None:
        """The session's live binding with its machine (draining bindings included)."""

    @abstractmethod
    async def save(self, lease: ComputeLease) -> None:
        """Persist a machine; saving a released machine retires its live bindings."""

    @abstractmethod
    async def save_slot(self, binding: ComputeSlot) -> None:
        """Persist a binding while its machine's operation lock is held."""

    @abstractmethod
    async def get_slot(self, slot_id: UUID) -> ComputeSlot | None: ...

    @abstractmethod
    async def slot(self, allocation_id: UUID, slot: int) -> ComputeSlot | None:
        """The live occupant of a slot, if any."""

    @abstractmethod
    async def slots(self, allocation_id: UUID) -> list[ComputeSlot]:
        """Every binding a machine has had, oldest first."""

    @abstractmethod
    async def list_slots(self, pool_id: str, *, live_only: bool = True) -> list[ComputeSlot]: ...

    # ``list`` shadows the builtin inside this class: keep it after every ``list[...]`` hint.
    @abstractmethod
    async def list(self, pool_id: str, *, include_released: bool = True) -> list[ComputeLease]: ...

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
    slots_per_machine: int = Field(default=1, gt=0)
    max_sessions_per_machine: int | None = Field(default=None, gt=0)
    max_machine_age_seconds: float | None = Field(default=None, gt=0)
    reuse_policy: Literal["replace", "reuse"] = "replace"
    warm_min: int = Field(default=0, ge=0)
    max_provisioning: int = Field(default=1, gt=0)
    idle_timeout_seconds: float = Field(default=3600, gt=0)
    provisioning_timeout_seconds: float = Field(default=600, gt=0)
    paused: bool = False
    drain: bool = False

    @model_validator(mode="after")
    def capacity_bounds(self):
        if self.warm_min > self.max_machines * self.slots_per_machine:
            raise ValueError("Warm minimum cannot exceed maximum machines times slots per machine")
        has_drain_limit = (
            self.max_sessions_per_machine is not None or self.max_machine_age_seconds is not None
        )
        if self.reuse_policy == "reuse" and has_drain_limit:
            raise ValueError(
                "Drain limits apply to replace; clear them or set reuse_policy: replace"
            )
        if (
            self.max_sessions_per_machine is not None
            and self.max_sessions_per_machine < self.slots_per_machine
        ):
            raise ValueError(
                "Sessions per machine before replacement cannot be below slots per machine"
            )
        return self


def session_limit(policy: ComputePoolPolicy, machine: ComputeLease) -> int | None:
    """Sessions a machine may serve before it drains; None when it never drains for count."""
    if policy.reuse_policy == "reuse":
        return None
    if policy.max_sessions_per_machine is not None:
        return policy.max_sessions_per_machine
    return machine.slots


_OFFERABLE_STATES = frozenset({LeaseState.PROVISIONING, LeaseState.READY, LeaseState.IDLE})


def usable_slots(
    machine: ComputeLease,
    bindings: Sequence[ComputeSlot],
    policy: ComputePoolPolicy,
    now: datetime,
) -> int:
    """Slots a new session may take now.

    ``bindings`` is every binding the machine has had: a released slot is free again
    but still counts as served against the session limit.
    """
    if machine.state not in _OFFERABLE_STATES:
        return 0
    if any(b.stop_requested for b in bindings if b.state in LIVE_SLOT_STATES):
        return 0
    age = policy.max_machine_age_seconds
    if age is not None and (now - machine.created_at).total_seconds() >= age:
        return 0
    free = machine.slots - sum(b.state is not SlotState.RELEASED for b in bindings)
    limit = session_limit(policy, machine)
    if limit is not None:
        free = min(free, limit - len(bindings))
    return max(0, free)


def next_free_slot(machine: ComputeLease, bindings: Sequence[ComputeSlot]) -> int:
    """The lowest slot index no binding occupies."""
    occupied = {b.slot for b in bindings if b.state is not SlotState.RELEASED}
    for index in range(machine.slots):
        if index not in occupied:
            return index
    raise ComputeCapacityError(f"Allocation {machine.id} has no free slot")


def display_state(machine: ComputeLease, bindings: Sequence[ComputeSlot]) -> LeaseState:
    """The state an operator sees; one slot looks as a one-session machine always did."""
    live = [b for b in bindings if b.state in LIVE_SLOT_STATES]
    if any(b.state is SlotState.BUSY for b in live):
        return LeaseState.BUSY
    if any(b.state is SlotState.FAILED for b in live):
        return LeaseState.FAILED
    if machine.state is LeaseState.IDLE and any(b.state is SlotState.STARTING for b in live):
        return LeaseState.READY
    return machine.state

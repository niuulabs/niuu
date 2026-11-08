"""Provider-neutral VM allocation contracts.

An allocation is infrastructure, not a Forge session. Provider credentials and
provider-specific manifests never cross this boundary.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from niuu.domain.tags import matches_tags
from volundr.domain.execution_catalog import ResolvedExecutionPlan


class BootstrapFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(pattern=r"^/[^\x00\n]+$")
    content: str = Field(repr=False)
    permissions: str = Field(default="0600", pattern=r"^0[0-7]{3}$")


class HostPlatform(StrEnum):
    """A host's operating system as ``uname -s`` names it, lowercased; fixed at join."""

    LINUX = "linux"
    DARWIN = "darwin"


class MachineBootstrap(BaseModel):
    """Portable first-boot content; an adapter chooses its delivery format."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    files: tuple[BootstrapFile, ...] = ()
    commands: tuple[tuple[str, ...], ...] = Field(default=(), repr=False)
    ssh_authorized_keys: tuple[str, ...] = ()


class HostSelector(BaseModel):
    """Tags a machine must carry, with ``niuu.domain.tags.matches_tags`` semantics.

    The same ``tags`` + ``match`` vocabulary that targets a whole Forge targets one of its
    machines: ``all`` needs every tag, ``any`` needs one. An empty selector matches every
    machine.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tags: tuple[str, ...] = ()
    match: Literal["all", "any"] = "all"

    @field_validator("tags")
    @classmethod
    def reject_blank_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for tag in value:
            validate_tag(tag)
        return tuple(sorted(set(value)))

    def accepts(self, tags: Iterable[str]) -> bool:
        return matches_tags(tags, self.tags, self.match)


def validate_tag(tag: str) -> str:
    """A tag is one non-empty word: no whitespace or commas, which would split it."""
    if not tag or tag != tag.strip() or any(c.isspace() or c == "," for c in tag):
        raise ValueError(f"Tag {tag!r} must be non-empty with no whitespace or commas")
    return tag


class MachineRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allocation_id: UUID
    profile: str = Field(min_length=1)
    bootstrap: MachineBootstrap = Field(default_factory=MachineBootstrap, repr=False)
    selector: HostSelector = Field(
        default_factory=HostSelector,
        description="Tags the machine must carry; only a fixed inventory can choose by them",
    )


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
    slots: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Sessions this machine serves at once, when the provider sets it per machine (a "
            "registered host); None means the pool's slots_per_machine"
        ),
    )
    tags: tuple[str, ...] = Field(default=(), description="Tags host selectors match against")
    accepting: bool = Field(
        default=True,
        description=(
            "False while the provider wants no new sessions placed here (a draining, "
            "quarantined or disconnected host); running sessions are not affected"
        ),
    )
    platform: HostPlatform | None = Field(
        default=None,
        description=(
            "The machine's operating system, when the provider knows it (a registered "
            "host's, fixed at join); a runtime per platform is chosen by it"
        ),
    )
    host_public_key: str | None = Field(
        default=None,
        description=(
            "The machine's own SSH host key (OpenSSH format), when the provider vouches for "
            "one it did not generate: a registered host's key from its pairing"
        ),
    )
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_proxy_command: tuple[str, ...] = Field(
        default=(),
        description=(
            "argv OpenSSH runs to reach the machine's sshd (ProxyCommand), when a connector "
            "carries the bytes; empty means connect to addresses[0] directly"
        ),
    )


#: Where a bootstrap built for several host platforms keeps each platform's files. Only
#: this module reads it: a runtime is always handed one platform's bootstrap.
PLATFORM_BOOTSTRAP_ROOT = "/etc/niuu/platform-bootstraps"
_REFUSED = ".refused"


def encode_platform_bootstraps(
    built: dict[HostPlatform, MachineBootstrap], refused: dict[HostPlatform, str]
) -> MachineBootstrap:
    """One bootstrap holding each platform's own, and why the others refused the session.

    Built before Forge knows which host a session gets. Commands and authorized keys are
    the machine's, not a platform's: every platform's bootstrap must carry the same.
    """
    if set(built) & set(refused):
        raise ValueError("A platform cannot both build and refuse a bootstrap")
    bootstraps = list(built.values())
    if any(
        (item.commands, item.ssh_authorized_keys)
        != (bootstraps[0].commands, bootstraps[0].ssh_authorized_keys)
        for item in bootstraps
    ):
        raise ValueError(
            "The platforms' runtimes disagree on the machine's boot commands or authorized "
            "keys; a host execution's runtimes must share its guest access"
        )
    files = [
        BootstrapFile(
            path=f"{PLATFORM_BOOTSTRAP_ROOT}/{platform.value}{item.path}",
            content=item.content,
            permissions=item.permissions,
        )
        for platform, bootstrap in sorted(built.items())
        for item in bootstrap.files
    ]
    files += [
        BootstrapFile(path=f"{PLATFORM_BOOTSTRAP_ROOT}/{platform.value}{_REFUSED}", content=reason)
        for platform, reason in sorted(refused.items())
    ]
    first = bootstraps[0] if bootstraps else MachineBootstrap()
    return MachineBootstrap(
        files=tuple(files),
        commands=first.commands,
        ssh_authorized_keys=first.ssh_authorized_keys,
    )


def _encoded(bootstrap: MachineBootstrap) -> bool:
    return any(item.path.startswith(PLATFORM_BOOTSTRAP_ROOT + "/") for item in bootstrap.files)


def platform_bootstrap(
    bootstrap: MachineBootstrap, platform: HostPlatform | None
) -> MachineBootstrap:
    """``platform``'s own bootstrap; a bootstrap built for one platform is returned as is."""
    if not _encoded(bootstrap):
        return bootstrap
    if platform is None:
        raise ValueError(
            "This bootstrap was built for several host platforms, but the machine records "
            "none; its host must join again"
        )
    prefix = f"{PLATFORM_BOOTSTRAP_ROOT}/{platform.value}/"
    files = tuple(
        BootstrapFile(
            path="/" + item.path[len(prefix) :],
            content=item.content,
            permissions=item.permissions,
        )
        for item in bootstrap.files
        if item.path.startswith(prefix)
    )
    return bootstrap.model_copy(update={"files": files})


def refused_platforms(bootstrap: MachineBootstrap) -> dict[HostPlatform, str]:
    """The platforms whose runtime refused the session, with each one's reason."""
    found = {}
    for item in bootstrap.files:
        for platform in HostPlatform:
            if item.path == f"{PLATFORM_BOOTSTRAP_ROOT}/{platform.value}{_REFUSED}":
                found[platform] = item.content
    return found


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

    @property
    def fixed_inventory(self) -> bool:
        """Whether ``create`` claims an existing machine instead of making one.

        A fixed inventory never boots on demand: ``claimable`` says how many machines a
        ``create`` could take now, and the pool reports a shortfall instead of reserving
        a claim that cannot be served.
        """
        return False

    async def claimable(self, profile: str, selector: HostSelector) -> int:
        """Machines ``create`` could claim now for ``profile`` and ``selector``."""
        raise NotImplementedError(f"{type(self).__name__} is not a fixed inventory")

    async def tag_sets(self) -> tuple[tuple[str, ...], ...]:
        """The distinct tag sets of machines that can take sessions; none carry tags here."""
        return ()


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
    host_selector: HostSelector = Field(default_factory=HostSelector)
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

    @abstractmethod
    async def machine_slots(self, pool_id: str) -> dict[UUID, list[ComputeSlot]]:
        """Every binding of each machine in the pool that is not released, by machine.

        What placement and capacity need: a released machine's history never counts.
        """

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


def machine_expired(machine: ComputeLease, policy: ComputePoolPolicy, now: datetime) -> bool:
    """Whether the machine reached ``max_machine_age_seconds`` and so takes no new sessions."""
    age = policy.max_machine_age_seconds
    if age is None:
        return False
    return (now - machine.created_at).total_seconds() >= age


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
    if machine.machine is not None and not machine.machine.accepting:
        return 0
    if any(b.stop_requested for b in bindings if b.state in LIVE_SLOT_STATES):
        return 0
    if machine_expired(machine, policy, now):
        return 0
    free = machine.slots - sum(b.state is not SlotState.RELEASED for b in bindings)
    limit = session_limit(policy, machine)
    if limit is not None:
        free = min(free, limit - len(bindings))
    return max(0, free)


def bindings_by_allocation(bindings: Iterable[ComputeSlot]) -> dict[UUID, list[ComputeSlot]]:
    """Group bindings by the machine they are on, keeping their order."""
    grouped: dict[UUID, list[ComputeSlot]] = {}
    for binding in bindings:
        grouped.setdefault(binding.allocation_id, []).append(binding)
    return grouped


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

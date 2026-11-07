"""Registered hosts: machines that join a Forge as fixed compute instead of being created.

A registered host is inventory of one Forge's compute pool. It is claimed by an allocation
instead of created, and cleaned and audited instead of deleted. How Forge reaches its sshd is
a ``HostConnector``; whatever carries the bytes, the session inside is SSH, pinned to the
host's own key on one side and Forge's key on the other.

See ``docs/design/bm-compute-hosts.md``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from volundr.domain.compute import HostSelector, Machine, validate_tag


class HostPlatform(StrEnum):
    """A host's operating system as ``uname -s`` names it, lowercased; fixed at join."""

    LINUX = "linux"
    DARWIN = "darwin"


#: What a host runs on unless its runtime or connector declares more.
LINUX_ONLY: frozenset[HostPlatform] = frozenset({HostPlatform.LINUX})


class HostHealth(StrEnum):
    """What the last check of the host found. Written only by checks."""

    PENDING = "pending"  # registered; Forge has not reached it yet
    HEALTHY = "healthy"  # reached with the pinned key; the audit found nothing
    QUARANTINED = "quarantined"  # the audit or a check found something; an operator acts
    UNREACHABLE = "unreachable"  # no connection, or the key does not match the registered one


class HostService(StrEnum):
    """What the operator wants of the host. Written only by operator actions."""

    ACTIVE = "active"
    DRAINING = "draining"  # no new sessions; out of service once empty
    OUT = "out"  # out of service: nothing runs, nothing is placed
    REMOVING = "removing"  # drained, then removed once empty


class HostDisplayState(StrEnum):
    """The one state an operator sees, derived from health, service and the hold."""

    PENDING = "pending"
    READY = "ready"
    IN_SERVICE = "in_service"
    DRAINING = "draining"
    OUT_OF_SERVICE = "out_of_service"
    QUARANTINED = "quarantined"
    UNREACHABLE = "unreachable"
    REMOVING = "removing"


class RegisteredHost(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    pool_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    tags: tuple[str, ...] = ()
    profile: str = Field(min_length=1, description="Provider profile its execution names")
    execution: str = Field(min_length=1, description="The one execution it runs (id@revision)")
    connector: str = Field(min_length=1, description="Binding id of the connector it joined with")
    connection: dict[str, JsonValue] = Field(
        default_factory=dict, description="The connector's own data for this host"
    )
    host_public_key: str = Field(min_length=1, description="OpenSSH ssh-ed25519 public key")
    slots: int | None = Field(default=None, ge=1, description="None until the admin sets it")
    facts: dict[str, JsonValue] = Field(default_factory=dict)
    health: HostHealth = HostHealth.PENDING
    service: HostService = HostService.ACTIVE
    allocation_id: UUID | None = None
    findings: tuple[str, ...] = ()
    registered_by: str = Field(min_length=1)
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_checked_at: datetime | None = None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_tags(value)

    @property
    def platform(self) -> HostPlatform:
        """The platform the host joined as; the join records it, and checks never change it."""
        value = self.facts.get("platform")
        try:
            return HostPlatform(value)
        except ValueError:
            raise ValueError(
                f"Host {self.name} has no known platform fact ({value!r}); remove it and join "
                "it again with this Forge's join.sh"
            ) from None

    @property
    def claimable(self) -> bool:
        """Whether a new allocation may hold this host now."""
        return (
            self.health is HostHealth.HEALTHY
            and self.service is HostService.ACTIVE
            and self.allocation_id is None
            and self.slots is not None
        )

    @property
    def accepting(self) -> bool:
        """Whether new sessions may be placed on this host while it is held."""
        return self.health is HostHealth.HEALTHY and self.service is HostService.ACTIVE

    def display_state(self) -> HostDisplayState:
        if self.service is HostService.REMOVING:
            return HostDisplayState.REMOVING
        if self.health is HostHealth.UNREACHABLE:
            return HostDisplayState.UNREACHABLE
        if self.health is HostHealth.QUARANTINED:
            return HostDisplayState.QUARANTINED
        if self.service is HostService.DRAINING:
            return HostDisplayState.DRAINING
        if self.service is HostService.OUT:
            return HostDisplayState.OUT_OF_SERVICE
        if self.health is HostHealth.PENDING:
            return HostDisplayState.PENDING
        if self.allocation_id is not None:
            return HostDisplayState.IN_SERVICE
        return HostDisplayState.READY


def normalize_tags(tags: Iterable[str]) -> tuple[str, ...]:
    """Validated, de-duplicated and sorted, so equal tag sets compare equal."""
    return tuple(sorted({validate_tag(tag) for tag in tags}))


class HostPairingCode(BaseModel):
    """A single-use code an admin minted for one host to join with.

    Everything the host will be (pool, runtime, connector, tags, slots) is fixed here by
    the admin and never taken from the joining host.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    code_hash: str = Field(min_length=64, max_length=64)
    pool_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    execution: str = Field(min_length=1)
    profile: str = Field(min_length=1)
    connector: str = Field(min_length=1)
    tags: tuple[str, ...] = ()
    slots: int | None = Field(default=None, ge=1)
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    consumed_at: datetime | None = None
    consumed_by_host_id: UUID | None = None
    revoked_at: datetime | None = None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_tags(value)


class HostConflictError(RuntimeError):
    """The joining host's name or key is already registered."""


class RegisteredHostRepository(ABC):
    @abstractmethod
    async def create_pairing_code(self, code: HostPairingCode) -> None: ...

    @abstractmethod
    async def pairing_code(self, code_hash: str) -> HostPairingCode | None:
        """The code if it can still be used: unconsumed, unrevoked, unexpired."""

    @abstractmethod
    async def pairing_codes(self, pool_id: str) -> list[HostPairingCode]:
        """Codes of the pool that can still be used, newest first."""

    @abstractmethod
    async def revoke_pairing_code(self, pool_id: str, code_id: UUID) -> bool: ...

    @abstractmethod
    async def consume_and_register(
        self, code_hash: str, host: RegisteredHost
    ) -> RegisteredHost | None:
        """Consume the code and insert the host in one transaction.

        None when the code cannot be used; ``HostConflictError`` when the name or key is
        taken, in which case the code stays unconsumed.
        """

    @abstractmethod
    async def get(self, host_id: UUID) -> RegisteredHost | None: ...

    @abstractmethod
    async def held_by(self, allocation_id: UUID) -> RegisteredHost | None: ...

    @abstractmethod
    async def list(self, pool_id: str) -> list[RegisteredHost]: ...

    @abstractmethod
    async def save(self, host: RegisteredHost) -> None:
        """Persist the operator's fields (name, tags, slots, service) and nothing else.

        Checks and holds have their own writes, so an operator edit never overwrites a
        concurrent check result or the hold.
        """

    @abstractmethod
    async def record_check(
        self,
        host_id: UUID,
        *,
        facts: dict[str, JsonValue],
        findings: tuple[str, ...],
        health: HostHealth,
        checked_at: datetime,
    ) -> RegisteredHost:
        """Persist a check's result and return the host as stored."""

    @abstractmethod
    async def claim(
        self, pool_id: str, allocation_id: UUID, profile: str, selector: HostSelector
    ) -> RegisteredHost | None:
        """Atomically hold one claimable host for the allocation, or return the host it
        already holds. Hosts with the most slots are claimed first."""

    @abstractmethod
    async def release(self, allocation_id: UUID, update: RegisteredHost) -> None:
        """End the allocation's hold and store ``update`` (health, findings) in one write."""

    @abstractmethod
    async def delete(self, host_id: UUID) -> bool:
        """Remove a host that no allocation holds."""


class SshDial(BaseModel):
    """How OpenSSH reaches a host's sshd: an address and port, or a ProxyCommand."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    address: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    proxy_command: tuple[str, ...] = ()


class HostConnectionStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    connected: bool
    target: str = Field(description="What an operator reads: an address, or a tunnel")
    since: datetime | None = None
    last_error: str | None = None


class HostJoinSetup(BaseModel):
    """What ``join.sh`` sets up on the host for this connector, besides Forge's key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(min_length=1)
    authorized_key_options: str = Field(
        description="OpenSSH authorized_keys options for Forge's key on this host"
    )
    files: tuple[Mapping[str, str], ...] = Field(
        default=(), description="Extra files: {'path', 'content', 'mode'}"
    )
    commands: tuple[tuple[str, ...], ...] = ()

    @field_validator("files")
    @classmethod
    def plain_paths(cls, value: tuple[Mapping[str, str], ...]) -> tuple[Mapping[str, str], ...]:
        # niuu-host-leave removes them by a space-separated list.
        for item in value:
            path = item.get("path", "")
            if not path.startswith("/") or any(c.isspace() or c in "'\"$`" for c in path):
                raise ValueError(f"Setup file path {path!r} must be absolute and plain")
            if not {"content", "mode"} <= set(item):
                raise ValueError("A setup file names its path, content and mode")
        return value


class HostConnector(ABC):
    """How Forge reaches a registered host's sshd. Carries bytes; never authenticates SSH.

    The provider and the runtimes never see a connector: it only decides the ``SshDial``
    that ends up on the machine record, which ``SshGuestAccess`` dials.
    """

    @property
    @abstractmethod
    def kind(self) -> str: ...

    @property
    @abstractmethod
    def label(self) -> str:
        """What the Add host form calls this way of connecting."""

    @abstractmethod
    def join_setup(self, host: RegisteredHost) -> HostJoinSetup:
        """What join.sh sets up on the host for this connector."""

    @property
    def platforms(self) -> frozenset[HostPlatform]:
        """The platforms ``join_setup`` works on; a host on any other is refused at join."""
        return LINUX_ONLY

    @abstractmethod
    async def admit(
        self, host: RegisteredHost, offered: Mapping[str, JsonValue]
    ) -> dict[str, JsonValue]:
        """Make the joining host reachable and return its connection data; idempotent.

        ``offered`` is what the host reported at join (its addresses); the connector decides
        what it trusts of it.
        """

    @abstractmethod
    def dial(self, host: RegisteredHost) -> SshDial:
        """Where OpenSSH connects; raises ``HostDisconnectedError`` when there is no way."""

    @abstractmethod
    async def status(self, host: RegisteredHost) -> HostConnectionStatus: ...

    @abstractmethod
    async def revoke(self, host: RegisteredHost) -> None:
        """Stop admitting the host and cut any live connection; idempotent."""

    async def close(self) -> None:
        return None


class HostDisconnectedError(RuntimeError):
    """The connector has no way to reach the host right now."""


class HostInspector(ABC):
    """Reads a host's facts and audits it for anything a slot left behind."""

    @abstractmethod
    async def facts(self, host: RegisteredHost) -> dict[str, JsonValue]: ...

    @abstractmethod
    async def audit(
        self, host: RegisteredHost, *, live_slots: Iterable[int], clean: bool
    ) -> tuple[str, ...]:
        """Findings; empty means clean. ``clean`` first clears the allocation's markers and
        is only allowed with no live slots."""

    @abstractmethod
    def machine(self, host: RegisteredHost, allocation_id: UUID | None = None) -> Machine:
        """The machine record guest access dials for this host."""

    @abstractmethod
    async def leave(self, host: RegisteredHost) -> None:
        """Run the host's own ``niuu-host-leave``: remove Forge's key and what join set up."""

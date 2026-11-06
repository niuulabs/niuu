"""How a VM guest receives its session's credentials, and how it proves who it is.

A session pod gets ServiceAccount tokens from the kubelet and its Forge session
credential from a projection. A VM guest gets the same credentials through one
configured ``GuestCredentialDelivery``:

* SSH push: the controller writes and refreshes them over the pinned SSH channel.
* Metadata: a guest agent fetches them from Forge's metadata endpoint, writes the
  same files, and serves them on the guest's link-local metadata address. Forge
  answers only a guest that a configured ``GuestIdentity`` authenticates.

Delivery and identity are chosen in configuration per backend; neither switches
to another at runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from volundr.domain.compute import ComputeLease, MachineBootstrap

#: Where the guest agent serves credentials to the guest's own processes.
METADATA_ADDRESS = "169.254.169.254"
#: Header a guest process must send, so a forwarded request (SSRF) cannot read it.
METADATA_HEADER = "Niuu-Metadata"
#: Forge route the guest agent calls (POST, JSON body {"allocation_id": "<uuid>"}).
METADATA_CREDENTIALS_PATH = "/api/v1/internal/compute/metadata/credentials"


class GuestAuthenticationError(Exception):
    """A metadata request did not prove an allocation identity (always one 401)."""


class GuestUnavailableReason(StrEnum):
    """Why an authenticated allocation gets no credentials (the 409's ``error``)."""

    NOT_RUNNING = "allocation_not_running"
    STOPPING = "session_stopping"
    UNBOUND = "no_bound_session"
    NOT_LAUNCHED = "session_not_launched"
    NO_SERVICE_ACCOUNT = "no_service_account"
    NO_FORGE_SESSION = "forge_session_unavailable"


class GuestCredentialUnavailableError(Exception):
    """An authenticated allocation has no running session to issue credentials for.

    ``reason`` is what the guest is told; the message carries detail for Forge's log.
    """

    def __init__(self, reason: GuestUnavailableReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


@dataclass(frozen=True)
class GuestToken:
    """One projected ServiceAccount token file, as a pod volume declares it."""

    path: str
    audience: str
    expiration_seconds: int


@dataclass(frozen=True)
class GuestTokenVolume:
    volume: str
    #: Guest directory bind-mounted read-only where the pod mounts the volume.
    directory: str
    tokens: tuple[GuestToken, ...]


@dataclass(frozen=True)
class GuestCredentialPlan:
    """What a bound session needs delivered, decoded from its launch payload."""

    service_account: str
    token_volumes: tuple[GuestTokenVolume, ...] = ()
    #: Whether the broker reads a Forge session credential from ``forge_session_directory``.
    forge_session: bool = False
    forge_session_directory: str = ""
    forge_session_file: str = "token"

    @property
    def empty(self) -> bool:
        return not self.token_volumes and not self.forge_session

    @classmethod
    def from_launch(cls, launch: Mapping) -> GuestCredentialPlan:
        return cls(
            service_account=str(launch.get("service_account") or ""),
            token_volumes=tuple(
                GuestTokenVolume(
                    volume=str(volume["volume"]),
                    directory=str(volume["directory"]),
                    tokens=tuple(
                        GuestToken(
                            path=str(token["path"]),
                            audience=str(token["audience"]),
                            expiration_seconds=int(token["expiration_seconds"]),
                        )
                        for token in volume["tokens"]
                    ),
                )
                for volume in launch.get("token_volumes") or ()
            ),
            forge_session=bool(launch.get("forge_session")),
            forge_session_directory=str(launch.get("forge_session_directory") or ""),
            forge_session_file=str(launch.get("forge_session_file") or "token"),
        )


@dataclass(frozen=True)
class IssuedGuestToken:
    volume: str
    path: str
    token: str
    expires_at: datetime

    def __repr__(self) -> str:
        return f"IssuedGuestToken(volume={self.volume!r}, path={self.path!r})"


@dataclass(frozen=True)
class IssuedGuestForgeSession:
    token: str
    grants: tuple[str, ...]

    def __repr__(self) -> str:
        return f"IssuedGuestForgeSession(grants={self.grants!r})"


@dataclass(frozen=True)
class GuestCredentials:
    """The metadata endpoint's answer for one authenticated allocation."""

    service_account_tokens: tuple[IssuedGuestToken, ...] = ()
    forge_session: IssuedGuestForgeSession | None = None


@dataclass(frozen=True)
class GuestRequest:
    """The parts of an HTTP request a ``GuestIdentity`` may authenticate."""

    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes = b""


@dataclass(frozen=True)
class GuestContainers:
    """Containers a delivery adds to the session, in the pod-shaped launch layout.

    Each container dict uses the VM runtime's translated form (name, image,
    command, args, environment, user, mounts).
    """

    init_containers: tuple[dict, ...] = ()
    sidecars: tuple[dict, ...] = ()
    #: Guest tmpfs directories the containers need created before they start.
    directories: tuple[str, ...] = ()
    #: Extra launch fields the guest start program needs (e.g. ``metadata_address``).
    launch: dict = field(default_factory=dict)


class GuestIdentity(ABC):
    """Proves a metadata request comes from one allocation's guest.

    The same adapter configures the guest side (what goes into the machine
    bootstrap, what the guest agent signs with) and verifies at Forge.
    """

    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        """Add what the guest needs to prove itself; delivered privately by the provider.

        Built before the allocation exists, so it may not depend on its id;
        ``register`` binds what it adds to the allocation later.
        """
        return defaults

    @abstractmethod
    def agent_auth(self, allocation_id: UUID) -> dict:
        """JSON-compatible signer configuration for the guest agent."""

    def agent_mounts(self) -> tuple[dict, ...]:
        """Read-only guest paths the agent container needs (key files, sockets)."""
        return ()

    async def register(self, lease: ComputeLease, machine: MachineBootstrap) -> None:
        """Make the allocation's identity valid before its guest asks (idempotent).

        ``machine`` is the allocation's stored machine bootstrap, holding whatever
        ``machine_bootstrap`` added.
        """

    async def unregister(self, lease: ComputeLease, machine: MachineBootstrap) -> None:
        """Retire the allocation's identity when its session stops (idempotent)."""

    @abstractmethod
    async def authenticate(self, request: GuestRequest) -> UUID:
        """Return the allocation the request proves, or raise ``GuestAuthenticationError``."""

    async def close(self) -> None:
        """Release client resources."""


class GuestFiles(Protocol):
    """The runtime's authenticated file channel into one allocation's guest."""

    async def write_files(
        self, lease: ComputeLease, bootstrap: MachineBootstrap, items: list[dict]
    ) -> None:
        """Atomically write ``{"directory", "name", "content"}`` items (0644, dirs 0755)."""

    async def file_exists(
        self, lease: ComputeLease, bootstrap: MachineBootstrap, path: str
    ) -> bool:
        """Whether ``path`` is a regular file in the guest."""


class GuestCredentialDelivery(ABC):
    """Gets a bound session's credentials into its guest."""

    @property
    def delivers_forge_session_token(self) -> bool:
        return True

    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        """Add what an unbound guest needs for delivery (e.g. its identity)."""
        return defaults

    def session_containers(self, plan: GuestCredentialPlan, *, image: str) -> GuestContainers:
        """Containers the guest runs for delivery, given the session's Skuld image."""
        return GuestContainers()

    def stage_forge_session(self, session_id: UUID, credential: IssuedForgeSessionToken) -> None:
        """The launch credential the next start of ``session_id`` delivers, if pushed."""

    def discard_forge_session(self, session_id: UUID) -> None:
        """Forget a staged launch credential."""

    @abstractmethod
    async def start(
        self,
        lease: ComputeLease,
        bootstrap: MachineBootstrap,
        plan: GuestCredentialPlan,
        launch: dict,
        files: GuestFiles,
    ) -> None:
        """Before Skuld starts: make every planned credential reachable in the guest.

        May update ``launch`` (the start program's payload), e.g. ``relaunch``.
        """

    async def resume(
        self,
        lease: ComputeLease,
        bootstrap: MachineBootstrap,
        plan: GuestCredentialPlan,
        files: GuestFiles,
    ) -> None:
        """The runtime routes to a running guest: resume any refresh this delivery owns."""

    async def stop(self, lease: ComputeLease, bootstrap: MachineBootstrap) -> None:
        """The session stops: end refresh and retire what ``start`` set up."""

    async def close(self) -> None:
        """Release resources; guests keep their current credentials."""

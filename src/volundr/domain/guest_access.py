"""Authenticated control of an allocated guest, independent of its provider."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import BinaryIO

from niuu.ports.session_proxy import SessionProxyTarget
from volundr.domain.compute import AllocationRef, MachineBootstrap


@dataclass(frozen=True)
class GuestCommandResult:
    """Sanitized result of a command executed through authenticated guest access."""

    exit_code: int
    stdout: bytes = b""


class GuestAccess(ABC):
    """Provider-neutral guest command, stream, and tunnel operations."""

    @property
    @abstractmethod
    def principal(self) -> str:
        """Account authenticated by this access binding."""

    @abstractmethod
    def configure_trust(self, *, principal: str, mode: str, kwargs: dict) -> None:
        """Apply the resolved access contract or reject unsupported trust semantics."""

    @abstractmethod
    def validate_host_preparation(self) -> None:
        """Reject legacy bootstrap behavior that cannot accompany a host recipe."""

    @abstractmethod
    def machine_bootstrap(self, defaults: MachineBootstrap) -> MachineBootstrap:
        """Add only the minimum identity material needed for guest access."""

    @abstractmethod
    async def ensure(self, lease: AllocationRef, bootstrap: MachineBootstrap) -> None:
        """Wait for access and prove that the allocation's pinned identity is reusable."""

    @abstractmethod
    async def execute(
        self,
        lease: AllocationRef,
        bootstrap: MachineBootstrap,
        command: str,
        *,
        data: bytes | None = None,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
        capture_stdout: bool = False,
        timeout_seconds: float | None = None,
        expected_exit_codes: tuple[int, ...] = (0,),
        safe_error_prefix: str | None = None,
        safe_detail_prefix: str | None = None,
    ) -> GuestCommandResult:
        """Execute one fixed remote command without exposing remote stderr."""

    @abstractmethod
    async def open_tunnel(
        self,
        lease: AllocationRef,
        bootstrap: MachineBootstrap,
        *,
        remote_port: int,
        reverse_bind_host: str,
        reverse_port: int,
        controller_host: str,
        controller_port: int,
        remote_host: str = "127.0.0.1",
        key: str | None = None,
    ) -> SessionProxyTarget:
        """Open or reuse the runtime and callback tunnel kept under ``key``.

        ``key`` defaults to the allocation; a VM serving several sessions keys
        each slot's tunnel apart. ``remote_host`` is where the guest end of the
        forward connects, its loopback unless the runtime listens elsewhere.
        """

    @abstractmethod
    async def close_tunnel(self, lease_id: str) -> None:
        """Close the tunnel kept under ``lease_id`` (an allocation or slot key)."""

    @abstractmethod
    async def close(self) -> None:
        """Close all controller-side resources without changing the guest."""

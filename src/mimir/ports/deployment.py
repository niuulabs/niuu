"""Deployment control independent of Kubernetes and Flux.

Two things are deployed through this port:

- **knowledge instances** (``deploy``/``list_deployments``/…), the named mounts a
  tenant creates from the knowledge registry; and
- **memory systems** (``provision_memory_system``/``memory_system``/
  ``memory_systems``), one gbrain per org for the memory gateway
  (``docs/design/memory/README.md``, Tenancy). The gateway provisions one on an org's
  first use when ``memory.provisioning`` is configured. A target that can do this sets
  ``provisions_memory_systems``; the gateway refuses at startup to use one that does
  not.

A memory system's record lives with what was deployed (the HelmRelease and its
Secrets on Kubernetes, a private directory on this computer), so the record and the
deployment cannot drift apart, and creating it is atomic on the target: two replicas
asking for one org at once deploy it once.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field


class DeploymentRequest(BaseModel):
    tenant_id: str = Field(default="", exclude=True)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    backend: Literal["gbrain"]
    target: str = ""
    secret: str = Field(default="", pattern=r"^([a-z0-9][a-z0-9.-]*)?$")


class MemorySystemPhase(StrEnum):
    """Where an org's provisioned memory system stands."""

    #: Deployed and not answering yet (or not deployed yet): ask again later.
    PREPARING = "preparing"
    #: Answering, with its connection known.
    READY = "ready"
    #: Provisioning failed and will not finish by itself; ``message`` names the remedy.
    FAILED = "failed"


@dataclass(frozen=True)
class MemorySystemRecord:
    """One org's provisioned memory system, as its deployment target sees it.

    ``mcp_url``, ``admin_token`` and ``signing_key`` are set only when ``phase`` is
    ``READY``: gbrain's MCP endpoint, its owner bootstrap credential
    (``GBRAIN_ADMIN_BOOTSTRAP_TOKEN``) and the key that seals attribution, created once
    for the system and kept.
    """

    tenant_id: str
    name: str
    phase: MemorySystemPhase
    message: str
    backend: Literal["gbrain"] = "gbrain"
    #: True only on the answer to the call that began provisioning it.
    started: bool = False
    mcp_url: str = ""
    admin_token: str = field(default="", repr=False)
    signing_key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if self.phase is not MemorySystemPhase.READY:
            return
        missing = [
            name
            for name, value in (
                ("mcp_url", self.mcp_url),
                ("admin_token", self.admin_token),
                ("signing_key", self.signing_key),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"memory system {self.name} of org {self.tenant_id!r} is ready without "
                f"{', '.join(missing)}"
            )


class MemoryProvisioningError(RuntimeError):
    """The target could not provision or read an org's memory system."""


def memory_system_key(tenant_id: str) -> str:
    """The stable, name-safe key of *tenant_id*'s memory system (16 hex characters).

    Tenant ids are free text; deployment names are not. The full tenant id is kept
    beside the key (an annotation, a record field) and checked on every read, so two
    tenants whose keys collided would be refused, never mixed.
    """
    if not tenant_id:
        raise MemoryProvisioningError("a memory system belongs to an org; the tenant id is empty")
    return hashlib.sha256(tenant_id.encode()).hexdigest()[:16]


def memory_system_name(tenant_id: str) -> str:
    """The gateway-local name of *tenant_id*'s provisioned memory system."""
    return f"memory-{memory_system_key(tenant_id)}"


class KnowledgeDeploymentPort(ABC):
    #: Whether this target implements the memory-system methods below.
    provisions_memory_systems: ClassVar[bool] = False

    @abstractmethod
    async def list_deployments(self, *, tenant_id: str = "") -> dict[str, Any]: ...

    @abstractmethod
    async def deploy(self, request: DeploymentRequest) -> dict[str, Any]: ...

    def mounted_ports(self) -> list[dict]:
        return []

    async def discover_mounts(self, tenant_id: str, authorization: str = "") -> list[dict]:
        return [m for m in self.mounted_ports() if m.get("tenant_id", "") == tenant_id]

    async def inspect_deployment(self, name: str, *, tenant_id: str = "") -> dict:
        raise NotImplementedError("Deployment inspection is not supported by this target")

    async def control(self, name: str, action: str, *, tenant_id: str = "") -> dict:
        raise NotImplementedError("Lifecycle control is not supported by this target")

    # -- memory systems --------------------------------------------------

    async def provision_memory_system(self, tenant_id: str) -> MemorySystemRecord:
        """Make sure *tenant_id* has a memory system and say where it stands.

        Idempotent and safe to call concurrently, from this process and from other
        replicas: the first call starts the deployment, every call returns the current
        record, and the org is deployed once. Returns at once; a deployment still in
        progress is ``PREPARING``.

        Raises:
            MemoryProvisioningError: the target cannot be asked or refused the request.
        """
        raise NotImplementedError("This target does not provision memory systems")

    async def memory_system(self, tenant_id: str) -> MemorySystemRecord | None:
        """*tenant_id*'s memory system, or None when none was provisioned. Never deploys."""
        raise NotImplementedError("This target does not provision memory systems")

    async def memory_systems(self) -> list[MemorySystemRecord]:
        """Every memory system this target provisioned, in any phase."""
        raise NotImplementedError("This target does not provision memory systems")

    async def close(self) -> None:
        pass

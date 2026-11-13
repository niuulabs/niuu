"""The memory systems the gateway talks to, and which org's layers live on which.

Two sources, in this order:

1. **Static configuration**, built once at startup from ``memory.systems`` (dynamic
   adapters with a gateway ``name``) and ``memory.tenants`` (tenant -> system). It is
   the operator's override: an org named there always uses that system and is never
   provisioned. Everything that can be checked is checked here, so a misconfiguration
   stops the service instead of failing a session's first call: a system that cannot
   be built, two systems with one name, a tenant naming a missing system, and a system
   that cannot hold the layers placed on it.
2. **Provisioning** (``memory.provisioning``, optional): an org not in
   ``memory.tenants`` gets its own memory system on its first use, deployed through the
   knowledge deployment port (:class:`ProvisionedSystems`). Off, an unmapped org is
   refused naming ``memory.tenants``, as before.

Every way an org's system can be unusable is a :class:`MemorySystemUnavailableError`,
never an empty answer: not configured, still being prepared (with how long to wait),
or failed to provision.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from mimir.exceptions import MimirConfigurationError
from mimir.memory.models import SYSTEM_LAYER_KINDS
from mimir.ports.deployment import (
    KnowledgeDeploymentPort,
    MemoryProvisioningError,
    MemorySystemPhase,
    MemorySystemRecord,
)
from mimir.ports.memory_backend import MemoryBackend, require_capabilities
from niuu.observability import get_observability
from niuu.utils import import_class

logger = logging.getLogger(__name__)

_SYSTEMS = "memory.systems"
_TENANTS = "memory.tenants"
_PROVISIONING = "memory.provisioning"

#: Counter of provisioning outcomes, by ``outcome`` (started, preparing, ready, failed).
PROVISIONING_COUNTER = "mimir.memory.provisioning"


class MemorySystemUnavailableError(Exception):
    """An org's memory system cannot serve this request."""


class TenantNotConfiguredError(MemorySystemUnavailableError):
    """A session's org has no memory system."""


class MemoryPreparingError(MemorySystemUnavailableError):
    """The org's memory system is being provisioned; ask again after ``retry_after``."""

    def __init__(self, message: str, *, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class MemoryProvisioningFailedError(MemorySystemUnavailableError):
    """The org's memory system could not be provisioned, or not be read back."""


#: Builds the backend of a ready provisioned system.
BackendFactory = Callable[[MemorySystemRecord], MemoryBackend]


class ProvisionedSystems:
    """Memory systems provisioned per org through a :class:`KnowledgeDeploymentPort`.

    The deployment target is the registry: it holds each org's record with what it
    deployed, so every replica reads the same answer and none keeps state of its own
    beyond a cache of ready backends. Within this process a per-org lock makes
    concurrent first requests ask the target once; across replicas the target's own
    atomic create does the same.

    Args:
        deployment: a target whose ``provisions_memory_systems`` is true.
        build: makes the backend of a ready record.
        retry_after_seconds: what a request for an org still being prepared is told to
            wait (the ``Retry-After`` it gets).
    """

    def __init__(
        self,
        deployment: KnowledgeDeploymentPort,
        *,
        build: BackendFactory,
        retry_after_seconds: float,
    ) -> None:
        if not deployment.provisions_memory_systems:
            raise MimirConfigurationError(
                f"{_PROVISIONING}.deployment adapter {type(deployment).__name__} does not "
                "provision memory systems; use mimir.adapters.local."
                "LocalKnowledgeDeploymentAdapter or mimir.adapters.flux."
                "FluxKnowledgeDeploymentAdapter, or remove memory.provisioning"
            )
        if retry_after_seconds <= 0:
            raise MimirConfigurationError(f"{_PROVISIONING}.retry_after_seconds must be positive")
        self._deployment = deployment
        self._build = build
        self._retry_after = retry_after_seconds
        self._backends: dict[str, tuple[str, MemoryBackend]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.counts: Counter[str] = Counter()

    def _count(self, outcome: str, tenant_id: str) -> None:
        self.counts[outcome] += 1
        get_observability().count(
            PROVISIONING_COUNTER,
            attributes={"outcome": outcome},
            description=(
                "Requests that met an org's provisioned memory system, by outcome: started "
                "(this request began provisioning), preparing (refused while it is "
                "prepared), ready (first use after it became ready), failed."
            ),
        )
        if outcome == "failed":
            logger.error("memory: provisioning the memory system of org %r failed", tenant_id)

    async def resolve(
        self, tenant_id: str, *, provision: bool, reserved: Sequence[str] = ()
    ) -> tuple[str, MemoryBackend]:
        """*tenant_id*'s provisioned system, provisioning it first when *provision*.

        Raises:
            TenantNotConfiguredError: nothing was provisioned and *provision* is false.
            MemoryPreparingError: it is being prepared.
            MemoryProvisioningFailedError: provisioning failed, or the target could not
                be asked.
        """
        cached = self._backends.get(tenant_id)
        if cached is not None:
            return cached
        lock = self._locks.setdefault(tenant_id, asyncio.Lock())
        async with lock:
            cached = self._backends.get(tenant_id)
            if cached is not None:
                return cached
            try:
                if provision:
                    record = await self._deployment.provision_memory_system(tenant_id)
                else:
                    record = await self._deployment.memory_system(tenant_id)
            except MemoryProvisioningError as exc:
                self._count("failed", tenant_id)
                raise MemoryProvisioningFailedError(
                    f"the memory system of org {tenant_id!r} could not be provisioned: {exc}"
                ) from exc
            if record is None:
                raise TenantNotConfiguredError(
                    f"org {tenant_id!r} has no memory system yet: it is provisioned on the "
                    "first use by a member of the org (a session or a person), or name one in "
                    f"{_TENANTS} ({tenant_id}: <system name>)"
                )
            return self._accept(record, reserved)

    def _accept(
        self, record: MemorySystemRecord, reserved: Sequence[str]
    ) -> tuple[str, MemoryBackend]:
        tenant_id = record.tenant_id
        match record.phase:
            case MemorySystemPhase.PREPARING:
                self._count("started" if record.started else "preparing", tenant_id)
                raise MemoryPreparingError(
                    f"memory for org {tenant_id!r} is being prepared ({record.message}); "
                    f"try again in {self._retry_after:g} s",
                    retry_after=self._retry_after,
                )
            case MemorySystemPhase.FAILED:
                self._count("failed", tenant_id)
                raise MemoryProvisioningFailedError(
                    f"the memory system {record.name} of org {tenant_id!r} could not be "
                    f"provisioned: {record.message}"
                )
        if record.name in reserved:
            self._count("failed", tenant_id)
            raise MemoryProvisioningFailedError(
                f"the provisioned memory system of org {tenant_id!r} is named {record.name}, "
                f"which a system in {_SYSTEMS} already uses; rename that system"
            )
        try:
            backend = self._build(record)
            for kind in SYSTEM_LAYER_KINDS:
                require_capabilities(record.name, backend, kind)
        except (MimirConfigurationError, TypeError, ValueError) as exc:
            self._count("failed", tenant_id)
            raise MemoryProvisioningFailedError(
                f"the memory system {record.name} of org {tenant_id!r} is ready but cannot be "
                f"used: {exc}"
            ) from exc
        self._count("ready", tenant_id)
        logger.info("memory: org %r uses its provisioned memory system %s", tenant_id, record.name)
        self._backends[tenant_id] = (record.name, backend)
        return record.name, backend

    async def ready(
        self, reserved: Sequence[str] = ()
    ) -> tuple[list[tuple[str, MemoryBackend]], list[MemoryProvisioningFailedError]]:
        """Every provisioned system that is ready, by name (the retention sweep's list),
        and the error of each ready one that cannot be used, so one bad system does not
        hide the others.

        Raises MemoryProvisioningFailedError when the target cannot list them at all.
        """
        try:
            records = await self._deployment.memory_systems()
        except MemoryProvisioningError as exc:
            raise MemoryProvisioningFailedError(
                f"the provisioned memory systems could not be listed: {exc}"
            ) from exc
        systems = []
        failures = []
        for record in records:
            if record.phase is not MemorySystemPhase.READY:
                continue
            cached = self._backends.get(record.tenant_id)
            if cached is None:
                try:
                    cached = self._accept(record, reserved)
                except MemoryProvisioningFailedError as exc:
                    failures.append(exc)
                    continue
            systems.append(cached)
        return systems, failures

    async def close(self) -> None:
        for _, backend in self._backends.values():
            await backend.close()
        self._backends.clear()
        await self._deployment.close()


class MemorySystems:
    def __init__(
        self,
        systems: Mapping[str, MemoryBackend],
        tenants: Mapping[str, str],
        *,
        provisioned: ProvisionedSystems | None = None,
    ) -> None:
        for tenant, name in tenants.items():
            if name not in systems:
                known = ", ".join(sorted(systems)) or "none"
                raise MimirConfigurationError(
                    f"{_TENANTS}.{tenant} names memory system {name!r}, which is not in "
                    f"{_SYSTEMS} (configured: {known})"
                )
            for kind in SYSTEM_LAYER_KINDS:
                require_capabilities(name, systems[name], kind)
        self._systems = dict(systems)
        self._tenants = dict(tenants)
        self._provisioned = provisioned

    @classmethod
    def from_config(
        cls,
        systems: Sequence[Mapping[str, Any]],
        tenants: Mapping[str, str],
        *,
        provisioned: ProvisionedSystems | None = None,
    ) -> MemorySystems:
        built: dict[str, MemoryBackend] = {}
        for index, spec in enumerate(systems):
            where = f"{_SYSTEMS}[{index}]"
            name = str(spec.get("name") or "").strip()
            if not name:
                raise MimirConfigurationError(f"{where} has no 'name'; give each system one")
            if name in built:
                raise MimirConfigurationError(f"{where}: two memory systems are named {name!r}")
            dotted = str(spec.get("adapter") or "").strip()
            if not dotted:
                raise MimirConfigurationError(
                    f"{where} ({name}) has no 'adapter'; name a MemoryBackend class such as "
                    "mimir.backends.gbrain.GBrainBackend"
                )
            backend_cls = backend_class(dotted, where=f"{where} ({name})")
            kwargs = {k: v for k, v in spec.items() if k not in ("adapter", "name")}
            try:
                built[name] = backend_cls(**kwargs)
            except (TypeError, ValueError) as exc:
                raise MimirConfigurationError(
                    f"{where} ({name}) could not be built: {exc}"
                ) from exc
        return cls(built, tenants, provisioned=provisioned)

    @property
    def provisioning(self) -> ProvisionedSystems | None:
        return self._provisioned

    async def for_tenant(
        self, tenant_id: str, *, provision: bool = True
    ) -> tuple[str, MemoryBackend]:
        """The system holding *tenant_id*'s layers.

        An org in ``memory.tenants`` uses that system. Otherwise, with provisioning on,
        its provisioned system; *provision* says whether this caller may start
        provisioning one. Only a caller whose org the platform verified (a session
        Forge confirmed, a person's own login) passes ``provision=True``; a caller that
        names the org itself (a connector) passes false.

        Raises a MemorySystemUnavailableError when the org's system cannot serve.
        """
        name = self._tenants.get(tenant_id)
        if name is not None:
            return name, self._systems[name]
        if self._provisioned is None:
            raise TenantNotConfiguredError(
                f"org {tenant_id!r} has no memory system: add it to {_TENANTS} "
                f"({tenant_id}: <system name>), or turn on {_PROVISIONING}"
            )
        return await self._provisioned.resolve(
            tenant_id, provision=provision, reserved=tuple(self._systems)
        )

    def items(self) -> list[tuple[str, MemoryBackend]]:
        """Every configured (static) system, by name."""
        return list(self._systems.items())

    async def provisioned(
        self,
    ) -> tuple[list[tuple[str, MemoryBackend]], list[MemoryProvisioningFailedError]]:
        """Every provisioned system that is ready, and the error of each that cannot be
        used (see :meth:`ProvisionedSystems.ready`). Empty without provisioning.

        Raises MemoryProvisioningFailedError when the provisioned systems cannot be listed.
        """
        if self._provisioned is None:
            return [], []
        return await self._provisioned.ready(tuple(self._systems))

    def tenants_of(self, name: str) -> list[str]:
        """The orgs whose layers live on configured system *name*."""
        return [tenant for tenant, system in self._tenants.items() if system == name]

    def health(self) -> dict[str, Any]:
        """Counts for the service's health surface."""
        body: dict[str, Any] = {"configured_systems": len(self._systems)}
        if self._provisioned is not None:
            body["provisioning"] = dict(self._provisioned.counts)
        return body

    async def close(self) -> None:
        for backend in self._systems.values():
            await backend.close()
        if self._provisioned is not None:
            await self._provisioned.close()


def backend_class(dotted: str, *, where: str) -> type[MemoryBackend]:
    """The MemoryBackend class *dotted* names, or a configuration error naming *where*."""
    try:
        backend_cls = import_class(dotted)
    except (ImportError, AttributeError, ValueError) as exc:
        raise MimirConfigurationError(
            f"{where} adapter {dotted!r} cannot be imported: {exc}"
        ) from exc
    if not isinstance(backend_cls, type) or not issubclass(backend_cls, MemoryBackend):
        raise MimirConfigurationError(f"{where} adapter {dotted!r} is not a MemoryBackend")
    return backend_cls

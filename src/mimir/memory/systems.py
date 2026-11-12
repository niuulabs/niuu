"""The memory systems the gateway talks to, and which org's layers live on which.

Built once at startup from ``memory.systems`` (dynamic adapters with a gateway
``name``) and ``memory.tenants`` (tenant -> system). Everything that can be
checked is checked here, so a misconfiguration stops the service instead of
failing a session's first call: a system that cannot be built, two systems with
one name, a tenant naming a missing system, and a system that cannot hold the
layers placed on it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from mimir.exceptions import MimirConfigurationError
from mimir.memory.models import LayerKind
from mimir.ports.memory_backend import MemoryBackend, require_capabilities
from niuu.utils import import_class

_SYSTEMS = "memory.systems"
_TENANTS = "memory.tenants"


class TenantNotConfiguredError(Exception):
    """A session's org has no memory system."""


class MemorySystems:
    def __init__(self, systems: Mapping[str, MemoryBackend], tenants: Mapping[str, str]) -> None:
        for tenant, name in tenants.items():
            if name not in systems:
                known = ", ".join(sorted(systems)) or "none"
                raise MimirConfigurationError(
                    f"{_TENANTS}.{tenant} names memory system {name!r}, which is not in "
                    f"{_SYSTEMS} (configured: {known})"
                )
            for kind in LayerKind:
                require_capabilities(name, systems[name], kind)
        self._systems = dict(systems)
        self._tenants = dict(tenants)

    @classmethod
    def from_config(
        cls, systems: Sequence[Mapping[str, Any]], tenants: Mapping[str, str]
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
            try:
                backend_cls = import_class(dotted)
            except (ImportError, AttributeError, ValueError) as exc:
                raise MimirConfigurationError(
                    f"{where} ({name}) adapter {dotted!r} cannot be imported: {exc}"
                ) from exc
            if not isinstance(backend_cls, type) or not issubclass(backend_cls, MemoryBackend):
                raise MimirConfigurationError(
                    f"{where} ({name}) adapter {dotted!r} is not a MemoryBackend"
                )
            kwargs = {k: v for k, v in spec.items() if k not in ("adapter", "name")}
            try:
                built[name] = backend_cls(**kwargs)
            except (TypeError, ValueError) as exc:
                raise MimirConfigurationError(
                    f"{where} ({name}) could not be built: {exc}"
                ) from exc
        return cls(built, tenants)

    def for_tenant(self, tenant_id: str) -> tuple[str, MemoryBackend]:
        name = self._tenants.get(tenant_id)
        if name is None:
            raise TenantNotConfiguredError(
                f"org {tenant_id!r} has no memory system: add it to {_TENANTS} "
                f"({tenant_id}: <system name>)"
            )
        return name, self._systems[name]

    async def close(self) -> None:
        for backend in self._systems.values():
            await backend.close()

"""Looks a project memory mount up in a Mimir registry file."""

from __future__ import annotations

import asyncio
from pathlib import Path

from volundr.domain.project_memory import (
    MemoryDirectoryUnavailableError,
    MemoryMountDirectory,
    RegistryTarget,
)


class MimirRegistryFileDirectory(MemoryMountDirectory):
    """The entries of a ``.mimir-registry.json``, as the Mimir that owns it keeps them.

    An entry with a tenant is reachable only by that tenant; one without is global.
    The file is read on every lookup, so an entry added or switched off takes effect at the
    next launch. A registry file that does not exist raises: "no entries" is an answer, an
    unreadable registry is not.
    """

    def __init__(self, registry_path: str, **_kwargs: object) -> None:
        if not registry_path.strip():
            raise ValueError(
                "projects.memory.directory_kwargs.registry_path is empty; set it to the "
                "Mimir registry file (.mimir-registry.json)"
            )
        self._path = Path(registry_path).expanduser()

    async def resolve(
        self, entry_id: str, *, tenant_id: str, owner_id: str
    ) -> RegistryTarget | None:
        return await asyncio.to_thread(self._resolve, entry_id, tenant_id)

    def _resolve(self, entry_id: str, tenant_id: str) -> RegistryTarget | None:
        from mimir.registry import MimirRegistryStore

        if not self._path.is_file():
            raise MemoryDirectoryUnavailableError(
                f"The Mimir registry file {self._path} does not exist; set "
                "projects.memory.directory_kwargs.registry_path to the registry of the Mimir "
                "this Forge uses, or remove the project's memory mounts"
            )
        for entry in MimirRegistryStore(self._path).list_entries(tenant_id=tenant_id):
            if entry.id != entry_id:
                continue
            return RegistryTarget(
                id=entry.id,
                name=entry.name,
                enabled=entry.enabled,
                role=entry.role,
                url=entry.url,
                adapter=entry.adapter,
                kwargs=dict(entry.kwargs),
                secret_kwargs_env=dict(entry.secret_kwargs_env),
                auth_ref=entry.auth_ref,
                categories=tuple(entry.categories or ()),
                default_read_priority=entry.default_read_priority,
                facts=entry.facts,
            )
        return None

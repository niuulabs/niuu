"""RegistryAttachedMemories — attached memories from the gateway's own mount registry.

The same lookup the Mímir router uses for its mounts (``MimirRouter._registry_port``
and ``_mount_definitions``): an entry is visible to an org when it belongs to that
org, or is a global entry and the org is the platform's own. Another org never
reaches a global entry. A disabled entry, or one without an adapter or URL, is
refused; an entry with an ``auth_ref`` needs a configured adapter, as there.
Connections are opened with :func:`mimir.connections.resolve_mimir_connection`
and kept per entry until the entry changes.
"""

from __future__ import annotations

from mimir.backends.mimir_port import MimirPortBackend
from mimir.connections import resolve_mimir_connection
from mimir.memory.models import Attachment
from mimir.ports.attached_memory import AttachedMemories, AttachedMemoryError
from mimir.ports.memory_backend import MemoryBackend
from mimir.registry import MimirRegistryEntry, MimirRegistryStore


class RegistryAttachedMemories(AttachedMemories):
    """Args:
    registry: the gateway's mount registry.
    platform_tenant_id: the org that runs this deployment; only it reaches global entries.
    """

    def __init__(self, registry: MimirRegistryStore, *, platform_tenant_id: str) -> None:
        self._registry = registry
        self._platform_tenant_id = platform_tenant_id
        self._open: dict[tuple[str, str], tuple[str, MimirPortBackend]] = {}
        self._replaced: list[MimirPortBackend] = []

    def _entry(self, tenant_id: str, attachment: Attachment) -> MimirRegistryEntry:
        other_org = tenant_id != self._platform_tenant_id
        for entry in self._registry.list_entries(tenant_id=tenant_id):
            if entry.id != attachment.registry_entry_id:
                continue
            if other_org and not entry.tenant_id:
                break
            return entry
        raise AttachedMemoryError(
            f"attached memory {attachment.name!r} names registry entry "
            f"{attachment.registry_entry_id!r}, which is not registered for org {tenant_id!r} "
            "on this gateway; register it in the Mímir registry or remove the attachment"
        )

    def open(self, tenant_id: str, attachment: Attachment, namespace: str) -> MemoryBackend:
        entry = self._entry(tenant_id, attachment)
        if not entry.enabled:
            raise AttachedMemoryError(
                f"attached memory {attachment.name!r} (registry entry {entry.id}) is disabled"
            )
        if not entry.url and not entry.adapter:
            raise AttachedMemoryError(
                f"registry entry {entry.id} of attached memory {attachment.name!r} has no "
                "adapter or url; configure its connection"
            )
        if entry.auth_ref and not entry.adapter:
            raise AttachedMemoryError(
                f"registry entry {entry.id} has an auth_ref but no adapter: authenticated "
                "connections must use a configured mount adapter"
            )
        key = (namespace, entry.id)
        fingerprint = entry.model_dump_json()
        cached = self._open.get(key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        try:
            port = resolve_mimir_connection(
                adapter=entry.adapter,
                kwargs=entry.kwargs,
                secret_kwargs_env=entry.secret_kwargs_env,
                url=entry.url,
            )
        except (TypeError, ValueError, ImportError, AttributeError) as exc:
            raise AttachedMemoryError(
                f"attached memory {attachment.name!r} (registry entry {entry.id}) could not be "
                f"opened: {exc}"
            ) from exc
        backend = MimirPortBackend(port, namespace=namespace, label=attachment.name)
        if cached is not None:
            self._replaced.append(cached[1])
        self._open[key] = (fingerprint, backend)
        return backend

    async def close(self) -> None:
        for backend in [*self._replaced, *(b for _, b in self._open.values())]:
            await backend.close()
        self._open.clear()
        self._replaced.clear()

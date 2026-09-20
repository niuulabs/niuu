"""Local memory binding checks shared by workflow editing and import adapters."""

from pathlib import Path

from mimir.registry import MimirRegistryStore


def binding_errors(bindings: dict[str, str], *, registry_path: str, tenant_id: str) -> list[str]:
    if not bindings:
        return []
    if not registry_path.strip():
        return ["Configure the local Mimir registry before resolving imported memory bindings"]
    entries = MimirRegistryStore(Path(registry_path).expanduser()).list_entries(tenant_id=tenant_id)
    enabled_ids = {entry.id for entry in entries if entry.enabled}
    return [
        f"Memory binding {node_id!r} refers to an unavailable local registry entry: {entry_id}"
        for node_id, entry_id in bindings.items()
        if entry_id not in enabled_ids
    ]

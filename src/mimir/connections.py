"""Resolve a configured knowledge connection for API and runtime consumers."""

import copy
from pathlib import Path
from typing import Any

from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.registry import MimirRegistryStore
from niuu.ports.mimir import MimirPort
from niuu.utils import import_class, resolve_secret_kwargs
from ravn.adapters.mimir.http import HttpMimirAdapter
from ravn.domain.mimir import MimirAuth


def resolve_mimir_connection(
    *,
    adapter: str = "",
    kwargs: dict[str, Any] | None = None,
    secret_kwargs_env: dict[str, str] | None = None,
    url: str = "",
    path: str = "",
    auth: MimirAuth | None = None,
    environment_id: str = "",
) -> MimirPort:
    """Honor the adapter connection, including gateway mount, before legacy endpoints.

    Remote managed wells point at the Mímir API with a mount selector. That
    API owns tenant discovery and backend resolution for both gbrain and Mímir.
    Local/native connections use the same explicit adapter contract.
    """
    if adapter:
        cls = import_class(adapter)
        if not isinstance(cls, type) or not issubclass(cls, MimirPort):
            raise TypeError(f"Registry adapter {adapter} must implement MimirPort")
        resolved = resolve_secret_kwargs(dict(kwargs or {}), secret_kwargs_env or {})
        if issubclass(cls, HttpMimirAdapter) and auth is not None:
            resolved["auth"] = auth
        return cls(**resolved)
    if url:
        return HttpMimirAdapter(base_url=url, auth=auth, environment_id=environment_id)
    if path:
        return MarkdownMimirAdapter(root=path)
    raise ValueError("Memory well has no adapter, path or url; configure its connection")


def normalize_mimir_workload_config(
    raw: dict[str, Any] | None = None,
    *,
    hosted_url: str = "",
) -> dict[str, Any]:
    if not raw and not hosted_url:
        return {}

    normalized = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    if hosted_url and not normalized.get("hosted_url"):
        normalized["hosted_url"] = hosted_url
    return normalized


def resolve_mimir_registry_refs(
    raw: dict[str, Any] | None = None,
    *,
    registry_path: str = "",
) -> dict[str, Any]:
    """Hydrate registry-backed Mimir refs with concrete path/url metadata.

    Workflow snapshots currently preserve registry IDs, mount names, and binding
    metadata. Before dispatching a flock, resolve those IDs against the local
    Mimir registry so Volundr can materialize real mount instances.
    """
    normalized = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    registry_refs = normalized.get("registry_refs")
    if not isinstance(registry_refs, list) or not registry_refs or not registry_path.strip():
        return normalized

    registry_file = Path(registry_path).expanduser()
    store = MimirRegistryStore(registry_file)
    entries = store.list_entries()
    if not entries:
        return normalized

    by_id = {entry.id: entry for entry in entries}
    by_name = {entry.name: entry for entry in entries}

    resolved_refs: list[dict[str, Any]] = []
    for raw_ref in registry_refs:
        if not isinstance(raw_ref, dict):
            continue
        resolved = dict(raw_ref)
        lookup_key = str(
            raw_ref.get("registry_entry_id")
            or raw_ref.get("registryEntryId")
            or raw_ref.get("mount_name")
            or raw_ref.get("mountName")
            or ""
        ).strip()
        entry = by_id.get(lookup_key) or by_name.get(lookup_key)
        if entry is None:
            resolved_refs.append(resolved)
            continue

        if entry.adapter:
            resolved["adapter"] = entry.adapter
            resolved["kwargs"] = dict(entry.kwargs)
            resolved["secret_kwargs_env"] = dict(entry.secret_kwargs_env)
        if not entry.enabled:
            raise ValueError(f"Memory well {entry.name!r} is disabled")
        if entry.path and not str(resolved.get("path") or "").strip():
            resolved["path"] = entry.path
        if entry.url and not str(resolved.get("url") or "").strip():
            resolved["url"] = entry.url
        if entry.role and not str(resolved.get("role") or "").strip():
            resolved["role"] = entry.role
        if entry.categories and not resolved.get("categories"):
            resolved["categories"] = list(entry.categories)
        if entry.auth_ref and not str(resolved.get("auth_ref") or "").strip():
            resolved["auth_ref"] = entry.auth_ref
        resolved.setdefault("default_read_priority", entry.default_read_priority)
        resolved.setdefault("enabled", entry.enabled)
        resolved_refs.append(resolved)

    normalized["registry_refs"] = resolved_refs
    return normalized


def resolve_mimir_workload(
    raw: dict[str, Any] | None = None,
    *,
    hosted_url: str = "",
    registry_path: str = "",
    bearer_token: str | None = None,
) -> MimirPort | None:
    """Open a workload's selected well through the shared connection resolver."""
    normalized = resolve_mimir_registry_refs(
        normalize_mimir_workload_config(raw, hosted_url=hosted_url),
        registry_path=registry_path,
    )
    default_mounts = list(normalized.get("default_mounts") or [])
    registry_refs = list(normalized.get("registry_refs") or [])
    ephemeral_locals = list(normalized.get("ephemeral_locals") or [])

    auth = MimirAuth(type="bearer", token=bearer_token) if bearer_token else None
    for collection in (ephemeral_locals, registry_refs):
        for ref in collection:
            if not isinstance(ref, dict):
                continue
            mount_name = str(ref.get("mount_name") or "")
            if default_mounts and mount_name not in default_mounts:
                continue
            if ref.get("enabled") is False:
                raise ValueError(f"Memory well {mount_name!r} is disabled")
            return resolve_mimir_connection(
                adapter=str(ref.get("adapter") or ""),
                kwargs=ref.get("kwargs"),
                secret_kwargs_env=ref.get("secret_kwargs_env"),
                url=str(ref.get("url") or "").strip(),
                path=str(ref.get("path") or "").strip(),
                auth=auth,
            )

    if default_mounts or registry_refs or ephemeral_locals:
        raise ValueError("No configured default memory well could be resolved")
    hosted_url = str(normalized.get("hosted_url") or "").strip()
    if hosted_url:
        return resolve_mimir_connection(url=hosted_url, auth=auth)
    return None

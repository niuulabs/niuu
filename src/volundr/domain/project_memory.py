"""Memory a Forge project owns: the Mimir mounts its sessions and workflow runs inherit.

A project holds a set of named mounts. Each points at one entry of the Mimir registry and
carries the access the project grants (``read``, or ``read_write`` for the listed page
prefixes). At every launch of a session attached to the project, the mounts are resolved
against the registry for the session's owner and merged with what a launch spec and the
session's own ``workload_config.mimir`` add, so a harness gets the same memory whichever
way the session was started. Two sources that use one mount name for different targets
are refused, naming the mount; none silently wins.
"""

from __future__ import annotations

import copy
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The ``source`` a launch-time merge puts on what it derives, so the next launch can drop
#: it and derive it again from what the project and the launch spec say then.
PROJECT_SOURCE = "project"
LAUNCH_SPEC_SOURCE = "launch_spec"
_DERIVED_SOURCES = frozenset({PROJECT_SOURCE, LAUNCH_SPEC_SOURCE})

#: Every Mimir mount reaches the harness as an MCP server named ``mimir-<mount name>``.
MIMIR_SERVER_PREFIX = "mimir-"

_FACT_INSTRUCTIONS = """\
## Project memory

This session has Mimir memory mounts (the `mimir-*` MCP servers). Use them:

1. At the start of the work, call `mimir_fact_context_pack` (the context pack) for the \
subjects the task is about, and read what it returns before you begin.
2. Before you re-derive something, call `mimir_fact_recall` to see whether it is already known.
3. Call `mimir_fact_remember` only for outcomes you have checked (a test that passed, a \
file you read, a command whose output you saw). Give the evidence it came from. Never record \
a guess or a plan as a fact.

Facts you write are recorded as an agent's. The platform assigns that level from who you \
are: you cannot raise it, and you must not claim a higher one in a statement. A fact a \
person confirmed ranks above yours; if it conflicts with what you found, say so in your \
answer instead of overwriting it.

A mount may refuse a write (read-only, or outside the pages it may write). The tool returns \
the refusal: report it, do not look for another way around it.
"""

_WIKI_INSTRUCTIONS = """\
## Project memory

This session has Mimir memory mounts (the `mimir-*` MCP servers). They hold wiki pages; they \
do not offer typed-fact tools. Use them:

1. At the start of the work, call `mimir_search` (and `mimir_related` on what it finds) for \
the subjects the task is about, and read the pages that matter with `mimir_read` before you begin.
2. Before you re-derive something, search the mounts to see whether it is already written down.
3. Call `mimir_write` only for outcomes you have checked (a test that passed, a file you \
read, a command whose output you saw). Cite where each came from in the page's sources or \
timeline. Never record a guess or a plan as a finding.

A mount may refuse a write (read-only, or outside the pages it may write). The tool returns \
the refusal: report it, do not look for another way around it.
"""

#: Kept for callers that want the typed-fact text; see ``memory_instructions``.
MEMORY_INSTRUCTIONS = _FACT_INSTRUCTIONS


class ProjectMemoryError(ValueError):
    """A project memory request or launch cannot be carried out; the message names the fix."""


class MemoryMountNotFoundError(LookupError):
    """The project has no mount of that name."""


class MemoryMountExistsError(ProjectMemoryError):
    """The project already has a mount of that name."""


class MemoryMountUnavailableError(ProjectMemoryError):
    """A project mount's registry entry is missing or its owner cannot reach it."""


class MemoryMountUnenforceableError(ProjectMemoryError):
    """A mount's access limits cannot be held for the way its target is served."""


class MemoryDirectoryUnavailableError(RuntimeError):
    """The registry could not be asked, so whether a mount's entry exists is not known."""


class MemoryMountClashError(ProjectMemoryError):
    """Two sources use one mount name for different targets."""


class MemoryAccess(StrEnum):
    READ = "read"
    READ_WRITE = "read_write"


MOUNT_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"


class ProjectMemoryMount(BaseModel):
    """One mount a project owns."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: UUID
    name: str = Field(pattern=MOUNT_NAME_PATTERN)
    registry_entry_id: str = Field(min_length=1, max_length=200)
    access: MemoryAccess = MemoryAccess.READ
    write_prefixes: tuple[str, ...] = ()
    added_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("write_prefixes")
    @classmethod
    def _prefixes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(prefix.strip() for prefix in value)
        for prefix in cleaned:
            if not prefix or prefix.startswith("/") or ".." in prefix.split("/"):
                raise ValueError(
                    f"Write prefix {prefix!r} must be a relative page path such as 'project/'"
                )
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("Write prefixes must be unique")
        return cleaned

    @model_validator(mode="after")
    def _access_matches_prefixes(self) -> ProjectMemoryMount:
        if self.access is MemoryAccess.READ and self.write_prefixes:
            raise ValueError("A read-only mount has no write prefixes; use access read_write")
        return self

    @property
    def restricts(self) -> bool:
        """True when the mount limits writes: it is read-only, or confined to prefixes."""
        return self.access is MemoryAccess.READ or bool(self.write_prefixes)


@dataclass(frozen=True)
class RegistryTarget:
    """What the Mimir registry says about one entry: how to reach it, and whether it is on."""

    id: str
    name: str
    enabled: bool = True
    role: str = "shared"
    url: str = ""
    path: str = ""
    adapter: str = ""
    kwargs: dict[str, Any] = field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = field(default_factory=dict)
    auth_ref: str | None = None
    categories: tuple[str, ...] = ()
    default_read_priority: int = 10
    facts: bool = False

    @property
    def served_by_forge(self) -> bool:
        """True when a session reaches this entry through a Mimir server Forge starts for it
        (a plain path), not through the Mimir that owns it (a url or an adapter)."""
        return bool(self.path) and not self.url and not self.adapter


class ProjectMemoryRepository(ABC):
    @abstractmethod
    async def list_for_project(self, project_id: UUID) -> list[ProjectMemoryMount]:
        """Every mount of the project, oldest first."""

    @abstractmethod
    async def get(self, project_id: UUID, name: str) -> ProjectMemoryMount | None: ...

    @abstractmethod
    async def add(self, mount: ProjectMemoryMount) -> ProjectMemoryMount:
        """Insert; raises ``MemoryMountExistsError`` when the project has that name."""

    @abstractmethod
    async def update(
        self,
        project_id: UUID,
        name: str,
        *,
        access: MemoryAccess,
        write_prefixes: tuple[str, ...],
    ) -> ProjectMemoryMount | None:
        """Change the access; ``None`` when the project has no such mount."""

    @abstractmethod
    async def remove(self, project_id: UUID, name: str) -> bool:
        """Remove a mount; False when there was none."""


class MemoryMountDirectory(ABC):
    """Where a mount's registry entry is looked up, for one tenant."""

    @abstractmethod
    async def resolve(
        self, entry_id: str, *, tenant_id: str, owner_id: str
    ) -> RegistryTarget | None:
        """The entry, or ``None`` when it does not exist or the owner's tenant cannot reach it.

        The two are one answer, so a project owner cannot probe which entries other tenants
        have. An adapter that cannot ask its registry raises
        ``MemoryDirectoryUnavailableError`` instead of answering ``None``.
        """


@dataclass(frozen=True)
class ProjectMemoryRefs:
    """A project's mounts, resolved for one launch: connection refs and their access."""

    refs: list[dict[str, Any]] = field(default_factory=list)
    bindings: list[dict[str, Any]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.refs)


def mount_ref(mount: ProjectMemoryMount, target: RegistryTarget) -> dict[str, Any]:
    """The ``registry_refs`` item that launches ``mount`` as ``target`` describes it."""
    ref: dict[str, Any] = {
        "mount_name": mount.name,
        "registry_entry_id": target.id,
        "role": target.role,
        "default_read_priority": target.default_read_priority,
        "source": PROJECT_SOURCE,
    }
    if target.adapter:
        ref["adapter"] = target.adapter
        ref["kwargs"] = dict(target.kwargs)
        ref["secret_kwargs_env"] = dict(target.secret_kwargs_env)
    if target.url:
        ref["url"] = target.url
    if target.path:
        ref["path"] = target.path
    if target.auth_ref:
        ref["auth_ref"] = target.auth_ref
    if target.categories:
        ref["categories"] = list(target.categories)
    if target.facts:
        ref["facts"] = True
    return ref


def mount_binding(mount: ProjectMemoryMount) -> dict[str, Any]:
    return {
        "mount_name": mount.name,
        "access": mount.access.value,
        "write_prefixes": list(mount.write_prefixes),
        # The launch holds this limit (or refuses the launch); other bindings only describe.
        "enforce": True,
        "source": PROJECT_SOURCE,
    }


class ProjectMemoryResolver:
    """Resolves a project's mounts against the registry for the session's owner."""

    def __init__(self, repository: ProjectMemoryRepository, directory: MemoryMountDirectory):
        self._repository = repository
        self._directory = directory

    async def target(
        self, mount: ProjectMemoryMount, *, tenant_id: str, owner_id: str
    ) -> RegistryTarget:
        """The entry ``mount`` points at, for an owner of ``tenant_id``.

        Raises naming the mount when the entry is not there, is switched off, has no way
        to be reached, or cannot be held to the mount's access limits.
        """
        target = await self._directory.resolve(
            mount.registry_entry_id, tenant_id=tenant_id, owner_id=owner_id
        )
        if target is None:
            reason = "does not exist or is not reachable by this owner's organization"
        elif not target.enabled:
            reason = "is disabled in the registry"
        elif not (target.url or target.path or target.adapter):
            reason = (
                "has no url, adapter or path a session can use (a path on another host's "
                "disk is not one)"
            )
        else:
            if mount.restricts and not target.served_by_forge:
                raise MemoryMountUnenforceableError(
                    f"Project memory mount {mount.name!r} limits writes, but registry entry "
                    f"{mount.registry_entry_id!r} is served by its own Mimir over a url or "
                    "adapter, where Forge cannot hold that limit. Mount it with access "
                    "read_write and no prefixes (that Mimir decides what a session may "
                    "write), or point the mount at an entry that is a plain path."
                )
            return target
        raise MemoryMountUnavailableError(
            f"Project memory mount {mount.name!r} points at registry entry "
            f"{mount.registry_entry_id!r}, which {reason}. Restore or enable the entry, or "
            "change or remove the mount through the project's memory API "
            "(PATCH or DELETE /projects/<id>/memory/<name>)."
        )

    async def for_launch(
        self, project_id: UUID, *, tenant_id: str, owner_id: str
    ) -> ProjectMemoryRefs:
        """The project's mounts as they launch for ``owner_id`` of ``tenant_id``."""
        mounts = await self._repository.list_for_project(project_id)
        refs: list[dict[str, Any]] = []
        bindings: list[dict[str, Any]] = []
        for mount in mounts:
            target = await self.target(mount, tenant_id=tenant_id, owner_id=owner_id)
            refs.append(mount_ref(mount, target))
            bindings.append(mount_binding(mount))
        return ProjectMemoryRefs(refs, bindings)


# --- merging --------------------------------------------------------------------------------

_LIST_KEYS = ("registry_refs", "instances", "ephemeral_locals", "bindings")
#: Settings of a ``mimir`` config that are not lists of mounts. The first source that sets one
#: (session, then launch spec) supplies it.
_SCALAR_KEYS = ("hosted_url", "default_mounts", "write_routing")
#: The key that lists which scalar settings a launch took from the launch spec, so the next
#: launch drops them with the rest of what it derived.
_DERIVED_KEYS = "derived_settings"


def _item_name(item: dict[str, Any]) -> str:
    for key in ("mount_name", "mountName", "name", "resource_node_id", "resourceNodeId"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _signature(key: str, item: dict[str, Any]) -> tuple:
    """What a mount points at: its registry entry, or else its connection."""
    if key == "ephemeral_locals":
        seed = str(item.get("seed_from_registry_id") or item.get("seedFromRegistryId") or "")
        return ("ephemeral", seed)
    entry = str(item.get("registry_entry_id") or item.get("registryEntryId") or "").strip()
    if entry:
        return ("entry", entry)
    return (
        "connection",
        str(item.get("adapter") or ""),
        json.dumps(item.get("kwargs") or {}, sort_keys=True, default=str),
        str(item.get("url") or ""),
        str(item.get("path") or ""),
    )


def without_derived(mimir: dict[str, Any] | None) -> dict[str, Any]:
    """``mimir`` as the caller wrote it: what an earlier launch derived is dropped."""
    if not isinstance(mimir, dict):
        return {}
    kept = copy.deepcopy(mimir)
    for key in kept.pop(_DERIVED_KEYS, None) or []:
        kept.pop(key, None)
    for key in ("registry_refs", "bindings"):
        items = kept.get(key)
        if not isinstance(items, list):
            continue
        kept[key] = [
            item
            for item in items
            if not (isinstance(item, dict) and item.get("source") in _DERIVED_SOURCES)
        ]
    return {k: v for k, v in kept.items() if v not in ([], {}, None, "")}


def has_derived(mimir: dict[str, Any] | None) -> bool:
    """True when an earlier launch put project or launch-spec mounts into ``mimir``."""
    if not isinstance(mimir, dict):
        return False
    return bool(mimir.get(_DERIVED_KEYS)) or any(
        isinstance(item, dict) and item.get("source") in _DERIVED_SOURCES
        for key in ("registry_refs", "bindings")
        for item in (mimir.get(key) or [])
    )


def _tagged(items: list[Any], source: str) -> list[Any]:
    """``items`` marked with ``source``. Anything that is not a mount object is kept as it is,
    so the launch refuses it instead of the merge hiding it."""
    return [{**item, "source": source} if isinstance(item, dict) else item for item in items]


def merge_memory(
    session: dict[str, Any] | None,
    launch_spec: dict[str, Any] | None,
    project: ProjectMemoryRefs,
) -> dict[str, Any]:
    """The ``mimir`` config of a launch: the project's mounts, the launch spec's and the
    session's own.

    A mount named by two sources must point at the same target; otherwise this raises
    ``MemoryMountClashError`` naming the mount and both sources. Repeating one target is
    allowed and keeps the first.
    """
    explicit = without_derived(session)
    spec = copy.deepcopy(launch_spec) if isinstance(launch_spec, dict) else {}
    sources: list[tuple[str, dict[str, Any]]] = [
        (PROJECT_SOURCE, {"registry_refs": project.refs, "bindings": project.bindings}),
        ("launch spec", {k: _tagged(spec.get(k) or [], LAUNCH_SPEC_SOURCE) for k in _LIST_KEYS}),
        ("session", {k: list(explicit.get(k) or []) for k in _LIST_KEYS}),
    ]
    merged: dict[str, Any] = {key: [] for key in _LIST_KEYS}
    seen: dict[str, tuple[str, tuple]] = {}
    for label, config in sources:
        for key in ("registry_refs", "instances", "ephemeral_locals"):
            for item in config.get(key) or []:
                if not isinstance(item, dict):
                    merged[key].append(item)
                    continue
                name = _item_name(item)
                signature = _signature(key, item)
                known = seen.get(name) if name else None
                if known is not None and known[1] != signature:
                    raise MemoryMountClashError(
                        f"Memory mount {name!r} is set by the {known[0]} and by the {label} "
                        "with different targets. Rename one of them or remove it from "
                        "one source."
                    )
                if known is not None:
                    continue
                if name:
                    seen[name] = (label, signature)
                merged[key].append(item)
        merged["bindings"].extend(config.get("bindings") or [])

    result: dict[str, Any] = {k: v for k, v in merged.items() if v}
    from_spec: list[str] = []
    for key in _SCALAR_KEYS:
        if explicit.get(key):
            result[key] = explicit[key]
        elif spec.get(key):
            result[key] = spec[key]
            from_spec.append(key)
    if from_spec:
        result[_DERIVED_KEYS] = from_spec
    for key, value in explicit.items():
        result.setdefault(key, value)
    return result


def offers_facts(mimir: dict[str, Any] | None) -> bool:
    """True when at least one mount of ``mimir`` serves the typed-fact tools."""
    if not isinstance(mimir, dict):
        return False
    return any(
        isinstance(item, dict) and item.get("facts") is True
        for key in ("registry_refs", "instances")
        for item in (mimir.get(key) or [])
    )


def with_memory_instructions(system_prompt: str, *, facts: bool) -> str:
    """``system_prompt`` followed by how to use the session's memory: the typed-fact tools
    when a mount offers them, the wiki tools otherwise."""
    text = _FACT_INSTRUCTIONS if facts else _WIKI_INSTRUCTIONS
    return "\n\n".join(part for part in (system_prompt, text) if part)

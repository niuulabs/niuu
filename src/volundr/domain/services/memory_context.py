"""What a session's memory layers are, as Forge knows them.

The memory gateway holds every memory system's credentials and enforces the read and write
rules, but it does not know who a session belongs to or which project and run it is in.
Forge does, and answers on every call (``GET /sessions/{id}/memory-context``), so a stopped or
restarted session's access to memory ends with the session. The answer names the owner, the
tenant, the project, the workflow run and the project's own write prefixes; the gateway turns
them into layers (``org``, ``project:<id>``, ``session:<id>``, ...).

The project's write prefixes are the project layer's own list (``ForgeProject.
memory_write_prefixes``, set by a project owner), not the prefixes of the mounts the project
attaches. A session with no project has no project layer, so ``project_memory`` is ``None``.

The answer also carries what the gateway needs to build the session's context pack: the
session's model, its objective, the project's brief and ``since`` (when this owner's previous
session in the project began), and the memories attached to the launch (``attached``): the
project's mounts, the launch spec's and the session's own, each with the access limit the
gateway enforces. When Forge is given the gateway, those limits are the gateway's to hold, so
a session no longer gets a per-mount server for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from niuu.domain.services.forge_session_token import ForgeSessionClaims
from volundr.domain.models import Session
from volundr.domain.project_memory import (
    LAUNCH_SPEC_SOURCE,
    PROJECT_SOURCE,
    MemoryAccess,
)
from volundr.domain.project_ports import ProjectRepository
from volundr.domain.services.session import SessionService
from volundr.domain.workflow_session import WORKFLOW_RUN_ID_KEY

#: The ``source`` of an attached memory the session itself set (no project or launch spec
#: derived it).
SESSION_SOURCE = "session"


class MemoryContextError(Exception):
    """A session's memory context cannot be given; the message names the fix."""


class MemoryContextUnavailableError(MemoryContextError):
    """This Forge cannot read projects, so it cannot say what the session's project holds."""


@dataclass(frozen=True)
class ProjectMemoryContext:
    """The project layer's rules for the session."""

    write_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class AttachedMemory:
    """One memory a launch carries beyond the layers Forge derives: a registry entry the
    gateway opens for the session's tenant, and the access the attachment allows."""

    name: str
    registry_entry_id: str
    access: MemoryAccess
    write_prefixes: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class SessionMemoryContext:
    session_id: UUID
    launch_id: str
    owner_id: str
    tenant_id: str
    project_id: UUID | None
    workflow_run_id: str | None
    project_memory: ProjectMemoryContext | None
    model: str | None = None
    objective: str | None = None
    project_brief: str | None = None
    since: datetime | None = None
    attached: tuple[AttachedMemory, ...] = ()


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _mount_name(item: dict[str, Any]) -> str:
    for key in (
        "mount_name",
        "mountName",
        "registry_entry_id",
        "registryEntryId",
        "resource_node_id",
        "resourceNodeId",
    ):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def attached_mounts(mimir: object) -> tuple[AttachedMemory, ...]:
    """The memories a launch's ``workload_config.mimir`` attaches, as the gateway opens them.

    Each is a registry ref (the project's, the launch spec's, the session's own) with the
    access its binding grants; a ref without a binding is ``read_write`` with no prefixes,
    which is what an unbound mount allowed before (that memory decides what it accepts).
    The gateway reaches memories only through its registry, so anything it could not open
    is refused naming the fix, never left out: a mount in ``instances`` or ``hosted_url``, an
    ``ephemeral_locals`` entry, or a ref with no ``registry_entry_id``.
    """
    if not isinstance(mimir, dict) or not mimir:
        return ()
    if mimir.get("instances"):
        raise MemoryContextError(
            "workload_config.mimir.instances names mounts by url or adapter, which the memory "
            "gateway cannot open: register each in the Mimir registry and attach it as a "
            "registry_refs entry with its registry_entry_id, or remove it"
        )
    if mimir.get("ephemeral_locals"):
        raise MemoryContextError(
            "workload_config.mimir.ephemeral_locals names scratch mounts, which are no longer "
            "served: remove them (a workflow run has its own run layer)"
        )
    refs = mimir.get("registry_refs") or []
    if mimir.get("hosted_url") and not refs:
        raise MemoryContextError(
            "workload_config.mimir.hosted_url names a Mimir by url, which the memory gateway "
            "cannot open: register it in the Mimir registry and attach it as a registry_refs "
            "entry with its registry_entry_id, or remove hosted_url"
        )
    bindings: dict[str, dict[str, Any]] = {}
    for binding in mimir.get("bindings") or []:
        if isinstance(binding, dict):
            bindings.setdefault(_mount_name(binding), binding)
    mounts: list[AttachedMemory] = []
    mounted = {_mount_name(ref) for ref in refs if isinstance(ref, dict)}
    for name in bindings:
        if name not in mounted:
            raise MemoryContextError(
                f"workload_config.mimir.bindings is for the mount {name!r}, which the session "
                f"does not mount (mounted: {', '.join(sorted(mounted)) or 'none'}); add the "
                "mount or remove the binding"
            )
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            raise MemoryContextError(
                f"workload_config.mimir.registry_refs[{index}] must be an object naming a "
                f"mount, not {type(ref).__name__}; fix or remove it"
            )
        name = _mount_name(ref)
        entry_id = str(ref.get("registry_entry_id") or ref.get("registryEntryId") or "").strip()
        if not name or not entry_id:
            raise MemoryContextError(
                f"workload_config.mimir.registry_refs[{index}] ({name or 'unnamed'}) has no "
                "registry_entry_id, so the memory gateway cannot open it: resolve it to a "
                "registry entry, or remove it"
            )
        binding = bindings.get(name)
        access = MemoryAccess.READ_WRITE
        prefixes: tuple[str, ...] = ()
        if binding is not None:
            access = (
                MemoryAccess.READ_WRITE
                if "write" in str(binding.get("access") or "read")
                else MemoryAccess.READ
            )
            prefixes = _string_list(binding.get("write_prefixes") or binding.get("writePrefixes"))
        source = ref.get("source")
        mounts.append(
            AttachedMemory(
                name=name,
                registry_entry_id=entry_id,
                access=access,
                write_prefixes=prefixes,
                source=source if source in (PROJECT_SOURCE, LAUNCH_SPEC_SOURCE) else SESSION_SOURCE,
            )
        )
    return tuple(mounts)


class MemoryContextService:
    """Builds a session's memory context from its row, its project and its owner's history."""

    def __init__(self, projects: ProjectRepository | None, sessions: SessionService) -> None:
        self._projects = projects
        self._sessions = sessions

    async def for_session(
        self, session: Session, credential: ForgeSessionClaims
    ) -> SessionMemoryContext:
        """The context of *session*, which *credential* (already verified live) is bound to."""
        run_id = session.workload_config.get(WORKFLOW_RUN_ID_KEY)
        context = SessionMemoryContext(
            session_id=session.id,
            launch_id=credential.launch_id,
            owner_id=credential.owner_id,
            tenant_id=credential.tenant_id,
            project_id=None,
            workflow_run_id=str(run_id) if run_id else None,
            project_memory=None,
            model=session.model or None,
            attached=attached_mounts(session.workload_config.get("mimir")),
        )
        if session.coordination is None:
            return context
        project_id = session.coordination.project_id
        if self._projects is None:
            raise MemoryContextUnavailableError(
                f"Session {session.id} belongs to project {project_id}, but this Forge runs "
                "without projects: set projects.enabled: true, or detach the session from "
                "its project"
            )
        project = await self._projects.get(project_id)
        if project is None:
            raise MemoryContextError(
                f"Session {session.id} belongs to project {project_id}, which no longer "
                "exists: detach the session from it (PUT /sessions/{id}/project)"
            )
        return SessionMemoryContext(
            session_id=context.session_id,
            launch_id=context.launch_id,
            owner_id=context.owner_id,
            tenant_id=context.tenant_id,
            project_id=project_id,
            workflow_run_id=context.workflow_run_id,
            project_memory=ProjectMemoryContext(write_prefixes=project.memory_write_prefixes),
            model=context.model,
            objective=session.coordination.objective or None,
            project_brief=project.brief or None,
            since=await self._previous_session_started(session, project_id, credential),
            attached=context.attached,
        )

    async def _previous_session_started(
        self, session: Session, project_id: UUID, credential: ForgeSessionClaims
    ) -> datetime | None:
        """When this owner's latest earlier session in the project began, or ``None``."""
        earlier = [
            other.created_at
            for other in await self._sessions.list_project_sessions(
                [project_id], credential.tenant_id
            )
            if other.owner_id == credential.owner_id
            and other.id != session.id
            and other.created_at < session.created_at
        ]
        return max(earlier, default=None)

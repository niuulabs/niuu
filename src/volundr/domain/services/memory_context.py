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
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from niuu.domain.services.forge_session_token import ForgeSessionClaims
from volundr.domain.models import Session
from volundr.domain.project_ports import ProjectRepository
from volundr.domain.workflow_session import WORKFLOW_RUN_ID_KEY


class MemoryContextError(Exception):
    """A session's memory context cannot be given; the message names the fix."""


class MemoryContextUnavailableError(MemoryContextError):
    """This Forge cannot read projects, so it cannot say what the session's project holds."""


@dataclass(frozen=True)
class ProjectMemoryContext:
    """The project layer's rules for the session."""

    write_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class SessionMemoryContext:
    session_id: UUID
    launch_id: str
    owner_id: str
    tenant_id: str
    project_id: UUID | None
    workflow_run_id: str | None
    project_memory: ProjectMemoryContext | None


class MemoryContextService:
    """Builds a session's memory context from its row and its project."""

    def __init__(self, projects: ProjectRepository | None) -> None:
        self._projects = projects

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
        )

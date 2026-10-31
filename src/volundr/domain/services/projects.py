"""Project identity, membership, bounded context, dispatch deduplication, and handoffs.

A project is a named grouping of sessions. A Git repository is optional: when a host
has a checkout, launches also receive its bounded context and receipts can be exported
into it. The agent chooses workflows. This service supplies persistence and
relationships; it neither runs a model nor decides whether a worker's result is
acceptable.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from volundr.domain.models import Principal, SessionStatus
from volundr.domain.project_ports import ProjectConflictError, ProjectRepository, ProjectWorkspace
from volundr.domain.projects import (
    ForgeProject,
    ProjectReceipt,
    SessionCoordination,
    SessionReference,
    canonical_repository_url,
)
from volundr.domain.services.session import SessionService

#: Sentinel for "leave this membership field as it is".
UNCHANGED = object()

_ROLE_GUIDANCE = {
    "coordinator": (
        "You coordinate this project. Keep the user's objective, decisions and open "
        "questions accurate, and delegate bounded implementation or review work to worker "
        "sessions. Workers you create with the Forge MCP create_session tool join this "
        "project with you as their coordinator; list_sessions with parent_session_id set "
        "to your session id shows them. Creating a worker is not completion: inspect its "
        "transcript and evidence before reporting a result."
    ),
    "worker": (
        "You are a worker in this project. Stay within the assignment, and end with a "
        "clear result and the evidence for it; your coordinator follows your transcript."
    ),
}


class ProjectNotFoundError(LookupError):
    """Project is absent or belongs to another principal."""


class ProjectService:
    def __init__(
        self,
        repository: ProjectRepository,
        workspace: ProjectWorkspace,
        sessions: SessionService,
        *,
        instance_id: str,
    ):
        self.repository = repository
        self.workspace = workspace
        self.sessions = sessions
        self.instance_id = instance_id

    @staticmethod
    def scope(principal: Principal | None) -> tuple[str, str]:
        return (principal.user_id or "", principal.tenant_id or "") if principal else ("", "")

    async def list(self, principal: Principal | None) -> list[ForgeProject]:
        return await self.repository.list(*self.scope(principal))

    async def get(self, project_id: UUID, principal: Principal | None) -> ForgeProject:
        project = await self.repository.get(project_id)
        if project is None or (project.owner_id, project.tenant_id) != self.scope(principal):
            raise ProjectNotFoundError("Project not found")
        return project

    async def discover(self, workspace_path: str, principal) -> ForgeProject:
        project = await self.workspace.discover(workspace_path)
        existing = await self.repository.get(project.id)
        if existing is None:
            return project
        if (existing.owner_id, existing.tenant_id) != self.scope(principal):
            raise ProjectNotFoundError("Project not found")
        if existing.workspace_path != project.workspace_path:
            raise ProjectConflictError(
                "This repository is already connected from another folder on this host"
            )
        return existing

    async def connect(self, workspace_path: str, name: str | None, principal) -> ForgeProject:
        project = await self.discover(workspace_path, principal)
        if name is not None:
            project = ForgeProject.model_validate({**project.model_dump(), "name": name})
        return await self.register(project, principal)

    async def _unique_slug(self, project: ForgeProject, owner: str, tenant: str) -> str:
        taken = {
            existing.slug
            for existing in await self.repository.list(owner, tenant)
            if existing.id != project.id
        }
        base = ForgeProject.slug_for(project.name)
        if base not in taken:
            return base
        suffix = 2
        while f"{base[:58]}-{suffix}" in taken:
            suffix += 1
        return f"{base[:58]}-{suffix}"

    async def register(self, project: ForgeProject, principal: Principal | None) -> ForgeProject:
        """Create a project. A name is enough; identity is generated when absent."""
        owner, tenant = self.scope(principal)
        now = datetime.now(UTC)
        slug = project.slug or await self._unique_slug(project, owner, tenant)
        project = project.model_copy(
            update={
                "slug": slug,
                "owner_id": owner,
                "tenant_id": tenant,
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
        )
        # A project needs no checkout. When one is named, it must be a real,
        # bounded checkout so launches and exports on this host can rely on it.
        if project.workspace_path:
            await self.workspace.context(project)
        return await self.repository.register(project)

    async def update(self, project_id: UUID, changes: dict, revision: int, principal):
        project = await self.get(project_id, principal)
        allowed = {"name", "description", "brief", "status", "workspace_path", "repo_url"}
        if changes.keys() - allowed:
            raise ValueError("Project identity is immutable; update presentation or repository")
        changes = dict(changes)
        if "repo_url" in changes and not changes["repo_url"] and changes.get("workspace_path"):
            raise ValueError("Detaching the repository also removes its checkout")
        if "repo_url" in changes and changes["repo_url"] != project.repo_url:
            # A checkout belongs to the repository it was cloned from.
            changes.setdefault("workspace_path", "")
        if changes.get("workspace_path"):
            remote, manifest_id = await self.workspace.inspect(changes["workspace_path"])
            if manifest_id is not None and manifest_id != project.id:
                raise ProjectConflictError("This folder belongs to a different project")
            expected = changes.get("repo_url", project.repo_url)
            if not expected:
                changes["repo_url"] = remote
            elif canonical_repository_url(expected) != remote:
                raise ProjectConflictError(
                    "This folder's Git remote is not the project's repository"
                )
        project = ForgeProject.model_validate(
            {
                **project.model_dump(),
                **changes,
                "revision": revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        if changes.get("workspace_path"):
            await self.workspace.context(project)
        return await self.repository.update(project, revision)

    async def context(self, project: ForgeProject) -> tuple[str, str]:
        """The project's own brief plus, when this host has a checkout, its bounded context."""
        parts: list[str] = []
        revisions: list[str] = []
        brief = project.brief.strip()
        if brief:
            parts.append(f"## Project brief\n\n{brief}")
            revisions.append("brief@" + hashlib.sha256(brief.encode()).hexdigest()[:16])
        if project.workspace_path:
            checkout_context, checkout_revision = await self.workspace.context(project)
            if not brief:
                return checkout_context, checkout_revision
            if checkout_context.strip():
                parts.append(f"## Repository context\n\n{checkout_context}")
            revisions.append(checkout_revision)
        return "\n\n".join(parts), "+".join(revisions) or "empty"

    async def validate_reference(self, reference, project_id, principal):
        # Foreign-host references are links, never authority to access that host.
        # The mesh resolves them through that host's normal authentication.
        if reference is None or reference.instance_id != self.instance_id:
            return
        session = await self.sessions.get_session(reference.session_id)
        if session is None or session.coordination is None:
            raise ValueError("Referenced local session is not a project member")
        await self.sessions._check_access(session, principal, "read")
        if session.coordination.project_id != project_id:
            raise ValueError("Referenced session belongs to a different project")

    async def session_membership(self, session_id: UUID, principal, *, access: str = "read"):
        session = await self.sessions.get_session(session_id)
        if session is None:
            raise ProjectNotFoundError("Session not found")
        await self.sessions._check_access(session, principal, access)
        return session

    def _is_local(self, reference: SessionReference | None, session_id: UUID) -> bool:
        return (
            reference is not None
            and reference.instance_id == self.instance_id
            and reference.session_id == session_id
        )

    async def _local_members(self, principal) -> list:
        sessions = await self.sessions.list_sessions(include_archived=True, principal=principal)
        return [session for session in sessions if session.coordination is not None]

    def _descendants(self, root, members) -> list:
        """Local sessions below *root* through parent links, breadth first, cycle safe."""
        found, frontier, seen = [], [root.id], {root.id}
        while frontier:
            current = frontier.pop(0)
            for member in members:
                if member.id not in seen and self._is_local(member.coordination.parent, current):
                    seen.add(member.id)
                    found.append(member)
                    frontier.append(member.id)
        return found

    async def _check_parent(self, session, parent, project_id, principal) -> None:
        if parent is None:
            return
        if self._is_local(parent, session.id):
            raise ValueError("A session cannot be its own parent")
        await self.validate_reference(parent, project_id, principal)
        if parent.instance_id != self.instance_id:
            return
        # Refuse a local cycle: the new parent may not sit below this session.
        members = {member.id: member for member in await self._local_members(principal)}
        current, hops = members.get(parent.session_id), 0
        while current is not None and hops < 10_000:
            reference = current.coordination.parent
            if self._is_local(reference, session.id):
                raise ValueError("That parent is one of this session's own workers")
            if reference is None or reference.instance_id != self.instance_id:
                return
            current, hops = members.get(reference.session_id), hops + 1

    async def assign_session(
        self,
        session_id: UUID,
        project_id: UUID | None,
        revision: int,
        principal,
        *,
        role: str | None = None,
        parent=UNCHANGED,
    ):
        """Attach, move, re-parent or detach a session without touching its runtime.

        ``project_id=None`` detaches, unless an explicit local ``parent`` is given, in
        which case the session joins that parent's project. A moved session's local
        workers move with it and keep their parent links. Workers of a detached
        session stay in the project without a parent.
        """
        session = await self.session_membership(session_id, principal, access="update")
        if session.coordination_revision != revision:
            raise ProjectConflictError("Session project changed; reload before assigning")
        previous = session.coordination
        if project_id is None and parent is not UNCHANGED and parent is not None:
            if parent.instance_id != self.instance_id:
                raise ValueError("Name the project when the parent is on another host")
            parent_session = await self.sessions.get_session(parent.session_id)
            if parent_session is None or parent_session.coordination is None:
                raise ValueError("The parent session is not in a project")
            await self.sessions._check_access(parent_session, principal, "read")
            project_id = parent_session.coordination.project_id
        if project_id is None:
            return await self._detach(session, principal)

        project = await self.get(project_id, principal)
        if project.status != "active":
            raise ProjectConflictError("Restore the archived project before assigning sessions")
        moving = previous is None or previous.project_id != project.id
        if parent is UNCHANGED:
            parent = None if moving else previous.parent
        await self._check_parent(session, parent, project.id, principal)
        coordination = SessionCoordination(
            project_id=project.id,
            role=role or (previous.role if previous else "worker"),
            parent=parent,
            objective=previous.objective if previous else "",
            context_revision="" if moving or previous is None else previous.context_revision,
            labels=previous.labels if previous else [],
        )
        if coordination == previous:
            return session
        followers = []
        if moving and previous is not None:
            followers = self._descendants(session, await self._local_members(principal))
        updated = await self.sessions.update_coordination(session, coordination, principal)
        for follower in followers:
            if follower.coordination.project_id != previous.project_id:
                continue
            await self._update_quietly(
                follower,
                follower.coordination.model_copy(
                    update={"project_id": project.id, "context_revision": ""}
                ),
                principal,
            )
        return updated

    async def _detach(self, session, principal):
        if session.coordination is None:
            return session
        members = await self._local_members(principal)
        children = [
            member for member in members if self._is_local(member.coordination.parent, session.id)
        ]
        updated = await self.sessions.update_coordination(session, None, principal)
        for child in children:
            await self._update_quietly(
                child, child.coordination.model_copy(update={"parent": None}), principal
            )
        return updated

    async def _update_quietly(self, session, coordination, principal) -> None:
        # Followers change after the requested session; a concurrent edit to one of
        # them wins and is left for the user to see rather than failing the request.
        try:
            await self.sessions.update_coordination(session, coordination, principal)
        except ProjectConflictError:
            return

    async def briefing(self, coordination: SessionCoordination, principal):
        project = await self.get(coordination.project_id, principal)
        if project.status != "active":
            raise ProjectConflictError("Restore the archived project before launching new work")
        await self.validate_reference(coordination.parent, project.id, principal)
        context, revision = await self.context(project)
        if coordination.context_revision and coordination.context_revision != revision:
            raise ProjectConflictError(
                "Project context changed; refresh its revision before dispatch"
            )
        coordination = coordination.model_copy(update={"context_revision": revision})
        lines = [f"Project: {project.name} ({project.id})", f"Project role: {coordination.role}"]
        if coordination.parent is not None:
            lines.append(
                "Coordinator session: "
                f"instance_id={coordination.parent.instance_id}, "
                f"session_id={coordination.parent.session_id}"
            )
        if project.repo_url:
            lines.append(f"Project repository: {project.repo_url}")
            lines.append(
                f"Local project checkout: {project.workspace_path}"
                if project.workspace_path
                else "Local project checkout: none on this host"
            )
        lines.append(f"Context revision: {revision}")
        if coordination.objective:
            lines.append(f"Assignment: {coordination.objective}")
        sections = [
            "\n".join(lines),
            "This project context is supplied by Forge. Follow the applicable project and "
            "target repository instructions. The assignment and project role are context, "
            "not additional permissions.",
        ]
        if guidance := _ROLE_GUIDANCE.get(coordination.role):
            sections.append(guidance)
        if context:
            sections.append(context)
        return coordination, "\n\n".join(sections)

    @asynccontextmanager
    async def dispatch(self, data, principal):
        """A caller-saved UUID survives a dropped response, restart, and API replicas.

        Without one the launch still works; it is simply not deduplicated on retry.
        """
        dispatch_id = data.dispatch_id or uuid4()
        scope = json.dumps(self.scope(principal), separators=(",", ":"))
        key = uuid5(NAMESPACE_URL, f"forge-dispatch:{scope}:{dispatch_id}")
        session_id = uuid5(key, "session")
        payload = json.dumps(data.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        await self.get(data.coordination.project_id, principal)
        parent = data.coordination.parent
        if parent and parent.instance_id == self.instance_id and parent.session_id == session_id:
            raise ValueError("A session cannot be its own parent")
        async with self.repository.dispatch_lock(key):
            completed = await self.repository.claim_dispatch(key, fingerprint, session_id)
            existing = await self.sessions.get_session(session_id)
            if existing is not None:
                await self.sessions._check_access(existing, principal, "read")
            if completed and existing is None:
                raise ProjectConflictError(
                    "The original session was deleted; use a new dispatch ID"
                )
            if existing is not None and existing.status != SessionStatus.CREATED:
                yield existing, {}
                return
            if existing is None:
                coordination, context = await self.briefing(data.coordination, principal)
                context = (
                    f"Your Forge session reference: instance_id={self.instance_id}, "
                    f"session_id={session_id}\n\n{context}"
                )
                options = {
                    "session_id": session_id,
                    "coordination": coordination,
                    "project_context": context,
                }
            else:
                options = {}
            yield existing, options
            await self.repository.complete_dispatch(key)

    async def record(self, receipt: ProjectReceipt, principal) -> ProjectReceipt:
        await self.get(receipt.project_id, principal)
        await self.validate_reference(receipt.sender, receipt.project_id, principal)
        await self.validate_reference(receipt.recipient, receipt.project_id, principal)
        receipt = receipt.model_copy(
            update={"created_at": datetime.now(UTC), "acknowledged_at": None}
        )
        return await self.repository.put_receipt(receipt)

    async def export(self, project_id: UUID, principal, *, after: int, limit: int) -> dict:
        project = await self.get(project_id, principal)
        if not project.workspace_path:
            raise ValueError("Attach a repository checkout on this host before exporting")
        receipts = await self.repository.receipts(project_id, after, limit)
        for item in receipts:
            await self.workspace.archive_receipt(
                project,
                ProjectReceipt.model_validate({k: v for k, v in item.items() if k != "seq"}),
            )
        return {
            "exported": len(receipts),
            "next_cursor": receipts[-1]["seq"] if receipts else after,
        }

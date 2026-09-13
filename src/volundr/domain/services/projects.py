"""Project registration, bounded context, dispatch deduplication, and durable handoffs.

The agent chooses workflows. This service supplies persistence and relationships;
it neither runs a model nor decides whether a worker's result is acceptable.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from volundr.domain.models import Principal, SessionStatus
from volundr.domain.project_ports import ProjectConflictError, ProjectRepository, ProjectWorkspace
from volundr.domain.projects import ForgeProject, ProjectReceipt, SessionCoordination
from volundr.domain.services.session import SessionService


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

    async def register(self, project: ForgeProject, principal: Principal | None) -> ForgeProject:
        owner, tenant = self.scope(principal)
        now = datetime.now(UTC)
        project = project.model_copy(
            update={
                "owner_id": owner,
                "tenant_id": tenant,
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
        )
        # Registration is allowed without a checkout for browsing a mesh replica.
        # Launching and exporting on that host require a real, bounded checkout.
        if project.workspace_path:
            await self.workspace.context(project)
        return await self.repository.register(project)

    async def update(self, project_id: UUID, changes: dict, revision: int, principal):
        project = await self.get(project_id, principal)
        allowed = {"name", "description", "status", "workspace_path"}
        if changes.keys() - allowed:
            raise ValueError("Project identity is immutable; update only presentation or checkout")
        project = ForgeProject.model_validate(
            {
                **project.model_dump(),
                **changes,
                "revision": revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        if "workspace_path" in changes and project.workspace_path:
            await self.workspace.context(project)
        return await self.repository.update(project, revision)

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

    async def briefing(self, coordination: SessionCoordination, principal):
        project = await self.get(coordination.project_id, principal)
        if project.status != "active":
            raise ProjectConflictError("Restore the archived project before launching new work")
        await self.validate_reference(coordination.parent, project.id, principal)
        context, revision = await self.workspace.context(project)
        if coordination.context_revision and coordination.context_revision != revision:
            raise ProjectConflictError(
                "Project context changed; refresh its revision before dispatch"
            )
        coordination = coordination.model_copy(update={"context_revision": revision})
        briefing = (
            f"Project: {project.name} ({project.id})\n"
            f"Meta-repository: {project.repo_url}\n"
            f"Local project checkout: {project.workspace_path}\n"
            f"Project role: {coordination.role}\nContext revision: {revision}\n"
            f"Assignment: {coordination.objective}\n\n"
            "This is a bounded project context snapshot supplied by its repository. "
            "Follow the applicable project and target repository instructions. "
            "The assignment and project role are context, not additional permissions.\n\n"
            f"{context}"
        )
        return coordination, briefing

    @asynccontextmanager
    async def dispatch(self, data, principal):
        """A UUID request key survives a dropped response, restart, and API replicas."""
        if data.dispatch_id is None:
            raise ValueError("Project launches require dispatch_id; reuse it when retrying")
        scope = json.dumps(self.scope(principal), separators=(",", ":"))
        key = uuid5(NAMESPACE_URL, f"forge-dispatch:{scope}:{data.dispatch_id}")
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

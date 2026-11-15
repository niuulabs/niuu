"""Project-only test adapters; production always uses durable storage."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from identity.models import TenantMembership, User, UserStatus
from tests.conftest import InMemoryUserRepository, make_session_participant_service
from volundr.adapters.outbound.contributors.prompt import PromptContributor
from volundr.domain.project_ports import (
    LastOwnerError,
    ProjectConflictError,
    ProjectMemberRepository,
    ProjectRepository,
    ProjectWorkspace,
)
from volundr.domain.projects import (
    ForgeProject,
    ProjectMember,
    ProjectMemberCandidate,
    ProjectRole,
)
from volundr.domain.services.forge import ForgeService
from volundr.domain.services.projects import ProjectService
from volundr.domain.services.session import SessionService


class MemoryUsers(InMemoryUserRepository):
    """Users with real tenant memberships, for the tenant checks on adding members."""

    def __init__(self):
        super().__init__()
        self.memberships: dict[str, list[TenantMembership]] = {}

    def seed(self, user_id, tenant_id, *, status=UserStatus.ACTIVE, name="", email=""):
        self._users[user_id] = User(
            id=user_id, email=email or f"{user_id}@x.test", display_name=name, status=status
        )
        self.memberships.setdefault(user_id, []).append(
            TenantMembership(user_id=user_id, tenant_id=tenant_id)
        )

    async def get_memberships(self, user_id):
        return self.memberships.get(user_id, [])


class MemoryProjectMembers(ProjectMemberRepository):
    def __init__(self, users=None):
        self.rows: dict[tuple, ProjectMember] = {}
        self.users = users

    def seed_owner(self, project):
        member = ProjectMember(
            project_id=project.id,
            user_id=project.owner_id,
            tenant_id=project.tenant_id,
            role=ProjectRole.OWNER,
            added_by=project.owner_id,
        )
        self.rows.setdefault((project.id, project.owner_id), member)

    async def list_for_project(self, project_id):
        return [m for (pid, _), m in self.rows.items() if pid == project_id]

    async def list_for_user(self, user_id, tenant_id):
        return [
            m for (_, uid), m in self.rows.items() if uid == user_id and m.tenant_id == tenant_id
        ]

    async def get(self, project_id, user_id):
        return self.rows.get((project_id, user_id))

    async def add(self, member):
        key = (member.project_id, member.user_id)
        if key in self.rows:
            raise ProjectConflictError("already a member")
        self.rows[key] = member
        return member

    def _owners(self, project_id):
        return {u for (p, u), m in self.rows.items() if p == project_id and m.role == "owner"}

    async def update(self, project_id, user_id, *, role=None, expertise=None, skills=None):
        current = self.rows.get((project_id, user_id))
        if current is None:
            return None
        if role is not None and role != ProjectRole.OWNER and self._owners(project_id) == {user_id}:
            raise LastOwnerError("last owner")
        changes = {
            k: v
            for k, v in {"role": role, "expertise": expertise, "skills": skills}.items()
            if v is not None
        }
        self.rows[(project_id, user_id)] = current.model_copy(update=changes)
        return self.rows[(project_id, user_id)]

    async def remove(self, project_id, user_id):
        if self._owners(project_id) == {user_id}:
            raise LastOwnerError("last owner")
        return self.rows.pop((project_id, user_id), None) is not None

    async def candidates(self, project_id, tenant_id, query):
        found = []
        for user in await self.users.list():
            in_tenant = any(m.tenant_id == tenant_id for m in self.users.memberships[user.id])
            if user.status != UserStatus.ACTIVE or not in_tenant:
                continue
            if (project_id, user.id) in self.rows:
                continue
            if query.lower() in f"{user.display_name} {user.email}".lower():
                found.append(
                    ProjectMemberCandidate(
                        user_id=user.id, display_name=user.display_name, email=user.email
                    )
                )
        return found


class MemoryProjects(ProjectRepository):
    def __init__(self, members=None):
        self.members = members
        self.projects = {}
        self.handoffs = {}
        self.dispatches = {}
        self.locks = {}

    @asynccontextmanager
    async def dispatch_lock(self, key):
        async with self.locks.setdefault(key, asyncio.Lock()):
            yield

    async def claim_dispatch(self, key, fingerprint, session_id):
        value = self.dispatches.setdefault(key, [fingerprint, False])
        if value[0] != fingerprint:
            raise ProjectConflictError("different launch request")
        return value[1]

    async def complete_dispatch(self, key):
        self.dispatches[key][1] = True

    async def list(self, owner_id, tenant_id):
        return [
            p for p in self.projects.values() if (p.owner_id, p.tenant_id) == (owner_id, tenant_id)
        ]

    async def get(self, project_id):
        return self.projects.get(project_id)

    async def list_member_projects(self, user_id, tenant_id):
        ids = {m.project_id for m in await self.members.list_for_user(user_id, tenant_id)}
        return [p for p in self.projects.values() if p.id in ids]

    async def register(self, project):
        stored = self.projects.setdefault(project.id, project)
        if stored is project and project.owner_id and self.members is not None:
            self.members.seed_owner(project)
        return stored

    async def update(self, project, expected_revision):
        if self.projects[project.id].revision != expected_revision:
            raise ProjectConflictError("Project changed")
        self.projects[project.id] = project
        return project

    async def put_receipt(self, receipt):
        old = self.handoffs.setdefault(receipt.id, receipt)
        exclude = {"created_at", "acknowledged_at"}
        if old.model_dump(exclude=exclude) != receipt.model_dump(exclude=exclude):
            raise ProjectConflictError("different handoff")
        return old

    async def receipts(self, project_id, after, limit):
        return [
            {**item.model_dump(), "seq": seq}
            for seq, item in enumerate(self.handoffs.values(), 1)
            if item.project_id == project_id and seq > after
        ][:limit]

    async def acknowledge(self, project_id, receipt_id):
        item = self.handoffs.get(receipt_id)
        if item is None or item.project_id != project_id:
            return False
        self.handoffs[receipt_id] = item.model_copy(update={"acknowledged_at": datetime.now(UTC)})
        return True


@pytest.fixture
async def rig(repository, pod_manager):
    users = MemoryUsers()
    members = MemoryProjectMembers(users)
    project_repo = MemoryProjects(members)
    workspace = AsyncMock(spec=ProjectWorkspace)
    workspace.context.return_value = ("Keep project decisions in Git.", "revision-a")
    sessions = SessionService(
        repository,
        pod_manager,
        validate_repos=False,
        provisioning_initial_delay=0,
        contributors=[PromptContributor()],
    )
    service = ProjectService(
        project_repo, workspace, sessions, instance_id="thor", members=members, users=users
    )
    project = await service.register(
        ForgeProject(
            slug="lexi",
            name="Lexi",
            repo_url="https://github.com/xteo/project-lexi",
            workspace_path="/projects/lexi",
        ),
        None,
    )
    workspace.context.reset_mock()  # registration validated the checkout; tests count their own
    forge = ForgeService(
        sessions,
        project_service=service,
        session_participant_service=make_session_participant_service(sessions),
    )
    yield service, forge, project, repository, pod_manager
    await asyncio.gather(*sessions._provisioning_tasks.values(), return_exceptions=True)

"""Project-only test adapters; production always uses durable storage."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.contributors.prompt import PromptContributor
from volundr.domain.project_ports import ProjectConflictError, ProjectRepository, ProjectWorkspace
from volundr.domain.projects import ForgeProject
from volundr.domain.services.forge import ForgeService
from volundr.domain.services.projects import ProjectService
from volundr.domain.services.session import SessionService


class MemoryProjects(ProjectRepository):
    def __init__(self):
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

    async def register(self, project):
        return self.projects.setdefault(project.id, project)

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
    project_repo = MemoryProjects()
    workspace = AsyncMock(spec=ProjectWorkspace)
    workspace.context.return_value = ("Keep project decisions in Git.", "revision-a")
    sessions = SessionService(
        repository,
        pod_manager,
        validate_repos=False,
        provisioning_initial_delay=0,
        contributors=[PromptContributor()],
    )
    service = ProjectService(project_repo, workspace, sessions, instance_id="thor")
    project = await service.register(
        ForgeProject(slug="lexi", name="Lexi", repo_url="https://github.com/xteo/project-lexi"),
        None,
    )
    forge = ForgeService(sessions, project_service=service)
    yield service, forge, project, repository, pod_manager
    await asyncio.gather(*sessions._provisioning_tasks.values(), return_exceptions=True)

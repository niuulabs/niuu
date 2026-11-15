"""Memory proposal notifications: the gateway tells a project's deciders through Forge's
own feed, and settles what it told them once a person decided."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from tests.support.forge_session import (
    OWNER,
    PREFIX,
    TENANT,
    ForgeApp,
    InMemoryUsers,
    bearer,
    build_forge_app,
)
from tests.test_projects.conftest import MemoryProjectMembers, MemoryProjects
from volundr.adapters.inbound.forge_session_auth import Binding, match_policy
from volundr.adapters.inbound.rest_memory_proposals import (
    create_memory_proposals_router,
    review_url,
)
from volundr.domain.notifications import MEMORY_PROPOSAL_KEY
from volundr.domain.project_ports import ProjectWorkspace
from volundr.domain.projects import ForgeProject, ProjectMember, ProjectRole, SessionCoordination
from volundr.domain.services.projects import ProjectService

PROPOSAL = "p-" + "ab" * 16


def as_user(user: str, roles: str = "volundr:developer") -> dict[str, str]:
    return {"x-auth-user-id": user, "x-auth-tenant": TENANT, "x-auth-roles": roles}


class Rig:
    def __init__(self, forge: ForgeApp, members: MemoryProjectMembers, repo: MemoryProjects):
        self.forge = forge
        self.members = members
        self.repo = repo

    async def project(self) -> ForgeProject:
        return await self.repo.register(
            ForgeProject(name="Booking platform", owner_id=OWNER, tenant_id=TENANT)
        )

    def member(self, project: ForgeProject, user: str, role: ProjectRole) -> None:
        self.members.rows[(project.id, user)] = ProjectMember(
            project_id=project.id, user_id=user, tenant_id=TENANT, role=role
        )

    async def session_in(self, project: ForgeProject):
        session = await self.forge.session(
            "planner", coordination=SessionCoordination(project_id=project.id)
        )
        return session, await self.forge.start(session)

    def waiting(self, session_id, credential: str, project_id, **body):
        return self.forge.client.post(
            f"{PREFIX}/sessions/{session_id}/memory/proposals",
            headers=bearer(credential),
            json={
                "proposal_id": PROPOSAL,
                "project_id": str(project_id),
                "title": "The booking cut-over date",
                "why": "The payments freeze moved a week.",
                **body,
            },
        )

    def decided(self, project_id, user: str, outcome: str = "kept"):
        return self.forge.client.post(
            f"{PREFIX}/projects/{project_id}/memory/proposals/{PROPOSAL}/decided",
            headers=as_user(user),
            json={"outcome": outcome},
        )

    def rows(self):
        return [
            n
            for n in self.forge.store.notifications.values()
            if n.metadata.get(MEMORY_PROPOSAL_KEY) == PROPOSAL
        ]


def build(*, feed: bool = True) -> Rig:
    forge = build_forge_app()
    members = MemoryProjectMembers()
    repo = MemoryProjects(members)
    projects = ProjectService(
        repo,
        AsyncMock(spec=ProjectWorkspace),
        forge.session_service,
        instance_id="thor",
        members=members,
        users=InMemoryUsers(),
    )
    forge.app.include_router(
        create_memory_proposals_router(
            forge.session_service,
            projects,
            members,
            forge.notifications if feed else None,
            prefix=PREFIX,
        )
    )
    return Rig(forge, members, repo)


@pytest.fixture
def rig() -> Rig:
    return build()


class TestWaiting:
    async def test_the_projects_approvers_and_owners_are_told(self, rig: Rig) -> None:
        project = await rig.project()
        rig.member(project, "marc", ProjectRole.APPROVER)
        rig.member(project, "vera", ProjectRole.CONTRIBUTOR)
        session, credential = await rig.session_in(project)

        response = rig.waiting(session.id, credential.token, project.id)

        assert response.status_code == 200, response.text
        assert response.json() == {"told": ["marc", OWNER], "reason": None}
        rows = rig.rows()
        assert sorted(row.owner_id for row in rows) == ["marc", OWNER]
        row = rows[0]
        assert row.title == "Booking platform: a change to its memory waits"
        assert row.body == "The booking cut-over date\nThe payments freeze moved a week."
        assert row.links[0].url == review_url(project.id)
        assert row.session_id == session.id

    async def test_telling_again_tells_nobody_twice(self, rig: Rig) -> None:
        project = await rig.project()
        session, credential = await rig.session_in(project)
        rig.waiting(session.id, credential.token, project.id)
        again = rig.waiting(session.id, credential.token, project.id)
        assert again.status_code == 200
        assert len(rig.rows()) == 1

    async def test_another_project_is_refused(self, rig: Rig) -> None:
        project = await rig.project()
        other = await rig.project()
        session, credential = await rig.session_in(project)
        response = rig.waiting(session.id, credential.token, other.id)
        assert response.status_code == 403
        assert rig.rows() == []

    async def test_a_person_or_another_session_is_refused(self, rig: Rig) -> None:
        project = await rig.project()
        session, _ = await rig.session_in(project)
        other, other_credential = await rig.session_in(project)
        by_person = rig.forge.client.post(
            f"{PREFIX}/sessions/{session.id}/memory/proposals",
            headers=as_user(OWNER),
            json={"proposal_id": PROPOSAL, "project_id": str(project.id), "title": "t"},
        )
        by_other = rig.waiting(session.id, other_credential.token, project.id)
        assert by_person.status_code == 403
        assert by_other.status_code == 403

    async def test_a_feedless_forge_says_nobody_was_told(self) -> None:
        rig = build(feed=False)
        project = await rig.project()
        session, credential = await rig.session_in(project)
        response = rig.waiting(session.id, credential.token, project.id)
        assert response.status_code == 200
        assert response.json()["told"] == [] and "no notification feed" in response.json()["reason"]

    async def test_a_malformed_proposal_id_is_refused(self, rig: Rig) -> None:
        project = await rig.project()
        session, credential = await rig.session_in(project)
        response = rig.waiting(session.id, credential.token, project.id, proposal_id="x/../y")
        assert response.status_code == 422

    def test_the_route_is_on_the_session_allow_list(self) -> None:
        found = match_policy("POST", f"{PREFIX}/sessions/{uuid.uuid4()}/memory/proposals")
        assert found is not None
        policy, _ = found
        assert policy.scope == "forge:memory" and policy.binding is Binding.OWN


class TestDecided:
    async def test_deciding_settles_every_deciders_row(self, rig: Rig) -> None:
        project = await rig.project()
        rig.member(project, "marc", ProjectRole.APPROVER)
        session, credential = await rig.session_in(project)
        rig.waiting(session.id, credential.token, project.id)

        response = rig.decided(project.id, "marc")

        assert response.status_code == 200, response.text
        assert response.json() == {"settled": 2}
        assert all(row.resolved for row in rig.rows())
        assert rig.decided(project.id, "marc").json() == {"settled": 0}

    async def test_a_proposal_never_announced_settles_nothing(self, rig: Rig) -> None:
        project = await rig.project()
        rig.member(project, "marc", ProjectRole.OWNER)
        assert rig.decided(project.id, "marc").json() == {"settled": 0}

    async def test_only_deciders_settle_and_others_learn_nothing(self, rig: Rig) -> None:
        project = await rig.project()
        rig.member(project, "vera", ProjectRole.CONTRIBUTOR)
        assert rig.decided(project.id, "vera").status_code == 403
        assert rig.decided(project.id, "stranger").status_code == 404
        assert rig.decided(uuid.uuid4(), OWNER).status_code == 404

    async def test_a_session_credential_is_refused(self, rig: Rig) -> None:
        project = await rig.project()
        _, credential = await rig.session_in(project)
        response = rig.forge.client.post(
            f"{PREFIX}/projects/{project.id}/memory/proposals/{PROPOSAL}/decided",
            headers=bearer(credential.token),
            json={"outcome": "kept"},
        )
        assert response.status_code == 403

    async def test_an_outcome_is_one_word(self, rig: Rig) -> None:
        project = await rig.project()
        assert rig.decided(project.id, OWNER, outcome="kept it all").status_code == 422

    async def test_a_feedless_forge_settles_nothing(self) -> None:
        rig = build(feed=False)
        project = await rig.project()
        assert rig.decided(project.id, OWNER).json() == {"settled": 0}

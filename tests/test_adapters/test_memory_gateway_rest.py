"""What the memory gateway asks Forge for people and for itself: a person's project role, a
run's owner, and when sessions and runs ended."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import jwt
import pytest

from niuu.domain.models import Principal
from niuu.ports.identity import IdentityPort
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
from volundr.adapters.inbound.forge_session_auth import match_policy
from volundr.adapters.inbound.rest_memory_gateway import create_memory_gateway_router
from volundr.domain.models import Session, SessionStatus
from volundr.domain.project_ports import ProjectWorkspace
from volundr.domain.projects import ForgeProject, ProjectMember, ProjectRole
from volundr.domain.services.memory_layers import MemoryLayerService
from volundr.domain.services.projects import ProjectService
from volundr.domain.workflow_session import WORKFLOW_RUN_ID_KEY

SUBJECT = "system:serviceaccount:niuu:mimir"
OTHER_TENANT = "tenant-2"
ENDED = (SessionStatus.STOPPED, SessionStatus.FAILED, SessionStatus.ARCHIVED)
ACTIVE = (
    SessionStatus.CREATED,
    SessionStatus.STARTING,
    SessionStatus.PROVISIONING,
    SessionStatus.RUNNING,
    SessionStatus.STOPPING,
)


def as_user(user: str, tenant: str = TENANT, roles: str = "volundr:developer") -> dict[str, str]:
    """A caller under the allow-all identity of mini mode (the dev impersonation headers)."""
    return {"x-auth-user-id": user, "x-auth-tenant": tenant, "x-auth-roles": roles}


class JwtIdentity(IdentityPort):
    """An identity adapter that accepts a JWT's claims (a stand-in for Envoy's verification)."""

    async def validate_token(self, raw_token: str) -> Principal:
        claims = jwt.decode(raw_token.removeprefix("Bearer "), options={"verify_signature": False})
        return Principal(
            user_id=claims["sub"],
            email="",
            tenant_id=claims.get("tenant_id", TENANT),
            roles=claims.get("roles", ["volundr:developer"]),
        )

    async def get_or_provision_user(self, principal: Principal):
        return None


def token(sub: str = OWNER, **claims) -> str:
    return jwt.encode(
        {"sub": sub, "tenant_id": TENANT, "exp": 4102444800, **claims}, "k" * 32, algorithm="HS256"
    )


class Rig:
    def __init__(self, forge: ForgeApp, members: MemoryProjectMembers, repo: MemoryProjects):
        self.forge = forge
        self.members = members
        self.repo = repo

    async def project(self, tenant: str = TENANT) -> ForgeProject:
        return await self.repo.register(ForgeProject(name="Lexi", owner_id=OWNER, tenant_id=tenant))

    async def member(self, project: ForgeProject, user: str, role: ProjectRole) -> None:
        self.members.rows[(project.id, user)] = ProjectMember(
            project_id=project.id, user_id=user, tenant_id=project.tenant_id, role=role
        )

    async def run_session(
        self,
        run: str,
        status: SessionStatus = SessionStatus.RUNNING,
        *,
        owner: str = OWNER,
        updated_at: datetime | None = None,
    ) -> Session:
        session = await self.forge.session(
            f"{run}-{uuid.uuid4().hex[:4]}", owner, workload_config={WORKFLOW_RUN_ID_KEY: run}
        )
        return await self.update(session, status, updated_at)

    async def update(
        self, session: Session, status: SessionStatus, updated_at: datetime | None = None
    ) -> Session:
        return await self.forge.sessions.update(
            session.model_copy(
                update={"status": status, "updated_at": updated_at or session.updated_at}
            )
        )


def build(*, allow_all: bool = True, subject: str = SUBJECT, with_projects: bool = True) -> Rig:
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
        create_memory_gateway_router(
            MemoryLayerService(forge.session_service),
            projects if with_projects else None,
            service_subject=subject,
            allow_all_identity=allow_all,
        )
    )
    if not allow_all:
        forge.app.state.identity = JwtIdentity()
    return Rig(forge, members, repo)


@pytest.fixture
def rig() -> Rig:
    return build()


@pytest.fixture
def verifying() -> Rig:
    """A Forge whose identity verifies callers (a cluster), not mini mode's allow-all."""
    return build(allow_all=False)


def role_url(project: ForgeProject | UUID) -> str:
    return f"{PREFIX}/projects/{getattr(project, 'id', project)}/role"


def run_url(run: str) -> str:
    return f"{PREFIX}/memory/runs/{run}"


ENDED_URL = f"{PREFIX}/memory/ended"


async def session_credential(rig: Rig) -> dict[str, str]:
    session = await rig.forge.session("agent")
    return bearer((await rig.forge.start(session)).token)


class TestProjectRole:
    async def test_each_member_gets_their_role(self, rig: Rig) -> None:
        project = await rig.project()
        for role in ProjectRole:
            await rig.member(project, f"user-{role.value}", role)

        for role in ProjectRole:
            response = rig.forge.client.get(
                role_url(project), headers=as_user(f"user-{role.value}")
            )

            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "no-store"
            assert response.json() == {"project_id": str(project.id), "role": role.value}

    async def test_the_creator_is_an_owner(self, rig: Rig) -> None:
        project = await rig.project()

        response = rig.forge.client.get(role_url(project), headers=as_user(OWNER))

        assert response.json()["role"] == "owner"

    async def test_a_tenant_admin_is_an_owner_without_being_a_member(self, rig: Rig) -> None:
        project = await rig.project()

        response = rig.forge.client.get(
            role_url(project), headers=as_user("admin", roles="volundr:admin")
        )

        assert response.json()["role"] == "owner"

    async def test_a_non_member_reads_as_not_found(self, rig: Rig) -> None:
        project = await rig.project()

        absent = rig.forge.client.get(role_url(uuid.uuid4()), headers=as_user("stranger"))
        stranger = rig.forge.client.get(role_url(project), headers=as_user("stranger"))

        assert stranger.status_code == absent.status_code == 404
        assert stranger.json() == absent.json()

    async def test_another_tenants_admin_cannot_tell_the_project_exists(self, rig: Rig) -> None:
        project = await rig.project()

        response = rig.forge.client.get(
            role_url(project), headers=as_user("admin", OTHER_TENANT, "volundr:admin")
        )

        assert response.status_code == 404

    async def test_a_forge_without_projects_says_so(self) -> None:
        rig = build(with_projects=False)

        response = rig.forge.client.get(role_url(uuid.uuid4()), headers=as_user(OWNER))

        assert response.status_code == 503
        assert "projects.enabled" in response.json()["detail"]

    async def test_a_session_credential_is_refused(self, rig: Rig) -> None:
        project = await rig.project()

        response = rig.forge.client.get(role_url(project), headers=await session_credential(rig))

        assert response.status_code == 403

    async def test_a_scoped_workload_credential_is_refused(self, rig: Rig) -> None:
        project = await rig.project()
        scoped = token(token_use="valkyrie_build", scopes=["forge:session:create"])

        response = rig.forge.client.get(role_url(project), headers=bearer(scoped))

        assert response.status_code == 403

    async def test_a_person_with_a_verified_login_gets_their_role(self, verifying: Rig) -> None:
        project = await verifying.project()
        await verifying.member(project, "bob", ProjectRole.APPROVER)

        response = verifying.forge.client.get(role_url(project), headers=bearer(token("bob")))

        assert response.json()["role"] == "approver"

    async def test_a_workload_acting_for_someone_is_refused(self, verifying: Rig) -> None:
        project = await verifying.project()
        acting = token(OWNER, act={"sub": "system:serviceaccount:ns:resident"})

        response = verifying.forge.client.get(role_url(project), headers=bearer(acting))

        assert response.status_code == 403

    async def test_the_gateways_own_credential_is_not_a_person(self, verifying: Rig) -> None:
        project = await verifying.project()

        response = verifying.forge.client.get(
            role_url(project), headers=bearer(token(OWNER, act={"sub": SUBJECT}))
        )

        assert response.status_code == 403


class TestRunOwner:
    async def test_the_owner_gets_the_run_and_its_sessions(self, rig: Rig) -> None:
        first = await rig.run_session("run-1")
        second = await rig.run_session("run-1", SessionStatus.STOPPED)
        await rig.run_session("run-2")

        response = rig.forge.client.get(run_url("run-1"), headers=as_user(OWNER))

        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "run_id": "run-1",
            "owner_id": OWNER,
            "tenant_id": TENANT,
            "session_ids": [str(first.id), str(second.id)],
        }

    async def test_a_tenant_admin_may_ask_about_anyones_run(self, rig: Rig) -> None:
        await rig.run_session("run-1", owner="alice")

        response = rig.forge.client.get(
            run_url("run-1"), headers=as_user("admin", roles="volundr:admin")
        )

        assert response.status_code == 200
        assert response.json()["owner_id"] == "alice"

    async def test_someone_elses_run_reads_as_not_found(self, rig: Rig) -> None:
        await rig.run_session("run-1", owner="alice")

        theirs = rig.forge.client.get(run_url("run-1"), headers=as_user("bob"))
        absent = rig.forge.client.get(run_url("no-such-run"), headers=as_user("bob"))

        assert theirs.status_code == absent.status_code == 404

    async def test_a_run_with_a_session_of_another_owner_is_not_the_callers(self, rig: Rig) -> None:
        await rig.run_session("run-1", owner="alice")
        await rig.run_session("run-1", owner="bob")

        response = rig.forge.client.get(run_url("run-1"), headers=as_user("alice"))

        assert response.status_code == 404

    async def test_another_tenants_sessions_are_not_visible(self, rig: Rig) -> None:
        await rig.run_session("run-1")

        response = rig.forge.client.get(run_url("run-1"), headers=as_user(OWNER, OTHER_TENANT))
        admin = rig.forge.client.get(
            run_url("run-1"), headers=as_user("admin", OTHER_TENANT, "volundr:admin")
        )

        assert response.status_code == admin.status_code == 404

    async def test_a_session_credential_is_refused(self, rig: Rig) -> None:
        await rig.run_session("run-1")

        response = rig.forge.client.get(run_url("run-1"), headers=await session_credential(rig))

        assert response.status_code == 403

    async def test_a_scoped_workload_credential_is_refused(self, rig: Rig) -> None:
        await rig.run_session("run-1")
        scoped = token(token_use="valkyrie_build", scopes=["forge:session:create"])

        response = rig.forge.client.get(run_url("run-1"), headers=bearer(scoped))

        assert response.status_code == 403

    async def test_a_workload_acting_for_someone_is_refused(self, verifying: Rig) -> None:
        await verifying.run_session("run-1")
        acting = token(OWNER, act={"sub": SUBJECT})

        response = verifying.forge.client.get(run_url("run-1"), headers=bearer(acting))

        assert response.status_code == 403


class TestEnded:
    async def test_each_session_is_active_ended_or_gone(self, rig: Rig) -> None:
        ended_at = datetime(2026, 11, 1, 12, tzinfo=UTC)
        active = await rig.run_session("r", SessionStatus.RUNNING)
        stopped = await rig.run_session("r", SessionStatus.STOPPED, updated_at=ended_at)
        failed = await rig.run_session("r", SessionStatus.FAILED, updated_at=ended_at)
        archived = await rig.run_session("r", SessionStatus.ARCHIVED, updated_at=ended_at)
        missing = uuid.uuid4()
        ids = [active.id, stopped.id, failed.id, archived.id, missing]

        response = rig.forge.client.post(ENDED_URL, json={"sessions": [str(i) for i in ids]})

        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        answer = response.json()["sessions"]
        assert answer[str(active.id)] == {"state": "active", "ended_at": None}
        for session in (stopped, failed, archived):
            entry = answer[str(session.id)]
            assert entry["state"] == "ended"
            assert datetime.fromisoformat(entry["ended_at"]) == ended_at
        assert answer[str(missing)] == {"state": "gone", "ended_at": None}

    @pytest.mark.parametrize("status", ACTIVE)
    async def test_a_session_that_has_not_stopped_is_active(
        self, rig: Rig, status: SessionStatus
    ) -> None:
        session = await rig.run_session("r", status)

        response = rig.forge.client.post(ENDED_URL, json={"sessions": [str(session.id)]})

        assert response.json()["sessions"][str(session.id)]["state"] == "active"

    @pytest.mark.parametrize("status", ENDED)
    async def test_a_session_that_stopped_failed_or_was_archived_has_ended(
        self, rig: Rig, status: SessionStatus
    ) -> None:
        session = await rig.run_session("r", status)

        response = rig.forge.client.post(ENDED_URL, json={"sessions": [str(session.id)]})

        assert response.json()["sessions"][str(session.id)]["state"] == "ended"

    async def test_a_run_is_active_while_any_session_is(self, rig: Rig) -> None:
        stamp = datetime(2026, 11, 1, tzinfo=UTC)
        await rig.run_session("run-a", SessionStatus.STOPPED, updated_at=stamp)
        await rig.run_session("run-a", SessionStatus.RUNNING)

        response = rig.forge.client.post(ENDED_URL, json={"runs": ["run-a"]})

        assert response.json()["runs"]["run-a"] == {"state": "active", "ended_at": None}

    async def test_a_run_ends_at_the_latest_end_of_its_sessions(self, rig: Rig) -> None:
        early = datetime(2026, 11, 1, tzinfo=UTC)
        late = early + timedelta(hours=5)
        await rig.run_session("run-a", SessionStatus.STOPPED, updated_at=early)
        await rig.run_session("run-a", SessionStatus.FAILED, updated_at=late)
        await rig.run_session("run-b", SessionStatus.RUNNING)

        response = rig.forge.client.post(ENDED_URL, json={"runs": ["run-a", "run-b"]})

        runs = response.json()["runs"]
        assert runs["run-a"]["state"] == "ended"
        assert datetime.fromisoformat(runs["run-a"]["ended_at"]) == late
        assert runs["run-b"]["state"] == "active"

    async def test_a_run_forge_has_no_session_of_is_gone(self, rig: Rig) -> None:
        response = rig.forge.client.post(ENDED_URL, json={"runs": ["run-x"]})

        assert response.json() == {
            "sessions": {},
            "runs": {"run-x": {"state": "gone", "ended_at": None}},
        }

    async def test_an_empty_question_has_an_empty_answer(self, rig: Rig) -> None:
        response = rig.forge.client.post(ENDED_URL, json={})

        assert response.json() == {"sessions": {}, "runs": {}}

    async def test_a_session_id_is_answered_under_the_spelling_it_was_asked_in(
        self, rig: Rig
    ) -> None:
        session = await rig.run_session("r", SessionStatus.STOPPED)
        shouted = str(session.id).upper()

        response = rig.forge.client.post(ENDED_URL, json={"sessions": [shouted]})

        assert response.json()["sessions"][shouted]["state"] == "ended"

    async def test_a_value_that_is_not_a_session_id_is_a_422(self, rig: Rig) -> None:
        response = rig.forge.client.post(ENDED_URL, json={"sessions": ["not-a-uuid"]})

        assert response.status_code == 422
        assert "not-a-uuid" in response.json()["detail"]

    async def test_unknown_fields_are_refused(self, rig: Rig) -> None:
        response = rig.forge.client.post(ENDED_URL, json={"sessions": [], "layers": []})

        assert response.status_code == 422

    async def test_a_session_credential_is_refused(self, rig: Rig) -> None:
        response = rig.forge.client.post(ENDED_URL, json={}, headers=await session_credential(rig))

        assert response.status_code == 403

    async def test_a_scoped_workload_credential_is_refused_even_in_mini_mode(
        self, rig: Rig
    ) -> None:
        scoped = token(token_use="valkyrie_build", scopes=["forge:session:create"])

        response = rig.forge.client.post(ENDED_URL, json={}, headers=bearer(scoped))

        assert response.status_code == 403


class TestGatewayServiceCredential:
    """Where Forge verifies its callers, only the gateway's workload may ask."""

    def gateway(self, **claims) -> dict[str, str]:
        return bearer(token("gateway-owner", act={"sub": SUBJECT}, **claims))

    async def test_the_gateways_credential_is_admitted(self, verifying: Rig) -> None:
        session = await verifying.run_session("r", SessionStatus.STOPPED)

        response = verifying.forge.client.post(
            ENDED_URL, json={"sessions": [str(session.id)]}, headers=self.gateway()
        )

        assert response.status_code == 200, response.text
        assert response.json()["sessions"][str(session.id)]["state"] == "ended"

    async def test_a_person_is_refused(self, verifying: Rig) -> None:
        for person in (OWNER, "admin"):
            response = verifying.forge.client.post(
                ENDED_URL,
                json={},
                headers=bearer(token(person, roles=["volundr:admin", "volundr:developer"])),
            )

            assert response.status_code == 403
            assert "memory gateway's own service credential" in response.json()["detail"]

    async def test_another_workload_is_refused(self, verifying: Rig) -> None:
        other = bearer(token("owner", act={"sub": "system:serviceaccount:niuu:ting"}))

        response = verifying.forge.client.post(ENDED_URL, json={}, headers=other)

        assert response.status_code == 403

    async def test_a_session_credential_is_refused(self, verifying: Rig) -> None:
        response = verifying.forge.client.post(
            ENDED_URL, json={}, headers=await session_credential(verifying)
        )

        assert response.status_code == 403

    async def test_the_gateway_subject_on_a_scoped_token_is_refused(self, verifying: Rig) -> None:
        scoped = self.gateway(token_use="valkyrie_build", scopes=["forge:session:create"])

        response = verifying.forge.client.post(ENDED_URL, json={}, headers=scoped)

        assert response.status_code == 403

    async def test_a_request_without_a_bearer_is_unauthorized(self, verifying: Rig) -> None:
        response = verifying.forge.client.post(ENDED_URL, json={})

        assert response.status_code == 401

    async def test_a_forge_that_is_not_told_who_the_gateway_is_admits_no_one(self) -> None:
        rig = build(allow_all=False, subject="")

        response = rig.forge.client.post(
            ENDED_URL, json={}, headers=bearer(token("owner", act={"sub": ""}))
        )

        assert response.status_code == 403
        assert "memory.gateway_service_subject" in response.json()["detail"]


class TestPolicy:
    """The session allow-list is closed to these routes."""

    def test_no_session_credential_route_matches(self) -> None:
        for method, path in (
            ("GET", role_url(uuid.uuid4())),
            ("GET", run_url("run-1")),
            ("POST", ENDED_URL),
        ):
            assert match_policy(method, path) is None


class TestThroughThePlatformsFrontDoor:
    """The Guild's Forge front door forwards all three routes to the node's Forge. Step 1's
    live run 404'd on a route the front door did not forward."""

    @pytest.fixture
    def door(self, rig: Rig):
        from tests.test_niuu.test_rest_volundr import _client, _headers, _instance

        local = _instance(
            "local", base_url="embedded://local", is_default=True, config={"transport": "embedded"}
        )
        return _client([local], embedded_forge_app=rig.forge.app), _headers()

    async def test_a_persons_role_run_owner_and_the_ended_question_all_arrive(
        self, rig: Rig, door
    ) -> None:
        client, headers = door
        project = await rig.repo.register(
            ForgeProject(name="Lexi", owner_id="user-a", tenant_id="tenant-a")
        )
        run_session = await rig.forge.sessions.create(
            Session(
                name="planner",
                owner_id="user-a",
                tenant_id="tenant-a",
                status=SessionStatus.STOPPED,
                workload_config={WORKFLOW_RUN_ID_KEY: "run-9"},
            )
        )

        role = client.get(f"{PREFIX}/projects/{project.id}/role", headers=headers)
        owner = client.get(f"{PREFIX}/memory/runs/run-9", headers=headers)
        ended = client.post(
            ENDED_URL,
            headers=headers,
            json={"sessions": [str(run_session.id), str(uuid.uuid4())], "runs": ["run-9", "x"]},
        )

        assert role.json() == {"project_id": str(project.id), "role": "owner"}
        assert owner.json() == {
            "run_id": "run-9",
            "owner_id": "user-a",
            "tenant_id": "tenant-a",
            "session_ids": [str(run_session.id)],
        }
        answer = ended.json()
        assert answer["sessions"][str(run_session.id)]["state"] == "ended"
        assert {k: v["state"] for k, v in answer["runs"].items()} == {
            "run-9": "ended",
            "x": "gone",
        }
        assert [r.headers["cache-control"] for r in (owner, ended)] == ["no-store", "no-store"]

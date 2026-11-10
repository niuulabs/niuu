"""``GET /sessions/{id}/people``: who is on the session's project, for the session itself."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from niuu.domain.agent_directory import AgentSkill
from niuu.domain.services.forge_session_policy import policy_refusal
from niuu.domain.services.token_scope import FORGE_NOTIFY_SCOPE, FORGE_SESSION_READ_SCOPE
from niuu.forge_mcp.models import ForgeApiError
from niuu.forge_mcp.rest_client import RestForgeClient
from tests.support.forge_session import OWNER, PREFIX, TENANT, bearer, build_forge_app
from tests.test_projects.conftest import MemoryProjectMembers
from volundr.domain.projects import ProjectMember, ProjectRole, SessionCoordination

PROJECT = uuid.uuid4()
OWNER_HEADERS = {"x-auth-user-id": OWNER, "x-auth-tenant": TENANT}


def _member(user_id: str, role: ProjectRole, **extra) -> ProjectMember:
    return ProjectMember(
        project_id=PROJECT,
        user_id=user_id,
        tenant_id=TENANT,
        role=role,
        display_name=user_id.title(),
        **extra,
    )


@pytest.fixture
async def forge():
    members = MemoryProjectMembers()
    for member in (
        _member("alice", ProjectRole.OWNER, expertise="Kubernetes, Cilium"),
        _member(
            "bob",
            ProjectRole.VIEWER,
            skills=[AgentSkill(id="billing", name="Billing", tags=["invoices"])],
        ),
        _member("carol", ProjectRole.APPROVER),
        _member("dora", ProjectRole.CONTRIBUTOR),
    ):
        await members.add(member)
    # A row from another org never reaches the agent.
    await members.add(
        ProjectMember(project_id=PROJECT, user_id="mallory", tenant_id="other", role="owner")
    )
    return build_forge_app(project_members=members)


async def _own_session(forge, **extra):
    session = await forge.session("own", **extra)
    credential = await forge.start(session)
    return session, credential.token


def _in_project():
    return {"coordination": SessionCoordination(project_id=PROJECT)}


class TestOwnCredential:
    async def test_lists_members_with_role_expertise_skills_and_gate_authority(self, forge):
        session, token = await _own_session(forge, **_in_project())

        response = forge.client.get(f"{PREFIX}/sessions/{session.id}/people", headers=bearer(token))

        assert response.status_code == 200
        people = {p["user_id"]: p for p in response.json()}
        assert set(people) == {"alice", "bob", "carol", "dora"}
        assert people["alice"] == {
            "user_id": "alice",
            "display_name": "Alice",
            "role": "owner",
            "expertise": "Kubernetes, Cilium",
            "skills": [],
            "can_answer_gates": True,
        }
        assert people["bob"]["skills"][0]["id"] == "billing"
        assert people["bob"]["skills"][0]["tags"] == ["invoices"]
        assert {u: p["can_answer_gates"] for u, p in people.items()} == {
            "alice": True,
            "bob": False,
            "carol": True,
            "dora": False,
        }

    async def test_a_session_without_a_project_is_a_409_not_an_empty_list(self, forge):
        session, token = await _own_session(forge)

        response = forge.client.get(f"{PREFIX}/sessions/{session.id}/people", headers=bearer(token))

        assert response.status_code == 409
        assert "not attached to a project" in response.json()["detail"]

    async def test_a_token_bound_to_another_session_is_refused(self, forge):
        _, token = await _own_session(forge, **_in_project())
        peer = await forge.session("peer", **_in_project())

        response = forge.client.get(f"{PREFIX}/sessions/{peer.id}/people", headers=bearer(token))

        assert response.status_code == 403
        assert "bound to another session" in response.json()["detail"]

    async def test_an_unknown_session_is_a_404_for_the_owner(self, forge):
        response = forge.client.get(
            f"{PREFIX}/sessions/{uuid.uuid4()}/people", headers=OWNER_HEADERS
        )
        assert response.status_code == 404

    async def test_a_stopped_session_credential_is_revoked(self, forge):
        from volundr.domain.models import SessionStatus

        session, token = await _own_session(forge, **_in_project())
        await forge.set_status(session.id, SessionStatus.STOPPED)

        response = forge.client.get(f"{PREFIX}/sessions/{session.id}/people", headers=bearer(token))

        assert response.status_code == 401


class TestOwner:
    async def test_the_session_owner_lists_without_a_session_credential(self, forge):
        session = await forge.session("owned", **_in_project())

        response = forge.client.get(f"{PREFIX}/sessions/{session.id}/people", headers=OWNER_HEADERS)

        assert response.status_code == 200
        assert [p["user_id"] for p in response.json()] == ["alice", "bob", "carol", "dora"]

    async def test_a_host_without_projects_says_so(self):
        forge = build_forge_app()
        session = await forge.session("owned", **_in_project())

        response = forge.client.get(f"{PREFIX}/sessions/{session.id}/people", headers=OWNER_HEADERS)

        assert response.status_code == 409
        assert "projects.enabled" in response.json()["detail"]


class TestPolicy:
    def test_the_route_needs_the_read_scope(self):
        path = f"{PREFIX}/sessions/{uuid.uuid4()}/people"
        refusal = policy_refusal("GET", path, (FORGE_NOTIFY_SCOPE,))
        assert refusal is not None and FORGE_SESSION_READ_SCOPE in refusal
        assert policy_refusal("GET", path, (FORGE_SESSION_READ_SCOPE,)) is None

    def test_only_reading_is_allowed(self):
        path = f"{PREFIX}/sessions/{uuid.uuid4()}/people"
        scopes = (FORGE_SESSION_READ_SCOPE, FORGE_NOTIFY_SCOPE)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert policy_refusal(method, path, scopes) is not None


class TestRestClient:
    def _client(self, handler) -> RestForgeClient:
        async def factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://f")

        return RestForgeClient(
            http_client=factory, headers={"Authorization": "Bearer t"}, timeout_s=5
        )

    async def test_reads_the_sessions_people(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=[{"user_id": "alice"}])

        session_id = str(uuid.uuid4())
        people = await self._client(handler).list_session_people(session_id)

        assert people == [{"user_id": "alice"}]
        [request] = seen
        assert (request.method, request.url.path) == (
            "GET",
            f"{PREFIX}/sessions/{session_id}/people",
        )
        assert request.headers["authorization"] == "Bearer t"

    async def test_no_project_is_forges_error_not_an_empty_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"detail": "not attached to a project"})

        with pytest.raises(ForgeApiError) as raised:
            await self._client(handler).list_session_people(str(uuid.uuid4()))
        assert raised.value.status == 409 and "not attached" in raised.value.detail

    async def test_an_object_where_a_list_is_expected_is_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=json.dumps({"people": []}))

        with pytest.raises(ForgeApiError, match="unexpected response shape"):
            await self._client(handler).list_session_people(str(uuid.uuid4()))

"""GET /sessions/{id}/memory-context: what the memory gateway asks Forge about a session."""

from __future__ import annotations

import uuid
from uuid import UUID

import jwt
import pytest

from niuu.domain.models import Principal
from niuu.domain.services.token_scope import FORGE_MEMORY_SCOPE, FORGE_SESSION_SCOPES
from tests.support.forge_session import OWNER, PREFIX, TENANT, ForgeApp, bearer, build_forge_app
from tests.test_projects.conftest import MemoryProjects
from volundr.adapters.inbound.forge_session_auth import Binding, match_policy
from volundr.adapters.inbound.rest_memory_context import create_memory_context_router
from volundr.domain.models import SessionStatus
from volundr.domain.projects import ForgeProject, SessionCoordination
from volundr.domain.services.memory_context import MemoryContextService
from volundr.domain.workflow_session import WORKFLOW_RUN_ID_KEY

PREFIXES = ("decisions/", "runbooks/")


def _url(session_id: UUID) -> str:
    return f"{PREFIX}/sessions/{session_id}/memory-context"


class Rig:
    def __init__(self, forge: ForgeApp, projects: MemoryProjects) -> None:
        self.forge = forge
        self.projects = projects

    async def project(self, prefixes: tuple[str, ...] = PREFIXES) -> ForgeProject:
        return await self.projects.register(
            ForgeProject(name="Lexi", memory_write_prefixes=prefixes)
        )

    async def started(self, name: str = "s", **extra):
        session = await self.forge.session(name, **extra)
        return session, await self.forge.start(session)


def _rig(*, with_projects: bool = True) -> Rig:
    forge = build_forge_app()
    projects = MemoryProjects()
    forge.app.include_router(
        create_memory_context_router(
            MemoryContextService(projects if with_projects else None), forge.session_service
        )
    )
    return Rig(forge, projects)


@pytest.fixture
def rig() -> Rig:
    return _rig()


class TestAnswer:
    async def test_names_the_layers_of_a_session_in_a_project_and_run(self, rig: Rig) -> None:
        project = await rig.project()
        session, credential = await rig.started(
            coordination=SessionCoordination(project_id=project.id),
            workload_config={WORKFLOW_RUN_ID_KEY: "run-7"},
        )

        response = rig.forge.client.get(_url(session.id), headers=bearer(credential.token))

        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "session_id": str(session.id),
            "launch_id": credential.launch_id,
            "owner_id": OWNER,
            "tenant_id": TENANT,
            "project_id": str(project.id),
            "workflow_run_id": "run-7",
            "project_memory": {"write_prefixes": list(PREFIXES)},
        }

    async def test_a_project_without_prefixes_answers_an_empty_list(self, rig: Rig) -> None:
        project = await rig.project(prefixes=())
        session, credential = await rig.started(
            coordination=SessionCoordination(project_id=project.id)
        )

        body = rig.forge.client.get(_url(session.id), headers=bearer(credential.token)).json()

        assert body["project_memory"] == {"write_prefixes": []}

    async def test_a_session_without_project_or_run_has_no_project_memory(self, rig: Rig) -> None:
        session, credential = await rig.started()

        body = rig.forge.client.get(_url(session.id), headers=bearer(credential.token)).json()

        assert body["project_id"] is None
        assert body["project_memory"] is None
        assert body["workflow_run_id"] is None

    async def test_the_prefixes_are_read_live_at_every_call(self, rig: Rig) -> None:
        project = await rig.project()
        session, credential = await rig.started(
            coordination=SessionCoordination(project_id=project.id)
        )
        rig.projects.projects[project.id] = project.model_copy(
            update={"memory_write_prefixes": ("plans/",)}
        )

        body = rig.forge.client.get(_url(session.id), headers=bearer(credential.token)).json()

        assert body["project_memory"] == {"write_prefixes": ["plans/"]}


class TestPolicy:
    def test_the_route_is_the_sessions_own_with_its_own_scope(self) -> None:
        sid = uuid.uuid4()
        matched = match_policy("GET", _url(sid))
        assert matched is not None
        policy, params = matched
        assert policy.scope == FORGE_MEMORY_SCOPE
        assert policy.binding is Binding.OWN
        assert params["session_id"] == str(sid)

    def test_only_get_is_admitted(self) -> None:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert match_policy(method, _url(uuid.uuid4())) is None

    def test_the_scope_belongs_to_every_session_credential(self) -> None:
        assert FORGE_MEMORY_SCOPE in FORGE_SESSION_SCOPES


class TestRefusals:
    async def test_another_sessions_context_is_refused(self, rig: Rig) -> None:
        _, credential = await rig.started("own")
        other = await rig.forge.session("other")

        response = rig.forge.client.get(_url(other.id), headers=bearer(credential.token))

        assert response.status_code == 403
        assert "another session" in response.json()["detail"]

    async def test_a_credential_without_the_scope_is_refused(self, rig: Rig) -> None:
        session, credential = await rig.started()
        claims = jwt.decode(credential.token, options={"verify_signature": False})
        narrowed = [scope for scope in claims["scopes"] if scope != FORGE_MEMORY_SCOPE]
        token = rig.forge.tokens.signing_issuer.issue_token(
            principal=Principal(
                user_id=OWNER, email="", tenant_id=TENANT, roles=["volundr:developer"]
            ),
            workload_subject=f"forge-session:{session.id}",
            workload_name="s",
            audiences=["volundr-api"],
            token_use="forge_session",
            claims={
                "session_id": str(session.id),
                "launch_id": credential.launch_id,
                "scopes": narrowed,
            },
            ttl_seconds=600,
        ).token

        response = rig.forge.client.get(_url(session.id), headers=bearer(token))

        assert response.status_code == 403
        assert FORGE_MEMORY_SCOPE in response.json()["detail"]

    async def test_a_person_is_refused_even_on_their_own_session(self, rig: Rig) -> None:
        """It is a session-only route: Forge's allow-all identity admits the caller, and the
        route still wants the session's own credential."""
        session, _ = await rig.started()

        response = rig.forge.client.get(_url(session.id))

        assert response.status_code == 403
        assert "session's own Forge session credential" in response.json()["detail"]

    async def test_a_plain_bearer_token_is_refused(self, rig: Rig) -> None:
        session, _ = await rig.started()
        plain = jwt.encode({"sub": OWNER, "exp": 4102444800}, "k" * 32, algorithm="HS256")

        response = rig.forge.client.get(_url(session.id), headers=bearer(plain))

        assert response.status_code == 403

    @pytest.mark.parametrize("state", [SessionStatus.STOPPED, SessionStatus.ARCHIVED])
    async def test_a_stopped_session_has_no_memory_access(
        self, rig: Rig, state: SessionStatus
    ) -> None:
        session, credential = await rig.started()
        await rig.forge.set_status(session.id, state)

        response = rig.forge.client.get(_url(session.id), headers=bearer(credential.token))

        assert response.status_code == 401
        assert state.value in response.json()["detail"]

    async def test_a_restart_revokes_the_previous_launchs_credential(self, rig: Rig) -> None:
        session, first = await rig.started()
        await rig.forge.set_status(session.id, SessionStatus.STOPPED)
        await rig.forge.start(await rig.forge.sessions.get(session.id))

        response = rig.forge.client.get(_url(session.id), headers=bearer(first.token))

        assert response.status_code == 401
        assert "restarted" in response.json()["detail"]


class TestMisconfiguration:
    async def test_a_session_whose_project_is_gone_is_a_409_naming_the_fix(self, rig: Rig) -> None:
        session, credential = await rig.started(
            coordination=SessionCoordination(project_id=uuid.uuid4())
        )

        response = rig.forge.client.get(_url(session.id), headers=bearer(credential.token))

        assert response.status_code == 409
        assert "detach the session" in response.json()["detail"]

    async def test_a_forge_without_projects_cannot_answer_for_a_project_session(self) -> None:
        rig = _rig(with_projects=False)
        session, credential = await rig.started(
            coordination=SessionCoordination(project_id=uuid.uuid4())
        )

        response = rig.forge.client.get(_url(session.id), headers=bearer(credential.token))

        assert response.status_code == 503
        assert "projects.enabled" in response.json()["detail"]

    async def test_a_forge_without_projects_still_answers_for_a_plain_session(self) -> None:
        rig = _rig(with_projects=False)
        session, credential = await rig.started()

        response = rig.forge.client.get(_url(session.id), headers=bearer(credential.token))

        assert response.status_code == 200
        assert response.json()["project_memory"] is None

"""``POST /api/v1/forge/teams``: a team started over HTTP, or nothing created.

The real session, forge, participant and team services over in-memory state, with real Cedar
authorization; only the pod manager, repositories, user directory and persona catalog are fakes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.services.forge_session_policy import match_policy
from tests.support.team_rig import DIRECTORY, TENANT, TeamRig
from volundr.adapters.inbound.rest_session_participants import invitations_refused
from volundr.adapters.inbound.rest_teams import TASK_MAX_CHARS, create_teams_router
from volundr.adapters.outbound.identity import AllowAllIdentityAdapter
from volundr.domain.execution_catalog import ExecutionCatalogError, ExecutionSelectionError
from volundr.domain.models import SessionStatus, WorkloadTypeRemovedError
from volundr.domain.ports import PersonaNotPlayableError, SessionCapacity
from volundr.domain.project_memory import MemoryDirectoryUnavailableError, ProjectMemoryError
from volundr.domain.services import RepoValidationError, SessionAccessDeniedError
from volundr.domain.services.forge import HostedUnitNotFoundError
from volundr.domain.services.forge_session_launch import ForgeMcpGrantEscalationError
from volundr.domain.services.session import SessionCapacityError, SessionStateError
from volundr.domain.services.team import TeamError, TeamRollbackError, TeamRuntimeError

PREFIX = "/api/v1/forge"
HEADERS = {"x-auth-user-id": "ada", "x-auth-tenant": TENANT, "x-auth-roles": "volundr:developer"}
SOURCE = {"type": "git", "repo": "https://github.com/acme/repo", "branch": "main"}


def _body(**fields) -> dict:
    body = {
        "name": "ship-it",
        "source": SOURCE,
        "members": [
            {
                "model": "claude-sonnet-4-6",
                "definition": "skuldClaude",
                "persona_name": "architect",
                "lead": True,
            },
            {"model": "gpt-5", "definition": "skuldCodex", "persona_name": "coder"},
        ],
    }
    body.update(fields)
    return body


def _client(rig: TeamRig, service=None, instance_fields=None) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_teams_router(
            service or rig.service,
            rig.participants,
            server_public_host="127.0.0.1",
            instance_fields=instance_fields,
            prefix=PREFIX,
        )
    )
    users = AsyncMock()
    users.get.side_effect = DIRECTORY.get
    app.state.identity = AllowAllIdentityAdapter(user_repository=users)
    return TestClient(app)


@pytest.fixture
def rig() -> TeamRig:
    return TeamRig()


def _post(client: TestClient, body: dict):
    return client.post(f"{PREFIX}/teams", json=body, headers=HEADERS)


class TestStartingATeam:
    async def test_the_response_is_the_root_and_its_members(self, rig: TeamRig):
        response = _post(_client(rig), _body(task="Fix the login test", task_to="lead"))
        await rig.settle()

        assert response.status_code == 201, response.text
        answer = response.json()
        root, members = answer["session"], answer["members"]
        assert root["name"] == "ship-it"
        assert root["team"] == {"task_to": "lead"}
        assert [m["name"] for m in members] == ["ship-it-1", "ship-it-2"]
        assert [m["team_member"] for m in members] == [{"lead": True}, {"lead": False}]
        assert {m["hosted_by"] for m in members} == {root["id"]}
        assert [m["model"] for m in members] == ["claude-sonnet-4-6", "gpt-5"]

    async def test_people_are_counted_with_the_invitations_not_yet_accepted_left_out(
        self, rig: TeamRig
    ):
        response = _post(_client(rig), _body(invitees=["bo"]))
        await rig.settle()

        assert response.status_code == 201, response.text
        assert response.json()["session"]["people_count"] == 1
        grants = await rig.grants.list_for_session(
            next(s.id for s in await rig.repository.list() if s.name == "ship-it")
        )
        assert [(g.user_id, g.role.value) for g in grants] == [("bo", "viewer")]

    async def test_the_instance_a_guild_node_names_is_on_every_session(self, rig: TeamRig):
        response = _post(_client(rig, instance_fields={"instance_name": "thor"}), _body())
        await rig.settle()

        answer = response.json()
        assert answer["session"]["instance_name"] == "thor"
        assert {m["instance_name"] for m in answer["members"]} == {"thor"}

    async def test_the_gateways_host_selector_places_the_whole_unit(self, rig: TeamRig):
        selector = {"tags": ["gpu"], "match": "all"}
        response = _post(_client(rig), _body(workload_config={"host_selector": selector}))
        await rig.settle()

        assert response.status_code == 201, response.text
        root = next(s for s in await rig.repository.list() if s.name == "ship-it")
        assert root.workload_config["host_selector"] == selector

    async def test_the_team_is_started_and_not_held(self, rig: TeamRig):
        _post(_client(rig), _body())
        await rig.settle()

        roots = [s for s in await rig.repository.list() if s.name == "ship-it"]
        assert roots[0].status != SessionStatus.CREATED
        assert len(rig.pod_manager.start_calls) == 1


class TestRefusals:
    @pytest.mark.parametrize(
        ("body", "named"),
        [
            (_body(members=[]), "members"),
            (_body(name="Ship It"), "lowercase"),
            (_body(name=""), "name"),
            (_body(task="x" * (TASK_MAX_CHARS + 1), task_to="lead"), "task"),
            (_body(task_to="somebody"), "task_to"),
            (
                _body(members=[{"model": "", "lead": True}]),
                "model",
            ),
            (_body(workload_config={"mimir": {}}), "takes only host_selector"),
            (_body(persona="coder"), "persona"),
        ],
    )
    async def test_a_malformed_request_is_422_and_creates_nothing(
        self, rig: TeamRig, body: dict, named: str
    ):
        response = _post(_client(rig), body)

        assert response.status_code == 422
        assert named in response.text
        assert await rig.repository.list() == []

    @pytest.mark.parametrize(
        ("body", "named"),
        [
            (_body(task="do it"), "needs task_to"),
            (_body(task_to="lead"), "task_to is only for a team started with a task"),
            (
                _body(members=[{"model": "gpt-5"}, {"model": "gpt-5"}]),
                "0 members are marked lead",
            ),
            (
                _body(members=[{"model": "gpt-5", "lead": True}, {"model": "gpt-5", "lead": True}]),
                "2 members are marked lead",
            ),
            (_body(invitees=["bo", "bo"]), "lists a person twice"),
            (_body(invitees=["zed"]), "cannot invite user 'zed'"),
            (
                _body(members=[{"model": "gpt-5", "lead": True, "persona_name": "ghost"}]),
                "'ghost' was not found",
            ),
        ],
    )
    async def test_an_invalid_team_is_422_naming_the_fix_and_leaves_nothing(
        self, rig: TeamRig, body: dict, named: str
    ):
        response = _post(_client(rig), body)

        assert response.status_code == 422
        assert named in response.json()["detail"]
        assert await rig.repository.list() == []

    async def test_a_runtime_that_cannot_host_a_team_is_409_naming_it(self):
        rig = TeamRig(runtime_backend="docker")

        response = _post(_client(rig), _body())

        assert response.status_code == 409
        assert "'docker' runtime" in response.json()["detail"]
        assert await rig.repository.list() == []

    async def test_invitees_where_a_grant_could_never_attach_are_409(self):
        rig = TeamRig(invitations_refused="its pods authorize attach by ownership only.")

        response = _post(_client(rig), _body(invitees=["bo"]))

        assert response.status_code == 409
        assert "authorize attach by ownership" in response.json()["detail"]
        assert await rig.repository.list() == []

    async def test_a_persona_a_harness_cannot_play_is_422_and_leaves_nothing(self):
        rig = TeamRig(refused_models={"gpt-5": "no harness plays this persona"})

        response = _post(_client(rig), _body())

        assert response.status_code == 422
        assert "no harness plays this persona" in response.json()["detail"]
        assert await rig.repository.list() == []


class TestErrorsOfTheLaunchAreAnsweredAsTheSessionRouteAnswersThem:
    @pytest.mark.parametrize(
        ("error", "code"),
        [
            (SessionAccessDeniedError(uuid4(), "ada"), 403),
            (ForgeMcpGrantEscalationError(None, []), 403),
            (HostedUnitNotFoundError(uuid4(), "unit is gone"), 404),
            (RepoValidationError("acme/repo", "no such repository"), 422),
            (PersonaNotPlayableError("persona cannot run there"), 422),
            (WorkloadTypeRemovedError("workload type removed"), 422),
            (ProjectMemoryError("memory mount is wrong"), 422),
            (TeamError("not a team"), 422),
            (ExecutionSelectionError("bad selection"), 422),
            (TeamRuntimeError("cannot host"), 409),
            (SessionCapacityError(SessionCapacity(limit=2, active=2, remedy="raise the cap")), 409),
            (SessionStateError(uuid4(), "start", SessionStatus.RUNNING), 409),
            (MemoryDirectoryUnavailableError("registry unreachable"), 503),
            (ExecutionCatalogError("catalog broken"), 503),
            (TeamRollbackError("could not remove session x"), 500),
        ],
        ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value),
    )
    async def test_the_status(self, rig: TeamRig, error: Exception, code: int):
        service = AsyncMock()
        service.start_team.side_effect = error

        response = _post(_client(rig, service), _body())

        assert response.status_code == code


class TestWhoMayStartATeam:
    def test_a_session_broker_cannot_reach_the_route(self):
        assert match_policy("POST", f"{PREFIX}/teams") is None


class TestWhereInvitationsWork:
    @pytest.mark.parametrize(
        ("backend", "role_source"),
        [("process", "deployment"), ("vm", "deployment"), ("kubernetes", "remote")],
    )
    def test_a_grant_can_attach(self, backend: str, role_source: str):
        assert invitations_refused(backend, role_source) is None

    @pytest.mark.parametrize(
        ("backend", "named"),
        [
            ("kubernetes", "authorize attach by ownership only"),
            ("docker", "not reached through Forge's session proxy"),
            ("openshell", "not reached through Forge's session proxy"),
        ],
    )
    def test_a_grant_that_could_never_attach_is_refused_with_the_remedy(
        self, backend: str, named: str
    ):
        assert named in invitations_refused(backend, "deployment")

"""``GET /sessions/{id}/members`` and the team fields every session response carries.

Real Cedar end to end, over in-memory repositories: the owner and an invited room
participant may list the members of a session, a stranger may not, and a running session
whose room cannot be read is a 502 that names the cause.
"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.cedar import CedarAuthorizationAdapter
from identity.models import TenantMembership, User
from niuu.domain.models import Principal
from tests.conftest import (
    InMemorySessionParticipantRepository,
    InMemorySessionRepository,
    MockPodManager,
)
from volundr.adapters.inbound.rest import create_router
from volundr.adapters.inbound.rest_session_members import create_session_members_router
from volundr.adapters.outbound.identity import AllowAllIdentityAdapter
from volundr.config import LocalMountsConfig
from volundr.domain.models import (
    ConnectedPerson,
    GitSource,
    RoomParticipantInfo,
    RoomRoster,
    Session,
    SessionStatus,
)
from volundr.domain.services.session import SessionService
from volundr.domain.services.session_members import SessionMembersService
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.session_participants import ParticipantRole
from volundr.domain.team import TEAM_KEY, TEAM_MEMBER_KEY

PREFIX = "/api/v1/forge"
TENANT = "acme"
ROLES = "volundr:developer"


def _headers(user_id: str) -> dict:
    return {"x-auth-user-id": user_id, "x-auth-tenant": TENANT, "x-auth-roles": ROLES}


class FakeRoom:
    def __init__(self) -> None:
        self.roster: list[RoomParticipantInfo] = []
        self.connected: list[ConnectedPerson] = []
        self.error: Exception | None = None

    async def read_room(self, session_id):
        if self.error is not None:
            raise self.error
        return RoomRoster(participants=list(self.roster), connected_people=list(self.connected))


class Forge:
    def __init__(self) -> None:
        self.repository = InMemorySessionRepository()
        self.session_service = SessionService(
            self.repository, MockPodManager(), authorization=CedarAuthorizationAdapter()
        )
        users = AsyncMock()
        directory = {
            "ada": User(id="ada", email="ada@example.test", display_name="Ada"),
            "bo": User(id="bo", email="bo@example.test", display_name="Bo"),
            "cy": User(id="cy", email="cy@example.test", display_name="Cy"),
        }
        users.get.side_effect = directory.get
        users.get_memberships.return_value = [TenantMembership(user_id="x", tenant_id=TENANT)]
        self.grants = InMemorySessionParticipantRepository()
        self.participants = SessionParticipantService(self.grants, self.session_service, users)
        self.room = FakeRoom()
        app = FastAPI()
        app.include_router(
            create_router(
                self.session_service,
                prefix=PREFIX,
                session_participant_service=self.participants,
            )
        )
        app.include_router(
            create_session_members_router(
                SessionMembersService(
                    self.repository, self.session_service, self.participants, self.room
                ),
                prefix=PREFIX,
            )
        )
        app.state.identity = AllowAllIdentityAdapter(user_repository=users)
        app.state.settings = type("Settings", (), {"local_mounts": LocalMountsConfig()})()
        app.state.admin_settings = {}
        self.client = TestClient(app)

    async def session(self, name: str = "solo", **fields) -> Session:
        fields.setdefault("owner_id", "ada")
        fields.setdefault("tenant_id", TENANT)
        return await self.repository.create(
            Session(
                name=name,
                model=fields.pop("model", "claude-sonnet-4-6"),
                source=GitSource(repo="https://github.com/acme/repo", branch="main"),
                **fields,
            )
        )

    async def invite(self, session: Session, user_id: str, *, accept: bool) -> None:
        owner = Principal("ada", "", TENANT, [ROLES])
        await self.participants.invite(
            session, owner, user_id=user_id, role=ParticipantRole.VIEWER, expires_at=None
        )
        if accept:
            await self.participants.accept(session, Principal(user_id, "", TENANT, [ROLES]))

    def members(self, session: Session, user_id: str):
        return self.client.get(f"{PREFIX}/sessions/{session.id}/members", headers=_headers(user_id))


@pytest.fixture
def forge() -> Forge:
    return Forge()


async def _team(forge: Forge) -> tuple[Session, Session, Session]:
    root = await forge.session(
        "team", workload_config={TEAM_KEY: {"task_to": "lead"}, "hosted_unit": {}}
    )
    lead = await forge.session(
        "planner",
        session_definition="skuldClaude",
        workload_config={TEAM_MEMBER_KEY: {"lead": True}, "persona": "architect"},
    )
    worker = await forge.session(
        "coder",
        model="gpt-5",
        session_definition="skuldCodex",
        workload_config={TEAM_MEMBER_KEY: {"lead": False}, "persona": "coder"},
    )
    lead = await forge.repository.attach_to_unit(lead.id, root.id, 8000)
    worker = await forge.repository.attach_to_unit(worker.id, root.id, 8000)
    root = await forge.repository.update(root.model_copy(update={"status": SessionStatus.RUNNING}))
    return root, lead, worker


class TestMembersRoute:
    async def test_a_team_root_answers_with_its_agents_and_people(self, forge):
        root, lead, worker = await _team(forge)
        await forge.invite(root, "bo", accept=True)
        await forge.invite(root, "cy", accept=False)
        forge.room.roster = [
            RoomParticipantInfo(peer_id="ada", persona="", participant_type="human"),
        ]
        forge.room.connected = [ConnectedPerson("bo", "Bo")]

        response = forge.members(root, "ada")

        assert response.status_code == 200
        assert response.json() == {
            "agents": [
                {
                    "session_id": str(lead.id),
                    "name": "planner",
                    "persona_name": "architect",
                    "model": "claude-sonnet-4-6",
                    "definition": "skuldClaude",
                    "status": "running",
                    "lead": True,
                },
                {
                    "session_id": str(worker.id),
                    "name": "coder",
                    "persona_name": "coder",
                    "model": "gpt-5",
                    "definition": "skuldCodex",
                    "status": "running",
                    "lead": False,
                },
            ],
            "people": [
                {
                    "user_id": "ada",
                    "display_name": "Ada",
                    "role": "owner",
                    "status": "active",
                    "online": True,
                },
                {
                    "user_id": "bo",
                    "display_name": "Bo",
                    "role": "viewer",
                    "status": "active",
                    "online": True,
                },
                {
                    "user_id": "cy",
                    "display_name": "Cy",
                    "role": "viewer",
                    "status": "invited",
                    "online": False,
                },
            ],
        }

    async def test_a_plain_stopped_session_is_one_agent_and_nobody_is_online(self, forge):
        session = await forge.session("solo", status=SessionStatus.STOPPED)

        response = forge.members(session, "ada")

        assert response.status_code == 200
        body = response.json()
        assert [(a["session_id"], a["lead"], a["status"]) for a in body["agents"]] == [
            (str(session.id), False, "stopped")
        ]
        assert [(p["user_id"], p["online"]) for p in body["people"]] == [("ada", None)]

    async def test_an_active_participant_may_list_who_else_is_in_the_room(self, forge):
        root, _, _ = await _team(forge)
        await forge.invite(root, "bo", accept=True)

        response = forge.members(root, "bo")

        assert response.status_code == 200
        assert [p["user_id"] for p in response.json()["people"]] == ["ada", "bo"]

    async def test_an_invitee_who_has_not_accepted_may_not(self, forge):
        root, _, _ = await _team(forge)
        await forge.invite(root, "cy", accept=False)

        assert forge.members(root, "cy").status_code == 403

    async def test_a_stranger_is_refused(self, forge):
        root, _, _ = await _team(forge)

        response = forge.members(root, "mallory")

        assert response.status_code == 403

    async def test_a_member_whose_unit_is_gone_is_a_409_naming_the_unit(self, forge):
        unit_id = uuid4()
        orphan = await forge.session("orphan", hosted_by=unit_id, hosted_port=8000)

        response = forge.members(orphan, "ada")

        assert response.status_code == 409
        assert str(unit_id) in response.json()["detail"]
        assert "no longer exists" in response.json()["detail"]

    async def test_an_unknown_session_is_a_404(self, forge):
        response = forge.client.get(f"{PREFIX}/sessions/{uuid4()}/members", headers=_headers("ada"))

        assert response.status_code == 404

    async def test_a_running_session_whose_room_cannot_be_read_is_a_502_with_the_cause(self, forge):
        root, _, _ = await _team(forge)
        forge.room.error = ConnectionError("broker refused the connection")

        response = forge.members(root, "ada")

        assert response.status_code == 502
        assert "ConnectionError: broker refused the connection" in response.json()["detail"]
        assert str(root.id) in response.json()["detail"]


class TestSessionResponseFields:
    async def test_a_team_root_carries_team_and_a_member_its_lead_flag(self, forge):
        root, lead, worker = await _team(forge)
        await forge.invite(root, "bo", accept=True)
        await forge.invite(root, "cy", accept=False)

        by_id = {
            item["id"]: item
            for item in forge.client.get(f"{PREFIX}/sessions", headers=_headers("ada")).json()
        }

        root_body = by_id[str(root.id)]
        assert root_body["team"] == {"task_to": "lead"}
        assert root_body["team_member"] is None
        assert root_body["hosted_by"] is None
        # The owner and the one accepted grant: an open invitation is not a person yet.
        assert root_body["people_count"] == 2
        assert by_id[str(lead.id)]["team_member"] == {"lead": True}
        assert by_id[str(worker.id)]["team_member"] == {"lead": False}
        assert by_id[str(lead.id)]["hosted_by"] == str(root.id)
        assert by_id[str(lead.id)]["team"] is None
        assert by_id[str(lead.id)]["people_count"] == 1

    async def test_listing_counts_every_session_in_one_query(self, forge):
        for index in range(5):
            session = await forge.session(f"s{index}")
            await forge.invite(session, "bo", accept=True)
        forge.grants.count_active_for_sessions = AsyncMock(
            wraps=forge.grants.count_active_for_sessions
        )

        response = forge.client.get(f"{PREFIX}/sessions", headers=_headers("ada"))

        assert [item["people_count"] for item in response.json()] == [2] * 5
        forge.grants.count_active_for_sessions.assert_awaited_once()

    async def test_a_single_session_read_counts_its_people_too(self, forge):
        session = await forge.session("solo")
        await forge.invite(session, "bo", accept=True)
        await forge.invite(session, "cy", accept=True)

        response = forge.client.get(f"{PREFIX}/sessions/{session.id}", headers=_headers("ada"))

        assert response.status_code == 200
        body = response.json()
        assert body["people_count"] == 3
        assert body["team"] is None
        assert body["team_member"] is None
        assert body["hosted_by"] is None

    async def test_a_team_started_without_a_task_has_no_task_target(self, forge):
        root = await forge.session("team", workload_config={TEAM_KEY: {"task_to": None}})

        response = forge.client.get(f"{PREFIX}/sessions/{root.id}", headers=_headers("ada"))

        assert response.json()["team"] == {"task_to": None}

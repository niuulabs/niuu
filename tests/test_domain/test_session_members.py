"""``SessionMembersService``: the agents and people of a session, and who is in its room."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from identity.models import User
from tests.conftest import (
    InMemorySessionParticipantRepository,
    InMemorySessionRepository,
    MockPodManager,
)
from volundr.domain.models import (
    ConnectedPerson,
    GitSource,
    RoomParticipantInfo,
    RoomRoster,
    Session,
    SessionStatus,
)
from volundr.domain.services.session import SessionNotFoundError, SessionService
from volundr.domain.services.session_members import (
    RoomUnreadableError,
    SessionMembersService,
    UnitNotFoundError,
)
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.session_participants import ParticipantRole
from volundr.domain.team import TEAM_KEY, TEAM_MEMBER_KEY

TENANT = "acme"
SOURCE = GitSource(repo="https://github.com/acme/repo", branch="main")


class FakeRoom:
    """The live room as the session's broker reports it."""

    def __init__(self, roster: list[RoomParticipantInfo] | None = None) -> None:
        self.roster = roster or []
        self.connected: list[ConnectedPerson] = []
        self.error: Exception | None = None
        self.asked: list[UUID] = []

    async def read_room(self, session_id: UUID) -> RoomRoster:
        self.asked.append(session_id)
        if self.error is not None:
            raise self.error
        return RoomRoster(participants=list(self.roster), connected_people=list(self.connected))


def _human(user_id: str) -> RoomParticipantInfo:
    return RoomParticipantInfo(peer_id=user_id, persona="", participant_type="human")


def _agent(peer_id: str) -> RoomParticipantInfo:
    return RoomParticipantInfo(peer_id=peer_id, persona="coder", participant_type="ravn")


def _session(name: str = "solo", **fields) -> Session:
    fields.setdefault("owner_id", "ada")
    fields.setdefault("tenant_id", TENANT)
    fields.setdefault("model", "claude-sonnet-4-6")
    return Session(name=name, source=SOURCE, **fields)


@pytest.fixture
def repository() -> InMemorySessionRepository:
    return InMemorySessionRepository()


@pytest.fixture
def grants() -> InMemorySessionParticipantRepository:
    return InMemorySessionParticipantRepository()


@pytest.fixture
def room() -> FakeRoom:
    return FakeRoom()


@pytest.fixture
def service(repository, grants, room) -> SessionMembersService:
    session_service = SessionService(repository, MockPodManager())
    users = AsyncMock()
    directory = {
        "ada": User(id="ada", email="ada@example.test", display_name="Ada Lovelace"),
        "bo": User(id="bo", email="bo@example.test", display_name=""),
        "cy": User(id="cy", email="cy@example.test", display_name="Cy"),
    }
    users.get.side_effect = directory.get
    participants = SessionParticipantService(grants, session_service, users)
    return SessionMembersService(repository, session_service, participants, room)


async def _invite(grants, session, user_id, role=ParticipantRole.VIEWER, *, expires_at=None):
    await grants.invite(session.id, user_id, TENANT, role, "ada", expires_at)


async def _team(repository) -> tuple[Session, Session, Session]:
    root = await repository.create(
        _session(
            "team",
            workload_config={TEAM_KEY: {"task_to": "lead"}, "hosted_unit": {}},
        )
    )
    lead = await repository.create(
        _session(
            "planner",
            status=SessionStatus.STARTING,
            session_definition="skuldClaude",
            workload_config={TEAM_MEMBER_KEY: {"lead": True}, "persona": "architect"},
        )
    )
    worker = await repository.create(
        _session(
            "coder",
            model="gpt-5",
            status=SessionStatus.STARTING,
            session_definition="skuldCodex",
            workload_config={TEAM_MEMBER_KEY: {"lead": False}, "persona": "coder"},
        )
    )
    lead = await repository.attach_to_unit(lead.id, root.id, 8000)
    worker = await repository.attach_to_unit(worker.id, root.id, 8000)
    root = await repository.update(root.model_copy(update={"status": SessionStatus.RUNNING}))
    return root, lead, worker


class TestAgents:
    async def test_a_plain_session_is_its_own_one_agent(self, service, repository):
        session = await repository.create(
            _session(
                "solo",
                session_definition="skuldClaude",
                workload_config={"persona": "reviewer"},
            )
        )

        members = await service.members(session.id, None)

        [agent] = members.agents
        assert agent.session_id == session.id
        assert agent.name == "solo"
        assert agent.persona_name == "reviewer"
        assert agent.model == "claude-sonnet-4-6"
        assert agent.definition == "skuldClaude"
        assert agent.status == "created"
        assert agent.lead is False

    async def test_a_team_root_lists_its_hosted_members_with_the_lead(self, service, repository):
        root, lead, worker = await _team(repository)

        members = await service.members(root.id, None)

        assert [(a.session_id, a.name, a.persona_name, a.lead) for a in members.agents] == [
            (lead.id, "planner", "architect", True),
            (worker.id, "coder", "coder", False),
        ]
        assert [(a.model, a.definition) for a in members.agents] == [
            ("claude-sonnet-4-6", "skuldClaude"),
            ("gpt-5", "skuldCodex"),
        ]

    async def test_a_member_reads_as_its_unit_shows_it(self, service, repository):
        root, lead, _ = await _team(repository)

        running = await service.members(root.id, None)

        assert [a.status for a in running.agents] == ["running", "running"]

        await repository.update(root.model_copy(update={"status": SessionStatus.STOPPED}))
        stopped = await service.members(root.id, None)

        assert [a.status for a in stopped.agents] == ["stopped", "stopped"]
        assert lead.status == SessionStatus.STARTING  # stored on its own row, shown by the unit

    async def test_a_member_asked_for_is_its_own_one_agent_in_its_unit_state(
        self, service, repository
    ):
        _, lead, _ = await _team(repository)

        members = await service.members(lead.id, None)

        [agent] = members.agents
        assert (agent.session_id, agent.status, agent.lead) == (lead.id, "running", False)

    async def test_a_unit_root_that_is_not_a_team_lists_itself(self, service, repository):
        root = await repository.create(_session("run", workload_config={"hosted_unit": {}}))
        member = await repository.create(_session("step"))
        await repository.attach_to_unit(member.id, root.id, 8000)

        members = await service.members(root.id, None)

        assert [a.session_id for a in members.agents] == [root.id]

    async def test_an_unknown_session_is_not_found(self, service):
        with pytest.raises(SessionNotFoundError):
            await service.members(uuid4(), None)

    async def test_a_member_whose_unit_is_gone_is_an_error_not_a_silent_session(
        self, service, repository
    ):
        orphan = await repository.create(_session("orphan", hosted_by=uuid4(), hosted_port=8000))

        with pytest.raises(UnitNotFoundError, match="no longer exists") as raised:
            await service.members(orphan.id, None)

        assert (raised.value.session_id, raised.value.unit_id) == (orphan.id, orphan.hosted_by)


class TestPeople:
    async def test_the_owner_leads_and_grants_follow_with_their_roles_and_names(
        self, service, repository, grants
    ):
        session = await repository.create(_session())
        await _invite(grants, session, "bo", ParticipantRole.APPROVER)
        await grants.accept(session.id, "bo")
        await _invite(grants, session, "cy")

        members = await service.members(session.id, None)

        assert [
            (p.user_id, p.display_name, p.role, p.status, p.online) for p in members.people
        ] == [
            ("ada", "Ada Lovelace", "owner", "active", None),
            ("bo", "bo@example.test", "approver", "active", None),
            ("cy", "Cy", "viewer", "invited", None),
        ]

    async def test_revoked_and_expired_grants_are_not_people_of_the_session(
        self, service, repository, grants
    ):
        session = await repository.create(_session())
        await _invite(grants, session, "bo")
        await grants.revoke(session.id, "bo")
        await _invite(grants, session, "cy", expires_at=datetime.now(UTC) - timedelta(hours=1))

        members = await service.members(session.id, None)

        assert [p.user_id for p in members.people] == ["ada"]

    async def test_a_person_the_directory_does_not_know_has_an_empty_name(
        self, service, repository, grants
    ):
        session = await repository.create(_session())
        await _invite(grants, session, "dee")

        members = await service.members(session.id, None)

        assert [(p.user_id, p.display_name) for p in members.people] == [
            ("ada", "Ada Lovelace"),
            ("dee", ""),
        ]


class TestOnline:
    async def test_a_running_session_reports_who_is_in_its_room(
        self, service, repository, grants, room
    ):
        session = await repository.create(_session(status=SessionStatus.RUNNING))
        await _invite(grants, session, "bo")
        await grants.accept(session.id, "bo")
        await _invite(grants, session, "cy")
        room.roster = [_human("ada"), _agent("cy"), _human("bo")]

        members = await service.members(session.id, None)

        assert room.asked == [session.id]
        # cy has the same id as an agent peer in the room: only people count as online.
        assert [(p.user_id, p.online) for p in members.people] == [
            ("ada", True),
            ("bo", True),
            ("cy", False),
        ]

    async def test_a_person_with_a_live_browser_connection_is_online(
        self, service, repository, grants, room
    ):
        session = await repository.create(_session(status=SessionStatus.RUNNING))
        await _invite(grants, session, "bo")
        await grants.accept(session.id, "bo")
        await _invite(grants, session, "cy")
        room.roster = [_human("cy")]
        room.connected = [ConnectedPerson("ada", "Ada"), ConnectedPerson("bo")]

        members = await service.members(session.id, None)

        # ada and bo are connected in a browser; cy joined the room as a human participant.
        assert [(p.user_id, p.online) for p in members.people] == [
            ("ada", True),
            ("bo", True),
            ("cy", True),
        ]

    async def test_a_connected_stranger_is_not_listed_as_a_person(self, service, repository, room):
        session = await repository.create(_session(status=SessionStatus.RUNNING))
        room.connected = [ConnectedPerson("mallory")]

        members = await service.members(session.id, None)

        assert [(p.user_id, p.online) for p in members.people] == [("ada", False)]

    async def test_a_session_that_is_not_running_does_not_ask_the_room(
        self, service, repository, room
    ):
        session = await repository.create(_session(status=SessionStatus.STOPPED))

        members = await service.members(session.id, None)

        assert room.asked == []
        assert [p.online for p in members.people] == [None]

    async def test_a_team_root_asks_its_own_room(self, service, repository, room):
        root, _, _ = await _team(repository)
        room.roster = [_human("ada")]

        members = await service.members(root.id, None)

        assert room.asked == [root.id]
        assert [p.online for p in members.people] == [True]

    async def test_a_room_that_cannot_be_read_is_an_error_naming_the_cause(
        self, service, repository, room
    ):
        session = await repository.create(_session(status=SessionStatus.RUNNING))
        room.error = ConnectionError("broker refused the connection")

        with pytest.raises(RoomUnreadableError) as raised:
            await service.members(session.id, None)

        assert raised.value.session_id == session.id
        assert "ConnectionError: broker refused the connection" in str(raised.value)
        assert isinstance(raised.value.__cause__, ConnectionError)

"""Who is in a session: the agents that run in it and the people who can talk to them.

A team's root hosts the team's agents as member sessions (``domain.team``); any other
session is one agent on its own. People are the session's owner and the participants
granted on it. Whether each person is in the live room right now comes from the room
itself, so a session that is running and whose room cannot be read is an error naming
the cause, never everyone reported offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from volundr.domain.models import Principal, Session, SessionStatus
from volundr.domain.ports import SessionRepository, SessionRoomPort
from volundr.domain.services.session import (
    SessionAccessDeniedError,
    SessionNotFoundError,
    SessionService,
)
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.team import TEAM_KEY, TEAM_MEMBER_KEY
from volundr.domain.workflow_session import session_persona_name

#: The role and status the session's owner is listed with.
OWNER_ROLE = "owner"
OWNER_STATUS = "active"

#: The room participant type of a person who joined the room (``/api/room/join``).
HUMAN_PARTICIPANT_TYPE = "human"


class UnitNotFoundError(Exception):
    """A hosted member's unit root no longer exists."""

    def __init__(self, session_id: UUID, unit_id: UUID) -> None:
        self.session_id = session_id
        self.unit_id = unit_id
        super().__init__(
            f"Session {session_id} is hosted by unit {unit_id}, which no longer exists. "
            "Delete the orphaned member session."
        )


class RoomUnreadableError(Exception):
    """The session is running but its live room could not be read."""

    def __init__(self, session_id: UUID, cause: Exception) -> None:
        self.session_id = session_id
        super().__init__(
            f"Cannot tell who is in the room of running session {session_id}: "
            f"{type(cause).__name__}: {cause}. Check that the session's broker is reachable."
        )


@dataclass(frozen=True)
class AgentMember:
    """One agent of a session: a member session of a team, or the session itself."""

    session_id: UUID
    name: str
    persona_name: str
    model: str
    definition: str | None
    status: str
    lead: bool


@dataclass(frozen=True)
class PersonMember:
    """One person of a session: its owner or a participant grant."""

    user_id: str
    display_name: str
    role: str
    status: str
    online: bool | None


@dataclass(frozen=True)
class SessionMembers:
    agents: list[AgentMember]
    people: list[PersonMember]


class SessionMembersService:
    """Lists the agents and people of a session."""

    def __init__(
        self,
        repository: SessionRepository,
        session_service: SessionService,
        participants: SessionParticipantService,
        room: SessionRoomPort,
    ) -> None:
        self._repository = repository
        self._session_service = session_service
        self._participants = participants
        self._room = room

    async def members(self, session_id: UUID, principal: Principal | None) -> SessionMembers:
        """The agents and people of *session_id*, for whoever may read the session.

        Reading the session or being in its room admits the caller: a person invited to a
        team sees who else is on it.

        Raises:
            SessionNotFoundError: No such session.
            PermissionError: Authorization is configured and no principal was given.
            SessionAccessDeniedError: The caller may neither read the session nor its room.
            UnitNotFoundError: The session is a member whose unit root is gone.
            RoomUnreadableError: The session is running and its room could not be read.
        """
        session = await self._session_service.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        await self._authorize(session, principal)
        unit = await self._unit_of(session)
        shown = session if unit is None else session.hosted_in(unit)
        agents = await self._agents(shown)
        people = await self._people(shown)
        return SessionMembers(agents=agents, people=people)

    async def _authorize(self, session: Session, principal: Principal | None) -> None:
        try:
            await self._session_service.get_authorized_session(session.id, principal, "read")
        except (SessionNotFoundError, SessionAccessDeniedError):
            await self._participants.check_room_access(session, principal, "read_room")

    async def _unit_of(self, session: Session) -> Session | None:
        if session.hosted_by is None:
            return None
        unit = await self._repository.get(session.hosted_by)
        if unit is None:
            raise UnitNotFoundError(session.id, session.hosted_by)
        return unit

    async def _agents(self, session: Session) -> list[AgentMember]:
        if TEAM_KEY not in session.workload_config:
            return [_agent(session, lead=False)]
        hosted = await self._repository.list_hosted(session.id)
        return [_agent(member.hosted_in(session), lead=_is_lead(member)) for member in hosted]

    async def _people(self, session: Session) -> list[PersonMember]:
        grants = await self._participants.list_grants(session)
        user_ids = {grant.user_id for grant in grants}
        if session.owner_id:
            user_ids.add(session.owner_id)
        names = await self._participants.display_names(user_ids)
        online = await self._online_users(session)
        people: list[PersonMember] = []
        if session.owner_id:
            people.append(
                PersonMember(
                    user_id=session.owner_id,
                    display_name=names.get(session.owner_id, ""),
                    role=OWNER_ROLE,
                    status=OWNER_STATUS,
                    online=_online(online, session.owner_id),
                )
            )
        people.extend(
            PersonMember(
                user_id=grant.user_id,
                display_name=names.get(grant.user_id, ""),
                role=grant.role.value,
                status=grant.status.value,
                online=_online(online, grant.user_id),
            )
            for grant in grants
            if grant.user_id != session.owner_id
        )
        return people

    async def _online_users(self, session: Session) -> set[str] | None:
        """The people in the live room; ``None`` while the session is not running.

        A person is in it when they have a live browser connection (``connected_people``) or
        joined the room as a ``human`` participant.
        """
        if session.status != SessionStatus.RUNNING:
            return None
        try:
            roster = await self._room.read_room(session.id)
        except Exception as exc:
            raise RoomUnreadableError(session.id, exc) from exc
        seated = {
            participant.peer_id
            for participant in roster.participants
            if participant.participant_type == HUMAN_PARTICIPANT_TYPE
        }
        return seated | {person.user_id for person in roster.connected_people}


def _online(online: set[str] | None, user_id: str) -> bool | None:
    if online is None:
        return None
    return user_id in online


def _is_lead(member: Session) -> bool:
    marker = member.workload_config.get(TEAM_MEMBER_KEY)
    return isinstance(marker, dict) and marker.get("lead") is True


def _agent(session: Session, *, lead: bool) -> AgentMember:
    return AgentMember(
        session_id=session.id,
        name=session.name,
        persona_name=session_persona_name(session.workload_config),
        model=session.effective_model,
        definition=session.session_definition,
        status=session.status.value,
        lead=lead,
    )

"""Who is in a session: its agents and its people.

``GET /api/v1/forge/sessions/{session_id}/members`` answers for a team's root with the
agents it hosts (one of them leads) and for any other session with the session itself as
its one agent. People are the owner and the participants granted on the session, each with
whether they are in the live room right now (``null`` while the session is not running).
A running session whose room cannot be read is a 502 naming the cause.

The listing follows the rule for reading the session, with room membership also admitting
the caller, so a person invited to a team sees who else is on it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Request, status
from pydantic import BaseModel, Field

from volundr.adapters.inbound.auth import extract_principal
from volundr.domain.services.session import SessionAccessDeniedError, SessionNotFoundError
from volundr.domain.services.session_members import (
    AgentMember,
    PersonMember,
    RoomUnreadableError,
    SessionMembers,
    SessionMembersService,
    UnitNotFoundError,
)


class AgentMemberResponse(BaseModel):
    """One agent of the session."""

    session_id: UUID
    name: str
    persona_name: str = Field(description="The persona the agent plays; empty when it has none")
    model: str
    definition: str | None = Field(description="The runtime definition the agent launched with")
    status: str = Field(description="The member session's status, as its unit shows it")
    lead: bool = Field(description="True for the one agent that leads a team")

    @classmethod
    def from_agent(cls, agent: AgentMember) -> AgentMemberResponse:
        return cls(
            session_id=agent.session_id,
            name=agent.name,
            persona_name=agent.persona_name,
            model=agent.model,
            definition=agent.definition,
            status=agent.status,
            lead=agent.lead,
        )


class PersonMemberResponse(BaseModel):
    """One person of the session."""

    user_id: str
    display_name: str = Field(description="The person's name where the directory has one")
    role: str = Field(description="owner for the session owner, else the grant's role")
    status: str = Field(description="active for the owner, else the grant's status")
    online: bool | None = Field(
        description="Whether the person is in the live room; null when the session is not running"
    )

    @classmethod
    def from_person(cls, person: PersonMember) -> PersonMemberResponse:
        return cls(
            user_id=person.user_id,
            display_name=person.display_name,
            role=person.role,
            status=person.status,
            online=person.online,
        )


class SessionMembersResponse(BaseModel):
    agents: list[AgentMemberResponse]
    people: list[PersonMemberResponse]

    model_config = {
        "json_schema_extra": {
            "example": {
                "agents": [
                    {
                        "session_id": "6f1c1f0e-3d2b-4c55-9a77-1b2d8e3f4a10",
                        "name": "review-lead",
                        "persona_name": "reviewer",
                        "model": "claude-sonnet-4-6",
                        "definition": "skuldClaude",
                        "status": "running",
                        "lead": True,
                    }
                ],
                "people": [
                    {
                        "user_id": "u-ada",
                        "display_name": "Ada",
                        "role": "owner",
                        "status": "active",
                        "online": True,
                    },
                    {
                        "user_id": "u-bo",
                        "display_name": "Bo",
                        "role": "viewer",
                        "status": "invited",
                        "online": False,
                    },
                ],
            }
        }
    }

    @classmethod
    def from_members(cls, members: SessionMembers) -> SessionMembersResponse:
        return cls(
            agents=[AgentMemberResponse.from_agent(agent) for agent in members.agents],
            people=[PersonMemberResponse.from_person(person) for person in members.people],
        )


def create_session_members_router(
    service: SessionMembersService,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Sessions"])

    @router.get(
        "/sessions/{session_id}/members",
        response_model=SessionMembersResponse,
        responses={
            401: {"description": "Authentication is required"},
            403: {"description": "The caller may neither read the session nor its room"},
            404: {"description": "No such session"},
            409: {"description": "A member session whose unit root no longer exists"},
            502: {"description": "The session is running and its room could not be read"},
        },
    )
    async def list_session_members(
        request: Request, session_id: UUID = Path(description="Session id")
    ) -> SessionMembersResponse:
        """The agents and people of a session; a team's agents are its hosted members."""
        principal = await extract_principal(request)
        try:
            members = await service.members(session_id, principal)
        except SessionNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
        except SessionAccessDeniedError:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Not authorized to list the members of {session_id}"
            ) from None
        except UnitNotFoundError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except RoomUnreadableError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        return SessionMembersResponse.from_members(members)

    return router

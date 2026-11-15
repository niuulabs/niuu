"""The people on a session's project, for the session and everyone in its room.

``GET /api/v1/forge/sessions/{session_id}/people`` lists the members of the project the
session is attached to, each with the role, expertise and skills the project holds for
them, so an agent (through the ``find_people`` Forge MCP tool) can judge who knows most
about a topic. Niuu does not rank them.

Callers are the session's owner, the participants of its room (invited ones and project
members) and tenant admins, all through ``read_room``, and the session's own
``forge_session`` credential with ``forge:session:read``. A session with no project is a
409 that says so: an empty list would read as "nobody is on this project".
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field

from niuu.domain.agent_directory import AgentSkill
from niuu.domain.services.token_scope import (
    FORGE_SESSION_READ_SCOPE,
    FORGE_SESSION_TOKEN_USE,
    require_scope,
)
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.forge_session_auth import require_bound_session
from volundr.domain.projects import ProjectMember, ProjectRole
from volundr.domain.services.session import SessionAccessDeniedError, SessionService
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.session_participants import SessionNotInProjectError

#: The lowest project role that may resolve gates and permission prompts, answer the
#: agent's questions and so be asked to (``room_role_for_project_role``).
GATE_ANSWER_ROLE = ProjectRole.APPROVER


class SessionPersonResponse(BaseModel):
    """One member of the session's project, as the agent sees them."""

    user_id: str
    display_name: str = Field(description="From the member's profile; empty when it has none.")
    role: ProjectRole
    expertise: str = Field(description="Free text the member or an owner wrote.")
    skills: list[AgentSkill] = Field(
        description="A2A AgentSkill entries: what the person can do or knows."
    )
    can_answer_gates: bool = Field(
        description="The role is approver or owner: they may decide gates and permission "
        "prompts and answer the agent's questions on the project's behalf."
    )

    @classmethod
    def from_member(cls, member: ProjectMember) -> SessionPersonResponse:
        return cls(
            user_id=member.user_id,
            display_name=member.display_name,
            role=member.role,
            expertise=member.expertise,
            skills=member.skills,
            can_answer_gates=member.role.at_least(GATE_ANSWER_ROLE),
        )


def create_session_people_router(
    service: SessionParticipantService,
    session_service: SessionService,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Sessions"])

    @router.get(
        "/sessions/{session_id}/people",
        response_model=list[SessionPersonResponse],
        dependencies=[Depends(require_scope(FORGE_SESSION_READ_SCOPE))],
        responses={
            403: {"description": "Not in the session's room, or a token bound to another session"},
            404: {"description": "No such session"},
            409: {"description": "The session is not attached to a project"},
        },
    )
    async def list_session_people(
        request: Request, session_id: UUID = Path(description="Session id")
    ) -> list[SessionPersonResponse]:
        """The members of the session's project, oldest first, with role, expertise and
        skills. 409 when the session has no project."""
        principal = await extract_principal(request)
        require_bound_session(request, session_id)
        if principal.token_use == FORGE_SESSION_TOKEN_USE and principal.bound_session_id != str(
            session_id
        ):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "This Forge session token is bound to another session"
            )
        session = await session_service.get_session(session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session not found: {session_id}")
        try:
            members = await service.list_people(session, principal)
        except SessionAccessDeniedError:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Not authorized to list the people of {session_id}"
            ) from None
        except SessionNotInProjectError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return [SessionPersonResponse.from_member(member) for member in members]

    return router

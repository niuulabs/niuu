"""REST adapter for memory proposal notifications (under ``/api/v1/forge``).

Forge owns notifications, so the memory gateway does not keep a second channel: it tells
Forge when a proposal to a project waits, and when it was decided.

* ``POST /sessions/{session_id}/memory/proposals`` — the gateway, with the proposing
  session's own ``forge_session`` credential (scope ``forge:memory``, the session's own).
  Forge tells the approvers and owners of **the session's own project**; a proposal named
  for another project is refused (403). 200 names who was told (nobody when the project
  has no decider, or this Forge keeps no notification feed).
* ``POST /projects/{project_id}/memory/proposals/{proposal_id}/decided`` — the gateway,
  with the deciding person's own login (no session or scoped credential), once the person
  decided the proposal. Only an approver or owner of the project settles it (403 for
  another member, 404 for anyone not on it). 200 says how many rows settled.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field

from niuu.domain.services.token_scope import FORGE_MEMORY_SCOPE, require_scope
from volundr.adapters.inbound.forge_session_auth import (
    forge_session_claims,
    require_bound_session,
)
from volundr.adapters.inbound.rest_memory_gateway import person_login
from volundr.domain.models import Principal
from volundr.domain.project_ports import ProjectMemberRepository
from volundr.domain.projects import ProjectRole
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.projects import ProjectService
from volundr.domain.services.session import SessionService

#: Project roles that decide proposals to the project's memory (the gateway's rule).
DECIDING_ROLES = frozenset({ProjectRole.APPROVER, ProjectRole.OWNER})
#: A proposal id as the gateway mints it (``p-<hex>``).
_PROPOSAL_ID = r"^p-[0-9a-f]{8,64}$"
_NO_FEED = "this Forge keeps no notification feed (notifications.enabled is off)"


class WaitingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(pattern=_PROPOSAL_ID)
    project_id: UUID = Field(description="The project whose memory the proposal changes")
    title: str = Field(min_length=1, description="What the proposal is about, in a few words")
    why: str = Field(default="", description="The proposer's own words: why it is worth keeping")


class WaitingResponse(BaseModel):
    told: list[str] = Field(description="The deciders who were told")
    reason: str | None = Field(default=None, description="Why nobody was told, when nobody was")


class DecidedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(
        pattern=r"^[a-z]+$", description="How it was decided, one word: kept or discarded"
    )


class DecidedResponse(BaseModel):
    settled: int = Field(description="Notification rows settled by this call")


def review_url(project_id: UUID) -> str:
    """Where a decider reviews the project's waiting changes, in the Niuu web app."""
    return f"/mimir/review?layer=project:{project_id}"


def create_memory_proposals_router(
    sessions: SessionService,
    projects: ProjectService,
    members: ProjectMemberRepository,
    notifications: NotificationService | None,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Memory"])

    async def deciders(project_id: UUID, tenant_id: str, owner_id: str) -> list[str]:
        people = {owner_id} if owner_id else set()
        for member in await members.list_for_project(project_id):
            if member.tenant_id == tenant_id and member.role in DECIDING_ROLES:
                people.add(member.user_id)
        return sorted(people)

    @router.post(
        "/sessions/{session_id}/memory/proposals",
        response_model=WaitingResponse,
        dependencies=[Depends(require_scope(FORGE_MEMORY_SCOPE))],
        responses={
            403: {"description": "Not the session's own credential, or not its own project"},
            404: {"description": "No such session"},
            409: {"description": "The session's project no longer exists"},
        },
    )
    async def post_waiting(
        request: Request,
        body: WaitingBody,
        session_id: UUID = Path(description="Session id"),
    ) -> WaitingResponse:
        """Tell the session's project's approvers and owners that a proposal waits."""
        if forge_session_claims(request) is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "A proposal is announced only with the proposing session's own Forge session "
                "credential",
            )
        require_bound_session(request, session_id)
        session = await sessions.get_session(session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Session not found: {session_id}")
        if session.coordination is None or session.coordination.project_id != body.project_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "A session announces proposals only to its own project's deciders",
            )
        project = await projects.repository.get(body.project_id)
        if project is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Project {body.project_id} no longer exists: detach the session from it",
            )
        if notifications is None:
            return WaitingResponse(told=[], reason=_NO_FEED)
        told = await notifications.memory_proposal_waiting(
            session,
            proposal_id=body.proposal_id,
            project_name=project.name,
            title=body.title,
            why=body.why,
            deciders=await deciders(project.id, project.tenant_id, project.owner_id),
            review_url=review_url(project.id),
        )
        if not told:
            return WaitingResponse(told=[], reason="the project has no approver or owner")
        return WaitingResponse(told=told)

    @router.post(
        "/projects/{project_id}/memory/proposals/{proposal_id}/decided",
        response_model=DecidedResponse,
        responses={
            403: {"description": "A session or scoped credential, or not a decider"},
            404: {"description": "No such project, or the caller is not on it"},
        },
    )
    async def post_decided(
        body: DecidedBody,
        project_id: UUID = Path(description="Project id"),
        proposal_id: str = Path(pattern=_PROPOSAL_ID, description="Proposal id"),
        principal: Principal = Depends(person_login),
    ) -> DecidedResponse:
        """Settle every decider's notification of a decided proposal."""
        project = await projects.repository.get(project_id)
        role = await projects.role_of(project, principal) if project is not None else None
        if project is None or role is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        if role not in DECIDING_ROLES:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Your role on this project is {role.value}; its approvers and owners decide "
                "its memory proposals",
            )
        if notifications is None:
            return DecidedResponse(settled=0)
        resolution = _resolution(body.outcome, principal)
        return DecidedResponse(
            settled=await notifications.memory_proposal_decided(proposal_id, resolution=resolution)
        )

    return router


def _resolution(outcome: str, principal: Principal) -> str:
    who = principal.email or principal.user_id or "someone"
    return f"{outcome.capitalize()} by {who}."

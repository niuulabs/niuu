"""A project's team where this Forge is its home, and where it only has a copy.

* ``GET  /projects/{id}/team``          the team, whether this Forge is the home, and where it
  stands with the Guild
* ``POST /projects/{id}/team/refresh``  copy the team from the Guild now

``ProjectTeamRoute`` carries the member and team integration routes across: on a copy a
change is made at the home through the Guild, and a read first copies what is newer; at the
home a change is published once it is made.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from niuu.domain.project_team import ProjectTeam
from volundr.adapters.inbound.rest_project_results import project_result
from volundr.domain.project_ports import TeamGuildError, TeamGuildReply
from volundr.domain.projects import ProjectTeamSync
from volundr.domain.services.project_teams import ProjectTeamService
from volundr.domain.services.projects import ProjectService

#: The project operations that make up its team; the rest of a project is each Forge's own.
TEAM_OPERATIONS = ("members", "integrations")
#: Reads that answer from this Forge alone: what is configured here, and what was used here.
LOCAL_READS = ("integrations/usage", "member-candidates")

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class ProjectTeamView(BaseModel):
    home: bool = Field(description="Whether this Forge decides who is on the project")
    home_instance_id: str = Field(description="The home Forge; empty when it is this one")
    guild: bool = Field(description="Whether the team is shared with other Forges")
    sync: ProjectTeamSync
    team: ProjectTeam


class TeamRefresh(BaseModel):
    state: str
    version: int


def bearer_of(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" else ""


def team_service(request: Request) -> ProjectTeamService | None:
    return getattr(request.app.state, "project_team_service", None)


class ProjectTeamRoute(APIRoute):
    """Keep a project's team one team, whichever Forge is asked for it."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        answer = super().get_route_handler()

        async def handle(request: Request) -> Response:
            teams = team_service(request)
            project_id = request.path_params.get("project_id")
            operation = _team_operation(request, project_id)
            if teams is None or operation is None:
                return await answer(request)
            try:
                project_uuid = UUID(str(project_id))
            except ValueError:
                return await answer(request)
            copy = await teams.copy_of(project_uuid)
            bearer = bearer_of(request)
            if copy is None:
                response = await answer(request)
                await teams.publish_pending(
                    project_uuid, bearer, changed=request.method not in READ_METHODS
                )
                return response
            if request.method in READ_METHODS:
                await teams.refresh_for_read(copy, bearer)
                return await answer(request)
            return await team_reply(
                teams.forward(
                    copy,
                    method=request.method,
                    operation=operation,
                    query=request.url.query,
                    body=await request.body(),
                    bearer=bearer,
                )
            )

        return handle


async def team_reply(asked: Awaitable[TeamGuildReply]) -> Response:
    """What the home answered, or that it could not be asked and nothing was changed."""
    try:
        reply = await asked
    except TeamGuildError as exc:
        raise HTTPException(503, f"Nothing was changed. {exc}") from exc
    return Response(reply.content, status_code=reply.status, media_type=reply.media_type)


def _team_operation(request: Request, project_id: Any) -> str | None:
    """The path under the project, when it is one of its team's operations."""
    if not project_id:
        return None
    _, found, operation = request.url.path.partition(f"/projects/{project_id}/")
    if not found or operation in LOCAL_READS:
        return None
    return operation if operation.split("/", 1)[0] in TEAM_OPERATIONS else None


def create_project_team_router(
    projects: ProjectService, teams: ProjectTeamService, principal_for_request
) -> APIRouter:
    router = APIRouter(prefix="/projects/{project_id}/team", tags=["Projects"])

    @router.get("")
    async def get_team(request: Request, project_id: UUID) -> ProjectTeamView:
        principal = await principal_for_request(request)
        project = await project_result(projects.get(project_id, principal))
        copy = teams.is_copy(project)
        return ProjectTeamView(
            home=not copy,
            home_instance_id=project.home_instance_id if copy else "",
            guild=teams.linked,
            sync=await teams.status(project_id),
            team=await teams.team(project),
        )

    @router.post("/refresh")
    async def refresh_team(request: Request, project_id: UUID) -> TeamRefresh:
        await principal_for_request(request)
        try:
            state, version = await project_result(teams.refresh(project_id, bearer_of(request)))
        except TeamGuildError as exc:
            raise HTTPException(502, str(exc)) from exc
        return TeamRefresh(state=state, version=version)

    return router

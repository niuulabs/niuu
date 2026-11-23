"""The Guild's record of each project's team, and how it reaches every Forge.

* ``GET  /projects/{id}/team/record``   the team the Guild holds, to the people on it and
  the org's admins, who hold an owner's authority over the org's projects on every Forge;
  ``?fresh=true`` asks the home first, so a change it could not publish is not missed
* ``POST /projects/{id}/team/publish``  take the newest team from the project's home Forge
  and have every other Forge copy it
* ``POST /projects/memberships``        which projects the caller is on, for Ting, which
  reads the trackers of a person's projects whichever Forge is their home

The Guild never takes a team from a request body. It reads it from the Forge that says it
is the project's home, with the caller's own login, so that Forge's checks decide what the
caller may see. The first publication fixes which Forge is the home: the one that has had
the project longest, when several registered it on their own. A Forge that is one person's
alone is never one, because its owner decides what it answers. A refused publication names
the home in the ``Niuu-Project-Home`` header, so the refused Forge can hold the project as
a copy of it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from identity.models import TenantRole
from niuu.adapters.inbound.auth import extract_principal
from niuu.cap_telemetry import record_degraded
from niuu.domain.models import InstanceVisibility, Principal, RegisteredInstance
from niuu.domain.project_team import (
    PROJECT_HOME_HEADER,
    ProjectTeam,
    ProjectTeamRecord,
    TeamMembership,
    TeamMemberships,
)
from niuu.ports.project_teams import ProjectTeamHomeConflictError, ProjectTeamRecords

#: A request to one Forge with the caller's login: instance, request, method, path.
ForgeCall = Callable[[RegisteredInstance, Request, str, str], Awaitable[httpx.Response]]

#: A Forge's answer to someone who is not on a project, or for one it does not have.
PROJECT_NOT_FOUND = "Project not found"
NOT_FOUND = "No team is recorded for this project, or you are not on it"


class TeamPublication(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(min_length=1, description="The project's home Forge")


class MembershipQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_ids: list[UUID] = Field(
        default_factory=list, description="Projects to say of whether the Guild records them"
    )


class PublishedTeam(BaseModel):
    home_instance_id: str
    version: int
    forges: dict[str, str] = Field(
        description="Every other Forge and how its copy fared: updated, current, absent "
        "(it does not have the project), connections (the Guild's own store of team "
        "connections), or why it could not copy"
    )


def add_project_team_routes(
    router: APIRouter,
    records: ProjectTeamRecords,
    *,
    resolve: Callable[[Principal, str], Awaitable[RegisteredInstance]],
    visible: Callable[[Principal], Awaitable[list[RegisteredInstance]]],
    call: ForgeCall,
) -> None:
    """Add the routes; they come before the facade's catch-all project operations."""

    def person(principal: Principal) -> None:
        if principal.token_use:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "A project's team answers only to a person's own login"
            )

    @router.post("/projects/memberships")
    async def memberships(
        data: MembershipQuery, principal: Principal = Depends(extract_principal)
    ) -> TeamMemberships:
        person(principal)
        recorded = await records.recorded(data.project_ids)
        return TeamMemberships(
            user_id=principal.user_id,
            member_of=[
                TeamMembership(
                    project_id=record.team.project_id,
                    connection_ids=[connection.id for connection in record.team.connections],
                )
                for record in await records.of_member(principal.user_id, principal.tenant_id)
            ],
            unrecorded=[pid for pid in data.project_ids if pid not in recorded],
        )

    @router.get("/projects/{project_id}/team/record")
    async def team_record(
        request: Request,
        project_id: UUID,
        fresh: bool = False,
        principal: Principal = Depends(extract_principal),
    ) -> ProjectTeamRecord:
        person(principal)
        record = await records.get(project_id)
        if fresh and record is not None and record.team.tenant_id == principal.tenant_id:
            record = await _newest(request, record, principal)
        # One answer for "none", "another org's" and "not on it", as GET project gives.
        if (
            record is None
            or record.team.tenant_id != principal.tenant_id
            or not (
                record.team.has_member(principal.user_id) or TenantRole.ADMIN in principal.roles
            )
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
        return record

    @router.post("/projects/{project_id}/team/publish")
    async def publish_team(
        request: Request,
        project_id: UUID,
        data: TeamPublication,
        principal: Principal = Depends(extract_principal),
    ) -> PublishedTeam:
        person(principal)
        home = await resolve(principal, data.instance_id)
        if home.visibility == InstanceVisibility.USER:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Forge {home.name} is one person's alone, so it publishes no project team; "
                "register it for the org to share its projects' teams",
            )
        team = await _team_of(request, home, project_id, principal)
        others = [forge for forge in await visible(principal) if forge.id != home.id]
        if await records.get(project_id) is None:
            await _had_it_first(request, home, others, project_id)
        try:
            kept = await records.put(ProjectTeamRecord(home_instance_id=home.id, team=team))
        except ProjectTeamHomeConflictError as exc:
            raise _not_the_home(str(exc), exc.home_instance_id) from exc
        return PublishedTeam(
            home_instance_id=home.id,
            version=kept.team.version,
            forges=await _copy_to_all(request, others, project_id),
        )

    async def _newest(
        request: Request, record: ProjectTeamRecord, principal: Principal
    ) -> ProjectTeamRecord | None:
        """The home's team now, when the home can be asked and it is newer than the record.

        ``None`` when the home answers that the caller is not on the project. A home that is
        away leaves the record, which is what every Forge goes by meanwhile.
        """
        project_id = record.team.project_id
        try:
            home = await resolve(principal, record.home_instance_id)
            team = await _team_of(request, home, project_id, principal)
        except (HTTPException, httpx.HTTPError) as exc:
            if isinstance(exc, HTTPException) and exc.status_code == status.HTTP_404_NOT_FOUND:
                return None if exc.detail == PROJECT_NOT_FOUND else record
            record_degraded(
                "guild.project_team.home",
                f"the home of project {project_id}, Forge {record.home_instance_id}, could "
                f"not be asked for its team: {exc}",
            )
            return record
        if team.version <= record.team.version:
            return record
        kept = await records.put(ProjectTeamRecord(home_instance_id=home.id, team=team))
        others = [forge for forge in await visible(principal) if forge.id != home.id]
        await _copy_to_all(request, others, project_id)
        return kept

    async def _copy_to_all(
        request: Request, forges: list[RegisteredInstance], project_id: UUID
    ) -> dict[str, str]:
        outcomes = await asyncio.gather(*(_copy_to(request, f, project_id) for f in forges))
        return {forge.id: outcome for forge, outcome in zip(forges, outcomes, strict=True)}

    async def _team_of(
        request: Request, home: RegisteredInstance, project_id: UUID, principal: Principal
    ) -> ProjectTeam:
        response = await call(home, request, "GET", f"/projects/{project_id}/team")
        payload = _json(response)
        if response.status_code != status.HTTP_200_OK:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise HTTPException(
                response.status_code,
                detail or f"Forge {home.name} answered HTTP {response.status_code}",
            )
        if not isinstance(payload, dict) or payload.get("home") is not True:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Forge {home.name} is not the home of project {project_id}; publish its team "
                "from the Forge that is",
            )
        try:
            team = ProjectTeam.model_validate(payload.get("team"))
        except ValidationError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"Forge {home.name} returned an unreadable team"
            ) from exc
        if team.project_id != project_id or team.tenant_id != principal.tenant_id:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"Forge {home.name} returned another project's team"
            )
        return team

    async def _had_it_first(
        request: Request,
        home: RegisteredInstance,
        others: list[RegisteredInstance],
        project_id: UUID,
    ) -> None:
        """Refuse a first publication when another Forge had the project before *home*."""
        shared = [forge for forge in others if forge.visibility != InstanceVisibility.USER]
        registered = await _registered(request, home, project_id)
        for forge in shared:
            earlier = await _registered(request, forge, project_id)
            if earlier is None or registered is None or earlier >= registered:
                continue
            raise _not_the_home(
                f"Forge {forge.name} registered project {project_id} before {home.name} and is "
                "its home; publish its team from there",
                forge.id,
            )

    async def _registered(
        request: Request, forge: RegisteredInstance, project_id: UUID
    ) -> datetime | None:
        """When the Forge registered the project as its own; ``None`` when it did not.

        A Forge that cannot be asked does not hold the team back, and is reported: should it
        turn out to be the home, its own publication is refused and says so.
        """
        try:
            response = await call(forge, request, "GET", f"/projects/{project_id}")
        except (HTTPException, httpx.HTTPError) as exc:
            record_degraded(
                "guild.project_team.home",
                f"Forge {forge.name} could not be asked whether it is the home of project "
                f"{project_id}: {exc}",
            )
            return None
        payload = _json(response)
        if response.status_code != status.HTTP_200_OK or not isinstance(payload, dict):
            return None
        if payload.get("home_instance_id"):
            return None
        return TypeAdapter(datetime).validate_python(payload.get("created_at"))

    async def _copy_to(request: Request, forge: RegisteredInstance, project_id: UUID) -> str:
        """Have one Forge copy the team; what it did, or why it could not.

        A Forge that cannot be reached copies it later, when the project is next read or
        launched there; that it has not yet is reported, never taken for a copy.
        """
        path = f"/projects/{project_id}/team/refresh"
        try:
            response = await call(forge, request, "POST", path)
        except (HTTPException, httpx.HTTPError) as exc:
            reason = exc.detail if isinstance(exc, HTTPException) else f"{type(exc).__name__}"
            return _not_copied(forge, project_id, str(reason))
        payload = _json(response)
        state = payload.get("state") if isinstance(payload, dict) else None
        if response.status_code == status.HTTP_200_OK and isinstance(state, str):
            return state
        detail = payload.get("detail") if isinstance(payload, dict) else None
        return _not_copied(forge, project_id, str(detail or f"HTTP {response.status_code}"))


def _not_the_home(message: str, home_instance_id: str) -> HTTPException:
    """A refusal that names the Forge that is the home, for the refused one to copy it."""
    return HTTPException(
        status.HTTP_409_CONFLICT, message, headers={PROJECT_HOME_HEADER: home_instance_id}
    )


def _not_copied(forge: RegisteredInstance, project_id: UUID, reason: str) -> str:
    record_degraded(
        "guild.project_team.copy",
        f"Forge {forge.name} did not copy the team of project {project_id}: {reason}",
    )
    return f"not copied: {reason}"


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None

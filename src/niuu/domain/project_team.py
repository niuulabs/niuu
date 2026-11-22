"""A project's team as it travels between Forges: its people, connections and routes.

One Forge is a project's home. It checks every change to the team, and the Guild keeps
the newest team the home published and hands it to every other Forge that has the
project. A secret never travels: a connection names the credential it uses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from niuu.domain.agent_directory import AgentSkill


class _TeamModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TeamMember(_TeamModel):
    user_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    expertise: str = ""
    skills: list[AgentSkill] = Field(default_factory=list)
    added_by: str = ""
    created_at: datetime
    updated_at: datetime


class TeamConnection(_TeamModel):
    """A team integration; ``credential_name`` names its secret in the credential store."""

    id: UUID
    integration_type: str = Field(min_length=1)
    adapter: str = ""
    credential_name: str = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    slug: str
    connected_by: str = ""
    created_at: datetime
    updated_at: datetime


class TeamRouteSetting(_TeamModel):
    """Which connection one event goes through, and to whom."""

    event: str = Field(min_length=1)
    connection_id: UUID
    deliver_to: str = Field(min_length=1)
    channel: str = ""
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ProjectTeam(_TeamModel):
    """A project's team at ``version``, which the home raises with every change."""

    project_id: UUID
    tenant_id: str
    version: int = Field(ge=0)
    members: list[TeamMember] = Field(default_factory=list)
    connections: list[TeamConnection] = Field(default_factory=list)
    routes: list[TeamRouteSetting] = Field(default_factory=list)

    @model_validator(mode="after")
    def routes_use_the_teams_own_connections(self):
        known = {connection.id for connection in self.connections}
        stray = [route.event for route in self.routes if route.connection_id not in known]
        if stray:
            raise ValueError(f"routes {stray} go through a connection that is not the team's")
        return self

    def has_member(self, user_id: str) -> bool:
        return any(member.user_id == user_id for member in self.members)


class ProjectTeamRecord(_TeamModel):
    """The team a Guild holds for a project, and the one Forge it takes it from."""

    home_instance_id: str = Field(min_length=1)
    team: ProjectTeam


class TeamMembership(_TeamModel):
    """A project someone is on, and the team integrations its recorded team has."""

    project_id: UUID
    connection_ids: list[UUID]


class TeamMemberships(_TeamModel):
    """What a Guild's records say about one person's projects.

    ``unrecorded`` are the projects asked about that no Forge has published, so the Guild
    says nothing about who is on them.
    """

    user_id: str
    member_of: list[TeamMembership]
    unrecorded: list[UUID]

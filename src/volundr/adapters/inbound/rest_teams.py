"""Start a Forge team.

``POST /api/v1/forge/teams`` creates the team's root session (the room), one member session
per agent, invites the people, and starts them all together, or creates nothing
(``volundr.domain.services.team``). A session broker may not start teams: the route is not on
the Forge session route allow-list.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from niuu.domain.services.token_scope import FORGE_SESSION_LIFECYCLE_SCOPE, require_scope
from niuu.ports.identity import UserProvisioningError
from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest import (
    ErrorResponse,
    SessionCreate,
    SessionResponse,
    _execution_catalog_http_error,
)
from volundr.adapters.inbound.rest_projects import project_result
from volundr.domain.execution_catalog import ExecutionCatalogError, ExecutionSelectionError
from volundr.domain.models import (
    GitSource,
    Principal,
    Session,
    SessionSource,
)
from volundr.domain.services import (
    RepoValidationError,
    SessionCapacityError,
    SessionStateError,
)
from volundr.domain.services.forge import HostedUnitNotFoundError
from volundr.domain.services.forge_session_launch import ForgeMcpGrantEscalationError
from volundr.domain.services.session_participants import SessionParticipantService
from volundr.domain.services.team import (
    TeamLaunch,
    TeamMemberSpec,
    TeamRollbackError,
    TeamRuntimeError,
    TeamService,
)
from volundr.domain.session_runtimes import HOST_SELECTOR_KEY

#: The longest opening task, as long as any session's initial prompt (``SessionCreate``).
TASK_MAX_CHARS = 100_000


class TeamMemberCreate(BaseModel):
    """One agent of the team."""

    model: str = Field(
        min_length=1, max_length=100, description="LLM model identifier the agent runs on"
    )
    definition: str | None = Field(
        default=None,
        max_length=100,
        description="Session definition key (e.g. 'skuldClaude'); the model's default if omitted",
    )
    persona_name: str = Field(
        default="",
        max_length=255,
        description="Persona from the caller's persona catalog; empty plays none",
    )
    lead: bool = Field(default=False, description="Exactly one member of a team leads")


class TeamCreate(BaseModel):
    """Request model for starting a team.

    Unknown fields are refused, never dropped: a field this route does not know would
    otherwise change nothing while the caller believes it took effect.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=63,
        description="Name of the team's root session (RFC 1123 label); members are numbered "
        "after it",
    )
    source: SessionSource = Field(
        default_factory=GitSource, description="Workspace source (git repo or local mount)"
    )
    task: str = Field(
        default="",
        max_length=TASK_MAX_CHARS,
        description="The opening task. Empty starts the team waiting for its first room message.",
    )
    task_to: Literal["lead", "everyone"] | None = Field(
        default=None,
        description="Who receives the task: the lead alone, or every agent. Required with a "
        "task and refused without one.",
    )
    members: list[TeamMemberCreate] = Field(min_length=1, description="The team's agents")
    invitees: list[str] = Field(
        default_factory=list,
        description="User ids invited to the team's room with the viewer role (read and speak)",
    )
    launch_spec: str | None = Field(default=None, max_length=255)
    credential_names: list[str] = Field(default_factory=list)
    integration_ids: list[str] = Field(default_factory=list)
    resource_config: dict = Field(default_factory=dict)
    terminal_restricted: bool = False
    workload_config: dict = Field(
        default_factory=dict,
        description=(
            f"Placement only: ``{HOST_SELECTOR_KEY}`` (set by the gateway from target_tags). "
            "The team's root carries it and every member runs where its unit does."
        ),
    )

    @field_validator("workload_config")
    @classmethod
    def _placement_only(cls, value: dict) -> dict:
        unknown = sorted(set(value) - {HOST_SELECTOR_KEY})
        if unknown:
            raise ValueError(
                f"a team's workload_config takes only {HOST_SELECTOR_KEY}; remove "
                f"{', '.join(unknown)}"
            )
        return value

    @field_validator("name")
    @classmethod
    def _valid_session_name(cls, value: str) -> str:
        return SessionCreate.validate_rfc1123(value)

    def to_launch(self) -> TeamLaunch:
        return TeamLaunch(
            name=self.name,
            source=self.source,
            members=[
                TeamMemberSpec(
                    model=m.model,
                    definition=m.definition,
                    persona_name=m.persona_name,
                    lead=m.lead,
                )
                for m in self.members
            ],
            task=self.task,
            task_to=self.task_to,
            invitees=self.invitees,
            launch_spec=self.launch_spec,
            credential_names=self.credential_names,
            integration_ids=self.integration_ids,
            resource_config=self.resource_config,
            terminal_restricted=self.terminal_restricted,
            placement=dict(self.workload_config),
        )


class TeamResponse(BaseModel):
    """A started team: its root session and its member sessions."""

    session: SessionResponse = Field(description="The team's root, which hosts its room")
    members: list[SessionResponse] = Field(description="The agents, in the order requested")


def create_teams_router(
    team_service: TeamService,
    participants: SessionParticipantService,
    *,
    server_public_host: str,
    instance_fields: dict[str, str] | None = None,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["Teams"])

    async def _principal(request: Request) -> Principal:
        principal = await extract_principal(request)
        identity = getattr(request.app.state, "identity", None)
        if identity is None:
            return principal
        try:
            await identity.get_or_provision_user(principal)
        except UserProvisioningError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="User provisioning unavailable",
                headers={"Retry-After": "5"},
            ) from exc
        return principal

    async def _responses(sessions: list[Session]) -> list[SessionResponse]:
        counts = await participants.people_counts([s.id for s in sessions])
        responses = [
            SessionResponse.from_session(
                s, people_count=counts[s.id], public_host=server_public_host
            )
            for s in sessions
        ]
        if not instance_fields:
            return responses
        return [response.model_copy(update=instance_fields) for response in responses]

    @router.post(
        "/teams",
        response_model=TeamResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            403: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def create_team(
        request: Request,
        data: TeamCreate,
        _scope: None = Depends(
            require_scope("forge:session:create", FORGE_SESSION_LIFECYCLE_SCOPE)
        ),
    ) -> TeamResponse:
        """Start a team: a room, its agents and the people invited to it, all or nothing.

        The root session hosts the room and runs no agent; each member is a session of its
        own with its persona and engine, and the lead is the one marked ``lead``. Nothing is
        left behind when any step fails before the team has started.
        """
        principal = await _principal(request)
        # project_result answers the errors every launch shares: a refused request (a
        # ValueError: TeamError, an unplayable persona, a removed workload type, project
        # memory) is 422, access denied is 403, an unavailable memory directory is 503.
        try:
            team = await project_result(team_service.start_team(data.to_launch(), principal))
        except ForgeMcpGrantEscalationError as e:
            raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from e
        except HostedUnitNotFoundError as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
        except RepoValidationError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
        except (TeamRuntimeError, SessionCapacityError, SessionStateError) as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
        except (ExecutionCatalogError, ExecutionSelectionError) as e:
            raise _execution_catalog_http_error(e) from e
        except TeamRollbackError as e:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)) from e
        root, *members = await _responses([team.root, *team.members])
        return TeamResponse(session=root, members=members)

    return router

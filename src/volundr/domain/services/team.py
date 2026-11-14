"""Start a Forge team: a root that hosts the team's room, and one member session per agent.

A team is a unit (see ``volundr.domain.team``). This service creates the root and every
member through ``ForgeService`` (the same ``hosts_members`` and ``hosted_by`` launch paths a
workflow run's unit uses, so there is no second launch path), invites the people, and starts
the unit. It is all or nothing: anything that fails before the root has started deletes every
session this request created and revokes every invitation it sent, then raises the failure.
Nothing is left behind, and a rollback that cannot finish says what remains.

Which agent leads, and who receives the opening task, are recorded on the sessions
(``TEAM_KEY`` on the root, ``TEAM_MEMBER_KEY`` on each member); the contributor that builds
the runtime settings (``TeamRoomContributor``) reads them and nothing else.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from volundr.domain.models import Principal, Session, SessionSource
from volundr.domain.ports import SessionPersona, SessionPersonaProvider
from volundr.domain.session_participants import (
    CrossTenantInviteError,
    InviteeNotFoundError,
    OwnerInviteError,
    ParticipantRole,
    SelfInviteError,
)
from volundr.domain.team import TEAM_KEY, TEAM_MEMBER_KEY, TEAM_TASK_TARGETS

if TYPE_CHECKING:
    from volundr.domain.services.forge import ForgeService
    from volundr.domain.services.session_participants import SessionParticipantService

#: Runtime backends (``PodManager.runtime_backend``) that host a unit and render the mesh realm
#: and the room of a team for its root and members: the chart (Kubernetes) or the environment
#: (``workflow_session_environment``) of every session. Docker starts a unit root alone, so a
#: team there would run agents without a room.
TEAM_RUNTIME_BACKENDS = frozenset({"kubernetes", "process", "vm", "openshell"})

#: The longest a session name may be (RFC 1123 label).
_SESSION_NAME_MAX = 63

#: The tool an agent uses to message one teammate, and the one that lists them with the ids
#: that tool takes. Both are Forge MCP tools of a session whose mesh is enabled, and a team's
#: members have it enabled (``TeamRoomContributor``).
MESSAGE_PEER_TOOL = "mesh_send"
LIST_PEERS_TOOL = "mesh_peers"
ASK_PEERS_TOOL = "mesh_ask"

_INVITE_NOT_ALLOWED = (
    InviteeNotFoundError,
    CrossTenantInviteError,
    SelfInviteError,
    OwnerInviteError,
)


class TeamError(ValueError):
    """The team request is not a valid team; the message names what to change."""


class TeamRuntimeError(RuntimeError):
    """This Forge cannot host the team as asked; the message names the runtime and the remedy."""


class TeamRollbackError(RuntimeError):
    """The team failed to start and what the request created could not all be removed."""


@dataclass(frozen=True)
class TeamMemberSpec:
    """One agent of the team: its engine, its persona and whether it leads."""

    model: str
    definition: str | None = None
    persona_name: str = ""
    lead: bool = False


@dataclass(frozen=True)
class TeamLaunch:
    """A request to start a team."""

    name: str
    source: SessionSource
    members: Sequence[TeamMemberSpec]
    task: str = ""
    task_to: str | None = None
    invitees: Sequence[str] = ()
    launch_spec: str | None = None
    credential_names: Sequence[str] = ()
    integration_ids: Sequence[str] = ()
    resource_config: dict[str, Any] = field(default_factory=dict)
    terminal_restricted: bool = False
    #: Where the unit runs (the gateway's host selector); the root carries it and every
    #: member is placed with its unit.
    placement: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Team:
    """A started team: its root (the room) and its member sessions, in request order."""

    root: Session
    members: list[Session]


@dataclass(frozen=True)
class TeamSessionRequest:
    """What ``ForgeService.create_and_start_session`` reads of a session create request.

    The fields the REST ``SessionCreate`` carries and that a team sets; every other field the
    service reads with a default stays at it.
    """

    name: str
    model: str
    source: SessionSource
    workload_config: dict[str, Any]
    definition: str | None = None
    launch_spec: str | None = None
    launch_spec_id: UUID | None = None
    workspace_id: UUID | None = None
    issue_id: str | None = None
    issue_url: str | None = None
    terminal_restricted: bool = False
    credential_names: list[str] = field(default_factory=list)
    integration_ids: list[str] = field(default_factory=list)
    resource_config: dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""
    initial_prompt: str = ""
    workload_type: str = "session"
    persona_definition: dict[str, Any] | None = None
    hosts_members: bool = False
    hosted_by: UUID | None = None


@dataclass(frozen=True)
class PlannedMember:
    spec: TeamMemberSpec
    name: str
    persona: SessionPersona | None


class TeamService:
    """Create, invite and start a team, or leave nothing behind."""

    def __init__(
        self,
        forge: ForgeService,
        participants: SessionParticipantService,
        *,
        runtime_backend: str,
        invitations_refused: str | None,
        persona_provider: SessionPersonaProvider | None = None,
    ) -> None:
        """``invitations_refused`` is why this Forge's sessions cannot be attached to by an
        invited person (and so a team cannot take invitees), or ``None`` when they can."""
        self._forge = forge
        self._participants = participants
        self._runtime_backend = runtime_backend
        self._invitations_refused = invitations_refused
        self._persona_provider = persona_provider

    async def start_team(self, launch: TeamLaunch, principal: Principal | None) -> Team:
        """Create the team's sessions, invite its people and start it, all or nothing.

        Raises:
            TeamError: The request is not a valid team (nothing was created).
            TeamRuntimeError: This Forge's runtime cannot host a team or its invitations
                (nothing was created).
            TeamRollbackError: The start failed and some created session could not be removed.
        """
        _validate(launch)
        self._require_runtime(launch)
        planned = await self._plan(launch, principal)
        root: Session | None = None
        members: list[Session] = []
        invited: list[str] = []
        try:
            root = await self._forge.create_and_start_session(
                _root_request(launch, planned), principal=principal
            )
            for index, member in enumerate(planned):
                members.append(
                    await self._forge.create_and_start_session(
                        _member_request(launch, planned, index, root.id), principal=principal
                    )
                )
            for user_id in launch.invitees:
                await self._invite(root, user_id, principal)
                invited.append(user_id)
            started = await self._forge.start_session(
                root.id, launch_spec=launch.launch_spec, principal=principal
            )
            running = [await self._member_view(member) for member in members]
        except BaseException as exc:
            await self._roll_back(root, members, invited, principal, exc)
            raise
        return Team(root=started, members=running)

    def _require_runtime(self, launch: TeamLaunch) -> None:
        if self._runtime_backend not in TEAM_RUNTIME_BACKENDS:
            raise TeamRuntimeError(
                f"this Forge runs sessions on the {self._runtime_backend!r} runtime, which "
                "cannot host a team's room and mesh; teams run on "
                f"{', '.join(sorted(TEAM_RUNTIME_BACKENDS))}"
            )
        if launch.invitees and self._invitations_refused is not None:
            raise TeamRuntimeError(
                f"this team invites people, but {self._invitations_refused} Start the team "
                "without invitees, or fix that first"
            )

    async def _plan(self, launch: TeamLaunch, principal: Principal | None) -> list[PlannedMember]:
        owner_id = principal.user_id if principal is not None else ""
        planned: list[PlannedMember] = []
        for index, spec in enumerate(launch.members, start=1):
            planned.append(
                PlannedMember(
                    spec=spec,
                    name=_member_name(launch.name, index),
                    persona=await self._persona(owner_id, spec),
                )
            )
        return planned

    async def _persona(self, owner_id: str, spec: TeamMemberSpec) -> SessionPersona | None:
        """The member's persona from the catalog, so the team plays the revision it resolved."""
        name = spec.persona_name.strip()
        if not name:
            return None
        if self._persona_provider is None:
            raise TeamError(
                f"persona {name!r} was requested by name, but this Forge has no persona "
                "catalog; start the member without a persona"
            )
        persona = await self._persona_provider.get(owner_id, name)
        if persona is None:
            raise TeamError(f"persona {name!r} was not found in your catalog")
        if not persona.document:
            raise TeamError(
                f"persona {name!r} carries no pinned document, so a team member cannot play it"
            )
        return persona

    async def _invite(self, root: Session, user_id: str, principal: Principal | None) -> None:
        try:
            await self._participants.invite(
                root, principal, user_id=user_id, role=ParticipantRole.VIEWER, expires_at=None
            )
        except _INVITE_NOT_ALLOWED as exc:
            # One answer for every reason, as the invite route gives: which user ids exist
            # is not the caller's to learn.
            raise TeamError(
                f"cannot invite user {user_id!r} to the team: remove them from invitees"
            ) from exc

    async def _member_view(self, member: Session) -> Session:
        current = await self._forge.get_session(member.id)
        if current is None:
            raise RuntimeError(f"team member {member.id} disappeared while the team started")
        return current

    async def _roll_back(
        self,
        root: Session | None,
        members: list[Session],
        invited: list[str],
        principal: Principal | None,
        cause: BaseException,
    ) -> None:
        """Remove what this request created; raise ``TeamRollbackError`` if any of it stays."""
        if root is None:
            return
        remaining: list[str] = []
        for user_id in invited:
            try:
                await self._participants.revoke(root, principal, user_id)
            except Exception as exc:
                remaining.append(f"invitation of {user_id} ({exc})")
        for member in members:
            try:
                await self._forge.delete_session(member.id, principal=principal)
            except Exception as exc:
                remaining.append(f"member session {member.id} ({exc})")
        try:
            await self._forge.delete_session(root.id, principal=principal)
        except Exception as exc:
            remaining.append(f"root session {root.id} ({exc})")
        if not remaining:
            return
        raise TeamRollbackError(
            f"the team failed to start ({cause}) and could not be fully removed: "
            f"{'; '.join(remaining)}. Delete those sessions by hand"
        ) from cause


def _validate(launch: TeamLaunch) -> None:
    if not launch.members:
        raise TeamError("a team needs at least one member: add an agent to members")
    for index, member in enumerate(launch.members, start=1):
        if not member.model.strip():
            raise TeamError(f"member {index} has no model: set the engine it runs")
    leads = sum(1 for member in launch.members if member.lead)
    if leads != 1:
        raise TeamError(
            f"a team has exactly one lead, and {leads} members are marked lead: "
            "set lead on one member and only one"
        )
    has_task = bool(launch.task.strip())
    if has_task and launch.task_to is None:
        raise TeamError(
            f"a team started with a task needs task_to ({' or '.join(TEAM_TASK_TARGETS)}): "
            "say who receives it, or send no task"
        )
    if has_task and launch.task_to not in TEAM_TASK_TARGETS:
        raise TeamError(f"task_to {launch.task_to!r} is not one of {', '.join(TEAM_TASK_TARGETS)}")
    if not has_task and launch.task_to is not None:
        raise TeamError(
            "task_to is only for a team started with a task: send a task, or leave task_to out"
        )
    if len(set(launch.invitees)) != len(launch.invitees):
        raise TeamError("invitees lists a person twice: list each user id once")


def _member_name(team_name: str, index: int) -> str:
    """A member's session name: the team's, then its number, as one valid RFC 1123 label."""
    suffix = f"-{index}"
    return team_name[: _SESSION_NAME_MAX - len(suffix)].rstrip("-") + suffix


def _launch_fields(launch: TeamLaunch) -> dict[str, Any]:
    return {
        "source": launch.source,
        "launch_spec": launch.launch_spec,
        "terminal_restricted": launch.terminal_restricted,
        "credential_names": list(launch.credential_names),
        "integration_ids": list(launch.integration_ids),
        "resource_config": dict(launch.resource_config),
    }


def _lead_of(planned: Sequence[PlannedMember]) -> PlannedMember:
    return next(member for member in planned if member.spec.lead)


def _root_request(launch: TeamLaunch, planned: Sequence[PlannedMember]) -> TeamSessionRequest:
    """The root: it runs no agent, so it takes the lead's engine only as its session record."""
    lead = _lead_of(planned).spec
    return TeamSessionRequest(
        name=launch.name,
        model=lead.model,
        definition=lead.definition,
        workload_config={**launch.placement, TEAM_KEY: {"task_to": launch.task_to}},
        hosts_members=True,
        **_launch_fields(launch),
    )


def _member_request(
    launch: TeamLaunch, planned: Sequence[PlannedMember], index: int, root_id: UUID
) -> TeamSessionRequest:
    member = planned[index]
    receives_task = bool(launch.task.strip()) and (launch.task_to == "everyone" or member.spec.lead)
    return TeamSessionRequest(
        name=member.name,
        model=member.spec.model,
        definition=member.spec.definition,
        workload_config={TEAM_MEMBER_KEY: {"lead": member.spec.lead}},
        persona_definition=dict(member.persona.document) if member.persona else None,
        system_prompt=team_context_prompt(launch.name, planned, index),
        initial_prompt=launch.task if receives_task else "",
        hosted_by=root_id,
        **_launch_fields(launch),
    )


def team_context_prompt(team_name: str, planned: Sequence[PlannedMember], index: int) -> str:
    """What one member is told about its team: who is in it, who leads, how to reach them."""
    me = planned[index]
    lead = _lead_of(planned)
    roster = "\n".join(
        f"- {other.name}{' (you)' if other is me else ''}{' (lead)' if other is lead else ''}: "
        f"persona {other.persona.name if other.persona else 'none'}, engine {other.spec.model}"
        for other in planned
    )
    role = (
        "You lead the team: decide how the work is split, hand parts to your teammates and "
        "bring their results together."
        if me is lead
        else f"{lead.name} leads the team: take direction from them and from the people in "
        "the room."
    )
    return (
        f"You are {me.name}, one of the agents of the team {team_name!r}. The team shares one "
        "room where its agents and the people invited to it talk; your replies and your work "
        "are shared there, and a message a person sends to the room without naming an agent "
        "reaches every agent of the team.\n\n"
        f"The team's agents:\n{roster}\n\n"
        f"{role}\n\n"
        f"To message one teammate directly, call `{MESSAGE_PEER_TOOL}` with `peer` (their peer "
        f"id) and `content`. `{LIST_PEERS_TOOL}` lists the peers on your mesh: a teammate's "
        "`name` is the name above and its `peer_id` is what "
        f"`{MESSAGE_PEER_TOOL}` takes. To put a question to several teammates at once, use "
        f"`{ASK_PEERS_TOOL}` with their peer ids in `peers`."
    )

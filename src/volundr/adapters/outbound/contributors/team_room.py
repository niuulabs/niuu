"""Put a Forge team's sessions on the team's mesh realm, and host its room on the root.

A team is a unit (``volundr.domain.team``): a root that runs no agent and member sessions
that each run one. The root hosts the room of the team's own mesh realm, ``team-{root id}``:
``values.mesh`` ``{enabled, realmId}`` and ``values.room`` ``{enabled, environmentId,
realmRoom, routed, untargetedDelivery: "everyone"}``. ``routed`` is a room with no agent of
its own, as a workflow run's room; ``untargetedDelivery: "everyone"`` is what makes a message
a person sends to the room without naming an agent reach every agent of the team, where a
workflow run's room refuses it.

Each member joins the same realm (``values.mesh`` ``{enabled, realmId, shareWork}``) and
announces its pinned persona, so the members find each other and the room shows them. The
member shares its work (its replies, tool calls and results), which is what the room keeps.
A member is not a workflow step host: it gets no ``values.workflowSteps``.

Every spec also carries ``values.team`` (``{"enabled": true}``), which the chart ignores and
the runtimes that start Skuld from the environment read to render the mesh and room variables
for a session that has no ``workflowSteps`` (``workflow_session_environment``).

A root with no members, a member of another unit than its root, or a team without exactly one
lead is refused at launch, naming what is missing: Skuld would otherwise start a room nobody
can talk to.
"""

import json
from collections.abc import Mapping
from typing import Any

from volundr.adapters.outbound.contributors.mesh_values import (
    MESH_VALUES_KEY,
    ROOM_VALUES_KEY,
    announced_persona,
)
from volundr.adapters.outbound.contributors.session_voice import VOICE_MEMBERS_ENV
from volundr.domain.models import PodSpecAdditions, Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor
from volundr.domain.team import (
    TEAM_KEY,
    TEAM_MEMBER_KEY,
    TEAM_TASK_TARGETS,
    member_voices,
    team_realm,
)
from volundr.domain.workflow_session import (
    PERSONA_DEFINITION_KEY,
    ROOM_SEATS_VALUES_KEY,
    TEAM_VALUES_KEY,
    session_persona_name,
)

#: What a person's untargeted message to a team's room does: every agent receives it.
UNTARGETED_TO_EVERYONE = "everyone"


class TeamRoomContributor(SessionContributor):
    """Turn a team's root and member markers into Skuld's mesh and room settings.

    ``voice_enabled`` is Forge's ``voice.enabled``: a team's root then runs Skuld's voice
    channel, and is handed the voices of the team's agents (``SKULD__VOICE__MEMBERS``) so a
    call speaks each agent's lines in its own voice.
    """

    def __init__(self, *, voice_enabled: bool = False, **_extra: object) -> None:
        self._voice_enabled = voice_enabled

    @property
    def voice_enabled(self) -> bool:
        """Whether a team's root is told its agents' voices (Forge's ``voice.enabled``)."""
        return self._voice_enabled

    @property
    def name(self) -> str:
        return "team_room"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        config = context.workload_config
        root = config.get(TEAM_KEY)
        member = config.get(TEAM_MEMBER_KEY)
        if root is None and member is None:
            return SessionContribution()
        if root is not None and member is not None:
            raise ValueError(
                f"session {session.id} carries both {TEAM_KEY!r} and {TEAM_MEMBER_KEY!r}: a "
                "session is the root of a team or one of its members, not both"
            )
        if root is not None:
            return self._root(session, context, root)
        return SessionContribution(values=self._member(session, config, member))

    def _root(self, session: Session, context: SessionContext, team: object) -> SessionContribution:
        """The team's room: its realm's room, hosted by a Skuld that runs no agent."""
        _require_mapping(session, TEAM_KEY, team)
        task_to = team.get("task_to")
        if task_to is not None and task_to not in TEAM_TASK_TARGETS:
            raise ValueError(
                f"session {session.id} is a team's root whose task_to is {task_to!r}; it must "
                f"be one of {', '.join(TEAM_TASK_TARGETS)} or absent"
            )
        _require_team_members(session, context)
        realm = team_realm(str(session.id))
        values = {
            TEAM_VALUES_KEY: {"enabled": True},
            MESH_VALUES_KEY: {"enabled": True, "realmId": realm},
            ROOM_VALUES_KEY: {
                "enabled": True,
                "environmentId": realm,
                "realmRoom": True,
                "routed": True,
                "untargetedDelivery": UNTARGETED_TO_EVERYONE,
            },
        }
        if not self._voice_enabled:
            return SessionContribution(values=values)
        members = json.dumps(_voice_members(session, context))
        return SessionContribution(
            values=values,
            pod_spec=PodSpecAdditions(env=({"name": VOICE_MEMBERS_ENV, "value": members},)),
        )

    def _member(
        self, session: Session, config: Mapping[str, Any], member: object
    ) -> dict[str, Any]:
        """A member: on the team's realm, sharing its work, announcing its persona."""
        _require_mapping(session, TEAM_MEMBER_KEY, member)
        if session.hosted_by is None:
            raise ValueError(
                f"session {session.id} is a team member and needs hosted_by (the team's "
                "root): the realm of its room is the root's"
            )
        realm = team_realm(str(session.hosted_by))
        return {
            # An agent of a team talks in its room: no outcome contract, no outcome events.
            TEAM_VALUES_KEY: {"enabled": True, "conversational": True},
            MESH_VALUES_KEY: {
                "enabled": True,
                "realmId": realm,
                # The team's room keeps what its members do: they share it on the mesh.
                "shareWork": True,
                # Known by its role in the room and on the mesh, not by its session's name.
                **({"displayName": member["name"]} if member.get("name") else {}),
                **announced_persona(config.get(PERSONA_DEFINITION_KEY)),
            },
            # Seated in the team's room from the start (its id is the realm's), so a member
            # can speak in it before anyone has said anything to it there.
            ROOM_SEATS_VALUES_KEY: [{"roomId": realm, "hostSessionId": str(session.hosted_by)}],
        }


def _voice_members(session: Session, context: SessionContext) -> dict[str, dict[str, Any]]:
    """Each agent of the team by its name in the room: persona, voices and whether it leads.

    What Skuld's team call reads (``voice.members``) to speak an agent's lines in its voice.
    """
    members: dict[str, dict[str, Any]] = {}
    for member in context.unit_members:
        marker = member.workload_config[TEAM_MEMBER_KEY]
        name = marker.get("name")
        if not name:
            raise ValueError(
                f"team member session {member.session_id} of {session.id} has no name in the "
                "team, so a call cannot speak its lines in its voice: launch the team through "
                "Forge's team route, which names every agent"
            )
        if name in members:
            raise ValueError(
                f"team {session.id} has two members named {name!r}: a call could not tell "
                "their voices apart"
            )
        members[name] = {
            "persona": session_persona_name(member.workload_config),
            "voices": member_voices(member.workload_config),
            "lead": marker.get("lead") is True,
        }
    return members


def _require_mapping(session: Session, key: str, value: object) -> None:
    if isinstance(value, Mapping):
        return
    raise ValueError(
        f"session {session.id} carries {key!r} as {type(value).__name__}; it must be a mapping "
        "(volundr.domain.team)"
    )


def _require_team_members(session: Session, context: SessionContext) -> None:
    """A team's root hosts at least one member, every one a team member, and one leads."""
    members = context.unit_members
    if not members:
        raise ValueError(
            f"session {session.id} is a team's root but hosts no member sessions: create the "
            "members hosted by it before it starts, or Skuld would host a room with no agent"
        )
    strangers = [
        m.session_id
        for m in members
        if not isinstance(m.workload_config.get(TEAM_MEMBER_KEY), Mapping)
    ]
    if strangers:
        raise ValueError(
            f"session {session.id} is a team's root but hosts sessions that are not team "
            f"members ({', '.join(strangers)}): a team's unit holds only its own members"
        )
    leads = [
        m.session_id for m in members if m.workload_config[TEAM_MEMBER_KEY].get("lead") is True
    ]
    if len(leads) != 1:
        raise ValueError(
            f"session {session.id} is a team's root with {len(leads)} lead members; exactly "
            "one member leads"
        )

"""How a Forge team is recorded on its sessions' stored ``workload_config``.

A team is a unit (a root created with ``hosts_members`` and member sessions ``hosted_by`` it)
whose root hosts a room of the team's own mesh realm and runs no agent of its own. Every agent
of the team is a member session with its own persona and engine; exactly one of them leads.
People join the room through ordinary session participant grants on the root.

The root carries ``TEAM_KEY``; each member carries ``TEAM_MEMBER_KEY``. The contributors that
build the runtime config, and the session and members responses, read these keys and nothing
else to tell a team from any other unit.
"""

from collections.abc import Mapping
from typing import Any, Literal

from volundr.domain.workflow_session import PERSONA_DEFINITION_KEY

#: On the team's root: ``{"task_to": "lead" | "everyone" | None}``. ``None`` means the team
#: was started without a task and waits for the first message in its room.
TEAM_KEY = "team"

#: On each member of a team: ``{"lead": bool, "name": str, "voices": {voice key: voice}}``.
#: ``name`` is what the agent is called in the team (its role, numbered when two agents share
#: one): its name in the room, on the mesh and in its teammates' instructions, apart from its
#: session's name. ``voices`` (only when the launch gave any) overrides, key by key, the voices
#: of the agent's persona on a team call.
TEAM_MEMBER_KEY = "team_member"

#: Who receives the opening task, when one is given at start.
TeamTaskTarget = Literal["lead", "everyone"]
TEAM_TASK_TARGETS: tuple[str, ...] = ("lead", "everyone")


def team_realm(root_session_id: str) -> str:
    """The mesh realm of one team: its members, and only its members."""
    return f"team-{root_session_id}"


#: The realtime voice keys of a persona's ``voice.voices`` (``personas.domain.persona``) that a
#: team's launch may override for one agent.
TEAM_VOICE_KEYS: tuple[str, ...] = ("grok-voice", "openai-realtime")


def persona_voices(document: object) -> dict[str, str]:
    """The ``voice.voices`` of a pinned persona document; empty when it has none."""
    if not isinstance(document, Mapping):
        return {}
    definition = document.get("definition")
    if not isinstance(definition, Mapping):
        return {}
    voice = definition.get("voice")
    if not isinstance(voice, Mapping):
        return {}
    voices = voice.get("voices")
    if not isinstance(voices, Mapping):
        return {}
    return {str(key): str(name) for key, name in voices.items()}


def member_voices(workload_config: Mapping[str, Any]) -> dict[str, str]:
    """The voices one agent speaks with on a team call.

    Its pinned persona's ``voice.voices``, overridden key by key by the ``voices`` of its
    ``TEAM_MEMBER_KEY`` marker (what the launch asked for). Empty when it has neither: its
    lines are then spoken in the host's voice.
    """
    voices = persona_voices(workload_config.get(PERSONA_DEFINITION_KEY))
    marker = workload_config.get(TEAM_MEMBER_KEY)
    if isinstance(marker, Mapping):
        voices.update(marker.get("voices") or {})
    return voices

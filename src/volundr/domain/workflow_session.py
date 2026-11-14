"""How a workflow step session is recorded on the session's stored ``workload_config``.

The create request carries a pinned persona document, the step-host flag and the workflow
run identity as top-level fields; Forge stores them under these keys so the contributors that
build the session's runtime config, and the session responses, read one place. The run and
member identity is what lets a workflow run find the session again after a restart.
"""

import json
from collections.abc import Mapping
from typing import Any

#: The pinned portable persona document the session plays (used verbatim, never looked up).
PERSONA_DEFINITION_KEY = "persona_definition"

#: The persona catalog name to look up at launch (the alternative to a pinned document).
PERSONA_NAME_KEY = "persona"

#: ``True`` when Skuld must take the run's offers from the mesh and offer ``workflow_step_close``.
WORKFLOW_STEP_HOST_KEY = "workflow_step_host"

#: The workflow run (campaign) id the session works for.
WORKFLOW_RUN_ID_KEY = "workflow_run_id"

#: The workflow stage member (persona id) the session serves.
WORKFLOW_MEMBER_KEY = "workflow_member"

#: The alias the workflow gives the member's pinned persona. An offer carries one contract per
#: alias, and the member finds its own by this.
WORKFLOW_PERSONA_ALIAS_KEY = "workflow_persona_alias"

#: The member's ``eventFilters`` from the workflow: a mapping of names to strings. Empty takes
#: every offer of the member's topics.
WORKFLOW_EVENT_FILTERS_KEY = "workflow_event_filters"
#: Every topic the workflow routes to a member: it subscribes to them on top of its persona's.
WORKFLOW_TOPICS_KEY = "workflow_topics"

#: Who decides the run's next step: ``engine`` (Ting's engine) or ``session`` (a coordinator
#: agent in the run's root).
WORKFLOW_ORCHESTRATION_KEY = "workflow_orchestration"

#: The modes ``WORKFLOW_ORCHESTRATION_KEY`` may hold (Skuld's ``workflow_steps.orchestration``).
WORKFLOW_ORCHESTRATION_MODES = ("engine", "session")

#: ``True`` when the session is its workflow run's room: a Skuld with no agent of its own that
#: hosts the run's thread, every member of the run nested under it.
WORKFLOW_RUN_ROOM_KEY = "workflow_run_room"


#: Top-level key of the session spec values that carries the workflow step settings
#: (``values.workflowSteps``); the skuld chart renders it as Skuld's ``workflow_steps``.
WORKFLOW_STEPS_VALUES_KEY = "workflowSteps"

#: The prefix of the environment variables Skuld reads ``SkuldSettings.workflow_steps`` from,
#: for the runtimes that start it from the environment (process, container, VM, sandbox, pod
#: env): one nested name per field, the mapping field as JSON.
WORKFLOW_STEPS_ENV_PREFIX = "SKULD__WORKFLOW_STEPS__"

#: Every field of ``values.workflowSteps`` and the name of the Skuld setting it becomes (after
#: ``WORKFLOW_STEPS_ENV_PREFIX``). The skuld chart renders the same fields.
WORKFLOW_STEPS_ENV_NAMES = {
    "enabled": "ENABLED",
    "root": "ROOT",
    "runId": "RUN_ID",
    "member": "MEMBER",
    "personaAlias": "PERSONA_ALIAS",
    "orchestratorPeerId": "ORCHESTRATOR_PEER_ID",
    "eventFilters": "EVENT_FILTERS",
    "topics": "TOPICS",
    "orchestration": "ORCHESTRATION",
}


#: The fields of ``values.mesh`` a workflow run sets on its sessions and the Skuld mesh setting
#: each becomes (after ``SKULD__MESH__``): the run's own realm, and whether a member shares its
#: work with the run's room. The skuld chart renders the same fields.
WORKFLOW_MESH_ENV_NAMES = {
    "enabled": "ENABLED",
    "realmId": "REALM_ID",
    "shareWork": "SHARE_WORK",
}

#: The fields of ``values.room`` (the run's room, on its root) and the Skuld room setting each
#: becomes (after ``SKULD__ROOM__``). The skuld chart renders the same fields.
WORKFLOW_ROOM_ENV_NAMES = {
    "enabled": "ENABLED",
    "environmentId": "ENVIRONMENT_ID",
    "realmRoom": "REALM_ROOM",
    "routed": "ROUTED",
    "untargetedDelivery": "UNTARGETED_DELIVERY",
}

#: Top-level key of the session spec values that marks a session as part of a Forge team
#: (``values.team``): ``{"enabled": true}``. The skuld chart ignores it; the runtimes that start
#: Skuld from the environment read it, because a team has no ``workflowSteps`` but needs the same
#: mesh realm and room variables a workflow run gets (``workflow_session_environment``).
TEAM_VALUES_KEY = "team"


def workflow_session_environment(values: Mapping[str, Any]) -> dict[str, str]:
    """The Skuld variables a workflow run's or a team's session spec values call for.

    Every ``values.workflowSteps`` field (``SKULD__WORKFLOW_STEPS__...``), and the mesh realm,
    work sharing and room (``values.mesh`` and ``values.room``), so a member or a root started
    from the environment gets the same settings the chart renders into a pod's config file.
    Without the room a root shows none of its members' work, and without the realm and work
    sharing the members have nothing to show it. Skuld refuses a workflow member or root that
    lacks a step setting at startup, so nothing is filled in here. A team (``values.team``) has
    no ``workflowSteps`` and gets only its mesh and room. An ordinary session has neither and
    gets none of these.
    """
    steps = values.get(WORKFLOW_STEPS_VALUES_KEY)
    if isinstance(steps, Mapping):
        environment = _prefixed(steps, WORKFLOW_STEPS_ENV_NAMES, WORKFLOW_STEPS_ENV_PREFIX)
    elif isinstance(values.get(TEAM_VALUES_KEY), Mapping):
        environment = {}
    else:
        return {}
    mesh = values.get("mesh")
    if isinstance(mesh, Mapping):
        environment.update(_prefixed(mesh, WORKFLOW_MESH_ENV_NAMES, "SKULD__MESH__"))
    room = values.get("room")
    if isinstance(room, Mapping) and room.get("enabled") is True:
        room_values = dict(room)
        # As the chart: the room's environment is the session's mesh realm unless it names one.
        if not room_values.get("environmentId") and isinstance(mesh, Mapping):
            room_values["environmentId"] = mesh.get("realmId")
        environment.update(_prefixed(room_values, WORKFLOW_ROOM_ENV_NAMES, "SKULD__ROOM__"))
    return environment


def _prefixed(values: Mapping[str, Any], names: Mapping[str, str], prefix: str) -> dict[str, str]:
    return {
        f"{prefix}{name}": _env_text(values[key])
        for key, name in names.items()
        if values.get(key) is not None
    }


def _env_text(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, Mapping | list):
        return json.dumps(value)
    return str(value)


def session_persona_name(workload_config: Mapping[str, Any]) -> str:
    """The persona a session plays: the catalog name, or the id of its pinned document."""
    named = str(workload_config.get(PERSONA_NAME_KEY) or "")
    if named:
        return named
    document = workload_config.get(PERSONA_DEFINITION_KEY)
    if not isinstance(document, Mapping):
        return ""
    return str(document.get("id") or "")

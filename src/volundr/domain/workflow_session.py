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
    "orchestration": "ORCHESTRATION",
}


def workflow_steps_environment(values: Mapping[str, Any]) -> dict[str, str]:
    """The ``SKULD__WORKFLOW_STEPS__...`` variables a session spec's values call for.

    Every field the contributor set, so a member or a root started from the environment gets
    the same settings the chart renders into a pod's config file. Skuld refuses a member or
    root that lacks one at startup, so nothing is filled in here. An ordinary session has no
    ``workflowSteps`` and gets none.
    """
    steps = values.get(WORKFLOW_STEPS_VALUES_KEY)
    if not isinstance(steps, Mapping):
        return {}
    environment: dict[str, str] = {}
    for key, name in WORKFLOW_STEPS_ENV_NAMES.items():
        value = steps.get(key)
        if value is None:
            continue
        environment[f"{WORKFLOW_STEPS_ENV_PREFIX}{name}"] = _env_text(value)
    return environment


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

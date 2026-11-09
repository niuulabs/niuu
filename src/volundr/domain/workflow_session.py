"""How a workflow step session is recorded on the session's stored ``workload_config``.

The create request carries a pinned persona document, the step-host flag and the workflow
run identity as top-level fields; Forge stores them under these keys so the contributors that
build the session's runtime config, and the session responses, read one place. The run and
member identity is what lets a workflow run find the session again after a restart.
"""

from collections.abc import Mapping
from typing import Any

#: The pinned portable persona document the session plays (used verbatim, never looked up).
PERSONA_DEFINITION_KEY = "persona_definition"

#: The persona catalog name to look up at launch (the alternative to a pinned document).
PERSONA_NAME_KEY = "persona"

#: ``True`` when Skuld must serve the workflow step API and offer ``workflow_step_close``.
WORKFLOW_STEP_HOST_KEY = "workflow_step_host"

#: The workflow run (campaign) id the session works for.
WORKFLOW_RUN_ID_KEY = "workflow_run_id"

#: The workflow stage member (persona id) the session serves.
WORKFLOW_MEMBER_KEY = "workflow_member"

#: ``True`` when the session is its workflow run's room: a Skuld with no agent of its own that
#: hosts the run's thread, every member of the run nested under it.
WORKFLOW_RUN_ROOM_KEY = "workflow_run_room"


#: Top-level key of the session spec values that carries the step-host setting
#: (``values.workflowSteps.enabled``); the skuld chart renders it as ``workflow_steps.enabled``.
WORKFLOW_STEPS_VALUES_KEY = "workflowSteps"

#: The environment variable Skuld reads ``SkuldSettings.workflow_steps.enabled`` from, for the
#: runtimes that start it from the environment (process, container, VM, sandbox, pod env).
WORKFLOW_STEPS_ENABLED_ENV = "SKULD__WORKFLOW_STEPS__ENABLED"


def workflow_steps_enabled(values: Mapping[str, Any]) -> bool:
    """Whether a session spec's values mark the session as a workflow step host."""
    steps = values.get(WORKFLOW_STEPS_VALUES_KEY)
    return isinstance(steps, Mapping) and steps.get("enabled") is True


def session_persona_name(workload_config: Mapping[str, Any]) -> str:
    """The persona a session plays: the catalog name, or the id of its pinned document."""
    named = str(workload_config.get(PERSONA_NAME_KEY) or "")
    if named:
        return named
    document = workload_config.get(PERSONA_DEFINITION_KEY)
    if not isinstance(document, Mapping):
        return ""
    return str(document.get("id") or "")

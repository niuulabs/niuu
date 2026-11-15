"""What a workflow member's session spec carries for Skuld, and the environment it becomes.

Shared by the tests of every runtime that starts Skuld from its environment (process,
container, VM, sandbox, pod env): each must hand over every field of ``values.workflowSteps``.
"""

import json

#: ``values.workflowSteps`` of a run's member, as the workflow_step_host contributor writes it.
MEMBER_STEPS_VALUES = {
    "enabled": True,
    "runId": "run-1",
    "member": "reviewer",
    "personaAlias": "reviewer-alias",
    "orchestratorPeerId": "root-session-1",
    "eventFilters": {"language": "python"},
    "orchestration": "session",
}

#: The environment Skuld reads those from.
MEMBER_STEPS_ENV = {
    "SKULD__WORKFLOW_STEPS__ENABLED": "true",
    "SKULD__WORKFLOW_STEPS__RUN_ID": "run-1",
    "SKULD__WORKFLOW_STEPS__MEMBER": "reviewer",
    "SKULD__WORKFLOW_STEPS__PERSONA_ALIAS": "reviewer-alias",
    "SKULD__WORKFLOW_STEPS__ORCHESTRATOR_PEER_ID": "root-session-1",
    "SKULD__WORKFLOW_STEPS__EVENT_FILTERS": json.dumps({"language": "python"}),
    "SKULD__WORKFLOW_STEPS__ORCHESTRATION": "session",
}

#: ``values.workflowSteps`` of a run's root, and its environment.
ROOT_STEPS_VALUES = {"root": True, "runId": "run-1", "orchestration": "engine"}
ROOT_STEPS_ENV = {
    "SKULD__WORKFLOW_STEPS__ROOT": "true",
    "SKULD__WORKFLOW_STEPS__RUN_ID": "run-1",
    "SKULD__WORKFLOW_STEPS__ORCHESTRATION": "engine",
}

#: ``values.mesh`` of a run's member: the run's realm, and its work shared with the run's room.
MEMBER_MESH_VALUES = {"enabled": True, "realmId": "workflow-run-1", "shareWork": True}
MEMBER_MESH_ENV = {
    "SKULD__MESH__ENABLED": "true",
    "SKULD__MESH__REALM_ID": "workflow-run-1",
    "SKULD__MESH__SHARE_WORK": "true",
}

#: ``values.mesh`` and ``values.room`` of a run's root: it hosts the run's room.
ROOT_MESH_VALUES = {"enabled": True, "realmId": "workflow-run-1"}
ROOT_ROOM_VALUES = {
    "enabled": True,
    "environmentId": "workflow-run-1",
    "realmRoom": True,
    "routed": True,
}
ROOT_ROOM_ENV = {
    "SKULD__MESH__ENABLED": "true",
    "SKULD__MESH__REALM_ID": "workflow-run-1",
    "SKULD__ROOM__ENABLED": "true",
    "SKULD__ROOM__ENVIRONMENT_ID": "workflow-run-1",
    "SKULD__ROOM__REALM_ROOM": "true",
    "SKULD__ROOM__ROUTED": "true",
}

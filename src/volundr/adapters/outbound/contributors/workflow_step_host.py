"""Mark a Forge session as a workflow step host, and join it to its run's mesh.

A workflow run launches one kept-alive harness session per stage member. The launch request's
``workflow_step_host`` flag becomes ``values.workflowSteps.enabled`` in the session spec, and
every pod manager hands it to Skuld as ``workflow_steps.enabled`` (the config file the chart
renders, or ``SKULD__WORKFLOW_STEPS__ENABLED`` for the process, container, VM and sandbox
runtimes). Skuld serves the step API and offers the ``workflow_step_close`` tool only then,
so an ordinary session pays for none of it.

The members of one run talk to each other over the mesh, so a step host of a run also gets
``values.mesh``: enabled, in a realm of its own run (``workflow-{run id}``), so the run's
members find each other and no other run's. Its topics are its pinned persona's
``consumes``/``produces``, which Skuld reads itself. On nng in a cluster the skuld chart
derives the rest (discovery of the realm's pods, the pod's address); a key the operator set
in the pod manager's ``session_defaults`` stays as set. ``join_mesh: false`` on this
contributor is the operator's decision to run step sessions without the mesh.

A run's member also shares its work on the run's mesh (``values.mesh.shareWork``): its
replies, its tool calls and results, and its step closes, so the run's room keeps them.

The run's room is a session of its own (the launch's ``workflow_run_room`` flag): a Skuld with
no agent or harness (``values.room.routed``) that hosts a realm room of the run's realm. Every
member of the run is nested under it, and it keeps what they share as their turns, so opening
it shows the run as one session with its workers, live or finished. ``host_run_room: false``
is the operator's decision to run without it (Forge then refuses a run-room launch).

Only the chart-rendered runtimes (the Flux pod manager) read ``values.mesh`` and
``values.room``; the others keep the mesh their pod manager's ``mesh`` setting gives every
session, and host no room.
"""

from typing import Any

from personas.domain.persona_document import (
    PortablePersonaDefinition,
    consumed_event_types,
    produced_event_types,
)
from volundr.domain.models import Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor
from volundr.domain.workflow_session import (
    PERSONA_DEFINITION_KEY,
    WORKFLOW_RUN_ID_KEY,
    WORKFLOW_RUN_ROOM_KEY,
    WORKFLOW_STEP_HOST_KEY,
    WORKFLOW_STEPS_VALUES_KEY,
)

#: Top-level key of the session spec values the skuld chart renders as Skuld's ``mesh``.
MESH_VALUES_KEY = "mesh"
#: Top-level key of the session spec values the skuld chart renders as Skuld's ``room``.
ROOM_VALUES_KEY = "room"
#: What every Skuld session publishes after each turn, besides its persona's own events.
_SESSION_OUTCOME_EVENT = "code.changed"


def workflow_realm(run_id: str) -> str:
    """The mesh realm of one workflow run: its members, and only its members."""
    return f"workflow-{run_id}"


class WorkflowStepHostContributor(SessionContributor):
    """Turn the launch's step-host flag into Skuld's workflow step and mesh settings."""

    def __init__(
        self, *, join_mesh: bool = True, host_run_room: bool = True, **_extra: Any
    ) -> None:
        self._join_mesh = join_mesh
        self._host_run_room = host_run_room

    @property
    def name(self) -> str:
        return "workflow_step_host"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        run_id = str(context.workload_config.get(WORKFLOW_RUN_ID_KEY) or "")
        if context.workload_config.get(WORKFLOW_RUN_ROOM_KEY) is True:
            return SessionContribution(values=self._run_room(run_id))
        if context.workload_config.get(WORKFLOW_STEP_HOST_KEY) is not True:
            return SessionContribution()
        values: dict[str, Any] = {WORKFLOW_STEPS_VALUES_KEY: {"enabled": True}}
        if not (self._join_mesh and run_id):
            return SessionContribution(values=values)
        values[MESH_VALUES_KEY] = {
            "enabled": True,
            "realmId": workflow_realm(run_id),
            # The run's room keeps what its members do: they share it on the mesh.
            "shareWork": self._host_run_room,
            **_announced(context.workload_config.get(PERSONA_DEFINITION_KEY)),
        }
        return SessionContribution(values=values)

    def _run_room(self, run_id: str) -> dict[str, Any]:
        """The run's room: its realm's room, hosted by a Skuld that runs no agent."""
        if not (self._join_mesh and self._host_run_room):
            raise ValueError(
                "a workflow run's room needs the run's mesh and host_run_room on the "
                "workflow_step_host contributor; this Forge has them off"
            )
        realm = workflow_realm(run_id)
        return {
            MESH_VALUES_KEY: {"enabled": True, "realmId": realm},
            ROOM_VALUES_KEY: {
                "enabled": True,
                "environmentId": realm,
                "realmRoom": True,
                "routed": True,
            },
        }


def _announced(document: object) -> dict[str, Any]:
    """What the member announces to the run's other members: its persona and its topics.

    The same facts Skuld derives from the pinned persona for its own mesh identity, written on
    the pod so pod discovery (and the run's room) can show them. A session pinned to no
    persona announces none.
    """
    if not isinstance(document, dict):
        return {}
    persona = PortablePersonaDefinition.from_dict(document)
    emits = {_SESSION_OUTCOME_EVENT, *produced_event_types(persona.definition)}
    return {
        "announce": {
            "persona": persona.id,
            "consumes": sorted(consumed_event_types(persona.definition)),
            "emits": sorted(emits),
        }
    }

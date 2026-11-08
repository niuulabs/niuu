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

Only the chart-rendered runtimes (the Flux pod manager) read ``values.mesh``; the others keep
the mesh their pod manager's ``mesh`` setting gives every session.
"""

from typing import Any

from volundr.domain.models import Session
from volundr.domain.ports import SessionContext, SessionContribution, SessionContributor
from volundr.domain.workflow_session import (
    WORKFLOW_RUN_ID_KEY,
    WORKFLOW_STEP_HOST_KEY,
    WORKFLOW_STEPS_VALUES_KEY,
)

#: Top-level key of the session spec values the skuld chart renders as Skuld's ``mesh``.
MESH_VALUES_KEY = "mesh"


def workflow_realm(run_id: str) -> str:
    """The mesh realm of one workflow run: its members, and only its members."""
    return f"workflow-{run_id}"


class WorkflowStepHostContributor(SessionContributor):
    """Turn the launch's step-host flag into Skuld's workflow step and mesh settings."""

    def __init__(self, *, join_mesh: bool = True, **_extra: Any) -> None:
        self._join_mesh = join_mesh

    @property
    def name(self) -> str:
        return "workflow_step_host"

    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        if context.workload_config.get(WORKFLOW_STEP_HOST_KEY) is not True:
            return SessionContribution()
        values: dict[str, Any] = {WORKFLOW_STEPS_VALUES_KEY: {"enabled": True}}
        run_id = str(context.workload_config.get(WORKFLOW_RUN_ID_KEY) or "")
        if self._join_mesh and run_id:
            values[MESH_VALUES_KEY] = {"enabled": True, "realmId": workflow_realm(run_id)}
        return SessionContribution(values=values)

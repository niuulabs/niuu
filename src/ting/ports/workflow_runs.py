"""Port for starting a workflow run on the step engine.

Services that start workflow runs without an HTTP request (the dispatcher) depend on
this, not on the engine: they build the campaign that records the run and hand it over.
``ting.domain.services.workflow_runs.WorkflowRuns`` implements it.
"""

from __future__ import annotations

from typing import Any, Protocol

from ting.domain.models import WorkflowCampaign
from ting.domain.workflow_steps import RunState


class WorkflowRunLauncher(Protocol):
    """Starts the run of a saved campaign."""

    async def launch(
        self,
        campaign: WorkflowCampaign,
        payload: dict[str, Any] | None = None,
        *,
        trigger_node_id: str | None = None,
    ) -> RunState:
        """Record the campaign's trigger and activate the run's first steps."""
        ...

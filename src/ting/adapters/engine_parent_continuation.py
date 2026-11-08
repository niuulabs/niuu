"""Resume a durable execution's parent run through the step engine.

A durable execution (``WorkflowExecution``) is a workflow run whose ``subworkflow`` and
``wait`` nodes wait on children and on outside conditions that Ting tracks in its own
ledger. The parent is the run itself: the execution id is the campaign id, so the
parent's step ledger is the one the engine keeps. When the children join or block, or
a wait is observed, the waiting step of that node is closed here with the verdict the
engine defines for it (``joined``, ``blocked`` or ``observed``) and the facts the
node's successors read as their input. Stopping the parent cancels the run.

Nothing here decides what an observation means: the verdicts are the engine's, and
the event each one hands off is read from the pinned graph by the engine.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ting.domain.workflow_execution import WorkflowExecution
from ting.domain.workflow_steps import (
    SUBWORKFLOW_BLOCKED_VERDICT,
    SUBWORKFLOW_JOINED_VERDICT,
    WAIT_OBSERVED_VERDICT,
)
from ting.domain.workflow_wait import WaitObservation, WorkflowWait
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_execution import SessionContinuation
from ting.ports.workflow_wait import WaitContinuation

if TYPE_CHECKING:
    from ting.domain.services.workflow_runs import WorkflowRuns


class EngineParentWorkflowContinuation(
    SessionContinuation[WorkflowExecution],
    WaitContinuation[WorkflowExecution],
):
    """Closes the parent run's waiting steps, and cancels the run when the owner cancels."""

    def __init__(
        self,
        *,
        runs: Callable[[], WorkflowRuns],
        campaigns: Callable[[], WorkflowCampaignRepository],
    ) -> None:
        # Both are looked up when used: the run service is built after the services that
        # hold this continuation, at the composition root.
        self._runs = runs
        self._campaigns = campaigns

    async def resume_parent(
        self,
        execution: WorkflowExecution,
        *,
        generation: int,
        results: list[dict],
    ) -> None:
        await self._runs().close_wait(
            execution.id,
            execution.parent_node_id,
            SUBWORKFLOW_JOINED_VERDICT,
            outcome={
                "executionId": str(execution.id),
                "parentNodeId": execution.parent_node_id,
                "generation": generation,
                "results": results,
            },
        )

    async def notify_parent(
        self,
        execution: WorkflowExecution,
        *,
        generation: int,
        correlation_revision: int,
        children: list[dict],
    ) -> None:
        await self._runs().close_wait(
            execution.id,
            execution.parent_node_id,
            SUBWORKFLOW_BLOCKED_VERDICT,
            outcome={
                "executionId": str(execution.id),
                "parentNodeId": execution.parent_node_id,
                "generation": generation,
                "revision": correlation_revision,
                "children": children,
            },
        )

    async def notify_wait_observation(
        self,
        execution: WorkflowExecution,
        wait: WorkflowWait,
        observation: WaitObservation,
    ) -> None:
        await self._runs().close_wait(
            execution.id,
            wait.node_id,
            WAIT_OBSERVED_VERDICT,
            outcome={
                "schemaVersion": 1,
                "executionId": str(execution.id),
                "waitId": str(wait.id),
                "nodeId": wait.node_id,
                "conditionType": wait.condition_type,
                "requestDigest": wait.request_digest,
                "parentNodeId": execution.parent_node_id,
                "generation": wait.execution_generation,
                **observation.to_dict(),
            },
        )

    async def stop_parent(self, execution: WorkflowExecution) -> None:
        campaign = await self._campaigns().get_campaign(execution.id)
        if campaign is None:
            raise LookupError(f"execution {execution.id} has no run to stop")
        runs = self._runs()
        state = await runs.state(campaign.id)
        if state.finished:
            return
        await runs.cancel(campaign, "the execution was canceled by its owner")

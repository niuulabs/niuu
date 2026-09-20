"""Resume the exact parent Ravn session through existing Volundr messaging."""

from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ting.domain.delivery_execution import DeliveryExecution
from ting.domain.workflow_continuation_events import (
    subworkflow_blocked_event,
    subworkflow_joined_event,
    wait_observed_event,
)
from ting.domain.workflow_wait import (
    WaitObservation,
    WorkflowWait,
)
from ting.ports.delivery_execution import ParentWorkflowContinuation
from ting.ports.volundr import VolundrFactory


class VolundrParentWorkflowContinuation(ParentWorkflowContinuation):
    def __init__(self, *, volundr_factory: VolundrFactory) -> None:
        self._volundr_factory = volundr_factory

    async def resume_parent(
        self,
        execution: DeliveryExecution,
        *,
        generation: int,
        results: list[dict],
    ) -> None:
        adapter = await self._adapter(execution)
        event_type = subworkflow_joined_event(self._graph(execution), execution.parent_node_id)
        await self._deliver(
            adapter,
            execution,
            event_type,
            seed=f"{generation}:verified",
            fields={
                "parentNodeId": execution.parent_node_id,
                "generation": generation,
                "results": results,
            },
        )

    async def notify_parent(
        self,
        execution: DeliveryExecution,
        *,
        generation: int,
        correlation_revision: int,
        children: list[dict],
    ) -> None:
        adapter = await self._adapter(execution)
        event_type = subworkflow_blocked_event(self._graph(execution), execution.parent_node_id)
        await self._deliver(
            adapter,
            execution,
            event_type,
            seed=f"{generation}:blocked:{correlation_revision}",
            fields={
                "parentNodeId": execution.parent_node_id,
                "generation": generation,
                "revision": correlation_revision,
                "children": children,
            },
        )

    async def notify_delivery_observation(
        self,
        execution: DeliveryExecution,
        wait: WorkflowWait,
        observation: WaitObservation,
    ) -> None:
        adapter = await self._adapter(execution)
        event_type = wait_observed_event(self._graph(execution))
        await self._deliver(
            adapter,
            execution,
            event_type,
            seed=f"delivery-wait:{wait.id}",
            fields={
                "schemaVersion": 1,
                "waitId": str(wait.id),
                "requestDigest": wait.request_digest,
                "parentNodeId": execution.parent_node_id,
                "generation": wait.execution_generation,
                "candidateDigest": wait.candidate_digest,
                "mode": wait.request.mode.value,
                **observation.to_dict(),
            },
        )

    async def stop_parent(self, execution: DeliveryExecution) -> None:
        adapter = await self._adapter(execution)
        await adapter.stop_session(execution.parent_session_id)

    @staticmethod
    def _graph(execution: DeliveryExecution) -> dict[str, Any]:
        snapshot = execution.workflow_snapshot
        graph = snapshot.get("graph") if isinstance(snapshot, dict) else None
        if not isinstance(graph, dict):
            raise RuntimeError(
                f"Workflow execution {execution.id} has no pinned graph snapshot to resolve "
                "its continuation events"
            )
        return graph

    @staticmethod
    async def _deliver(
        adapter: Any,
        execution: DeliveryExecution,
        event_type: str,
        *,
        seed: str,
        fields: dict[str, Any],
    ) -> None:
        continuation_id = str(
            uuid5(NAMESPACE_URL, f"niuulabs:workflow-execution:{execution.id}:{seed}")
        )
        payload = {
            "type": event_type,
            "continuationId": continuation_id,
            "executionId": str(execution.id),
            **fields,
        }
        await adapter.publish_workflow_event(
            execution.parent_session_id,
            event_type,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            payload=payload,
            request_id=continuation_id,
        )

    async def _adapter(self, execution: DeliveryExecution):
        if execution.connection_id:
            adapter = await self._volundr_factory.for_connection(
                execution.owner_id,
                execution.connection_id,
            )
        else:
            adapter = await self._volundr_factory.primary_for_owner(execution.owner_id)
        if adapter is None:
            raise RuntimeError(
                "Parent workflow connection is unavailable; restore the configured "
                "Volundr connection before reconciling this execution"
            )
        return adapter

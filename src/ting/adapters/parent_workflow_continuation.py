"""Resume the exact parent Ravn session through existing Volundr messaging."""

from __future__ import annotations

import json
from uuid import NAMESPACE_URL, uuid5

from ting.domain.developer_delivery_wait import (
    DeveloperDeliveryObservation,
    DeveloperDeliveryWait,
)
from ting.domain.developer_execution import DeveloperExecution
from ting.ports.developer_execution import ParentWorkflowContinuation
from ting.ports.volundr import VolundrFactory


class VolundrParentWorkflowContinuation(ParentWorkflowContinuation):
    def __init__(self, *, volundr_factory: VolundrFactory) -> None:
        self._volundr_factory = volundr_factory

    async def resume_parent(
        self,
        execution: DeveloperExecution,
        *,
        generation: int,
        results: list[dict],
    ) -> None:
        adapter = await self._adapter(execution)
        continuation_id = str(
            uuid5(
                NAMESPACE_URL,
                f"niuulabs:developer-execution:{execution.id}:{generation}:verified",
            )
        )
        payload = {
            "type": "developer.children.verified",
            "continuationId": continuation_id,
            "executionId": str(execution.id),
            "parentNodeId": execution.parent_node_id,
            "generation": generation,
            "results": results,
        }
        await adapter.publish_workflow_event(
            execution.parent_session_id,
            "developer.children.verified",
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            payload=payload,
            request_id=continuation_id,
        )

    async def notify_parent(
        self,
        execution: DeveloperExecution,
        *,
        event_type: str,
        generation: int,
        correlation_revision: int,
        children: list[dict],
    ) -> None:
        if event_type != "developer.children.blocked":
            raise ValueError(f"unsupported developer parent event {event_type!r}")
        adapter = await self._adapter(execution)
        continuation_id = str(
            uuid5(
                NAMESPACE_URL,
                (
                    f"niuulabs:developer-execution:{execution.id}:{generation}:"
                    f"blocked:{correlation_revision}"
                ),
            )
        )
        payload = {
            "type": event_type,
            "continuationId": continuation_id,
            "executionId": str(execution.id),
            "parentNodeId": execution.parent_node_id,
            "generation": generation,
            "revision": correlation_revision,
            "children": children,
        }
        await adapter.publish_workflow_event(
            execution.parent_session_id,
            event_type,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            payload=payload,
            request_id=continuation_id,
        )

    async def notify_delivery_observation(
        self,
        execution: DeveloperExecution,
        wait: DeveloperDeliveryWait,
        observation: DeveloperDeliveryObservation,
    ) -> None:
        adapter = await self._adapter(execution)
        continuation_id = str(
            uuid5(
                NAMESPACE_URL,
                f"niuulabs:developer-execution:{execution.id}:delivery-wait:{wait.id}",
            )
        )
        payload = {
            "schemaVersion": 1,
            "type": "developer.delivery.observed",
            "continuationId": continuation_id,
            "waitId": str(wait.id),
            "requestDigest": wait.request_digest,
            "executionId": str(execution.id),
            "parentNodeId": execution.parent_node_id,
            "generation": wait.execution_generation,
            "candidateDigest": wait.candidate_digest,
            "mode": wait.request.mode.value,
            **observation.to_dict(),
        }
        await adapter.publish_workflow_event(
            execution.parent_session_id,
            "developer.delivery.observed",
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            payload=payload,
            request_id=continuation_id,
        )

    async def stop_parent(self, execution: DeveloperExecution) -> None:
        adapter = await self._adapter(execution)
        await adapter.stop_session(execution.parent_session_id)

    async def _adapter(self, execution: DeveloperExecution):
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

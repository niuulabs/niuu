"""Resolve child definitions exclusively from the parent's frozen closure."""

import json

from ting.delivery.domain import ChildExecution, DeliveryExecution
from ting.domain.models import WorkflowDefinition, WorkflowScope
from ting.domain.workflow_document import load_workflow_document, workflow_document_revision
from ting.domain.workflow_execution import WorkflowExecutionError
from ting.domain.workflow_snapshot import build_workflow_snapshot


def pinned_child_workflow(
    execution: DeliveryExecution,
    child: ChildExecution,
) -> WorkflowDefinition:
    if child.execution_id != execution.id:
        raise WorkflowExecutionError("Child does not belong to this execution")
    snapshot = execution.workflow_snapshot
    alias = execution.policy.workflow_dependency
    pin = snapshot.get("workflow_dependencies", {}).get(alias)
    aggregate = snapshot.get("workflow_definitions", {}).get(alias)
    if not isinstance(pin, dict) or not isinstance(aggregate, dict):
        raise WorkflowExecutionError("Execution has no frozen child workflow dependency")
    if (
        pin.get("id") != str(child.template_id)
        or pin.get("revision") != child.template_revision
        or pin.get("digest") != child.template_digest
    ):
        raise WorkflowExecutionError("Child pin differs from the frozen execution dependency")
    document = load_workflow_document(json.dumps(aggregate.get("document")))
    if (
        document.id != child.template_id
        or workflow_document_revision(document) != child.template_digest
    ):
        raise WorkflowExecutionError("Frozen child workflow does not match its content digest")
    workflow = document.to_workflow(
        scope=WorkflowScope.USER,
        owner_id=execution.owner_id,
        tenant_id=execution.tenant_id,
        revision=child.template_revision,
        read_only=True,
        source=f"execution:{execution.id}",
        persona_definitions=aggregate.get("persona_definitions") or {},
        workflow_definitions=aggregate.get("workflow_definitions") or {},
    )
    # This validates every persona and nested workflow against its exact pin.
    build_workflow_snapshot(workflow)
    return workflow

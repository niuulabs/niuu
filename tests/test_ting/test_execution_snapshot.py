"""Deferred children execute the frozen closure, including its scoped personas."""

import copy
from dataclasses import replace

import pytest

from tests.test_ting.test_delivery_execution import _execution, _proposal
from ting.delivery.domain import make_children
from ting.domain.execution_snapshot import pinned_child_workflow
from ting.domain.workflow_execution import ChildTemplate, WorkflowExecutionError
from ting.domain.workflow_snapshot import build_workflow_snapshot
from ting.system_workflows import BUNDLED_SYSTEM_WORKFLOWS_PATH, load_system_workflows


def frozen_execution():
    root = next(
        workflow
        for workflow in load_system_workflows(BUNDLED_SYSTEM_WORKFLOWS_PATH)
        if workflow.name == "Developer Delivery"
    )
    pin = root.workflow_dependencies["workstream"]
    execution = _execution(current_generation=1)
    execution = replace(
        execution,
        policy=replace(
            execution.policy,
            templates={
                "workstream": ChildTemplate(
                    dependency_alias="workstream",
                    id=pin.id,
                    revision=pin.revision,
                    digest=pin.digest,
                ),
            },
        ),
        workflow_snapshot=build_workflow_snapshot(root),
    )
    child = make_children(execution, 1, (_proposal(execution, "parser"),))[0]
    return root, execution, child


def test_child_resolves_without_current_workflow_or_persona_catalog():
    root, execution, child = frozen_execution()
    root.graph.clear()
    root.workflow_definitions.clear()
    resolved = pinned_child_workflow(execution, child)
    assert resolved.id == child.template_id
    assert resolved.revision == child.template_revision
    assert resolved.persona_definitions["developer-code-reviewer"]
    assert resolved.graph["nodes"]


@pytest.mark.parametrize("mutation", ["absent", "pin", "document", "persona", "foreign"])
def test_tampered_or_foreign_closure_cannot_launch(mutation):
    _, execution, child = frozen_execution()
    snapshot = copy.deepcopy(execution.workflow_snapshot)
    if mutation == "absent":
        snapshot = {}
    elif mutation == "pin":
        snapshot["workflow_dependencies"]["workstream"]["digest"] = "sha256:" + "f" * 64
    elif mutation == "document":
        snapshot["workflow_definitions"]["workstream"]["document"]["name"] = "Changed after launch"
    elif mutation == "persona":
        snapshot["workflow_definitions"]["workstream"]["persona_definitions"].clear()
    elif mutation == "foreign":
        child = replace(child, execution_id=_execution().id)
    execution = replace(execution, workflow_snapshot=snapshot)
    with pytest.raises(ValueError):
        pinned_child_workflow(execution, child)


def test_parent_snapshot_has_exact_portable_child_pins():
    _, execution, child = frozen_execution()
    child = replace(child, template_revision="another-revision")
    with pytest.raises(WorkflowExecutionError, match="pin differs"):
        pinned_child_workflow(execution, child)

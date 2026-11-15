from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from tests.support.full_catalogue import ARCHIVE, full_catalogue_personas, full_catalogue_root
from tests.support.full_catalogue import load_full_catalogue as load_system_workflows
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowDependency
from ting.domain.workflow_document import (
    load_workflow_document,
    workflow_document_revision,
)
from ting.domain.workflow_snapshot import build_workflow_snapshot


def _developer_workflows():
    return {
        workflow.name: workflow
        for workflow in load_system_workflows()
        if workflow.name.startswith("Developer ")
    }


def test_developer_workflow_package_has_exact_transitive_closure() -> None:
    workflows = _developer_workflows()
    assert set(workflows) == {
        "Developer Delivery",
        "Developer Planning",
        "Developer Workstream",
        "Developer Integration",
    }

    root = workflows["Developer Delivery"]
    assert root.schema_version == 2
    assert root.graph["executionContract"] == "developer-delivery/v1"
    assert set(root.workflow_dependencies) == {"workstream", "planning", "integration"}
    snapshot = build_workflow_snapshot(root)
    assert set(snapshot["workflow_definitions"]) == set(root.workflow_dependencies)
    for alias, pin in root.workflow_dependencies.items():
        child = snapshot["workflow_definitions"][alias]
        document = load_workflow_document(
            __import__("yaml").safe_dump(child["document"], sort_keys=False)
        )
        assert document.id == pin.id
        assert workflow_document_revision(document) == pin.revision == pin.digest


def test_developer_workflows_pin_current_integration_reviewer_content() -> None:
    reviewer = full_catalogue_personas().load_current_portable("developer-integration-verifier")
    assert reviewer is not None

    workflows = _developer_workflows()
    for workflow_name in ("Developer Delivery", "Developer Integration"):
        pin = workflows[workflow_name].persona_dependencies["developer-integration-verifier"]
        assert pin.revision == reviewer.revision
        assert pin.digest == reviewer.digest


def test_developer_personas_and_templates_are_provider_neutral() -> None:
    files = [
        *(ARCHIVE / "personas").glob("developer-*.yaml"),
        *full_catalogue_root().glob("developer-*.yaml"),
    ]
    rendered = "\n".join(path.read_text(encoding="utf-8").casefold() for path in files)
    assert "github" not in rendered
    assert "gitlab" not in rendered

    coordinator_persona = full_catalogue_personas().load("developer-coordinator")
    assert coordinator_persona is not None
    handoff_fields = {
        "attempt_id": "string",
        "candidate_sha": "string",
        "candidate_tree": "string",
        "integration_allocation": "object",
        "integration_candidate": "object",
        "integration_receipts": "array",
        "integration_receipt": "object",
        "verification_receipts": "array",
    }
    for field_name, field_type in handoff_fields.items():
        field = coordinator_persona.produces.schema[field_name]
        assert field.type == field_type
        assert field.required is False
    for field_name in ("plan_revision", "generation", "workstreams", "task_handles"):
        assert coordinator_persona.produces.schema[field_name].required is False
    for field_name in ("verdict", "rationale", "result"):
        assert coordinator_persona.produces.schema[field_name].required is True
    assert "Copy these typed objects unchanged" in coordinator_persona.system_prompt_template
    assert "expand current_generation + 1" in coordinator_persona.system_prompt_template
    assert "Completed children are immutable evidence" in coordinator_persona.system_prompt_template
    assert "fresh generation-qualified integration allocation" in (
        coordinator_persona.system_prompt_template
    )
    assert "emit wait_delivery" in coordinator_persona.system_prompt_template
    assert "workflow_execution_wait with" in coordinator_persona.system_prompt_template
    assert "conditionType set to forge.checks or forge.merge" in (
        coordinator_persona.system_prompt_template
    )

    integration_reviewer = Path("archive/personas/developer-integration-verifier.yaml").read_text(
        encoding="utf-8"
    )
    assert "delivery_workspace.inspect" in integration_reviewer
    assert "bounded exact-candidate patch" in integration_reviewer
    assert "final delivery evidence policy" in integration_reviewer
    assert "Remote CI and full-policy" in integration_reviewer


@pytest.mark.parametrize(
    "workflow_name",
    ["developer-delivery.yaml", "developer-workstream.yaml", "developer-integration.yaml"],
)
def test_developer_workflows_pin_current_coordinator_content(workflow_name: str) -> None:
    source = full_catalogue_personas()
    coordinator = source.load_current_portable("developer-coordinator")
    assert coordinator is not None
    workflow = load_workflow_document(
        (full_catalogue_root() / workflow_name).read_text(encoding="utf-8")
    )

    pin = workflow.persona_dependencies["developer-coordinator"]
    assert pin.revision == coordinator.revision
    assert pin.digest == coordinator.digest


def test_delivery_pins_current_workstream_content() -> None:
    root = load_workflow_document(
        (full_catalogue_root() / "developer-delivery.yaml").read_text(encoding="utf-8")
    )
    child = load_workflow_document(
        (full_catalogue_root() / "developer-workstream.yaml").read_text(encoding="utf-8")
    )

    revision = workflow_document_revision(child)
    pin = root.workflow_dependencies["workstream"]
    assert pin.revision == revision
    assert pin.digest == revision

    expansion = next(node for node in root.graph["nodes"] if node["kind"] == "subworkflow")
    input_schema = expansion["inputSchema"]
    assert "expectedOutputs" in input_schema["required"]
    assert input_schema["properties"]["expectedOutputs"] == {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": 1,
    }
    requirement_evidence = expansion["resultSchema"]["properties"]["requirementEvidence"]
    assert requirement_evidence == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "requirement_id": {"type": "string", "minLength": 1},
                "implementation_paths": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
                "verification_contract_ids": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
            },
            "required": [
                "requirement_id",
                "implementation_paths",
                "verification_contract_ids",
            ],
            "additionalProperties": False,
        },
    }
    verification_receipt = expansion["resultSchema"]["properties"]["verificationReceipts"]["items"]
    receipt_fields = {
        "receipt_id",
        "campaign_id",
        "workstream_key",
        "attempt_id",
        "repository",
        "candidate_sha",
        "candidate_tree",
        "base_sha",
        "contract_id",
        "command_digest",
        "exit_code",
        "stdout_digest",
        "stderr_digest",
        "started_at",
        "completed_at",
        "provenance",
    }
    assert set(verification_receipt["properties"]) == receipt_fields
    assert set(verification_receipt["required"]) == receipt_fields
    assert verification_receipt["additionalProperties"] is False
    assert verification_receipt["properties"]["exit_code"] == {"type": "integer"}
    for field_name in receipt_fields - {"exit_code", "provenance"}:
        assert verification_receipt["properties"][field_name] == {
            "type": "string",
            "minLength": 1,
        }
    assert verification_receipt["properties"]["provenance"] == {
        "type": "object",
        "properties": {
            "producer_id": {"type": "string", "minLength": 1},
            "key_id": {"type": "string", "minLength": 1},
            "signature": {"type": "string", "minLength": 1},
        },
        "required": ["producer_id", "key_id", "signature"],
        "additionalProperties": False,
    }


def test_workflow_revision_ignores_transport_paths_for_both_dependency_kinds() -> None:
    document = load_workflow_document(
        (full_catalogue_root() / "developer-delivery.yaml").read_text()
    )
    changed = copy.deepcopy(document)
    changed.persona_dependencies["developer-coordinator"] = replace(
        changed.persona_dependencies["developer-coordinator"],
        path="personas/elsewhere.yaml",
    )
    changed.workflow_dependencies["workstream"] = replace(
        changed.workflow_dependencies["workstream"],
        path="workflows/elsewhere.yaml",
    )
    assert workflow_document_revision(changed) == workflow_document_revision(document)


@pytest.mark.parametrize("mutation", ["missing", "extra", "tamper", "cycle"])
def test_execution_snapshot_rejects_invalid_workflow_dependency_closure(mutation: str) -> None:
    root = _developer_workflows()["Developer Delivery"]
    definitions = copy.deepcopy(root.workflow_definitions)
    dependencies = dict(root.workflow_dependencies)
    if mutation == "missing":
        definitions.pop("workstream")
    elif mutation == "extra":
        definitions["undeclared"] = copy.deepcopy(definitions["workstream"])
    elif mutation == "tamper":
        definitions["workstream"]["document"]["description"] += " altered"
    else:
        definitions["workstream"]["document"]["id"] = str(root.id)
        child = load_workflow_document(
            __import__("yaml").safe_dump(definitions["workstream"]["document"], sort_keys=False)
        )
        revision = workflow_document_revision(child)
        dependencies["workstream"] = WorkflowDependency(
            id=root.id,
            revision=revision,
            digest=revision,
            path="workflows/developer-planning.yaml",
        )

    with pytest.raises(WorkflowDocumentError):
        build_workflow_snapshot(
            replace(
                root,
                workflow_dependencies=dependencies,
                workflow_definitions=definitions,
            )
        )

"""Untrusted portable child declarations must fail before import or execution."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
import yaml

from tests.test_ting.test_workflow_document import _workflow
from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.models import WorkflowDependency
from ting.domain.workflow_document import load_workflow_document, workflow_document_payload


def payload():
    child = WorkflowDependency(uuid4(), "revision-1", "sha256:" + "b" * 64)
    document = replace(_workflow(), schema_version=2, workflow_dependencies={"worker": child})
    raw = workflow_document_payload(document)
    raw["graph"]["nodes"].append(
        {
            "id": "children",
            "kind": "subworkflow",
            "workflowDependency": "worker",
            "allowedCoordinator": "reviewer",
            "maxChildren": 100,
            "maxAttempts": 3,
            "joinMode": "all",
            "blockedEvent": "children.blocked",
            "inputSchema": {"type": "object", "properties": {}},
            "resultSchema": {"type": "object", "properties": {}},
        }
    )
    raw["graph"]["edges"].append(
        {
            "id": "children-joined",
            "source": "children",
            "target": "review",
            "label": "children.joined -> children.joined",
        }
    )
    return raw


@pytest.mark.parametrize(
    "path,value,reason",
    [
        (("workflow_dependencies",), [], "must be a mapping"),
        (("workflow_dependencies",), {1: {}}, "aliases must be strings"),
        (("workflow_dependencies", "worker"), [], "must be a mapping"),
        (("workflow_dependencies", "worker"), {}, "missing"),
        (("workflow_dependencies", "worker", "surprise"), True, "unknown"),
        (("workflow_dependencies", "worker", "id"), "not-a-uuid", "UUID"),
        (("workflow_dependencies", "worker", "revision"), "", "non-empty"),
        (("workflow_dependencies", "worker", "digest"), "untrusted", "sha256"),
        (("workflow_dependencies", "worker", "path"), "workflows/../secret.yaml", "normalized"),
        (("workflow_dependencies", "worker", "path"), "/workflows/child.yaml", "normalized"),
        (("workflow_dependencies", "worker", "path"), "workflows\\child.yaml", "normalized"),
        (("graph", "nodes", 1, "workflowDependency"), "missing", "undeclared workflow"),
        (("graph", "nodes", 1, "allowedCoordinator"), "missing", "declared persona"),
        (("graph", "nodes", 1, "inputSchema"), [], "object JSON schema"),
        (("graph", "nodes", 1, "resultSchema", "type"), "string", "object JSON schema"),
        (("graph", "nodes", 1, "inputSchema", "properties"), {"x": "string"}, "map names"),
        (("graph", "nodes", 1, "resultSchema", "required"), ["missing"], "declared properties"),
        (("graph", "nodes", 1, "maxChildren"), True, "integer"),
        (("graph", "nodes", 1, "maxChildren"), 101, "integer"),
        (("graph", "nodes", 1, "maxAttempts"), 0, "integer"),
        (("graph", "nodes", 1, "joinMode"), "silent", "joinMode"),
        (("graph", "nodes", 1, "passingVerdicts"), ["pass"], "end node"),
        (("graph", "nodes", 1, "blockedEvent"), "", "non-empty"),
        (("graph", "nodes", 1, "blockedEvent"), "children.joined", "differ from its joined event"),
    ],
)
def test_rejects_invalid_child_contracts_at_portable_boundary(path, value, reason):
    raw = deepcopy(payload())
    target = raw
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    with pytest.raises(WorkflowDocumentError, match=reason):
        load_workflow_document(yaml.safe_dump(raw))


def test_v1_cannot_smuggle_a_dynamic_child_node():
    raw = payload()
    raw["schema_version"] = 1
    raw.pop("workflow_dependencies")
    with pytest.raises(WorkflowDocumentError, match="requires workflow schema_version 2"):
        load_workflow_document(yaml.safe_dump(raw))


def test_child_contract_survives_yaml_round_trip():
    raw = payload()
    raw["workflow_dependencies"]["worker"]["path"] = "workflows/child.yaml"
    document = load_workflow_document(yaml.safe_dump(raw))
    assert workflow_document_payload(document) == raw


def test_rejects_subworkflow_node_without_an_outgoing_joined_event() -> None:
    raw = payload()
    raw["graph"]["edges"] = []
    with pytest.raises(WorkflowDocumentError, match="exactly one outgoing edge"):
        load_workflow_document(yaml.safe_dump(raw))


def test_rejects_subworkflow_node_with_an_ambiguous_joined_event() -> None:
    raw = payload()
    raw["graph"]["edges"].append(
        {
            "id": "children-joined-2",
            "source": "children",
            "target": "review",
            "label": "children.other -> children.other",
        }
    )
    with pytest.raises(WorkflowDocumentError, match="exactly one outgoing edge"):
        load_workflow_document(yaml.safe_dump(raw))


def test_passive_wait_node_survives_round_trip_without_a_persona() -> None:
    raw = payload()
    raw["graph"]["nodes"].append(
        {"id": "external-wait", "kind": "wait", "label": "Await external observation"}
    )
    raw["graph"]["edges"].extend(
        [
            {
                "id": "wait-in",
                "source": "review",
                "target": "external-wait",
                "label": "review.waiting -> review.waiting",
            },
            {
                "id": "wait-out",
                "source": "external-wait",
                "target": "review",
                "label": "review.observed -> review.observed",
            },
        ]
    )

    document = load_workflow_document(yaml.safe_dump(raw))

    assert workflow_document_payload(document) == raw


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("v1", "requires workflow schema_version 2"),
        ("persona", "must not declare personas"),
        ("incoming", "requires incoming suspension"),
        ("outgoing", "requires incoming suspension"),
        ("label", "label must be a non-empty string"),
    ],
)
def test_rejects_invalid_passive_wait_nodes(mutation: str, reason: str) -> None:
    raw = payload()
    wait = {"id": "external-wait", "kind": "wait", "label": "Await observation"}
    raw["graph"]["nodes"].append(wait)
    incoming = {
        "id": "wait-in",
        "source": "review",
        "target": "external-wait",
        "label": "review.waiting -> review.waiting",
    }
    outgoing = {
        "id": "wait-out",
        "source": "external-wait",
        "target": "review",
        "label": "review.observed -> review.observed",
    }
    raw["graph"]["edges"].extend([incoming, outgoing])
    if mutation == "v1":
        raw["schema_version"] = 1
        raw.pop("workflow_dependencies")
        raw["graph"]["nodes"] = [node for node in raw["graph"]["nodes"] if node["id"] != "children"]
    elif mutation == "persona":
        wait["personaIds"] = ["reviewer"]
    elif mutation == "incoming":
        raw["graph"]["edges"].remove(incoming)
    elif mutation == "outgoing":
        raw["graph"]["edges"].remove(outgoing)
    else:
        wait["label"] = ""

    with pytest.raises(WorkflowDocumentError, match=reason):
        load_workflow_document(yaml.safe_dump(raw))


def test_rejects_wait_node_with_an_ambiguous_observed_event() -> None:
    raw = payload()
    raw["graph"]["nodes"].append(
        {"id": "external-wait", "kind": "wait", "label": "Await observation"}
    )
    raw["graph"]["edges"].extend(
        [
            {
                "id": "wait-in",
                "source": "review",
                "target": "external-wait",
                "label": "review.waiting -> review.waiting",
            },
            {
                "id": "wait-out-1",
                "source": "external-wait",
                "target": "review",
                "label": "review.observed -> review.observed",
            },
            {
                "id": "wait-out-2",
                "source": "external-wait",
                "target": "children",
                "label": "review.other -> review.other",
            },
        ]
    )
    with pytest.raises(WorkflowDocumentError, match="agree on exactly one observed event"):
        load_workflow_document(yaml.safe_dump(raw))


def test_rejects_more_than_one_wait_node() -> None:
    raw = payload()
    for suffix in ("a", "b"):
        raw["graph"]["nodes"].append(
            {"id": f"external-wait-{suffix}", "kind": "wait", "label": "Await observation"}
        )
        raw["graph"]["edges"].extend(
            [
                {
                    "id": f"wait-in-{suffix}",
                    "source": "review",
                    "target": f"external-wait-{suffix}",
                    "label": "review.waiting -> review.waiting",
                },
                {
                    "id": f"wait-out-{suffix}",
                    "source": f"external-wait-{suffix}",
                    "target": "review",
                    "label": "review.observed -> review.observed",
                },
            ]
        )

    with pytest.raises(WorkflowDocumentError, match="at most one wait node"):
        load_workflow_document(yaml.safe_dump(raw))

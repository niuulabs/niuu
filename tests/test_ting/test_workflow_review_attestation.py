from copy import deepcopy
from uuid import uuid4

import pytest
import yaml

from ting.domain.exceptions import WorkflowDocumentError
from ting.domain.workflow_document import (
    load_workflow_document,
    workflow_review_attestation,
)


def _document() -> dict:
    digest = "sha256:" + "a" * 64
    return {
        "schema_version": 2,
        "id": str(uuid4()),
        "name": "Portable audit",
        "description": "Review a non-code artifact",
        "version": "1.0.0",
        "persona_dependencies": {
            "platform-auditor": {
                "id": "platform-auditor",
                "revision": "audit-v1",
                "digest": digest,
            },
            "data-steward": {
                "id": "data-steward",
                "revision": "privacy-v1",
                "digest": digest,
            },
        },
        "workflow_dependencies": {},
        "graph": {
            "executionContract": "artifact-audit/v1",
            "reviewAttestation": {
                "version": 1,
                "scope": "artifact",
                "eventType": "artifact.review.completed",
                "roles": {
                    "architecture": "platform-auditor",
                    "privacy": "data-steward",
                },
            },
            "nodes": [
                {
                    "id": "reviews",
                    "kind": "stage",
                    "joinMode": "all",
                    "stageMembers": [
                        {"personaId": "platform-auditor"},
                        {"personaId": "data-steward"},
                    ],
                }
            ],
            "edges": [],
        },
    }


def test_custom_reviewer_roles_and_personas_are_portable() -> None:
    document = load_workflow_document(yaml.safe_dump(_document()))

    binding = workflow_review_attestation(
        document.graph,
        persona_dependencies=document.persona_dependencies,
    )

    assert binding is not None
    assert binding.scope == "artifact"
    assert binding.roles == {
        "architecture": "platform-auditor",
        "privacy": "data-steward",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unknown_field", "unknown"),
        ("missing_roles", "missing"),
        ("unknown_persona", "undeclared persona"),
        ("duplicate_persona", "personas must be unique"),
        ("unjoined_persona", "joinMode all stage"),
    ],
)
def test_rejects_tampered_review_attestation_bindings(
    mutation: str,
    message: str,
) -> None:
    raw = deepcopy(_document())
    attestation = raw["graph"]["reviewAttestation"]
    if mutation == "unknown_field":
        attestation["producer"] = "local"
    elif mutation == "missing_roles":
        attestation.pop("roles")
    elif mutation == "unknown_persona":
        attestation["roles"]["privacy"] = "missing-steward"
    elif mutation == "duplicate_persona":
        attestation["roles"]["privacy"] = "platform-auditor"
    else:
        raw["graph"]["nodes"][0]["joinMode"] = "any"

    with pytest.raises(WorkflowDocumentError, match=message):
        load_workflow_document(yaml.safe_dump(raw))


def test_review_verdict_policy_requires_explicit_review_binding() -> None:
    """A `reviewVerdictPolicy` stage gates its outcome on attested reviewer

    identity, so a graph that declares one without `reviewAttestation` is
    rejected regardless of what its `executionContract` is named.
    """
    raw = _document()
    raw["graph"].pop("reviewAttestation")
    raw["graph"]["nodes"] = [
        {
            "id": "reviews",
            "kind": "stage",
            "joinMode": "all",
            "stageMembers": [
                {"personaId": "platform-auditor"},
                {"personaId": "data-steward"},
            ],
        },
        {
            "id": "acceptance",
            "kind": "stage",
            "joinMode": "all",
            "reviewVerdictPolicy": {
                "eventType": "artifact.review.completed",
                "passOutcomes": ["accepted"],
                "failOutcomes": ["rejected"],
                "bindingFields": ["attempt_id"],
            },
            "stageMembers": [{"personaId": "platform-auditor"}],
        },
    ]
    raw["graph"]["edges"] = [
        {
            "id": "reviews-acceptance",
            "source": "reviews",
            "target": "acceptance",
            "label": "artifact.review.completed -> artifact.review.completed",
        },
        {
            "id": "acceptance-accept",
            "source": "acceptance",
            "target": "reviews",
            "label": "accepted -> accepted",
        },
        {
            "id": "acceptance-reject",
            "source": "acceptance",
            "target": "reviews",
            "label": "rejected -> rejected",
        },
    ]

    with pytest.raises(WorkflowDocumentError, match="requires graph.reviewAttestation"):
        load_workflow_document(yaml.safe_dump(raw))


def test_subworkflow_requiring_review_receipts_requires_explicit_review_binding() -> None:
    """A subworkflow whose resultSchema demands `reviewReceipts` hands a signed

    receipt across the child/parent boundary, so it also requires
    `reviewAttestation` on the declaring graph.
    """
    raw = _document()
    raw["graph"].pop("reviewAttestation")
    raw["graph"]["nodes"] = [
        {
            "id": "children",
            "kind": "subworkflow",
            "workflowDependency": "child",
            "allowedCoordinator": "platform-auditor",
            "maxChildren": 1,
            "maxAttempts": 1,
            "joinMode": "all",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
            "resultSchema": {
                "type": "object",
                "properties": {"reviewReceipts": {"type": "array"}},
                "required": ["reviewReceipts"],
            },
        }
    ]
    raw["graph"]["edges"] = []
    raw["workflow_dependencies"] = {
        "child": {
            "id": str(uuid4()),
            "revision": "content-abc",
            "digest": "sha256:" + "b" * 64,
        }
    }

    with pytest.raises(WorkflowDocumentError, match="requires graph.reviewAttestation"):
        load_workflow_document(yaml.safe_dump(raw))


def test_malformed_explicit_binding_never_falls_back() -> None:
    raw = _document()
    raw["graph"]["reviewAttestation"]["roles"] = {}

    with pytest.raises(WorkflowDocumentError, match="roles must be a non-empty mapping"):
        load_workflow_document(yaml.safe_dump(raw))


def test_graph_with_no_attestation_consuming_construct_never_requires_a_binding() -> None:
    graph = {"executionContract": "custom-delivery/v1", "nodes": [], "edges": []}

    assert workflow_review_attestation(graph) is None

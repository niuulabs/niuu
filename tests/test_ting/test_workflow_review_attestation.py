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


def test_new_developer_contract_requires_explicit_review_binding() -> None:
    raw = _document()
    raw["graph"]["executionContract"] = "developer-workstream/v2"
    raw["graph"].pop("reviewAttestation")

    with pytest.raises(WorkflowDocumentError, match="requires graph.reviewAttestation"):
        load_workflow_document(yaml.safe_dump(raw))


def test_frozen_v1_contract_uses_only_explicit_legacy_handler() -> None:
    raw = _document()
    raw["graph"]["executionContract"] = "developer-workstream/v1"
    raw["graph"].pop("reviewAttestation")
    raw["graph"]["nodes"] = []
    raw["persona_dependencies"] = {}
    document = load_workflow_document(yaml.safe_dump(raw))

    assert workflow_review_attestation(document.graph) is None
    legacy = workflow_review_attestation(document.graph, allow_legacy=True)
    assert legacy is not None
    assert legacy.roles == {
        "code": "developer-code-reviewer",
        "security": "developer-security-reviewer",
        "adversarial": "developer-adversarial-reviewer",
    }


def test_frozen_root_delivery_contract_retains_integration_review_binding() -> None:
    raw = _document()
    raw["graph"]["executionContract"] = "developer-delivery/v1"
    raw["graph"].pop("reviewAttestation")
    raw["graph"]["nodes"] = []
    raw["persona_dependencies"] = {}
    document = load_workflow_document(yaml.safe_dump(raw))

    assert workflow_review_attestation(document.graph) is None
    legacy = workflow_review_attestation(document.graph, allow_legacy=True)
    assert legacy is not None
    assert legacy.scope == "integration"
    assert legacy.event_type == "developer.integration.reviewed"
    assert legacy.roles == {"integration": "developer-integration-verifier"}


def test_malformed_explicit_legacy_binding_never_falls_back() -> None:
    raw = _document()
    raw["graph"]["executionContract"] = "developer-delivery/v1"
    raw["graph"]["reviewAttestation"]["roles"] = {}

    with pytest.raises(WorkflowDocumentError, match="roles must be a non-empty mapping"):
        load_workflow_document(yaml.safe_dump(raw))


def test_unknown_contract_never_infers_a_review_binding() -> None:
    graph = {"executionContract": "custom-delivery/v1", "nodes": [], "edges": []}

    assert workflow_review_attestation(graph, allow_legacy=True) is None

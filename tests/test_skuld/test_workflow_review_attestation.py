import pytest

from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.workflow_runtime import _workflow_review_attestation


def test_skuld_maps_custom_reviewer_personas_from_frozen_graph() -> None:
    binding = _workflow_review_attestation(
        {
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
                    "kind": "stage",
                    "joinMode": "all",
                    "stageMembers": [
                        {"personaId": "platform-auditor"},
                        {"personaId": "data-steward"},
                    ],
                }
            ],
        }
    )

    assert binding is not None
    assert binding.personas == {
        "platform-auditor": "architecture",
        "data-steward": "privacy",
    }


def test_broker_projects_custom_authenticated_persona_to_configured_role(tmp_path) -> None:
    graph = {
        "reviewAttestation": {
            "version": 1,
            "scope": "artifact",
            "eventType": "artifact.review.completed",
            "roles": {"privacy": "data-steward"},
        },
        "nodes": [
            {
                "kind": "stage",
                "joinMode": "all",
                "stageMembers": [{"personaId": "data-steward"}],
            }
        ],
    }
    broker = Broker(
        settings=SkuldSettings(
            session={"id": "audit-session", "workspace_dir": str(tmp_path)},
            workflow={"graph": graph},
        )
    )

    outcome = broker._developer_review_outcome(
        peer_id="peer-1",
        persona="data-steward",
        event_type="artifact.review.completed",
        fields={
            "attempt_id": "attempt-1",
            "candidate_sha": "a" * 40,
            "candidate_tree": "b" * 40,
            "verdict": "pass",
            "findings": [],
        },
        valid=True,
        event_id="event-1",
    )

    assert outcome is not None
    assert outcome["role"] == "privacy"
    assert outcome["personaId"] == "data-steward"
    assert outcome["scope"] == "artifact"


def test_broker_threads_configured_review_event_type_into_room_bridge(tmp_path) -> None:
    """The room bridge must treat the workflow's own reviewAttestation.eventType

    as strictly as the built-in developer.* event types, or a custom (v2) review
    event with no `valid` field would be coerced to `valid: True` downstream.
    """
    graph = {
        "reviewAttestation": {
            "version": 1,
            "scope": "artifact",
            "eventType": "artifact.review.completed",
            "roles": {"privacy": "data-steward"},
        },
        "nodes": [
            {
                "kind": "stage",
                "joinMode": "all",
                "stageMembers": [{"personaId": "data-steward"}],
            }
        ],
    }
    broker = Broker(
        settings=SkuldSettings(
            session={"id": "audit-room", "workspace_dir": str(tmp_path)},
            workflow={"graph": graph},
            room={"enabled": True},
        )
    )

    assert broker._room_bridge is not None
    assert "artifact.review.completed" in broker._room_bridge._strict_review_event_types
    assert "developer.review.completed" in broker._room_bridge._strict_review_event_types


def test_skuld_supports_frozen_v1_workstream_contract_explicitly() -> None:
    binding = _workflow_review_attestation({"executionContract": "developer-workstream/v1"})

    assert binding is not None
    assert binding.roles["code"] == "developer-code-reviewer"


def test_skuld_supports_frozen_v1_root_delivery_integration_binding() -> None:
    binding = _workflow_review_attestation({"executionContract": "developer-delivery/v1"})

    assert binding is not None
    assert binding.scope == "integration"
    assert binding.event_type == "developer.integration.reviewed"
    assert binding.roles == {"integration": "developer-integration-verifier"}


def test_frozen_root_delivery_broker_still_projects_integration_review(tmp_path) -> None:
    broker = Broker(
        settings=SkuldSettings(
            session={"id": "legacy-root", "workspace_dir": str(tmp_path)},
            workflow={"graph": {"executionContract": "developer-delivery/v1"}},
        )
    )

    outcome = broker._developer_review_outcome(
        peer_id="integration-peer",
        persona="developer-integration-verifier",
        event_type="developer.integration.reviewed",
        fields={
            "attempt_id": "allocation-1",
            "candidate_sha": "a" * 40,
            "candidate_tree": "b" * 40,
            "verdict": "pass",
            "findings": [],
        },
        valid=True,
        event_id="legacy-review-1",
    )

    assert outcome is not None
    assert outcome["role"] == "integration"
    assert outcome["scope"] == "integration"


def test_skuld_never_falls_back_from_malformed_explicit_legacy_binding() -> None:
    with pytest.raises(ValueError, match="roles are required"):
        _workflow_review_attestation(
            {
                "executionContract": "developer-delivery/v1",
                "reviewAttestation": {
                    "version": 1,
                    "scope": "integration",
                    "eventType": "developer.integration.reviewed",
                    "roles": {},
                },
            }
        )


def test_skuld_does_not_infer_bindings_for_unknown_contracts() -> None:
    assert _workflow_review_attestation({"executionContract": "custom-delivery/v1"}) is None


@pytest.mark.parametrize(
    "graph",
    [
        {"executionContract": "developer-workstream/v2"},
        {
            "reviewAttestation": {
                "version": 1,
                "scope": "artifact",
                "eventType": "artifact.review.completed",
                "roles": {"architecture": "same", "privacy": "same"},
            }
        },
        {
            "reviewAttestation": {
                "version": 1,
                "scope": "artifact",
                "eventType": "artifact.review.completed",
                "roles": {},
            }
        },
    ],
)
def test_skuld_rejects_missing_or_tampered_review_bindings(graph: dict) -> None:
    with pytest.raises(ValueError):
        _workflow_review_attestation(graph)

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

    outcome = broker._attested_review_outcome(
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

    strictly, or a review event with no `valid` field would be coerced to
    `valid: True` downstream. Only the event type(s) this workflow's own
    binding names are strict — nothing is built into the room bridge itself.
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
    assert broker._room_bridge._strict_review_event_types == frozenset(
        {"artifact.review.completed"}
    )


def test_skuld_reads_nothing_without_an_explicit_binding() -> None:
    assert _workflow_review_attestation({"executionContract": "developer-delivery/v1"}) is None
    assert _workflow_review_attestation({"executionContract": "custom-delivery/v1"}) is None
    assert _workflow_review_attestation({}) is None


def test_skuld_never_falls_back_from_a_malformed_explicit_binding() -> None:
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


@pytest.mark.parametrize(
    "graph",
    [
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
def test_skuld_rejects_tampered_review_bindings(graph: dict) -> None:
    with pytest.raises(ValueError):
        _workflow_review_attestation(graph)

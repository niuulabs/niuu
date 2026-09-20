from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from niuu.adapters.evidence_signing import RsaEvidenceAuthenticator
from niuu.domain.delivery import ReviewReceipt, evidence_payload
from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from skuld.broker import Broker
from skuld.config import SkuldSettings
from tests.test_ting.test_delivery_execution import MemoryRepository, _execution
from ting.adapters.delivery_integration_reviews import TrustedIntegrationReviewProjector
from ting.domain.delivery_execution import ExecutionState, WorkflowExecutionError
from ting.ports.volundr import ActivityEvent


def _context():
    execution = replace(
        _execution(state=ExecutionState.RUNNING),
        integration_allocation={
            "allocation_id": "integration-attempt-1",
            "campaign_id": "campaign",
            "workstream_key": "integration",
            "repository": "https://example.test/repo.git",
            "base_sha": "a" * 40,
            "worker_id": "integrator",
        },
        integration_receipts=({"receipt_id": "integration-receipt-1"},),
        integration_candidate={
            "candidate_sha": "b" * 40,
            "candidate_tree": "c" * 40,
        },
    )
    repository = MemoryRepository(execution)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    authenticator = RsaEvidenceAuthenticator(
        key_id="integration-reviews",
        private_key_pem=private_pem,
        trusted_public_keys={"integration-reviews": public_pem},
        producer_keys={"integration-review-producer": ["integration-reviews"]},
    )
    projector = TrustedIntegrationReviewProjector(
        repository=repository,
        authenticator=authenticator,
        producer_id="integration-review-producer",
    )
    review = {
        "schemaVersion": 1,
        "eventId": "integration-review-event-1",
        "sessionId": execution.parent_session_id,
        "scope": "integration",
        "role": "integration",
        "reviewerId": "integration-reviewer",
        "personaId": "developer-integration-verifier",
        "attemptId": "integration-attempt-1",
        "candidateSha": "b" * 40,
        "candidateTree": "c" * 40,
        "verdict": "pass",
        "summary": "Integrated candidate is acceptable",
        "findings": [],
        "valid": True,
    }
    event = ActivityEvent(
        session_id=execution.parent_session_id,
        state="active",
        metadata={"attested_review": review},
        owner_id=execution.owner_id,
    )
    return repository, authenticator, projector, event


@pytest.mark.asyncio
async def test_projects_signed_integration_review_onto_exact_inspected_candidate() -> None:
    repository, authenticator, projector, event = _context()

    assert await projector.handle_activity(event, repository.execution.owner_id)
    raw = repository.execution.integration_review_receipt
    receipt = ReviewReceipt.model_validate(raw)

    assert receipt.attempt_id == "integration-attempt-1"
    assert receipt.candidate_sha == "b" * 40
    unsigned = receipt.model_copy(update={"provenance": None})
    assert authenticator.verify(evidence_payload(unsigned), receipt.provenance)

    revision = repository.execution.revision
    assert await projector.handle_activity(event, repository.execution.owner_id)
    assert repository.execution.revision == revision


@pytest.mark.asyncio
async def test_projects_parent_session_error_with_persisted_reason() -> None:
    repository, _, projector, event = _context()
    failed = replace(
        event,
        state="error",
        metadata={"error": "Coordinator blocked: delivery evidence was denied"},
    )

    assert await projector.handle_activity(failed, repository.execution.owner_id)
    assert repository.execution.state == ExecutionState.FAILED
    assert (
        repository.execution.suspension_reason
        == "Coordinator blocked: delivery evidence was denied"
    )


@pytest.mark.parametrize("binding", ["child-session", "foreign-owner"])
@pytest.mark.asyncio
async def test_ignores_failure_not_bound_to_exact_parent_owner_and_session(
    binding: str,
) -> None:
    repository, _, projector, event = _context()
    initial = repository.execution
    failed = replace(
        event,
        session_id="child-session" if binding == "child-session" else event.session_id,
        owner_id="another-owner" if binding == "foreign-owner" else event.owner_id,
        state="error",
        metadata={"error": "foreign failure"},
    )

    assert not await projector.handle_activity(failed, repository.execution.owner_id)
    assert repository.execution == initial


@pytest.mark.parametrize("state", [ExecutionState.COMPLETED, ExecutionState.CANCELED])
@pytest.mark.asyncio
async def test_parent_failure_does_not_overwrite_terminal_execution(state) -> None:
    repository, _, projector, event = _context()
    repository.execution = replace(
        repository.execution,
        state=state,
        suspension_reason=state.value,
    )
    failed = replace(
        event,
        state="error",
        metadata={"error": "late replay"},
    )

    assert not await projector.handle_activity(failed, repository.execution.owner_id)
    assert repository.execution.state == state
    assert repository.execution.suspension_reason == state.value


@pytest.mark.asyncio
async def test_parent_failure_replay_is_idempotent() -> None:
    repository, _, projector, event = _context()
    failed = replace(
        event,
        state="error",
        metadata={"error": "coordinator failed"},
    )

    assert await projector.handle_activity(failed, repository.execution.owner_id)
    revision = repository.execution.revision
    assert not await projector.handle_activity(failed, repository.execution.owner_id)
    assert repository.execution.revision == revision
    assert repository.execution.state == ExecutionState.FAILED
    assert repository.execution.suspension_reason == "coordinator failed"


@pytest.mark.parametrize("valid", [False, None, "true", "false", 1, {"valid": True}])
@pytest.mark.asyncio
async def test_integration_projector_requires_literal_validation_before_signing(valid) -> None:
    repository, _, projector, event = _context()
    event.metadata["attested_review"]["valid"] = valid

    with pytest.raises(WorkflowExecutionError, match="lineage is invalid"):
        await projector.handle_activity(event, repository.execution.owner_id)
    assert repository.execution.integration_review_receipt is None


@pytest.mark.asyncio
async def test_real_integration_persona_contract_projects_exact_allocation_id(tmp_path) -> None:
    persona = FilesystemPersonaAdapter(persona_dirs=[], include_builtin=True).load(
        "developer-integration-verifier"
    )
    assert persona is not None
    assert "attempt_id" in persona.produces.schema
    assert "integration allocation ID" in persona.produces.schema["attempt_id"].description

    repository, _, projector, event = _context()
    broker = Broker(
        settings=SkuldSettings(
            session={"id": event.session_id, "workspace_dir": str(tmp_path)},
            room={"enabled": True},
            workflow={
                "graph": {
                    "reviewAttestation": {
                        "version": 1,
                        "scope": "integration",
                        "eventType": "developer.integration.reviewed",
                        "roles": {"integration": "developer-integration-verifier"},
                    },
                    "nodes": [
                        {
                            "kind": "stage",
                            "joinMode": "all",
                            "stageMembers": [{"personaId": "developer-integration-verifier"}],
                        }
                    ],
                }
            },
        )
    )
    broker._room_bridge = MagicMock()
    broker._room_bridge.participants = {
        "integration-review-peer": MagicMock(persona="developer-integration-verifier")
    }
    broker._emit_pipeline_event = AsyncMock()
    broker._maybe_activate_workflow_gate = AsyncMock()
    broker._maybe_emit_workflow_terminal_outcome = AsyncMock()
    broker._report_activity_state = AsyncMock()

    await broker._emit_peer_outcome_pipeline_event(
        "integration-review-peer",
        {
            "metadata": {
                "event_type": "developer.integration.reviewed",
                "task_id": "integration-review-event-1",
            },
            "data": {
                "event_type": "developer.integration.reviewed",
                "valid": True,
                "fields": {
                    "attempt_id": "integration-attempt-1",
                    "candidate_sha": "b" * 40,
                    "candidate_tree": "c" * 40,
                    "plan_revision": "plan-7",
                    "verdict": "pass",
                    "requirement_coverage": [],
                    "findings": [],
                    "summary": "Integrated candidate is acceptable",
                },
            },
        },
    )

    live = broker._report_activity_state.await_args.kwargs["extra_metadata"]
    assert live["attested_review"]["attemptId"] == "integration-attempt-1"
    projected = ActivityEvent(
        session_id=event.session_id,
        state="active",
        metadata=live,
        owner_id=repository.execution.owner_id,
    )
    assert await projector.handle_activity(projected, repository.execution.owner_id)
    receipt = ReviewReceipt.model_validate(repository.execution.integration_review_receipt)
    assert receipt.attempt_id == "integration-attempt-1"


@pytest.mark.asyncio
async def test_rejects_review_for_stale_integration_candidate() -> None:
    repository, _, projector, event = _context()
    repository.execution = replace(
        repository.execution,
        integration_candidate={
            "candidate_sha": "d" * 40,
            "candidate_tree": "e" * 40,
        },
    )

    with pytest.raises(WorkflowExecutionError, match="lineage"):
        await projector.handle_activity(event, repository.execution.owner_id)

    assert repository.execution.integration_review_receipt is None


@pytest.mark.asyncio
async def test_rejects_review_before_trusted_candidate_inspection() -> None:
    repository, _, projector, event = _context()
    repository.execution = replace(repository.execution, integration_candidate=None)

    with pytest.raises(WorkflowExecutionError, match="before trusted candidate"):
        await projector.handle_activity(event, repository.execution.owner_id)


@pytest.mark.parametrize("mutation", ["persona", "session", "reviewer"])
@pytest.mark.asyncio
async def test_rejects_foreign_integration_review_lineage(mutation: str) -> None:
    repository, _, projector, event = _context()
    review = event.metadata["attested_review"]
    if mutation == "persona":
        review["personaId"] = "developer-coder"
    elif mutation == "session":
        review["sessionId"] = "another-session"
    else:
        review["reviewerId"] = "integrator"

    with pytest.raises(WorkflowExecutionError):
        await projector.handle_activity(event, repository.execution.owner_id)

    assert repository.execution.integration_review_receipt is None


@pytest.mark.asyncio
async def test_ignores_activity_without_integration_review() -> None:
    repository, _, projector, event = _context()
    event.metadata.clear()

    assert not await projector.handle_activity(event, repository.execution.owner_id)

    event.metadata["attested_review"] = {"scope": "workstream"}
    assert not await projector.handle_activity(event, repository.execution.owner_id)


@pytest.mark.asyncio
async def test_rejects_unbound_session_and_malformed_findings() -> None:
    repository, _, projector, event = _context()

    with pytest.raises(WorkflowExecutionError, match="not bound"):
        await projector.handle_activity(event, "another-owner")

    event.metadata["attested_review"]["findings"] = ["not-an-object"]
    with pytest.raises(WorkflowExecutionError, match="findings are malformed"):
        await projector.handle_activity(event, repository.execution.owner_id)


@pytest.mark.asyncio
async def test_rejects_invalid_verdict_and_malformed_receipt() -> None:
    repository, _, projector, event = _context()
    event.metadata["attested_review"]["verdict"] = "approve-ish"
    with pytest.raises(WorkflowExecutionError, match="verdict is invalid"):
        await projector.handle_activity(event, repository.execution.owner_id)

    event.metadata["attested_review"]["verdict"] = "pass"
    event.metadata["attested_review"]["summary"] = "x" * 70_000
    with pytest.raises(WorkflowExecutionError, match="receipt is invalid"):
        await projector.handle_activity(event, repository.execution.owner_id)


def test_projector_requires_a_signing_producer() -> None:
    repository, authenticator, _, _ = _context()

    with pytest.raises(ValueError, match="producer is required"):
        TrustedIntegrationReviewProjector(
            repository=repository,
            authenticator=authenticator,
            producer_id=" ",
        )

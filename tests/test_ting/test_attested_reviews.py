import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from niuu.adapters.evidence_signing import RsaEvidenceAuthenticator
from niuu.domain.delivery import FindingDisposition, ReviewReceipt, ReviewVerdict, evidence_payload
from tests.test_ting.test_delivery_execution import _execution, _proposal
from ting.delivery.attested_reviews import TrustedChildReviewAttestor, _finding
from ting.delivery.domain import make_children
from ting.domain.workflow_execution import WorkflowExecutionError


def _context():
    execution = replace(_execution(), current_generation=1)
    child = replace(
        make_children(execution, 1, (_proposal(execution, "api"),))[0],
        task_id="a2a-task",
    )
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
    producers = {
        "code-producer": ["reviews"],
        "security-producer": ["reviews"],
        "adversarial-producer": ["reviews"],
    }
    authenticator = RsaEvidenceAuthenticator(
        key_id="reviews",
        private_key_pem=private_pem,
        trusted_public_keys={"reviews": public_pem},
        producer_keys=producers,
    )
    attestor = TrustedChildReviewAttestor(
        authenticator=authenticator,
        role_producers={
            "code": "code-producer",
            "security": "security-producer",
            "adversarial": "adversarial-producer",
        },
        workflow_resolver=lambda *_args: SimpleNamespace(
            graph={
                "executionContract": "developer-workstream/v1",
                "reviewAttestation": {
                    "version": 1,
                    "scope": "workstream",
                    "eventType": "developer.review.completed",
                    "roles": {
                        "code": "developer-code-reviewer",
                        "security": "developer-security-reviewer",
                        "adversarial": "developer-adversarial-reviewer",
                    },
                },
                "nodes": [
                    {
                        "kind": "stage",
                        "joinMode": "all",
                        "stageMembers": [
                            {"personaId": "developer-code-reviewer"},
                            {"personaId": "developer-security-reviewer"},
                            {"personaId": "developer-adversarial-reviewer"},
                        ],
                    }
                ],
            },
            persona_dependencies={
                "developer-code-reviewer": object(),
                "developer-security-reviewer": object(),
                "developer-adversarial-reviewer": object(),
            },
        ),
    )
    result = {
        "attemptId": str(child.id),
        "candidateSha": "b" * 40,
        "candidateTree": "c" * 40,
        "verificationReceipts": [],
        "requirementEvidence": [],
        "_trustedReviewEnvelope": {
            "taskId": child.task_id,
            "sessionId": "session-1",
            "workflowId": str(child.template_id),
            "workflowRevision": child.template_revision,
            "workflowDigest": child.template_digest,
            "reviews": [
                {
                    "eventId": f"event-{role}",
                    "sessionId": "session-1",
                    "role": role,
                    "scope": "workstream",
                    "reviewerId": f"reviewer-{role}",
                    "personaId": f"developer-{role}-reviewer",
                    "attemptId": str(child.id),
                    "candidateSha": "b" * 40,
                    "candidateTree": "c" * 40,
                    "verdict": "pass",
                    "summary": f"{role} passed",
                    "findings": [],
                    "valid": True,
                }
                for role in ("code", "security", "adversarial")
            ],
        },
    }
    result["_trustedReviewEnvelope"]["reviews"][2]["personaId"] = "developer-adversarial-reviewer"
    return execution, child, authenticator, attestor, result


@pytest.mark.asyncio
async def test_attests_custom_portable_roles_without_developer_persona_names() -> None:
    execution, child, authenticator, _, result = _context()
    roles = {"architecture": "platform-auditor", "privacy": "data-steward"}
    result["_trustedReviewEnvelope"]["reviews"] = [
        {
            "eventId": f"event-{role}",
            "sessionId": "session-1",
            "role": role,
            "scope": "workstream",
            "reviewerId": f"reviewer-{role}",
            "personaId": persona_id,
            "attemptId": str(child.id),
            "candidateSha": "b" * 40,
            "candidateTree": "c" * 40,
            "verdict": "pass",
            "summary": f"{role} passed",
            "findings": [],
            "valid": True,
        }
        for role, persona_id in roles.items()
    ]
    attestor = TrustedChildReviewAttestor(
        authenticator=authenticator,
        role_producers={
            "architecture": "code-producer",
            "privacy": "security-producer",
        },
        workflow_resolver=lambda *_args: SimpleNamespace(
            graph={
                "reviewAttestation": {
                    "version": 1,
                    "scope": "workstream",
                    "eventType": "review.finished",
                    "roles": roles,
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
            },
            persona_dependencies={name: object() for name in roles.values()},
        ),
    )

    attested = await attestor.attest(execution, child, result)

    assert {receipt["role"] for receipt in attested["reviewReceipts"]} == set(roles)


@pytest.mark.asyncio
async def test_attests_server_bound_reviews_and_discards_envelope() -> None:
    execution, child, authenticator, attestor, result = _context()
    attested = await attestor.attest(execution, child, result)

    assert "_trustedReviewEnvelope" not in attested
    assert {item["role"] for item in attested["reviewReceipts"]} == {
        "code",
        "security",
        "adversarial",
    }
    for raw in attested["reviewReceipts"]:
        receipt = ReviewReceipt.model_validate(raw)
        assert receipt.attempt_id == str(child.id)
        unsigned = receipt.model_copy(update={"provenance": None})
        assert authenticator.verify(evidence_payload(unsigned), receipt.provenance)


@pytest.mark.parametrize("valid", [False, None, "true", "false", 1, {"valid": True}])
@pytest.mark.asyncio
async def test_attestor_requires_literal_validation_before_signing(valid) -> None:
    execution, child, _, attestor, result = _context()
    result["_trustedReviewEnvelope"]["reviews"][0]["valid"] = valid

    with pytest.raises(WorkflowExecutionError, match="not validated"):
        await attestor.attest(execution, child, result)


@pytest.mark.asyncio
async def test_attestor_rejects_a_child_workflow_with_no_review_binding() -> None:
    """A child graph declaring neither `reviewAttestation` nor a construct that

    requires one has nothing for the attestor to authenticate reviewers
    against, so it must fail loudly rather than sign an ungoverned receipt.
    """
    execution, child, authenticator, _, result = _context()
    attestor = TrustedChildReviewAttestor(
        authenticator=authenticator,
        role_producers={"code": "code-producer"},
        workflow_resolver=lambda *_args: SimpleNamespace(
            graph={"nodes": [], "edges": []},
            persona_dependencies={},
        ),
    )

    with pytest.raises(WorkflowExecutionError, match="no review attestation binding"):
        await attestor.attest(execution, child, result)


@pytest.mark.parametrize(
    "mutation",
    [
        "task",
        "revision",
        "candidate",
        "persona",
        "worker",
        "duplicate",
        "unknown_role",
        "missing_role",
        "scope",
        "finding",
        "receipt_validation",
    ],
)
@pytest.mark.asyncio
async def test_rejects_untrusted_or_malformed_review_bindings(mutation: str) -> None:
    execution, child, _, attestor, result = _context()
    envelope = result["_trustedReviewEnvelope"]
    if mutation == "task":
        envelope["taskId"] = "other"
    elif mutation == "revision":
        envelope["workflowDigest"] = "sha256:" + "f" * 64
    elif mutation == "candidate":
        envelope["reviews"][0]["candidateSha"] = "f" * 40
    elif mutation == "persona":
        envelope["reviews"][0]["personaId"] = "developer-coder"
    elif mutation == "worker":
        envelope["reviews"][0]["reviewerId"] = child.workspace["worker_id"]
    elif mutation == "duplicate":
        envelope["reviews"][1]["eventId"] = envelope["reviews"][0]["eventId"]
    elif mutation == "unknown_role":
        envelope["reviews"][0]["role"] = "release"
    elif mutation == "missing_role":
        envelope["reviews"].pop()
    elif mutation == "scope":
        envelope["reviews"][0]["scope"] = "integration"
    elif mutation == "finding":
        envelope["reviews"][0]["findings"] = ["not structured"]
    else:
        envelope["reviews"][0]["summary"] = "x" * 4097

    with pytest.raises(WorkflowExecutionError):
        await attestor.attest(execution, child, result)


# ---------------------------------------------------------------------------
# TrustedChildReviewAttestor construction and defensive branches
# ---------------------------------------------------------------------------


def test_constructor_rejects_empty_role_producers() -> None:
    _, _, authenticator, _, _ = _context()

    with pytest.raises(ValueError, match="non-empty role producers"):
        TrustedChildReviewAttestor(authenticator=authenticator, role_producers={})


@pytest.mark.parametrize(
    "role_producers",
    [
        {"": "code-producer"},
        {"code": ""},
        {"  ": "code-producer"},
        {"code": "  "},
    ],
)
def test_constructor_rejects_blank_role_or_producer(role_producers) -> None:
    _, _, authenticator, _, _ = _context()

    with pytest.raises(ValueError, match="non-empty role producers"):
        TrustedChildReviewAttestor(authenticator=authenticator, role_producers=role_producers)


@pytest.mark.asyncio
async def test_attest_raises_when_no_producer_is_configured_for_a_pinned_role() -> None:
    execution, child, authenticator, _, result = _context()
    attestor = TrustedChildReviewAttestor(
        authenticator=authenticator,
        role_producers={"code": "code-producer", "security": "security-producer"},
        workflow_resolver=lambda *_args: SimpleNamespace(
            graph={
                "reviewAttestation": {
                    "version": 1,
                    "scope": "workstream",
                    "eventType": "developer.review.completed",
                    "roles": {
                        "code": "developer-code-reviewer",
                        "security": "developer-security-reviewer",
                        "adversarial": "developer-adversarial-reviewer",
                    },
                },
                "nodes": [
                    {
                        "kind": "stage",
                        "joinMode": "all",
                        "stageMembers": [
                            {"personaId": "developer-code-reviewer"},
                            {"personaId": "developer-security-reviewer"},
                            {"personaId": "developer-adversarial-reviewer"},
                        ],
                    }
                ],
            },
            persona_dependencies={
                "developer-code-reviewer": object(),
                "developer-security-reviewer": object(),
                "developer-adversarial-reviewer": object(),
            },
        ),
    )

    with pytest.raises(
        WorkflowExecutionError,
        match="no configured producer for role.*adversarial",
    ):
        await attestor.attest(execution, child, result)


@pytest.mark.asyncio
async def test_attest_raises_when_envelope_is_missing() -> None:
    execution, child, _, attestor, result = _context()
    del result["_trustedReviewEnvelope"]

    with pytest.raises(WorkflowExecutionError, match="no trusted reviewer envelope"):
        await attestor.attest(execution, child, result)


@pytest.mark.asyncio
async def test_attest_raises_when_envelope_is_not_a_mapping() -> None:
    execution, child, _, attestor, result = _context()
    result["_trustedReviewEnvelope"] = "not-a-mapping"

    with pytest.raises(WorkflowExecutionError, match="no trusted reviewer envelope"):
        await attestor.attest(execution, child, result)


@pytest.mark.asyncio
async def test_attest_raises_when_result_already_carries_review_receipts() -> None:
    execution, child, _, attestor, result = _context()
    result["reviewReceipts"] = [{"already": "supplied"}]

    with pytest.raises(WorkflowExecutionError, match="must not supply its own review receipts"):
        await attestor.attest(execution, child, result)


@pytest.mark.asyncio
async def test_attest_raises_when_worker_id_is_missing() -> None:
    execution, child, _, attestor, result = _context()
    child = replace(child, workspace={**child.workspace, "worker_id": ""})

    with pytest.raises(WorkflowExecutionError, match="envelope is incomplete"):
        await attestor.attest(execution, child, result)


@pytest.mark.asyncio
async def test_attest_raises_when_reviews_is_not_a_list() -> None:
    execution, child, _, attestor, result = _context()
    result["_trustedReviewEnvelope"]["reviews"] = {"not": "a list"}

    with pytest.raises(WorkflowExecutionError, match="envelope is incomplete"):
        await attestor.attest(execution, child, result)


@pytest.mark.asyncio
async def test_attest_raises_when_a_review_verdict_is_unrecognized() -> None:
    execution, child, _, attestor, result = _context()
    result["_trustedReviewEnvelope"]["reviews"][0]["verdict"] = "unsure"

    with pytest.raises(WorkflowExecutionError, match="review verdict is invalid"):
        await attestor.attest(execution, child, result)


@pytest.mark.asyncio
async def test_review_binding_translates_document_errors() -> None:
    """A malformed `reviewAttestation` block (caught by the workflow-document
    parser) must surface as a WorkflowExecutionError, not leak the internal
    WorkflowDocumentError type across the port boundary."""
    execution, child, authenticator, _, result = _context()
    attestor = TrustedChildReviewAttestor(
        authenticator=authenticator,
        role_producers={"code": "code-producer"},
        workflow_resolver=lambda *_args: SimpleNamespace(
            graph={
                "reviewAttestation": {
                    "version": 1,
                    "scope": "workstream",
                    # "eventType" and "roles" are both required; both are
                    # missing here to force the parser itself to fail.
                },
                "nodes": [],
            },
            persona_dependencies={},
        ),
    )

    with pytest.raises(WorkflowExecutionError, match="frozen child review attestation is invalid"):
        await attestor.attest(execution, child, result)


# ---------------------------------------------------------------------------
# _finding() helper
# ---------------------------------------------------------------------------


class TestFindingHelper:
    def test_defaults_blocking_from_a_failing_verdict(self) -> None:
        finding = _finding("event-1", 0, {"evidence": "stack trace here"}, ReviewVerdict.FAIL)

        assert finding.blocking is True
        assert finding.disposition == FindingDisposition.OPEN
        assert finding.finding_id == "event-1:0"
        assert finding.evidence == "stack trace here"

    def test_defaults_non_blocking_from_a_passing_verdict(self) -> None:
        finding = _finding("event-1", 1, {"evidence": "minor nit"}, ReviewVerdict.PASS)

        assert finding.blocking is False
        assert finding.disposition == FindingDisposition.RESOLVED

    def test_explicit_blocking_overrides_the_verdict_default(self) -> None:
        finding = _finding(
            "event-1", 0, {"evidence": "minor nit", "blocking": True}, ReviewVerdict.PASS
        )

        assert finding.blocking is True
        assert finding.disposition == FindingDisposition.OPEN

    def test_falls_back_to_summary_when_evidence_is_absent(self) -> None:
        finding = _finding(
            "event-1", 0, {"summary": "used the summary instead"}, ReviewVerdict.FAIL
        )

        assert finding.evidence == "used the summary instead"

    def test_falls_back_to_a_json_dump_when_evidence_and_summary_are_absent(self) -> None:
        raw = {"role": "code", "verdict": "fail"}
        finding = _finding("event-1", 0, raw, ReviewVerdict.FAIL)

        assert finding.evidence == json.dumps(raw, sort_keys=True, default=str)[:4096]

    def test_truncates_oversized_evidence_to_4096_characters(self) -> None:
        finding = _finding("event-1", 0, {"evidence": "x" * 5000}, ReviewVerdict.FAIL)

        assert len(finding.evidence) == 4096

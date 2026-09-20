"""Tests for the narrow Forge delivery orchestration service."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from niuu.adapters.evidence_signing import RsaEvidenceAuthenticator
from niuu.domain.delivery import (
    AcceptancePolicy,
    BranchPublicationReceipt,
    BranchPublicationRequest,
    CandidateEvidence,
    CheckConclusion,
    CheckReceipt,
    CheckRecord,
    EvidenceValidationReport,
    IntegrationCandidateInspection,
    IntegrationReceipt,
    MergeReceipt,
    MergeRequest,
    PublicationSource,
    PublicationState,
    RequirementEvidence,
    ResolvedRef,
    ReviewCandidate,
    WorkspaceAllocation,
    WorkstreamSpec,
    evidence_payload,
)
from volundr.domain.services.delivery import DeliveryPolicyNotFoundError, DeliveryService

BASE = "a" * 40
HEAD = "b" * 40
TREE = "c" * 40
NOW = datetime.now(UTC)
REPOSITORY = "https://gitlab.example/org/repo"


def _authenticator() -> RsaEvidenceAuthenticator:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return RsaEvidenceAuthenticator(
        key_id="key-1",
        private_key_pem=pem,
        producer_keys={"forge-service": ("key-1",)},
    )


def _evidence() -> CandidateEvidence:
    return CandidateEvidence(
        campaign_id="campaign-1",
        workstream_key="api",
        attempt_id="attempt-1",
        worker_id="worker-1",
        repository=REPOSITORY,
        base_sha=BASE,
        candidate_sha=HEAD,
        candidate_tree=TREE,
        requirements=(
            RequirementEvidence(
                requirement_id="REQ-1",
                implementation_paths=("src/api.py",),
                verification_contract_ids=("unit",),
            ),
        ),
    )


def _allocation() -> WorkspaceAllocation:
    return WorkspaceAllocation(
        allocation_id="allocation-1",
        campaign_id="campaign-1",
        workstream_key="api",
        repository=REPOSITORY,
        workspace_path="/tmp/worktree",
        repository_path="/tmp/repository",
        base_sha=BASE,
        branch_name="campaign/api",
        worker_id="worker-1",
        allowed_paths=("src",),
        test_contract_ids=("unit",),
    )


@pytest.fixture
def parts():
    workstreams = MagicMock()
    workstreams.allocate = AsyncMock(return_value=_allocation())
    workstreams.verify = AsyncMock(return_value="verification")
    workstreams.integrate = AsyncMock(return_value="integration")
    workstreams.publication_source = AsyncMock(
        return_value=PublicationSource(
            repository=REPOSITORY,
            repository_path="/tmp/worktree",
            candidate_sha=HEAD,
            candidate_tree=TREE,
        )
    )
    workstreams.inspect_integration = AsyncMock(
        return_value=IntegrationCandidateInspection(
            receipt_id="integration-1",
            campaign_id="campaign-1",
            integration_allocation_id="allocation-1",
            repository=REPOSITORY,
            base_sha=BASE,
            candidate_sha=HEAD,
            candidate_tree=TREE,
            receipt_ids=("integration-1",),
            integrated_candidates=(
                {
                    "workstream_key": "api",
                    "attempt_id": "attempt-1",
                    "candidate_sha": "d" * 40,
                    "candidate_tree": "e" * 40,
                },
            ),
            changed_paths=("src/api.py",),
            patch="diff --git a/src/api.py b/src/api.py",
            inspected_at=NOW,
        )
    )
    forge = MagicMock()
    verifier = MagicMock()
    verifier.validate.return_value = EvidenceValidationReport(
        accepted=True,
        blocking_reasons=(),
        manifest_digest="d" * 64,
    )
    authenticator = _authenticator()
    policy = AcceptancePolicy(
        required_review_roles=("code",),
        required_test_contract_ids=("unit",),
        review_producers={"code": ("reviewer",)},
        test_producers={"unit": ("runner",)},
        required_check_names=("ci",),
        require_forge_checks=True,
        forge_producers=("forge-service",),
        integration_producers=("forge-service",),
    )
    service = DeliveryService(
        workstreams=workstreams,
        forge=forge,
        verifier=verifier,
        authenticator=authenticator,
        policies={"strict": policy},
        producer_id="forge-service",
    )
    return service, workstreams, forge, verifier


@pytest.mark.asyncio
async def test_delegates_ref_allocation_and_verification(parts) -> None:
    service, workstreams, forge, _ = parts
    resolved = ResolvedRef(
        provider="gitlab",
        repository="https://gitlab.example/org/repo",
        ref="main",
        sha=BASE,
        observed_at=NOW,
    )
    forge.resolve_ref = AsyncMock(return_value=resolved)
    assert await service.resolve_ref(resolved.repository, "main") == resolved
    spec = WorkstreamSpec(
        campaign_id="campaign-1",
        workstream_key="api",
        repository=REPOSITORY,
        repository_path="/tmp/repository",
        base_sha=BASE,
        branch_name="campaign/api",
        worker_id="worker-1",
        allowed_paths=("src",),
        test_contract_ids=("unit",),
    )
    assert await service.allocate_workstream(spec) == _allocation()
    assert (
        await service.run_verification(
            _allocation(),
            attempt_id="attempt-1",
            candidate_sha=HEAD,
            contract_id="unit",
        )
        == "verification"
    )
    workstreams.verify.assert_awaited_once()


def test_validation_uses_registered_policy_and_rejects_unknown(parts) -> None:
    service, _, _, verifier = parts
    report = service.validate_evidence(_evidence(), policy_id="strict")
    assert report.accepted is True
    verifier.validate.assert_called_once()
    with pytest.raises(DeliveryPolicyNotFoundError):
        service.validate_evidence(_evidence(), policy_id="caller-policy")


@pytest.mark.asyncio
async def test_integration_requires_evidence_before_git(parts) -> None:
    service, workstreams, _, verifier = parts
    assert (
        await service.integrate_candidate(
            _allocation(),
            expected_integration_head=BASE,
            evidence=_evidence(),
            policy_id="strict",
        )
        == "integration"
    )
    verifier.require.assert_called_once()
    workstreams.integrate.assert_awaited_once()


@pytest.mark.asyncio
async def test_branch_publication_requires_signed_exact_integration_receipt(parts) -> None:
    service, workstreams, forge, _ = parts
    unsigned = IntegrationReceipt(
        receipt_id="integration-1",
        campaign_id="campaign-1",
        integration_allocation_id="allocation-1",
        workstream_key="api",
        attempt_id="attempt-1",
        repository=REPOSITORY,
        base_sha=BASE,
        candidate_sha="d" * 40,
        candidate_tree="e" * 40,
        previous_integration_sha=BASE,
        integrated_commits=("d" * 40,),
        resulting_sha=HEAD,
        resulting_tree=TREE,
        completed_at=NOW,
    )
    provenance = service._authenticator.sign(evidence_payload(unsigned), "forge-service")
    receipt = unsigned.model_copy(update={"provenance": provenance})
    request = BranchPublicationRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        branch="campaign/api",
        expected_head_sha=HEAD,
    )
    remote = BranchPublicationReceipt(
        receipt_id="publication-1",
        campaign_id="campaign-1",
        provider="gitlab",
        repository=REPOSITORY,
        branch=request.branch,
        source_sha=HEAD,
        resulting_remote_sha=HEAD,
        published_at=NOW,
    )
    forge.publish_branch = AsyncMock(return_value=remote)
    result = await service.publish_branch(_allocation(), receipt, request, policy_id="strict")
    assert result.provenance is not None
    workstreams.publication_source.assert_awaited_once_with(_allocation(), receipt)
    forge.publish_branch.assert_awaited_once()

    forged = receipt.model_copy(update={"resulting_sha": "f" * 40})
    with pytest.raises(ValueError, match="signature is invalid"):
        await service.publish_branch(_allocation(), forged, request, policy_id="strict")

    substituted_branch = request.model_copy(update={"branch": "campaign/other"})
    with pytest.raises(ValueError, match="identities do not match"):
        await service.publish_branch(_allocation(), receipt, substituted_branch, policy_id="strict")
    workstreams.publication_source.assert_awaited_once()
    forge.publish_branch.assert_awaited_once()

    inspected = await service.inspect_integration(_allocation(), receipt, policy_id="strict")
    assert inspected.candidate_sha == HEAD
    workstreams.inspect_integration.assert_awaited_once_with(_allocation(), receipt)

    replayed = receipt.model_copy(
        update={"integration_allocation_id": "allocation-other", "provenance": None}
    )
    replayed = replayed.model_copy(
        update={
            "provenance": service._authenticator.sign(evidence_payload(replayed), "forge-service")
        }
    )
    with pytest.raises(ValueError, match="identities do not match"):
        await service.inspect_integration(_allocation(), replayed, policy_id="strict")

    first_unsigned = unsigned.model_copy(
        update={
            "receipt_id": "integration-first",
            "resulting_sha": "1" * 40,
            "resulting_tree": "2" * 40,
            "provenance": None,
        }
    )
    first = first_unsigned.model_copy(
        update={
            "provenance": service._authenticator.sign(
                evidence_payload(first_unsigned), "forge-service"
            )
        }
    )
    second_unsigned = unsigned.model_copy(
        update={
            "receipt_id": "integration-second",
            "workstream_key": "ui",
            "attempt_id": "attempt-2",
            "candidate_sha": "3" * 40,
            "candidate_tree": "4" * 40,
            "previous_integration_sha": first.resulting_sha,
            "provenance": None,
        }
    )
    second = second_unsigned.model_copy(
        update={
            "provenance": service._authenticator.sign(
                evidence_payload(second_unsigned), "forge-service"
            )
        }
    )
    chain = await service.inspect_integration_chain(
        _allocation(), (first, second), policy_id="strict"
    )
    assert chain.receipt_ids == ("integration-first", "integration-second")
    assert [item.workstream_key for item in chain.integrated_candidates] == ["api", "ui"]
    with pytest.raises(ValueError, match="discontinuous"):
        await service.inspect_integration_chain(_allocation(), (second, first), policy_id="strict")


@pytest.mark.asyncio
async def test_inspection_signs_provider_facts(parts) -> None:
    service, _, forge, _ = parts
    candidate = ReviewCandidate(
        provider="gitlab",
        repository="https://gitlab.example/org/repo",
        review_number=7,
        source_branch="campaign/api",
        target_branch="main",
        candidate_sha=HEAD,
        tested_base_sha=BASE,
        current_target_sha=BASE,
        mergeable=True,
        checks=(CheckRecord(name="ci", conclusion=CheckConclusion.PASSING),),
        serialized_publication=True,
    )
    checks = CheckReceipt(
        receipt_id="checks-1",
        provider="gitlab",
        repository=candidate.repository,
        review_number=7,
        candidate_sha=HEAD,
        tested_base_sha=BASE,
        checks=candidate.checks,
        observed_at=NOW,
    )
    forge.inspect_delivery_candidate = AsyncMock(return_value=(candidate, checks))
    returned, signed = await service.inspect_candidate(candidate.repository, 7, policy_id="strict")
    assert returned == candidate
    assert signed.provenance is not None
    assert signed.provenance.producer_id == "forge-service"


@pytest.mark.asyncio
async def test_conditional_merge_rechecks_current_forge_facts(parts) -> None:
    service, _, forge, verifier = parts
    repository = "https://gitlab.example/org/repo"
    candidate = ReviewCandidate(
        provider="gitlab",
        repository=repository,
        review_number=7,
        source_branch="campaign/api",
        target_branch="main",
        candidate_sha=HEAD,
        tested_base_sha=BASE,
        current_target_sha=BASE,
        mergeable=True,
        checks=(CheckRecord(name="ci", conclusion=CheckConclusion.PASSING),),
        serialized_publication=True,
    )
    checks = CheckReceipt(
        receipt_id="checks-1",
        provider="gitlab",
        repository=repository,
        review_number=7,
        candidate_sha=HEAD,
        tested_base_sha=BASE,
        checks=candidate.checks,
        observed_at=NOW,
    )
    forge.inspect_delivery_candidate = AsyncMock(return_value=(candidate, checks))
    queued = MergeReceipt(
        receipt_id="merge-1",
        campaign_id="campaign-1",
        provider="gitlab",
        repository=repository,
        review_number=7,
        source_sha=HEAD,
        base_sha=BASE,
        target_branch="main",
        method="squash",
        state=PublicationState.QUEUED,
        provider_operation_id="99",
    )
    forge.conditional_merge = AsyncMock(return_value=queued)
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=repository,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
    )
    receipt = await service.conditional_merge(
        request,
        evidence=_evidence(),
        policy_id="strict",
    )
    assert receipt.provenance is not None
    verifier.require.assert_called_once()
    forge.conditional_merge.assert_awaited_once_with(request)

    stale = candidate.model_copy(update={"current_target_sha": "d" * 40})
    forge.inspect_delivery_candidate.return_value = (stale, checks)
    with pytest.raises(ValueError, match="target moved"):
        await service.conditional_merge(request, evidence=_evidence(), policy_id="strict")


@pytest.mark.asyncio
async def test_reconcile_signs_remote_receipt(parts) -> None:
    service, _, forge, _ = parts
    request = MergeRequest(
        campaign_id="campaign-1",
        repository="https://gitlab.example/org/repo",
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
    )
    merged = MergeReceipt(
        receipt_id="merge-1",
        campaign_id="campaign-1",
        provider="gitlab",
        repository=request.repository,
        review_number=7,
        source_sha=HEAD,
        base_sha=BASE,
        target_branch="main",
        result_sha="d" * 40,
        canonical_target_sha="d" * 40,
        method="squash",
        state=PublicationState.MERGED,
        provider_operation_id="177482",
        verified_at=NOW,
    )
    forge.reconcile_merge = AsyncMock(return_value=merged)
    receipt = await service.reconcile_merge(request)
    assert receipt.provenance is not None
    assert receipt.state is PublicationState.MERGED
    assert receipt.provider_operation_id == "177482"
    forge.reconcile_merge.assert_awaited_once_with(request)


def test_describe_policy_returns_deployment_requirements(parts) -> None:
    service, _, _, _ = parts
    policy = service.describe_policy("strict")
    assert policy.required_test_contract_ids == ("unit",)
    assert policy.required_check_names == ("ci",)
    with pytest.raises(DeliveryPolicyNotFoundError):
        service.describe_policy("missing")

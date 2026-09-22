"""Authorization boundary tests for delivery REST operations."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.domain.delivery import (
    AcceptancePolicy,
    BranchPublicationReceipt,
    CandidateEvidence,
    CheckReceipt,
    EvidenceValidationReport,
    IntegrationCandidateInspection,
    IntegrationReceipt,
    MergeReceipt,
    PublicationState,
    RequirementEvidence,
    ResolvedRef,
    ReviewCandidate,
    ReviewPublication,
    VerificationReceipt,
    WorkspaceAllocation,
)
from niuu.domain.models import Principal
from volundr.adapters.inbound.rest_delivery import create_delivery_router
from volundr.domain.services.delivery import DeliveryPolicyNotFoundError


def _principal() -> Principal:
    return Principal(
        user_id="coordinator-1",
        email="coordinator@example.invalid",
        tenant_id="tenant-1",
        roles=["delivery:coordinator"],
    )


def _client(*, denied: bool = False):
    now = datetime.now(UTC)
    base = "a" * 40
    head = "b" * 40
    tree = "c" * 40
    repository = "https://gitlab.example/org/repo"
    service = MagicMock()
    service.resolve_ref = AsyncMock(
        return_value=ResolvedRef(
            provider="gitlab",
            repository=repository,
            ref="main",
            sha=base,
            observed_at=now,
        )
    )
    allocation = WorkspaceAllocation(
        allocation_id="allocation-1",
        campaign_id="campaign-1",
        workstream_key="api",
        repository=repository,
        workspace_path="/tmp/worktree",
        repository_path="/tmp/repository",
        base_sha=base,
        branch_name="campaign/api",
        worker_id="worker-1",
        allowed_paths=("src",),
        test_contract_ids=("unit",),
    )
    verification = VerificationReceipt(
        receipt_id="verification-1",
        campaign_id="campaign-1",
        workstream_key="api",
        attempt_id="attempt-1",
        repository=repository,
        candidate_sha=head,
        candidate_tree=tree,
        base_sha=base,
        contract_id="unit",
        command_digest="d" * 64,
        exit_code=0,
        stdout_digest="d" * 64,
        stderr_digest="d" * 64,
        started_at=now,
        completed_at=now,
    )
    integration = IntegrationReceipt(
        receipt_id="integration-1",
        campaign_id="campaign-1",
        integration_allocation_id="allocation-1",
        workstream_key="api",
        attempt_id="attempt-1",
        repository=repository,
        base_sha=base,
        candidate_sha=head,
        candidate_tree=tree,
        previous_integration_sha=base,
        integrated_commits=(head,),
        resulting_sha="e" * 40,
        resulting_tree="f" * 40,
        completed_at=now,
    )
    branch_publication = BranchPublicationReceipt(
        receipt_id="branch-publication-1",
        campaign_id="campaign-1",
        provider="gitlab",
        repository=repository,
        branch="campaign/integration",
        source_sha="e" * 40,
        resulting_remote_sha="e" * 40,
        published_at=now,
    )
    publication = ReviewPublication(
        receipt_id="review-publication-1",
        campaign_id="campaign-1",
        provider="gitlab",
        repository=repository,
        review_number=7,
        url="https://gitlab.example/org/repo/-/merge_requests/7",
        candidate_sha=head,
        source_branch="campaign/api",
        target_branch="main",
        created=True,
    )
    candidate = ReviewCandidate(
        provider="gitlab",
        repository=repository,
        review_number=7,
        source_branch="campaign/api",
        target_branch="main",
        candidate_sha=head,
        tested_base_sha=base,
        current_target_sha=base,
        mergeable=True,
        checks=(),
        serialized_publication=True,
    )
    checks = CheckReceipt(
        receipt_id="checks-1",
        provider="gitlab",
        repository=repository,
        review_number=7,
        candidate_sha=head,
        tested_base_sha=base,
        checks=(),
        observed_at=now,
    )
    merge = MergeReceipt(
        receipt_id="merge-1",
        campaign_id="campaign-1",
        provider="gitlab",
        repository=repository,
        review_number=7,
        source_sha=head,
        base_sha=base,
        target_branch="main",
        method="squash",
        state=PublicationState.QUEUED,
        provider_operation_id="99",
    )
    service.open_review = AsyncMock(return_value=publication)
    service.publish_branch = AsyncMock(return_value=branch_publication)
    service.allocate_workstream = AsyncMock(return_value=allocation)
    service.run_verification = AsyncMock(return_value=verification)
    service.validate_evidence.return_value = EvidenceValidationReport(
        accepted=True, blocking_reasons=(), manifest_digest="d" * 64
    )
    service.integrate_candidate = AsyncMock(return_value=integration)
    service.inspect_integration = AsyncMock(
        return_value=IntegrationCandidateInspection(
            receipt_id=integration.receipt_id,
            campaign_id=integration.campaign_id,
            integration_allocation_id=integration.integration_allocation_id,
            repository=integration.repository,
            base_sha=integration.base_sha,
            candidate_sha=integration.resulting_sha,
            candidate_tree=integration.resulting_tree,
            receipt_ids=(integration.receipt_id,),
            integrated_candidates=(
                {
                    "workstream_key": integration.workstream_key,
                    "attempt_id": integration.attempt_id,
                    "candidate_sha": integration.candidate_sha,
                    "candidate_tree": integration.candidate_tree,
                },
            ),
            changed_paths=("src/api.py",),
            patch="diff --git a/src/api.py b/src/api.py",
            inspected_at=now,
        )
    )
    service.inspect_integration_chain = AsyncMock(
        return_value=service.inspect_integration.return_value
    )
    service.inspect_candidate = AsyncMock(return_value=(candidate, checks))
    service.conditional_merge = AsyncMock(return_value=merge)
    service.reconcile_merge = AsyncMock(return_value=merge)

    async def principal_for_request(_request):
        return _principal()

    authorizer = MagicMock()
    if denied:
        authorizer.authorize = AsyncMock(side_effect=PermissionError("campaign access denied"))
    else:
        authorizer.authorize = AsyncMock(return_value=None)
    app = FastAPI()
    app.include_router(
        create_delivery_router(
            lambda _principal: service,
            principal_for_request,
            authorizer,
        )
    )
    return TestClient(app), service, authorizer


def test_unauthorized_request_is_denied_before_provider_call() -> None:
    client, service, authorizer = _client(denied=True)
    response = client.post(
        "/api/v1/forge/delivery/refs/resolve",
        json={"repository": "https://gitlab.example/org/repo", "ref": "main"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "campaign access denied"
    service.resolve_ref.assert_not_called()
    authorizer.authorize.assert_awaited_once_with(
        _principal(),
        None,
        "resolve_ref",
        repository="https://gitlab.example/org/repo",
        base_sha=None,
        candidate_sha=None,
        candidate_tree=None,
        target_branch=None,
        policy_id=None,
        credential=None,
    )


def test_policy_discovery_preserves_configured_requirements_and_authorizes_campaign() -> None:
    client, service, authorizer = _client()
    policy = AcceptancePolicy(
        required_review_roles=("code",),
        required_test_contract_ids=("syntax", "unit"),
        review_producers={"code": ("reviewer",)},
        test_producers={"syntax": ("runner",), "unit": ("runner",)},
    )
    service.describe_policy.return_value = policy
    response = client.post(
        "/api/v1/forge/delivery/evidence/policy",
        json={"campaign_id": "campaign-1", "repository": "repo", "policy_id": "workstream"},
    )
    assert response.status_code == 200
    assert response.json() == policy.model_dump(mode="json")
    service.describe_policy.assert_called_once_with("workstream")
    assert authorizer.authorize.call_args.args[1:3] == ("campaign-1", "validate_evidence")


def test_policy_discovery_denies_access_before_reading_configuration() -> None:
    client, service, _ = _client(denied=True)
    response = client.post(
        "/api/v1/forge/delivery/evidence/policy",
        json={"campaign_id": "other", "repository": "repo", "policy_id": "workstream"},
    )
    assert response.status_code == 403
    service.describe_policy.assert_not_called()


def test_policy_discovery_reports_missing_policy() -> None:
    client, service, _ = _client()
    service.describe_policy.side_effect = DeliveryPolicyNotFoundError("Unknown delivery policy")
    response = client.post(
        "/api/v1/forge/delivery/evidence/policy",
        json={"campaign_id": "campaign-1", "repository": "repo", "policy_id": "missing"},
    )
    assert response.status_code == 400


def test_authorized_request_uses_principal_scoped_service() -> None:
    client, service, authorizer = _client()
    response = client.post(
        "/api/v1/forge/delivery/refs/resolve",
        json={"repository": "https://gitlab.example/org/repo", "ref": "main"},
    )
    assert response.status_code == 200
    assert response.json()["sha"] == "a" * 40
    service.resolve_ref.assert_awaited_once()
    authorizer.authorize.assert_awaited_once()


def test_reconcile_accepts_missing_operation_id_for_read_only_provider_recovery() -> None:
    client, service, authorizer = _client()
    response = client.post(
        "/api/v1/forge/delivery/forge/reconcile",
        json={
            "campaign_id": "campaign-1",
            "repository": "https://gitlab.example/org/repo",
            "review_number": 7,
            "expected_head_sha": "b" * 40,
            "expected_base_sha": "a" * 40,
            "expected_target_branch": "main",
            "method": "squash",
        },
        headers={"Authorization": "Bearer verified-token"},
    )

    assert response.status_code == 200
    assert response.json()["provider_operation_id"] == "99"
    request = service.reconcile_merge.await_args.args[0]
    assert request.provider_operation_id is None
    assert authorizer.authorize.await_args.args[2] == "reconcile_merge"


def test_all_campaign_operations_are_typed_and_authorized() -> None:
    client, service, authorizer = _client()
    base = "a" * 40
    head = "b" * 40
    tree = "c" * 40
    repository = "https://gitlab.example/org/repo"
    allocation = {
        "allocation_id": "allocation-1",
        "campaign_id": "campaign-1",
        "workstream_key": "api",
        "repository": repository,
        "workspace_path": "/tmp/worktree",
        "repository_path": "/tmp/repository",
        "base_sha": base,
        "branch_name": "campaign/api",
        "worker_id": "worker-1",
        "allowed_paths": ["src"],
        "test_contract_ids": ["unit"],
    }
    evidence = CandidateEvidence(
        campaign_id="campaign-1",
        workstream_key="api",
        attempt_id="attempt-1",
        worker_id="worker-1",
        repository=repository,
        base_sha=base,
        candidate_sha=head,
        candidate_tree=tree,
        requirements=(
            RequirementEvidence(
                requirement_id="REQ-1",
                implementation_paths=("src/api.py",),
                verification_contract_ids=("unit",),
            ),
        ),
    ).model_dump(mode="json")
    review = {
        "campaign_id": "campaign-1",
        "repository": repository,
        "title": "Delivery",
        "description": "Verified candidate",
        "source_branch": "campaign/api",
        "target_branch": "main",
        "expected_head_sha": head,
        "labels": ["niuu"],
    }
    spec = {
        "campaign_id": "campaign-1",
        "workstream_key": "api",
        "repository": repository,
        "repository_path": "/tmp/repository",
        "base_sha": base,
        "branch_name": "campaign/api",
        "worker_id": "worker-1",
        "allowed_paths": ["src"],
        "test_contract_ids": ["unit"],
    }
    merge = {
        "campaign_id": "campaign-1",
        "repository": repository,
        "review_number": 7,
        "expected_head_sha": head,
        "expected_base_sha": base,
        "expected_target_branch": "main",
        "method": "squash",
    }
    calls = [
        ("/forge/reviews", review, 200),
        (
            "/forge/branches",
            {
                "allocation": allocation,
                "integration_receipt": {
                    "receipt_id": "integration-1",
                    "campaign_id": "campaign-1",
                    "integration_allocation_id": "allocation-1",
                    "workstream_key": "api",
                    "attempt_id": "attempt-1",
                    "repository": repository,
                    "base_sha": base,
                    "candidate_sha": head,
                    "candidate_tree": tree,
                    "previous_integration_sha": base,
                    "integrated_commits": [head],
                    "resulting_sha": "e" * 40,
                    "resulting_tree": "f" * 40,
                    "completed_at": datetime.now(UTC).isoformat(),
                },
                "publication": {
                    "campaign_id": "campaign-1",
                    "repository": repository,
                    "branch": "campaign/integration",
                    "expected_head_sha": "e" * 40,
                },
                "policy_id": "strict",
            },
            200,
        ),
        ("/workspaces/allocate", spec, 201),
        (
            "/workspaces/verify",
            {
                "allocation": allocation,
                "attempt_id": "attempt-1",
                "candidate_sha": head,
                "contract_id": "unit",
            },
            200,
        ),
        (
            "/workspaces/integration/inspect",
            {
                "allocation": allocation,
                "integration_receipt": {
                    "receipt_id": "integration-1",
                    "campaign_id": "campaign-1",
                    "integration_allocation_id": "allocation-1",
                    "workstream_key": "api",
                    "attempt_id": "attempt-1",
                    "repository": repository,
                    "base_sha": base,
                    "candidate_sha": head,
                    "candidate_tree": tree,
                    "previous_integration_sha": base,
                    "integrated_commits": [head],
                    "resulting_sha": "e" * 40,
                    "resulting_tree": "f" * 40,
                    "completed_at": datetime.now(UTC).isoformat(),
                },
                "policy_id": "strict",
            },
            200,
        ),
        (
            "/workspaces/integration/inspect-chain",
            {
                "allocation": allocation,
                "integration_receipts": [
                    {
                        "receipt_id": "integration-1",
                        "campaign_id": "campaign-1",
                        "integration_allocation_id": "allocation-1",
                        "workstream_key": "api",
                        "attempt_id": "attempt-1",
                        "repository": repository,
                        "base_sha": base,
                        "candidate_sha": head,
                        "candidate_tree": tree,
                        "previous_integration_sha": base,
                        "integrated_commits": [head],
                        "resulting_sha": "e" * 40,
                        "resulting_tree": "f" * 40,
                        "completed_at": datetime.now(UTC).isoformat(),
                    }
                ],
                "policy_id": "strict",
            },
            200,
        ),
        ("/evidence/validate", {"evidence": evidence, "policy_id": "strict"}, 200),
        (
            "/workspaces/integrate",
            {
                "evidence": evidence,
                "policy_id": "strict",
                "integration": allocation,
                "expected_integration_head": base,
            },
            200,
        ),
        (
            "/forge/inspect",
            {
                "campaign_id": "campaign-1",
                "repository": repository,
                "review_number": 7,
                "policy_id": "strict",
            },
            200,
        ),
        (
            "/forge/merge",
            {"evidence": evidence, "policy_id": "strict", "merge": merge},
            200,
        ),
        ("/forge/reconcile", merge, 200),
    ]
    for path, body, expected_status in calls:
        response = client.post(
            f"/api/v1/forge/delivery{path}",
            json=body,
            headers={"Authorization": "Bearer verified-token"},
        )
        assert response.status_code == expected_status, response.text
    assert authorizer.authorize.await_count == len(calls)
    assert all(
        call.kwargs["credential"] == "Bearer verified-token"
        for call in authorizer.authorize.await_args_list
    )
    merge_authorization = next(
        call for call in authorizer.authorize.await_args_list if call.args[2] == "conditional_merge"
    )
    assert merge_authorization.kwargs["candidate_sha"] == head
    assert merge_authorization.kwargs["candidate_tree"] == tree
    assert merge_authorization.kwargs["target_branch"] == "main"
    assert merge_authorization.kwargs["policy_id"] == "strict"
    review_authorization = next(
        call for call in authorizer.authorize.await_args_list if call.args[2] == "open_review"
    )
    assert review_authorization.kwargs["candidate_sha"] == head
    assert review_authorization.kwargs["target_branch"] == "main"
    publish_authorization = next(
        call for call in authorizer.authorize.await_args_list if call.args[2] == "publish_branch"
    )
    assert publish_authorization.kwargs["target_branch"] == "campaign/integration"
    service.open_review.assert_awaited_once()
    service.integrate_candidate.assert_awaited_once()
    service.inspect_integration.assert_awaited_once()
    service.inspect_integration_chain.assert_awaited_once()
    service.conditional_merge.assert_awaited_once()

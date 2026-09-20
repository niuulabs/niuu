"""Only server-verified publication can terminate a developer execution."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from niuu.domain.delivery import (
    CandidateEvidence,
    CheckReceipt,
    EvidenceValidationReport,
    MergeReceipt,
    MergeRequest,
    PublicationState,
    ReviewReceipt,
    ReviewVerdict,
    evidence_digest,
)
from niuu.domain.models import Principal
from tests.test_ting.test_developer_execution import _execution, _proposal
from ting.domain.developer_execution import (
    ChildExecutionState,
    DeveloperExecutionError,
    ExecutionState,
    JoinStatus,
    make_children,
)
from ting.domain.services.developer_completion import DeveloperCompletionService


def context():
    execution = _execution(state=ExecutionState.RUNNING, current_generation=1)
    children = make_children(execution, 1, (_proposal(execution, "one"),))
    child = replace(children[0], state=ChildExecutionState.COMPLETED)
    principal = Principal(
        user_id=execution.owner_id, tenant_id=execution.tenant_id, email="", roles=[]
    )
    merge = MergeRequest(
        campaign_id=str(execution.id),
        repository=execution.repository,
        review_number=1,
        expected_head_sha="b" * 40,
        expected_base_sha=execution.base_sha,
        expected_target_branch=execution.base_ref,
        method="merge",
    )
    evidence = CandidateEvidence(
        campaign_id=str(execution.id),
        repository=execution.repository,
        workstream_key="integration",
        attempt_id="integration-1",
        worker_id="integrator",
        base_sha=execution.base_sha,
        candidate_sha=merge.expected_head_sha,
        candidate_tree="c" * 40,
        requirements=[
            {
                "requirement_id": "REQ-one",
                "implementation_paths": ["src/parser.py"],
                "verification_contract_ids": ["tests"],
            }
        ],
        checks=CheckReceipt(
            receipt_id="ci",
            provider="forge",
            repository=execution.repository,
            review_number=1,
            candidate_sha=merge.expected_head_sha,
            tested_base_sha=execution.base_sha,
            checks=(),
            observed_at=datetime.now(UTC),
        ),
        reviews=(
            ReviewReceipt(
                receipt_id="integration-review",
                campaign_id=str(execution.id),
                workstream_key="integration",
                attempt_id="integration-1",
                repository=execution.repository,
                candidate_sha=merge.expected_head_sha,
                candidate_tree="c" * 40,
                base_sha=execution.base_sha,
                reviewer_id="independent-reviewer",
                worker_id="integrator",
                role="integration",
                verdict=ReviewVerdict.PASS,
                summary="Inspected combined changes",
            ),
        ),
    )
    execution = replace(
        execution,
        integration_receipts=({"receipt_id": "already-inspected-chain-receipt"},),
        integration_candidate={
            "campaign_id": str(execution.id),
            "repository": execution.repository,
            "base_sha": execution.base_sha,
            "candidate_sha": evidence.candidate_sha,
            "candidate_tree": evidence.candidate_tree,
            "integration_allocation_id": evidence.attempt_id,
        },
        integration_allocation={
            "allocation_id": evidence.attempt_id,
            "worker_id": evidence.worker_id,
            "workstream_key": evidence.workstream_key,
        },
        integration_review_receipt=evidence.reviews[0].model_dump(mode="json"),
        integration_review_event_id="review-event",
    )
    receipt = MergeReceipt(
        receipt_id="merge",
        campaign_id=str(execution.id),
        provider="forge",
        repository=execution.repository,
        review_number=1,
        source_sha=merge.expected_head_sha,
        base_sha=execution.base_sha,
        target_branch=execution.base_ref,
        result_sha="d" * 40,
        canonical_target_sha="d" * 40,
        method="merge",
        state=PublicationState.MERGED,
        verified_at=datetime.now(UTC),
    )
    repository = AsyncMock()
    repository.join_status.return_value = JoinStatus(1, True, True, (), (), ())
    repository.list_children.return_value = [child]
    repository.complete_execution.return_value = replace(execution, state=ExecutionState.COMPLETED)
    factory = AsyncMock()
    forge = factory.for_connection.return_value
    forge.validate_delivery_evidence.return_value = EvidenceValidationReport(
        accepted=True,
        blocking_reasons=(),
        manifest_digest=evidence_digest(evidence),
    )
    forge.reconcile_delivery_merge.return_value = receipt
    service = DeveloperCompletionService(
        repository=repository,
        volundr_factory=factory,
        policy_id="integration-policy",
    )
    return execution, principal, merge, evidence, repository, factory, forge, service


@pytest.mark.asyncio
async def test_completes_after_authenticated_validation_and_remote_merge_confirmation():
    execution, principal, merge, evidence, repository, factory, forge, service = context()
    result = await service.complete(
        execution,
        merge=merge,
        evidence=evidence,
        principal=principal,
        auth_token="scoped-token",
    )
    assert result.state == ExecutionState.COMPLETED
    factory.for_connection.assert_awaited_once_with(execution.owner_id, execution.connection_id)
    forge.validate_delivery_evidence.assert_awaited_once_with(
        evidence,
        policy_id="integration-policy",
        auth_token="scoped-token",
        principal=principal,
    )
    forge.reconcile_delivery_merge.assert_awaited_once_with(
        merge,
        auth_token="scoped-token",
        principal=principal,
    )
    saved = repository.complete_execution.call_args.kwargs
    assert saved["expected_revision"] == execution.revision
    assert saved["merge_receipt"]["evidence_manifest_digest"] == evidence_digest(evidence)


@pytest.mark.parametrize(
    "mutation",
    [
        "owner",
        "repository",
        "base",
        "target",
        "candidate",
        "canceled",
        "join",
        "requirements",
        "checks",
        "connection",
        "veto",
        "digest",
        "remote_pending",
        "remote_head",
        "uninspected",
        "missing_chain",
        "unattested",
        "substituted_review",
        "stale_tree",
        "other_ci_review",
    ],
)
@pytest.mark.asyncio
async def test_never_completes_unverified_or_mismatched_publication(mutation):
    execution, principal, merge, evidence, repository, factory, forge, service = context()
    if mutation == "owner":
        principal = Principal(user_id="other", tenant_id=execution.tenant_id, email="", roles=[])
    elif mutation in {"repository", "base", "target"}:
        field, value = {
            "repository": ("repository", "https://other/repo"),
            "base": ("expected_base_sha", "f" * 40),
            "target": ("expected_target_branch", "other"),
        }[mutation]
        merge = merge.model_copy(update={field: value})
    elif mutation == "candidate":
        evidence = evidence.model_copy(update={"candidate_sha": "f" * 40})
    elif mutation == "canceled":
        execution = replace(execution, cancel_requested=True)
    elif mutation == "join":
        repository.join_status.return_value = JoinStatus(1, True, False, ("one",), (), ())
    elif mutation == "requirements":
        evidence = evidence.model_copy(update={"requirements": ()})
    elif mutation == "checks":
        evidence = evidence.model_copy(update={"checks": None})
    elif mutation == "uninspected":
        execution = replace(execution, integration_candidate=None)
    elif mutation == "missing_chain":
        execution = replace(execution, integration_receipts=())
    elif mutation == "unattested":
        execution = replace(execution, integration_review_receipt=None)
    elif mutation == "substituted_review":
        evidence = evidence.model_copy(
            update={
                "reviews": (
                    evidence.reviews[0].model_copy(update={"receipt_id": "different-review"}),
                )
            }
        )
    elif mutation == "stale_tree":
        execution = replace(
            execution,
            integration_candidate={
                **execution.integration_candidate,
                "candidate_tree": "e" * 40,
            },
        )
    elif mutation == "other_ci_review":
        evidence = evidence.model_copy(
            update={"checks": evidence.checks.model_copy(update={"review_number": 2})}
        )
    elif mutation == "connection":
        factory.for_connection.return_value = None
    elif mutation == "veto":
        forge.validate_delivery_evidence.return_value = EvidenceValidationReport(
            accepted=False,
            blocking_reasons=("untrusted review",),
            manifest_digest=evidence_digest(evidence),
        )
    elif mutation == "digest":
        forge.validate_delivery_evidence.return_value = EvidenceValidationReport(
            accepted=True,
            blocking_reasons=(),
            manifest_digest="f" * 64,
        )
    elif mutation == "remote_pending":
        forge.reconcile_delivery_merge.return_value = (
            forge.reconcile_delivery_merge.return_value.model_copy(
                update={"state": PublicationState.QUEUED},
            )
        )
    elif mutation == "remote_head":
        forge.reconcile_delivery_merge.return_value = (
            forge.reconcile_delivery_merge.return_value.model_copy(
                update={"source_sha": "f" * 40},
            )
        )
    with pytest.raises(DeveloperExecutionError):
        await service.complete(
            execution,
            merge=merge,
            evidence=evidence,
            principal=principal,
            auth_token="token",
        )
    repository.complete_execution.assert_not_called()
    factory.primary_for_owner.assert_not_called()


@pytest.mark.asyncio
async def test_completion_retry_is_idempotent_but_cannot_replace_receipt():
    execution, principal, merge, evidence, repository, factory, forge, service = context()
    completed = replace(
        execution,
        state=ExecutionState.COMPLETED,
        merge_receipt=forge.reconcile_delivery_merge.return_value.model_dump(mode="json"),
    )
    assert (
        await service.complete(
            completed,
            merge=merge,
            evidence=evidence,
            principal=principal,
            auth_token="token",
        )
        == completed
    )
    forge.reconcile_delivery_merge.assert_not_called()
    repository.complete_execution.assert_not_called()
    with pytest.raises(DeveloperExecutionError, match="another publication"):
        await service.complete(
            completed,
            merge=merge.model_copy(update={"review_number": 2}),
            evidence=evidence,
            principal=principal,
            auth_token="token",
        )

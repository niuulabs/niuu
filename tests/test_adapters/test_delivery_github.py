"""Strict delivery protocol tests for the GitHub provider."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import respx
from httpx import Response

from niuu.domain.delivery import (
    BranchPublicationRequest,
    CheckConclusion,
    MergeRequest,
    PublicationSource,
    PublicationState,
    ReviewRequest,
)
from volundr.adapters.outbound.github import GitHubProvider

REPOSITORY = "https://github.com/org/repo"
API = "https://api.github.com/repos/org/repo"
HEAD = "b" * 40
BASE = "a" * 40
RESULT = "c" * 40


@pytest.fixture
def provider() -> GitHubProvider:
    publisher = MagicMock()
    publisher.publish = AsyncMock(return_value=None)
    return GitHubProvider(
        name="github",
        base_url="https://api.github.com",
        token="token",
        branch_publisher=publisher,
    )


def _pr(
    *,
    merged: bool = False,
    target_sha: str = BASE,
    head_sha: str = HEAD,
    state: str | None = None,
) -> dict:
    return {
        "number": 7,
        "state": state or ("closed" if merged else "open"),
        "merged": merged,
        "mergeable": not merged,
        "merge_commit_sha": RESULT if merged else None,
        "head": {"sha": head_sha, "ref": "campaign/work"},
        "base": {"sha": target_sha, "ref": "main"},
    }


def _mock_inspection(*, target_sha: str = BASE) -> None:
    respx.get(f"{API}/pulls/7").mock(return_value=Response(200, json=_pr(target_sha=target_sha)))
    respx.get(f"{API}/git/ref/heads/main").mock(
        return_value=Response(200, json={"object": {"sha": target_sha}})
    )
    respx.get(f"{API}/commits/{HEAD}/check-runs").mock(
        return_value=Response(
            200,
            json={
                "check_runs": [
                    {
                        "name": "unit",
                        "status": "completed",
                        "conclusion": "success",
                        "details_url": "https://ci/7",
                    }
                ]
            },
        )
    )
    respx.get(f"{API}/commits/{HEAD}/status").mock(
        return_value=Response(200, json={"statuses": []})
    )


@pytest.mark.asyncio
@respx.mock
async def test_resolve_ref_returns_immutable_sha(provider: GitHubProvider) -> None:
    respx.get(f"{API}/commits/main").mock(return_value=Response(200, json={"sha": BASE}))
    resolved = await provider.resolve_ref(REPOSITORY, "main")
    assert resolved.sha == BASE
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_review_publication_recovers_existing_campaign_pr(provider: GitHubProvider) -> None:
    respx.get(f"{API}/commits/campaign/work").mock(return_value=Response(200, json={"sha": HEAD}))
    existing = {
        "number": 7,
        "body": "<!-- niuu-campaign:campaign-1 -->",
        "head": {"ref": "campaign/work", "sha": HEAD},
        "base": {"ref": "main"},
    }
    respx.get(f"{API}/pulls").mock(return_value=Response(200, json=[existing]))
    respx.patch(f"{API}/pulls/7").mock(
        return_value=Response(
            200,
            json={
                **existing,
                "html_url": "https://github.com/org/repo/pull/7",
            },
        )
    )
    request = ReviewRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        title="Delivery",
        description="Verified candidate",
        source_branch="campaign/work",
        target_branch="main",
        expected_head_sha=HEAD,
    )
    publication = await provider.ensure_review(request)
    assert publication.created is False
    assert publication.review_number == 7
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_inspect_binds_checks_to_exact_head_and_base(provider: GitHubProvider) -> None:
    _mock_inspection()
    candidate, receipt = await provider.inspect_delivery_candidate(REPOSITORY, 7, ("unit",))
    assert candidate.candidate_sha == HEAD
    assert candidate.current_target_sha == BASE
    assert candidate.serialized_publication is True
    assert receipt.checks[0].conclusion is CheckConclusion.PASSING
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_publish_branch_uses_remote_cas_and_verifies_result(
    provider: GitHubProvider,
) -> None:
    branch_url = f"{API}/git/ref/heads/campaign%2Fintegration"
    respx.get(branch_url).mock(
        side_effect=[Response(404), Response(200, json={"object": {"sha": HEAD}})]
    )
    source = PublicationSource(
        repository=REPOSITORY,
        repository_path="/trusted/worktree",
        candidate_sha=HEAD,
        candidate_tree="c" * 40,
    )
    request = BranchPublicationRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        branch="campaign/integration",
        expected_head_sha=HEAD,
    )
    receipt = await provider.publish_branch(source, request)
    assert receipt.resulting_remote_sha == HEAD
    provider._branch_publisher.publish.assert_awaited_once_with(
        source_repository="/trusted/worktree",
        source_sha=HEAD,
        remote_url="https://github.com/org/repo.git",
        branch="campaign/integration",
        expected_remote_sha=None,
        username="x-access-token",
        token="token",
    )
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_conditional_merge_uses_queue_and_expected_head(provider: GitHubProvider) -> None:
    _mock_inspection()
    respx.get(f"{API}/rules/branches/main").mock(
        return_value=Response(
            200,
            json=[
                {"type": "merge_queue", "parameters": {"grouping_strategy": "ALLGREEN"}},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [{"context": "unit", "integration_id": 1}],
                    },
                },
            ],
        )
    )
    route = respx.put(f"{API}/pulls/7/merge-async").mock(
        return_value=Response(
            202,
            json={"status": "pending", "details": {"uuid": "operation-7"}},
        )
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
    )
    receipt = await provider.conditional_merge(request)
    assert receipt.state is PublicationState.QUEUED
    assert receipt.provider_operation_id == "operation-7"
    assert b'"merge_action":"merge_queue"' in route.calls[0].request.content
    assert route.calls[0].request.headers["X-GitHub-Api-Version"] == "2026-03-10"
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_conditional_merge_rejects_queue_response_without_operation_id(
    provider: GitHubProvider,
) -> None:
    _mock_inspection()
    respx.get(f"{API}/rules/branches/main").mock(
        return_value=Response(
            200,
            json=[
                {"type": "merge_queue", "parameters": {}},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [{"context": "unit"}],
                    },
                },
            ],
        )
    )
    respx.put(f"{API}/pulls/7/merge-async").mock(
        return_value=Response(202, json={"status": "pending", "details": {}})
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
    )

    with pytest.raises(RuntimeError, match="omitted its operation ID"):
        await provider.conditional_merge(request)
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_conditional_merge_rejects_immediate_merged_response_without_operation_id(
    provider: GitHubProvider,
) -> None:
    _mock_inspection()
    respx.get(f"{API}/rules/branches/main").mock(
        return_value=Response(
            200,
            json=[
                {"type": "merge_queue", "parameters": {}},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [{"context": "unit"}],
                    },
                },
            ],
        )
    )
    respx.put(f"{API}/pulls/7/merge-async").mock(
        return_value=Response(200, json={"status": "merged", "details": {"sha": RESULT}})
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
    )

    with pytest.raises(RuntimeError, match="omitted required operation ID"):
        await provider.conditional_merge(request)
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_conditional_merge_retains_and_reconciles_immediate_operation_id(
    provider: GitHubProvider,
) -> None:
    operation_id = "operation-7"
    respx.get(f"{API}/pulls/7").mock(
        side_effect=[
            Response(200, json=_pr()),
            Response(200, json=_pr(merged=True)),
        ]
    )
    respx.get(f"{API}/git/ref/heads/main").mock(
        side_effect=[
            Response(200, json={"object": {"sha": BASE}}),
            Response(200, json={"object": {"sha": RESULT}}),
        ]
    )
    respx.get(f"{API}/commits/{HEAD}/check-runs").mock(
        return_value=Response(200, json={"check_runs": []})
    )
    respx.get(f"{API}/commits/{HEAD}/status").mock(
        return_value=Response(200, json={"statuses": []})
    )
    respx.get(f"{API}/rules/branches/main").mock(
        return_value=Response(
            200,
            json=[
                {"type": "merge_queue", "parameters": {}},
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [{"context": "unit"}],
                    },
                },
            ],
        )
    )
    respx.put(f"{API}/pulls/7/merge-async").mock(
        return_value=Response(
            200,
            json={
                "status": "merged",
                "details": {"uuid": operation_id, "sha": RESULT},
            },
        )
    )
    operation = respx.get(f"{API}/pulls/7/merge-async/{operation_id}").mock(
        return_value=Response(
            200,
            json={
                "status": "merged",
                "details": {
                    "uuid": operation_id,
                    "merge_method": "squash",
                    "expected_head_sha": HEAD,
                    "sha": RESULT,
                },
            },
        )
    )
    respx.get(f"{API}/git/commits/{RESULT}").mock(
        return_value=Response(200, json={"parents": [{"sha": BASE}]})
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
    )

    receipt = await provider.conditional_merge(request)

    assert operation.called
    assert receipt.state is PublicationState.MERGED
    assert receipt.provider_operation_id == operation_id
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_conditional_merge_refuses_moved_target(provider: GitHubProvider) -> None:
    _mock_inspection(target_sha="d" * 40)
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
    )
    with pytest.raises(RuntimeError, match="target branch moved"):
        await provider.conditional_merge(request)
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_conditional_merge_refuses_target_without_enforced_queue_rules(
    provider: GitHubProvider,
) -> None:
    _mock_inspection()
    respx.get(f"{API}/rules/branches/main").mock(
        return_value=Response(
            200,
            json=[
                {
                    "type": "required_status_checks",
                    "parameters": {
                        "strict_required_status_checks_policy": True,
                        "required_status_checks": [{"context": "unit"}],
                    },
                }
            ],
        )
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
    )
    with pytest.raises(RuntimeError, match="no enforced merge-queue rule"):
        await provider.conditional_merge(request)
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_requires_canonical_remote_result(provider: GitHubProvider) -> None:
    operation_id = "operation-7"
    respx.get(f"{API}/pulls/7").mock(return_value=Response(200, json=_pr(merged=True)))
    respx.get(f"{API}/pulls/7/merge-async/{operation_id}").mock(
        return_value=Response(200, json={"status": "merged", "details": {"sha": RESULT}})
    )
    respx.get(f"{API}/git/commits/{RESULT}").mock(
        return_value=Response(200, json={"parents": [{"sha": BASE}]})
    )
    respx.get(f"{API}/git/ref/heads/main").mock(
        return_value=Response(200, json={"object": {"sha": RESULT}})
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id=operation_id,
    )
    receipt = await provider.reconcile_merge(request)
    assert receipt.state is PublicationState.MERGED
    assert receipt.result_sha == RESULT
    assert receipt.canonical_target_sha == RESULT
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_rejects_merged_result_built_from_a_newer_base(
    provider: GitHubProvider,
) -> None:
    operation_id = "operation-7"
    newer_base = "d" * 40
    respx.get(f"{API}/pulls/7").mock(return_value=Response(200, json=_pr(merged=True)))
    respx.get(f"{API}/pulls/7/merge-async/{operation_id}").mock(
        return_value=Response(200, json={"status": "merged", "details": {"sha": RESULT}})
    )
    respx.get(f"{API}/git/commits/{RESULT}").mock(
        return_value=Response(200, json={"parents": [{"sha": newer_base}]})
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id=operation_id,
    )

    receipt = await provider.reconcile_merge(request)

    assert receipt.state is PublicationState.FAILED
    assert receipt.base_sha == newer_base
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_rejects_out_of_band_merge_not_completed_by_exact_operation(
    provider: GitHubProvider,
) -> None:
    operation_id = "operation-7"
    respx.get(f"{API}/pulls/7").mock(return_value=Response(200, json=_pr(merged=True)))
    operation = respx.get(f"{API}/pulls/7/merge-async/{operation_id}").mock(
        return_value=Response(
            200,
            json={"status": "failed", "details": {"message": "request was superseded"}},
        )
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id=operation_id,
    )

    receipt = await provider.reconcile_merge(request)

    assert operation.called
    assert receipt.state is PublicationState.FAILED
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_reports_async_queue_pending(provider: GitHubProvider) -> None:
    operation_id = "630b9d5e-3f2a-4f7e-8b0c-2d5f9a8c1e42"
    respx.get(f"{API}/pulls/7").mock(return_value=Response(200, json=_pr()))
    operation = respx.get(f"{API}/pulls/7/merge-async/{operation_id}").mock(
        return_value=Response(
            200,
            json={
                "status": "pending",
                "details": {
                    "uuid": operation_id,
                    "merge_method": "squash",
                    "expected_head_sha": HEAD,
                },
            },
        )
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id=operation_id,
    )

    receipt = await provider.reconcile_merge(request)

    assert receipt.state is PublicationState.QUEUED
    assert receipt.provider_operation_id == operation_id
    assert operation.calls[0].request.headers["X-GitHub-Api-Version"] == "2026-03-10"
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_open_github_queue_requires_durable_operation_id(provider: GitHubProvider) -> None:
    respx.get(f"{API}/pulls/7").mock(return_value=Response(200, json=_pr()))
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
    )

    with pytest.raises(RuntimeError, match="requires its provider operation ID"):
        await provider.reconcile_merge(request)
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_reports_async_queue_failure(provider: GitHubProvider) -> None:
    operation_id = "operation-7"
    respx.get(f"{API}/pulls/7").mock(return_value=Response(200, json=_pr()))
    respx.get(f"{API}/pulls/7/merge-async/{operation_id}").mock(
        return_value=Response(
            200,
            json={
                "status": "failed",
                "details": {
                    "uuid": operation_id,
                    "merge_method": "squash",
                    "expected_head_sha": HEAD,
                    "message": "required merge-group check failed",
                },
            },
        )
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id=operation_id,
    )

    receipt = await provider.reconcile_merge(request)

    assert receipt.state is PublicationState.FAILED
    assert receipt.source_sha == HEAD
    assert receipt.base_sha == BASE
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_reports_closed_or_moved_pr_without_retrying(
    provider: GitHubProvider,
) -> None:
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id="operation-7",
    )
    pr = respx.get(f"{API}/pulls/7").mock(
        side_effect=[
            Response(200, json=_pr(state="closed")),
            Response(200, json=_pr(head_sha="d" * 40)),
        ]
    )

    closed = await provider.reconcile_merge(request)
    moved = await provider.reconcile_merge(request)

    assert closed.state is PublicationState.FAILED
    assert moved.state is PublicationState.FAILED
    assert moved.source_sha == "d" * 40
    assert len(pr.calls) == 2
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_propagates_async_operation_auth_failure(provider: GitHubProvider) -> None:
    respx.get(f"{API}/pulls/7").mock(return_value=Response(200, json=_pr()))
    respx.get(f"{API}/pulls/7/merge-async/operation-7").mock(return_value=Response(403))
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id="operation-7",
    )

    with pytest.raises(RuntimeError, match="HTTP 403"):
        await provider.reconcile_merge(request)
    await provider.close()

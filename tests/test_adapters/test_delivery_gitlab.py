"""Strict delivery protocol tests for the GitLab provider."""

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
from volundr.adapters.outbound.gitlab import GitLabProvider

REPOSITORY = "https://gitlab.com/org/repo"
API = "https://gitlab.com/api/v4/projects/org%2Frepo"
HEAD = "b" * 40
BASE = "a" * 40
RESULT = "c" * 40


@pytest.fixture
def provider() -> GitLabProvider:
    publisher = MagicMock()
    publisher.publish = AsyncMock(return_value=None)
    return GitLabProvider(
        name="gitlab",
        base_url="https://gitlab.com",
        token="token",
        branch_publisher=publisher,
    )


def _mr(*, state: str = "opened", target_sha: str = BASE) -> dict:
    return {
        "iid": 7,
        "state": state,
        "sha": HEAD,
        "source_branch": "campaign/work",
        "target_branch": "main",
        "detailed_merge_status": "mergeable",
        "diff_refs": {"head_sha": HEAD, "start_sha": target_sha, "base_sha": target_sha},
        "head_pipeline": {"id": 44, "sha": HEAD},
        "squash_commit_sha": RESULT if state == "merged" else None,
        "merge_commit_sha": None,
    }


def _mock_inspection(*, target_sha: str = BASE, checks: bool = True) -> None:
    respx.get(f"{API}/merge_requests/7").mock(
        return_value=Response(200, json=_mr(target_sha=target_sha))
    )
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": target_sha}})
    )
    jobs = [{"name": "unit", "status": "success", "web_url": "https://ci/44"}] if checks else []
    respx.get(f"{API}/pipelines/44/jobs").mock(return_value=Response(200, json=jobs))


def _mock_completed_train(*, operation_id: str = "99") -> None:
    respx.get(f"{API}/merge_trains/merge_requests/7").mock(
        return_value=Response(
            200,
            json={
                "id": operation_id,
                "status": "merged",
                "target_branch": "main",
                "merge_request": {"iid": 7},
                "pipeline": {"status": "success"},
            },
        )
    )


def _mock_no_existing_train() -> None:
    respx.get(f"{API}/merge_trains/merge_requests/7").mock(return_value=Response(404))


@pytest.mark.asyncio
@respx.mock
async def test_resolve_ref_returns_immutable_sha(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/main").mock(return_value=Response(200, json={"id": BASE}))
    resolved = await provider.resolve_ref(REPOSITORY, "main")
    assert resolved.sha == BASE
    assert resolved.ref == "main"
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_review_publication_is_campaign_idempotent(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/campaign%2Fwork").mock(
        return_value=Response(200, json={"id": HEAD})
    )
    respx.get(f"{API}/merge_requests").mock(return_value=Response(200, json=[]))
    route = respx.post(f"{API}/merge_requests").mock(
        return_value=Response(
            201,
            json={
                "iid": 7,
                "web_url": "https://gitlab.com/org/repo/-/merge_requests/7",
                "sha": HEAD,
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
        labels=("niuu",),
    )
    publication = await provider.ensure_review(request)
    assert publication.created is True
    assert publication.candidate_sha == HEAD
    assert b"niuu-campaign:campaign-1" in route.calls[0].request.content
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_review_publication_recovers_existing_campaign_mr(provider: GitLabProvider) -> None:
    respx.get(f"{API}/repository/commits/campaign%2Fwork").mock(
        return_value=Response(200, json={"id": HEAD})
    )
    existing = {
        "iid": 7,
        "description": "<!-- niuu-campaign:campaign-1 -->",
        "sha": HEAD,
    }
    respx.get(f"{API}/merge_requests").mock(return_value=Response(200, json=[existing]))
    route = respx.put(f"{API}/merge_requests/7").mock(
        return_value=Response(
            200,
            json={
                **existing,
                "web_url": "https://gitlab.com/org/repo/-/merge_requests/7",
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
    assert route.called
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_inspect_binds_checks_to_exact_head_and_base(provider: GitLabProvider) -> None:
    _mock_inspection()
    candidate, receipt = await provider.inspect_delivery_candidate(REPOSITORY, 7, ("unit",))
    assert candidate.candidate_sha == HEAD
    assert candidate.tested_base_sha == BASE
    assert candidate.current_target_sha == BASE
    assert candidate.serialized_publication is True
    assert receipt.checks[0].conclusion is CheckConclusion.PASSING
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_missing_required_job_is_explicit_unknown(provider: GitLabProvider) -> None:
    _mock_inspection(checks=False)
    _, receipt = await provider.inspect_delivery_candidate(REPOSITORY, 7, ("unit",))
    assert len(receipt.checks) == 1
    assert receipt.checks[0].name == "unit"
    assert receipt.checks[0].conclusion is CheckConclusion.UNKNOWN
    await provider.close()


def _mock_merged_results_pipeline(
    *,
    source: str = "merge_request_event",
    ref: str = "refs/merge-requests/7/merge",
    parents: list[str] | None = None,
) -> None:
    _mock_inspection()
    respx.get(f"{API}/merge_requests/7").mock(
        return_value=Response(200, json={**_mr(), "head_pipeline": {"id": 44, "sha": RESULT}})
    )
    respx.get(f"{API}/pipelines/44").mock(
        return_value=Response(200, json={"id": 44, "sha": RESULT, "source": source, "ref": ref})
    )
    respx.get(f"{API}/repository/commits/{RESULT}").mock(
        return_value=Response(
            200, json={"id": RESULT, "parent_ids": parents if parents is not None else [BASE, HEAD]}
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_merged_results_pipeline_binds_temporary_commit_to_source_and_target(
    provider: GitLabProvider,
) -> None:
    _mock_merged_results_pipeline()
    candidate, receipt = await provider.inspect_delivery_candidate(REPOSITORY, 7, ("unit",))
    assert candidate.candidate_sha == HEAD
    assert candidate.tested_base_sha == BASE
    assert receipt.candidate_sha == HEAD
    assert receipt.checks[0].conclusion is CheckConclusion.PASSING
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parents",
    [[BASE, "d" * 40], ["d" * 40, HEAD], [BASE], [HEAD, BASE], [BASE, HEAD, "d" * 40]],
)
@respx.mock
async def test_merged_results_rejects_stale_or_unrelated_merge_commit(
    provider: GitLabProvider, parents: list[str]
) -> None:
    _mock_merged_results_pipeline(parents=parents)
    with pytest.raises(RuntimeError, match="stale source or target"):
        await provider.inspect_delivery_candidate(REPOSITORY, 7, ("unit",))
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "ref"),
    [
        ("push", "refs/merge-requests/7/merge"),
        ("merge_request_event", "refs/merge-requests/8/merge"),
    ],
)
@respx.mock
async def test_merged_results_rejects_wrong_pipeline_origin(
    provider: GitLabProvider, source: str, ref: str
) -> None:
    _mock_merged_results_pipeline(source=source, ref=ref)
    with pytest.raises(RuntimeError, match="stale for the MR candidate"):
        await provider.inspect_delivery_candidate(REPOSITORY, 7, ("unit",))
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_inspection_reads_required_jobs_beyond_first_page(provider: GitLabProvider) -> None:
    _mock_inspection()
    jobs = respx.get(f"{API}/pipelines/44/jobs").mock(
        side_effect=[
            Response(
                200, json=[{"name": "unit", "status": "success"}], headers={"X-Next-Page": "2"}
            ),
            Response(200, json=[{"name": "security", "status": "failed"}]),
        ]
    )
    _, receipt = await provider.inspect_delivery_candidate(REPOSITORY, 7, ("security",))
    assert [(item.name, item.conclusion) for item in receipt.checks] == [
        ("unit", CheckConclusion.PASSING),
        ("security", CheckConclusion.FAILING),
    ]
    assert jobs.calls[1].request.url.params["page"] == "2"
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_inspection_rejects_repeated_job_page(provider: GitLabProvider) -> None:
    _mock_inspection()
    respx.get(f"{API}/pipelines/44/jobs").mock(
        return_value=Response(200, json=[], headers={"X-Next-Page": "1"})
    )
    with pytest.raises(RuntimeError, match="pagination did not advance"):
        await provider.inspect_delivery_candidate(REPOSITORY, 7, ("unit",))
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_publish_branch_uses_remote_cas_and_verifies_result(
    provider: GitLabProvider,
) -> None:
    branch_url = f"{API}/repository/branches/campaign%2Fintegration"
    respx.get(branch_url).mock(
        side_effect=[Response(404), Response(200, json={"commit": {"id": HEAD}})]
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
        remote_url="https://gitlab.com/org/repo.git",
        branch="campaign/integration",
        expected_remote_sha=None,
        username="oauth2",
        token="token",
    )
    await provider.close()


@pytest.mark.parametrize(
    "train_payload",
    [
        {
            "id": 99,
            "status": "fresh",
            "target_branch": "main",
            "merge_request": {"iid": 7, "sha": HEAD},
        },
        [
            {
                "id": 41,
                "status": "fresh",
                "target_branch": "main",
                "merge_request": {"iid": 8, "sha": HEAD},
            },
            {
                "id": 99,
                "status": "fresh",
                "target_branch": "main",
                "merge_request": {"iid": 7, "sha": HEAD},
            },
        ],
    ],
)
@pytest.mark.asyncio
@respx.mock
async def test_conditional_merge_uses_gitlab_merge_train_and_expected_sha(
    provider: GitLabProvider,
    train_payload: object,
) -> None:
    _mock_no_existing_train()
    _mock_inspection()
    respx.get(API).mock(
        return_value=Response(
            200,
            json={
                "merge_trains_enabled": True,
                "merge_pipelines_enabled": True,
                "only_allow_merge_if_pipeline_succeeds": True,
            },
        )
    )
    route = respx.post(f"{API}/merge_trains/merge_requests/7").mock(
        return_value=Response(201, json=train_payload)
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
    assert receipt.provider_operation_id == "99"
    assert route.calls[0].request.content
    assert b'"sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"' in route.calls[0].request.content
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_conditional_merge_recovers_existing_exact_train_without_reposting(
    provider: GitLabProvider,
) -> None:
    train_route = respx.get(f"{API}/merge_trains/merge_requests/7").mock(
        side_effect=[
            Response(
                200,
                json=[
                    {
                        "id": 99,
                        "status": "merged",
                        "target_branch": "main",
                        "merge_request": {"iid": 7},
                        "pipeline": {"status": "success"},
                    }
                ],
            ),
            Response(
                200,
                json={
                    "id": 99,
                    "status": "merged",
                    "target_branch": "main",
                    "merge_request": {"iid": 7},
                    "pipeline": {"status": "success"},
                },
            ),
        ]
    )
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr(state="merged")))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": RESULT}})
    )
    respx.get(f"{API}/repository/commits/{RESULT}").mock(
        return_value=Response(200, json={"parent_ids": [BASE]})
    )
    post_route = respx.post(f"{API}/merge_trains/merge_requests/7").mock(return_value=Response(500))

    receipt = await provider.conditional_merge(
        MergeRequest(
            campaign_id="campaign-1",
            repository=REPOSITORY,
            review_number=7,
            expected_head_sha=HEAD,
            expected_base_sha=BASE,
            expected_target_branch="main",
            method="squash",
        )
    )

    assert receipt.state is PublicationState.MERGED
    assert receipt.provider_operation_id == "99"
    assert len(train_route.calls) == 2
    assert not post_route.called
    await provider.close()


@pytest.mark.parametrize(
    ("train_payload", "error"),
    [
        (
            [
                {
                    "status": "fresh",
                    "target_branch": "main",
                    "merge_request": {"iid": 7},
                }
            ],
            "omitted its operation ID",
        ),
        (
            [
                {
                    "id": 98,
                    "status": "fresh",
                    "target_branch": "main",
                    "merge_request": {"iid": 7},
                },
                {
                    "id": 99,
                    "status": "fresh",
                    "target_branch": "main",
                    "merge_request": {"iid": 7},
                },
            ],
            "ambiguous",
        ),
        (
            [
                {
                    "id": 99,
                    "status": "fresh",
                    "target_branch": "main",
                    "merge_request": {"iid": 7, "sha": "d" * 40},
                }
            ],
            "omitted the exact MR operation",
        ),
        (
            [
                {
                    "id": 99,
                    "status": "fresh",
                    "target_branch": "develop",
                    "merge_request": {"iid": 7, "sha": HEAD},
                }
            ],
            "omitted the exact MR operation",
        ),
    ],
)
@pytest.mark.asyncio
@respx.mock
async def test_conditional_merge_rejects_untrusted_train_response(
    provider: GitLabProvider,
    train_payload: object,
    error: str,
) -> None:
    _mock_no_existing_train()
    _mock_inspection()
    respx.get(API).mock(
        return_value=Response(
            200,
            json={
                "merge_trains_enabled": True,
                "merge_pipelines_enabled": True,
                "only_allow_merge_if_pipeline_succeeds": True,
            },
        )
    )
    respx.post(f"{API}/merge_trains/merge_requests/7").mock(
        return_value=Response(201, json=train_payload)
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

    with pytest.raises(RuntimeError, match=error):
        await provider.conditional_merge(request)
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_conditional_merge_refuses_moved_target(provider: GitLabProvider) -> None:
    _mock_no_existing_train()
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
async def test_conditional_merge_refuses_project_without_merge_train_guarantees(
    provider: GitLabProvider,
) -> None:
    _mock_no_existing_train()
    _mock_inspection()
    respx.get(API).mock(
        return_value=Response(
            200,
            json={
                "merge_trains_enabled": False,
                "merge_pipelines_enabled": True,
                "only_allow_merge_if_pipeline_succeeds": True,
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
    )
    with pytest.raises(RuntimeError, match="merge trains are not enabled"):
        await provider.conditional_merge(request)
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_requires_canonical_remote_result(provider: GitLabProvider) -> None:
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr(state="merged")))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": RESULT}})
    )
    _mock_completed_train()
    respx.get(f"{API}/repository/commits/{RESULT}").mock(
        return_value=Response(200, json={"parent_ids": [BASE]})
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id="99",
    )
    receipt = await provider.reconcile_merge(request)
    assert receipt.state is PublicationState.MERGED
    assert receipt.result_sha == RESULT
    assert receipt.canonical_target_sha == RESULT
    assert receipt.verified_at is not None
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_recovers_exact_operation_id_when_receipt_was_lost(
    provider: GitLabProvider,
) -> None:
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr(state="merged")))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": RESULT}})
    )
    _mock_completed_train(operation_id="177482")
    respx.get(f"{API}/repository/commits/{RESULT}").mock(
        return_value=Response(200, json={"parent_ids": [BASE]})
    )

    receipt = await provider.reconcile_merge(
        MergeRequest(
            campaign_id="campaign-1",
            repository=REPOSITORY,
            review_number=7,
            expected_head_sha=HEAD,
            expected_base_sha=BASE,
            expected_target_branch="main",
            method="squash",
        )
    )

    assert receipt.state is PublicationState.MERGED
    assert receipt.provider_operation_id == "177482"
    assert receipt.result_sha == RESULT
    assert receipt.canonical_target_sha == RESULT
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_without_operation_id_fails_when_train_is_absent(
    provider: GitLabProvider,
) -> None:
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr()))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": BASE}})
    )
    respx.get(f"{API}/merge_trains/merge_requests/7").mock(return_value=Response(404))

    with pytest.raises(RuntimeError, match="operation ID is unavailable"):
        await provider.reconcile_merge(
            MergeRequest(
                campaign_id="campaign-1",
                repository=REPOSITORY,
                review_number=7,
                expected_head_sha=HEAD,
                expected_base_sha=BASE,
                expected_target_branch="main",
                method="squash",
            )
        )
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_discovery_rejects_mixed_valid_and_idless_exact_entries(
    provider: GitLabProvider,
) -> None:
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr()))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": BASE}})
    )
    exact = {
        "status": "fresh",
        "target_branch": "main",
        "merge_request": {"iid": 7},
        "pipeline": {"status": "running"},
    }
    respx.get(f"{API}/merge_trains/merge_requests/7").mock(
        return_value=Response(200, json=[{**exact, "id": 99}, exact])
    )

    with pytest.raises(RuntimeError, match="omitted its operation ID"):
        await provider.reconcile_merge(
            MergeRequest(
                campaign_id="campaign-1",
                repository=REPOSITORY,
                review_number=7,
                expected_head_sha=HEAD,
                expected_base_sha=BASE,
                expected_target_branch="main",
                method="squash",
            )
        )
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_rejects_merged_result_built_from_a_newer_base(
    provider: GitLabProvider,
) -> None:
    newer_base = "d" * 40
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr(state="merged")))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": RESULT}})
    )
    _mock_completed_train()
    respx.get(f"{API}/repository/commits/{RESULT}").mock(
        return_value=Response(200, json={"parent_ids": [newer_base]})
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id="99",
    )

    receipt = await provider.reconcile_merge(request)

    assert receipt.state is PublicationState.FAILED
    assert receipt.base_sha == newer_base
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_rejects_out_of_band_merge_not_completed_by_exact_train(
    provider: GitLabProvider,
) -> None:
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr(state="merged")))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": RESULT}})
    )
    train = respx.get(f"{API}/merge_trains/merge_requests/7").mock(
        return_value=Response(
            200,
            json={
                "id": "99",
                "status": "skip_merged",
                "target_branch": "main",
                "merge_request": {"iid": 7},
                "pipeline": {"status": "success"},
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
        provider_operation_id="99",
    )

    receipt = await provider.reconcile_merge(request)

    assert train.called
    assert receipt.state is PublicationState.FAILED
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_squash_merge_reconciles_final_merge_commit_not_intermediate_squash(
    provider: GitLabProvider,
) -> None:
    final_sha = "d" * 40
    respx.get(f"{API}/merge_requests/7").mock(
        return_value=Response(200, json={**_mr(state="merged"), "merge_commit_sha": final_sha})
    )
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": final_sha}})
    )
    _mock_completed_train()
    respx.get(f"{API}/repository/commits/{final_sha}").mock(
        return_value=Response(200, json={"parent_ids": [BASE, RESULT]})
    )
    receipt = await provider.reconcile_merge(
        MergeRequest(
            campaign_id="campaign-1",
            repository=REPOSITORY,
            review_number=7,
            expected_head_sha=HEAD,
            expected_base_sha=BASE,
            expected_target_branch="main",
            method="squash",
            provider_operation_id="99",
        )
    )
    assert receipt.result_sha == final_sha
    assert receipt.canonical_target_sha == final_sha
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_rejects_merge_method_mismatch(provider: GitLabProvider) -> None:
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr(state="merged")))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": RESULT}})
    )
    _mock_completed_train()
    with pytest.raises(RuntimeError, match="method differs"):
        await provider.reconcile_merge(
            MergeRequest(
                campaign_id="campaign-1",
                repository=REPOSITORY,
                review_number=7,
                expected_head_sha=HEAD,
                expected_base_sha=BASE,
                expected_target_branch="main",
                method="merge",
                provider_operation_id="99",
            )
        )
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_reports_active_merge_train_as_pending(provider: GitLabProvider) -> None:
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr()))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": BASE}})
    )
    train = respx.get(f"{API}/merge_trains/merge_requests/7").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 41,
                    "status": "fresh",
                    "target_branch": "main",
                    "merge_request": {"iid": 8},
                    "pipeline": {"status": "running"},
                },
                {
                    "id": 99,
                    "status": "fresh",
                    "target_branch": "main",
                    "merge_request": {"iid": 7},
                    "pipeline": {"status": "running"},
                },
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
        provider_operation_id="99",
    )

    receipt = await provider.reconcile_merge(request)

    assert receipt.state is PublicationState.QUEUED
    assert receipt.provider_operation_id == "99"
    assert train.called
    await provider.close()


@pytest.mark.parametrize("operation_id", [None, "99"])
@pytest.mark.asyncio
@respx.mock
async def test_reconcile_rejects_ambiguous_exact_train_entries(
    provider: GitLabProvider,
    operation_id: str | None,
) -> None:
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr()))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": BASE}})
    )
    entry = {
        "id": 99,
        "status": "fresh",
        "target_branch": "main",
        "merge_request": {"iid": 7},
        "pipeline": {"status": "running"},
    }
    respx.get(f"{API}/merge_trains/merge_requests/7").mock(
        return_value=Response(200, json=[entry, entry])
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

    with pytest.raises(RuntimeError, match="ambiguous"):
        await provider.reconcile_merge(request)
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_rejects_train_that_differs_from_known_operation_id(
    provider: GitLabProvider,
) -> None:
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr()))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": BASE}})
    )
    respx.get(f"{API}/merge_trains/merge_requests/7").mock(
        return_value=Response(
            200,
            json={
                "id": 100,
                "status": "fresh",
                "target_branch": "main",
                "merge_request": {"iid": 7},
                "pipeline": {"status": "running"},
            },
        )
    )

    receipt = await provider.reconcile_merge(
        MergeRequest(
            campaign_id="campaign-1",
            repository=REPOSITORY,
            review_number=7,
            expected_head_sha=HEAD,
            expected_base_sha=BASE,
            expected_target_branch="main",
            method="squash",
            provider_operation_id="99",
        )
    )

    assert receipt.state is PublicationState.FAILED
    assert receipt.provider_operation_id == "99"
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_reports_failed_or_dropped_merge_train(provider: GitLabProvider) -> None:
    failed = {**_mr(), "head_pipeline": {"id": 44, "sha": HEAD, "status": "failed"}}
    respx.get(f"{API}/merge_requests/7").mock(
        side_effect=[Response(200, json=_mr()), Response(200, json=failed)]
    )
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": BASE}})
    )
    respx.get(f"{API}/merge_trains/merge_requests/7").mock(
        side_effect=[
            Response(
                200,
                json={
                    "id": 99,
                    "status": "fresh",
                    "target_branch": "main",
                    "merge_request": {"iid": 7},
                    "pipeline": {"status": "failed"},
                },
            ),
            Response(404),
        ]
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id="99",
    )

    failed_in_train = await provider.reconcile_merge(request)
    dropped_after_failure = await provider.reconcile_merge(request)

    assert failed_in_train.state is PublicationState.FAILED
    assert dropped_after_failure.state is PublicationState.FAILED
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_reports_moved_gitlab_candidate_identity(provider: GitLabProvider) -> None:
    moved_head = "d" * 40
    respx.get(f"{API}/merge_requests/7").mock(
        return_value=Response(200, json={**_mr(), "sha": moved_head})
    )
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": BASE}})
    )
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id="99",
    )

    receipt = await provider.reconcile_merge(request)

    assert receipt.state is PublicationState.FAILED
    assert receipt.source_sha == moved_head
    await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_reconcile_propagates_gitlab_train_auth_failure(provider: GitLabProvider) -> None:
    respx.get(f"{API}/merge_requests/7").mock(return_value=Response(200, json=_mr()))
    respx.get(f"{API}/repository/branches/main").mock(
        return_value=Response(200, json={"commit": {"id": BASE}})
    )
    respx.get(f"{API}/merge_trains/merge_requests/7").mock(return_value=Response(401))
    request = MergeRequest(
        campaign_id="campaign-1",
        repository=REPOSITORY,
        review_number=7,
        expected_head_sha=HEAD,
        expected_base_sha=BASE,
        expected_target_branch="main",
        method="squash",
        provider_operation_id="99",
    )

    with pytest.raises(RuntimeError, match="HTTP 401"):
        await provider.reconcile_merge(request)
    await provider.close()

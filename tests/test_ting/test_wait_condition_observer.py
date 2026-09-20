from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import respx
from httpx import Response

from niuu.domain.delivery import (
    CheckConclusion,
    CheckReceipt,
    CheckRecord,
    MergeReceipt,
    PublicationState,
    ReviewCandidate,
)
from tests.test_ting.test_delivery_execution import _execution
from ting.adapters.wait_condition_observer import ForgeWaitConditionObserver
from ting.domain.workflow_wait import WorkflowWaitRequest
from volundr.adapters.outbound.github import GitHubProvider
from volundr.adapters.outbound.gitlab import GitLabProvider


def _setup(*, mode="checks", conclusions=(CheckConclusion.PASSING,)):
    execution = _execution()
    request = WorkflowWaitRequest(
        mode=mode,
        repository=execution.repository,
        review_number=9,
        expected_head_sha="b" * 40,
        expected_base_sha=execution.base_sha,
        expected_target_branch=execution.base_ref,
        policy_id="integration",
        **({"method": "merge"} if mode == "merge" else {}),
    )
    records = tuple(CheckRecord(name=f"check-{i}", conclusion=c) for i, c in enumerate(conclusions))
    candidate = ReviewCandidate(
        provider="forge",
        repository=execution.repository,
        review_number=9,
        source_branch="work",
        target_branch=execution.base_ref,
        candidate_sha="b" * 40,
        tested_base_sha=execution.base_sha,
        current_target_sha=execution.base_sha,
        mergeable=True,
        checks=records,
        serialized_publication=True,
    )
    checks = CheckReceipt(
        receipt_id="check-receipt",
        provider="forge",
        repository=execution.repository,
        review_number=9,
        candidate_sha="b" * 40,
        tested_base_sha=execution.base_sha,
        checks=records,
        observed_at=datetime.now(UTC),
    )
    adapter = SimpleNamespace(
        inspect_delivery_candidate=AsyncMock(return_value=(candidate, checks)),
        describe_delivery_policy=AsyncMock(
            return_value=SimpleNamespace(
                required_check_names=("check-0",),
            )
        ),
        reconcile_delivery_merge=AsyncMock(),
    )
    factory = SimpleNamespace(
        for_connection=AsyncMock(return_value=adapter),
        primary_for_owner=AsyncMock(return_value=adapter),
    )
    issuer = MagicMock()
    issuer.issue_token.return_value = SimpleNamespace(token="scoped-test-token")
    observer = ForgeWaitConditionObserver(
        volundr_factory=factory,
        policy_id="integration",
        token_issuer=issuer,
    )
    return execution, request, candidate, checks, adapter, factory, issuer, observer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conclusion", "status"),
    [
        (CheckConclusion.PASSING, "checks_passed"),
        (CheckConclusion.PENDING, "checks_pending"),
        (CheckConclusion.UNKNOWN, "checks_pending"),
        (CheckConclusion.FAILING, "checks_failed"),
        (CheckConclusion.CANCELED, "checks_failed"),
        (CheckConclusion.SKIPPED, "checks_failed"),
    ],
)
async def test_required_check_status_and_exact_owner_binding(conclusion, status):
    execution, request, _, _, adapter, factory, issuer, observer = _setup(conclusions=(conclusion,))
    result = await observer.observe(execution, request)
    assert result.status == status
    assert result.expected_head_sha == request.expected_head_sha
    assert result.checks is not None
    factory.for_connection.assert_awaited_once_with(execution.owner_id, execution.connection_id)
    kwargs = adapter.inspect_delivery_candidate.await_args.kwargs
    assert kwargs["campaign_id"] == str(execution.id)
    assert kwargs["principal"].user_id == execution.owner_id
    assert kwargs["auth_token"] == "scoped-test-token"
    claims = issuer.issue_token.call_args.kwargs["claims"]
    assert claims["workflow_execution_id"] == str(execution.id)
    assert claims["parent_node_id"] == execution.parent_node_id
    assert claims["scopes"] == ["ting:workflow:coordinate"]
    assert claims["forge_session_id"] == execution.parent_session_id
    assert issuer.issue_token.call_args.kwargs["principal"].roles == ["volundr:developer"]
    adapter.reconcile_delivery_merge.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_required_check_cannot_look_like_success():
    execution, request, _, _, adapter, _, _, observer = _setup(conclusions=())
    result = await observer.observe(execution, request)
    assert result.status == "checks_pending"
    assert not result.terminal
    adapter.describe_delivery_policy.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_sha", "c" * 40),
        ("tested_base_sha", "c" * 40),
        ("current_target_sha", "c" * 40),
        ("target_branch", "other"),
        ("repository", "https://other.example/repo"),
        ("review_number", 10),
    ],
)
async def test_changed_candidate_is_terminal_stale(field, value):
    execution, request, candidate, checks, adapter, _, _, observer = _setup()
    adapter.inspect_delivery_candidate.return_value = (
        candidate.model_copy(update={field: value}),
        checks,
    )
    result = await observer.observe(execution, request)
    assert result.status == "stale_candidate"
    assert result.terminal
    adapter.describe_delivery_policy.assert_not_awaited()


def _merge_receipt(execution, request, *, state=PublicationState.MERGED):
    return MergeReceipt(
        receipt_id="merge-receipt",
        campaign_id=str(execution.id),
        provider="forge",
        repository=request.repository,
        review_number=request.review_number,
        source_sha=request.expected_head_sha,
        base_sha=request.expected_base_sha,
        target_branch=request.expected_target_branch,
        method=request.method,
        state=state,
        result_sha="d" * 40 if state is PublicationState.MERGED else None,
        canonical_target_sha="d" * 40 if state is PublicationState.MERGED else None,
        verified_at=datetime.now(UTC) if state is PublicationState.MERGED else None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "status"),
    [
        (PublicationState.QUEUED, "merge_pending"),
        (PublicationState.MERGED, "merged"),
    ],
)
async def test_merge_reconciles_receipt_without_rejecting_successful_target_move(state, status):
    execution, request, _, _, adapter, _, _, observer = _setup(mode="merge")
    adapter.reconcile_delivery_merge.return_value = _merge_receipt(execution, request, state=state)
    result = await observer.observe(execution, request)
    assert result.status == status
    adapter.inspect_delivery_candidate.assert_not_awaited()
    adapter.describe_delivery_policy.assert_not_awaited()
    assert adapter.reconcile_delivery_merge.await_args.args[0].campaign_id == str(execution.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "status"),
    [
        ("campaign_id", "other", "merge_failed"),
        ("source_sha", "e" * 40, "stale_candidate"),
        ("base_sha", "e" * 40, "stale_candidate"),
        ("repository", "https://other.example/repo", "merge_failed"),
        ("review_number", 10, "merge_failed"),
        ("target_branch", "other", "stale_candidate"),
        ("method", "squash", "merge_failed"),
        ("canonical_target_sha", "e" * 40, "merge_failed"),
    ],
)
async def test_foreign_merge_receipt_cannot_resume_as_success(field, value, status):
    execution, request, _, _, adapter, _, _, observer = _setup(mode="merge")
    adapter.reconcile_delivery_merge.return_value = _merge_receipt(execution, request).model_copy(
        update={field: value},
    )
    result = await observer.observe(execution, request)
    assert result.status == status
    assert result.terminal


@pytest.mark.asyncio
async def test_remote_errors_propagate_to_durable_retry_instead_of_successful_observation():
    execution, request, _, _, adapter, _, _, observer = _setup(mode="merge")
    adapter.reconcile_delivery_merge.side_effect = RuntimeError("remote identity unavailable")
    with pytest.raises(RuntimeError, match="remote identity unavailable"):
        await observer.observe(execution, request)


@pytest.mark.asyncio
async def test_terminal_provider_failure_notifies_merge_repair():
    execution, request, _, _, adapter, _, _, observer = _setup(mode="merge")
    adapter.reconcile_delivery_merge.return_value = _merge_receipt(
        execution,
        request,
        state=PublicationState.FAILED,
    )

    result = await observer.observe(execution, request)

    assert result.status == "merge_failed"
    assert result.terminal


class _DirectForgeAdapter:
    def __init__(self, provider):
        self._provider = provider

    async def reconcile_delivery_merge(self, request, **_kwargs):
        return await self._provider.reconcile_merge(request)


def _provider_observer(provider, *, repository: str, method: str = "squash"):
    execution = replace(
        _execution(),
        repository=repository,
        base_sha="a" * 40,
        base_ref="main",
    )
    request = WorkflowWaitRequest(
        mode="merge",
        repository=repository,
        review_number=9,
        expected_head_sha="b" * 40,
        expected_base_sha="a" * 40,
        expected_target_branch="main",
        policy_id="integration",
        method=method,
        provider_operation_id="operation-9",
    )
    adapter = _DirectForgeAdapter(provider)
    factory = SimpleNamespace(
        for_connection=AsyncMock(return_value=adapter),
        primary_for_owner=AsyncMock(return_value=adapter),
    )
    observer = ForgeWaitConditionObserver(
        volundr_factory=factory,
        policy_id="integration",
    )
    return execution, request, observer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote_state", "expected_status"),
    [
        ("pending", "merge_pending"),
        ("failed", "merge_failed"),
        ("closed", "merge_failed"),
        ("stale", "stale_candidate"),
        ("merged", "merged"),
    ],
)
@respx.mock
async def test_github_http_reconciliation_drives_typed_observation(
    remote_state,
    expected_status,
):
    repository = "https://github.com/org/repo"
    api = "https://api.github.com/repos/org/repo"
    provider = GitHubProvider(name="github", base_url="https://api.github.com", token="token")
    execution, request, observer = _provider_observer(provider, repository=repository)
    merged = remote_state == "merged"
    closed = remote_state == "closed"
    head = "d" * 40 if remote_state == "stale" else request.expected_head_sha
    result_sha = "c" * 40
    respx.get(f"{api}/pulls/9").mock(
        return_value=Response(
            200,
            json={
                "state": "closed" if merged or closed else "open",
                "merged": merged,
                "merge_commit_sha": result_sha if merged else None,
                "head": {"sha": head, "ref": "campaign/work"},
                "base": {"sha": request.expected_base_sha, "ref": "main"},
            },
        )
    )
    if remote_state in {"pending", "failed", "merged"}:
        respx.get(f"{api}/pulls/9/merge-async/operation-9").mock(
            return_value=Response(
                200,
                json={
                    "status": remote_state,
                    "details": {
                        "uuid": "operation-9",
                        "merge_method": "squash",
                        "expected_head_sha": request.expected_head_sha,
                        **({"sha": result_sha} if merged else {}),
                    },
                },
            )
        )
    if merged:
        respx.get(f"{api}/git/commits/{result_sha}").mock(
            return_value=Response(
                200,
                json={"parents": [{"sha": request.expected_base_sha}]},
            )
        )
        respx.get(f"{api}/git/ref/heads/main").mock(
            return_value=Response(200, json={"object": {"sha": result_sha}})
        )

    observation = await observer.observe(execution, request)

    assert observation.status == expected_status
    assert observation.terminal is (expected_status != "merge_pending")
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote_state", "expected_status"),
    [
        ("pending", "merge_pending"),
        ("failed", "merge_failed"),
        ("closed", "merge_failed"),
        ("stale", "stale_candidate"),
        ("merged", "merged"),
    ],
)
@respx.mock
async def test_gitlab_http_reconciliation_drives_typed_observation(
    remote_state,
    expected_status,
):
    repository = "https://gitlab.com/org/repo"
    api = "https://gitlab.com/api/v4/projects/org%2Frepo"
    provider = GitLabProvider(name="gitlab", base_url="https://gitlab.com", token="token")
    execution, request, observer = _provider_observer(provider, repository=repository)
    merged = remote_state == "merged"
    head = "d" * 40 if remote_state == "stale" else request.expected_head_sha
    result_sha = "c" * 40
    respx.get(f"{api}/merge_requests/9").mock(
        return_value=Response(
            200,
            json={
                "iid": 9,
                "state": (
                    "merged" if merged else remote_state if remote_state == "closed" else "opened"
                ),
                "sha": head,
                "target_branch": "main",
                "squash_commit_sha": result_sha if merged else None,
                "merge_commit_sha": None,
                "head_pipeline": {"status": "failed" if remote_state == "failed" else "running"},
            },
        )
    )
    respx.get(f"{api}/repository/branches/main").mock(
        return_value=Response(
            200,
            json={
                "commit": {
                    "id": result_sha if merged else request.expected_base_sha,
                }
            },
        )
    )
    if remote_state in {"pending", "failed", "merged"}:
        respx.get(f"{api}/merge_trains/merge_requests/9").mock(
            return_value=Response(
                200,
                json={
                    "id": "operation-9",
                    "status": "merged" if merged else "fresh",
                    "target_branch": "main",
                    "merge_request": {"iid": 9},
                    "pipeline": {
                        "status": (
                            "failed"
                            if remote_state == "failed"
                            else "success"
                            if merged
                            else "running"
                        )
                    },
                },
            )
        )
    if merged:
        respx.get(f"{api}/repository/commits/{result_sha}").mock(
            return_value=Response(200, json={"parent_ids": [request.expected_base_sha]})
        )

    observation = await observer.observe(execution, request)

    assert observation.status == expected_status
    assert observation.terminal is (expected_status != "merge_pending")
    await provider.close()


@pytest.mark.asyncio
async def test_unknown_policy_is_rejected_before_any_remote_call():
    execution, request, _, _, adapter, _, _, observer = _setup()
    with pytest.raises(ValueError, match="configured integration policy"):
        await observer.observe(execution, request.model_copy(update={"policy_id": "weaker"}))
    adapter.inspect_delivery_candidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_check_names_cannot_hide_a_failing_result():
    execution, request, candidate, checks, adapter, _, _, observer = _setup()
    duplicate = CheckRecord(name="check-0", conclusion=CheckConclusion.FAILING)
    adapter.inspect_delivery_candidate.return_value = (
        candidate,
        checks.model_copy(update={"checks": (duplicate, *checks.checks)}),
    )
    result = await observer.observe(execution, request)
    assert result.status == "checks_failed"
    assert "duplicate" in result.reason


@pytest.mark.asyncio
async def test_forge_http_observation_preserves_candidate_policy_and_scoped_auth():
    import httpx
    import respx

    from niuu.domain.delivery import AcceptancePolicy
    from niuu.domain.models import Principal
    from ting.adapters.volundr_http import VolundrHTTPAdapter

    execution, request, candidate, checks, _, _, _, _ = _setup()
    adapter = VolundrHTTPAdapter(base_url="https://forge.example", timeout=5.0)
    policy = AcceptancePolicy(
        required_review_roles=("integration",),
        required_test_contract_ids=("unit",),
        review_producers={"integration": ("reviewer",)},
        test_producers={"unit": ("runner",)},
        require_forge_checks=True,
        required_check_names=("check-0",),
        forge_producers=("forge",),
    )
    principal = Principal(
        user_id=execution.owner_id, email="", tenant_id=execution.tenant_id, roles=[]
    )
    with respx.mock:
        inspect = respx.post("https://forge.example/api/v1/forge/delivery/forge/inspect").mock(
            return_value=httpx.Response(
                200,
                json={
                    "candidate": candidate.model_dump(mode="json"),
                    "checks": checks.model_dump(mode="json"),
                },
            ),
        )
        describe = respx.post("https://forge.example/api/v1/forge/delivery/evidence/policy").mock(
            return_value=httpx.Response(200, json=policy.model_dump(mode="json")),
        )
        observed = await adapter.inspect_delivery_candidate(
            request.repository,
            request.review_number,
            campaign_id=str(execution.id),
            policy_id=request.policy_id,
            auth_token="test-scoped-token",
            principal=principal,
        )
        assert observed == (candidate, checks)
        observed_policy = await adapter.describe_delivery_policy(
            campaign_id=str(execution.id),
            repository=request.repository,
            policy_id=request.policy_id,
            auth_token="test-scoped-token",
            principal=principal,
        )
        assert observed_policy == policy
        for route in (inspect, describe):
            assert route.calls[0].request.headers["Authorization"] == "Bearer test-scoped-token"
            assert str(execution.id) in route.calls[0].request.content.decode()
        inspect.mock(return_value=httpx.Response(403))
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.inspect_delivery_candidate(
                request.repository,
                request.review_number,
                campaign_id=str(execution.id),
                policy_id=request.policy_id,
                auth_token="test-scoped-token",
                principal=principal,
            )

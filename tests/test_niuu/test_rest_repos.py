"""The repos routes see the caller the way every other route does."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.rest_repos import create_repos_router
from niuu.domain.models import (
    CIStatus,
    Principal,
    PullRequest,
    PullRequestStatus,
    ReviewStatus,
)
from niuu.domain.services.repo import RepoService
from niuu.ports.git import (
    GitAuthError,
    GitProvider,
    GitRepoNotFoundError,
    GitWorkflowProvider,
)
from tests.conftest import MockGitProvider, MockGitRegistry
from volundr.domain.models import GitProviderType, RepoInfo


def _client(user_id: str = "dev-user") -> tuple[TestClient, AsyncMock, MockGitProvider]:
    personal = MockGitProvider(
        name="github-signin",
        orgs=(),
        repos=[
            RepoInfo(
                provider=GitProviderType.GITHUB,
                org="jve",
                name="dotfiles",
                clone_url="https://github.com/jve/dotfiles.git",
                url="https://github.com/jve/dotfiles",
            )
        ],
    )
    user_integration = AsyncMock()
    user_integration.get_git_providers = AsyncMock(return_value=[personal])
    user_integration.find_git_provider_for = AsyncMock(return_value=None)
    app = FastAPI()
    app.include_router(
        create_repos_router(RepoService(MockGitRegistry([]), user_integration=user_integration))
    )
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id=user_id, email="dev@example.test", tenant_id="default", roles=[]
    )
    return TestClient(app), user_integration, personal


def test_repos_come_from_the_authenticated_persons_accounts() -> None:
    client, user_integration, personal = _client()

    response = client.get("/api/v1/niuu/repos")  # no x-auth-user-id header

    assert response.status_code == 200
    assert list(response.json()) == ["github-signin"]
    assert response.json()["github-signin"][0]["name"] == "dotfiles"
    user_integration.get_git_providers.assert_awaited_once_with("dev-user")
    assert personal.list_repos_calls == [""]


def test_branches_for_an_unknown_address_is_not_found_not_a_crash() -> None:
    client, _, _ = _client()

    response = client.get("/api/v1/niuu/repos/branches", params={"repo_url": "jve"})

    assert response.status_code == 404
    assert "No connected Git host serves 'jve'" in response.json()["detail"]


def test_branch_provider_outage_is_a_visible_gateway_error():
    import httpx

    client, integrations, _ = _client()
    provider = AsyncMock()
    provider.list_branches.side_effect = httpx.ConnectError("provider unreachable")
    integrations.find_git_provider_for.return_value = provider
    response = client.get(
        "/api/v1/niuu/repos/branches", params={"repo_url": "https://git.test/a/b"}
    )
    assert response.status_code == 502
    assert "Could not fetch branches" in response.json()["detail"]


# --- Pull requests: provider-backed, no Forge instance involved ---------------

NIUU_REPO = "https://github.com/niuulabs/niuu"


class WorkflowGitProvider(MockGitProvider, GitWorkflowProvider):
    """A Git host that can answer pull-request questions."""

    def __init__(self, *, merged: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self.merged = merged
        self.calls: list[tuple] = []
        self.failure: Exception | None = None

    def _record(self, *call) -> None:
        self.calls.append(call)
        if self.failure is not None:
            raise self.failure

    async def create_branch(self, repo_url, branch_name, from_branch="main") -> bool:
        raise AssertionError("not used by the repos routes")

    async def create_pull_request(self, *args, **kwargs) -> PullRequest:
        raise AssertionError("not used by the repos routes")

    async def get_pull_request(self, repo_url: str, pr_number: int) -> PullRequest | None:
        self._record("get", repo_url, pr_number)
        return _pr(pr_number) if pr_number == 7 else None

    async def list_pull_requests(self, repo_url: str, status: str = "open") -> list[PullRequest]:
        self._record("list", repo_url, status)
        return [_pr(7)]

    async def merge_pull_request(self, repo_url, pr_number, merge_method="squash") -> bool:
        self._record("merge", repo_url, pr_number, merge_method)
        return self.merged

    async def get_ci_status(self, repo_url: str, branch: str) -> CIStatus:
        self._record("ci", repo_url, branch)
        return CIStatus.PASSING


def _pr(number: int) -> PullRequest:
    return PullRequest(
        number=number,
        title="feat: resident sees its PRs",
        url=f"{NIUU_REPO}/pull/{number}",
        repo_url=NIUU_REPO,
        provider=GitProviderType.GITHUB,
        source_branch="feat/x",
        target_branch="dev",
        status=PullRequestStatus.OPEN,
        ci_status=CIStatus.PENDING,
        review_status=ReviewStatus.APPROVED,
        created_at=datetime(2026, 10, 20, tzinfo=UTC),
    )


def _pr_client(
    *,
    personal: GitProvider | None = None,
    shared: list[GitProvider] | None = None,
    with_integrations: bool = True,
) -> tuple[TestClient, AsyncMock]:
    user_integration = AsyncMock()
    user_integration.find_git_provider_for = AsyncMock(return_value=personal)
    service = RepoService(
        MockGitRegistry(shared or []),
        user_integration=user_integration if with_integrations else None,
    )
    app = FastAPI()
    app.include_router(create_repos_router(service))
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id="regin", email="regin@example.test", tenant_id="default", roles=[]
    )
    return TestClient(app), user_integration


def test_list_prs_uses_the_callers_own_git_account() -> None:
    personal = WorkflowGitProvider(name="github-regin")
    client, integrations = _pr_client(personal=personal)

    response = client.get("/api/v1/niuu/repos/prs", params={"repo_url": NIUU_REPO, "status": "all"})

    assert response.status_code == 200
    body = response.json()
    assert [pr["number"] for pr in body] == [7]
    assert body[0]["provider"] == "github"
    assert body[0]["status"] == "open"
    assert body[0]["ci_status"] == "pending"
    assert body[0]["review_status"] == "approved"
    assert body[0]["created_at"].startswith("2026-10-20")
    assert body[0]["updated_at"] is None
    integrations.find_git_provider_for.assert_awaited_once_with(NIUU_REPO, "regin")
    assert personal.calls == [("list", NIUU_REPO, "all")]


def test_list_prs_defaults_to_open_and_uses_shared_provider_without_integrations() -> None:
    shared = WorkflowGitProvider(name="github-org")
    client, _ = _pr_client(shared=[shared], with_integrations=False)

    response = client.get("/api/v1/niuu/repos/prs", params={"repo_url": NIUU_REPO})

    assert response.status_code == 200
    assert shared.calls == [("list", NIUU_REPO, "open")]


def test_list_prs_uses_shared_provider_when_the_caller_has_no_account_for_it() -> None:
    shared = WorkflowGitProvider(name="github-org")
    client, _ = _pr_client(personal=None, shared=[shared])

    response = client.get("/api/v1/niuu/repos/prs", params={"repo_url": NIUU_REPO})

    assert response.status_code == 200
    assert shared.calls == [("list", NIUU_REPO, "open")]


def test_list_prs_rejects_an_unknown_status_filter() -> None:
    client, _ = _pr_client(personal=WorkflowGitProvider())

    response = client.get(
        "/api/v1/niuu/repos/prs", params={"repo_url": NIUU_REPO, "status": "draft"}
    )

    assert response.status_code == 422


def test_pr_routes_name_the_missing_git_host_instead_of_a_bare_404() -> None:
    client, _ = _pr_client(personal=None, shared=[])

    response = client.get("/api/v1/niuu/repos/prs", params={"repo_url": NIUU_REPO})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert f"No connected Git host serves {NIUU_REPO!r}" in detail
    assert "Connect the account" in detail


def test_pr_routes_reject_a_host_without_pull_request_support() -> None:
    client, _ = _pr_client(personal=MockGitProvider(name="read-only"))

    response = client.get("/api/v1/niuu/repos/prs", params={"repo_url": NIUU_REPO})

    assert response.status_code == 400
    assert "read-only does not support pull requests" in response.json()["detail"]


@pytest.mark.parametrize(
    ("failure", "status_code", "detail"),
    [
        (GitRepoNotFoundError("Repository not found: niuu"), 404, "Repository not found"),
        (GitAuthError("token expired"), 403, "token expired"),
        (httpx.ConnectError("down"), 502, "Could not reach the Git provider"),
    ],
)
def test_pr_routes_type_git_host_failures(
    failure: Exception, status_code: int, detail: str
) -> None:
    personal = WorkflowGitProvider()
    personal.failure = failure
    client, _ = _pr_client(personal=personal)

    response = client.get("/api/v1/niuu/repos/prs/7", params={"repo_url": NIUU_REPO})

    assert response.status_code == status_code
    assert detail in response.json()["detail"]


def test_get_pr_returns_the_pull_request_or_a_named_404() -> None:
    personal = WorkflowGitProvider()
    client, _ = _pr_client(personal=personal)

    found = client.get("/api/v1/niuu/repos/prs/7", params={"repo_url": NIUU_REPO})
    missing = client.get("/api/v1/niuu/repos/prs/8", params={"repo_url": NIUU_REPO})

    assert found.status_code == 200
    assert found.json()["url"] == f"{NIUU_REPO}/pull/7"
    assert missing.status_code == 404
    assert missing.json()["detail"] == f"PR #8 not found in {NIUU_REPO}"


def test_merge_pr_defaults_to_squash_and_honours_a_requested_method() -> None:
    personal = WorkflowGitProvider()
    client, _ = _pr_client(personal=personal)

    default = client.post("/api/v1/niuu/repos/prs/7/merge", params={"repo_url": NIUU_REPO})
    chosen = client.post(
        "/api/v1/niuu/repos/prs/7/merge",
        params={"repo_url": NIUU_REPO},
        json={"merge_method": "merge"},
    )

    assert default.status_code == 200
    assert default.json() == {"merged": True}
    assert chosen.status_code == 200
    assert personal.calls == [
        ("merge", NIUU_REPO, 7, "squash"),
        ("merge", NIUU_REPO, 7, "merge"),
    ]


def test_merge_pr_refused_by_the_host_is_a_conflict() -> None:
    client, _ = _pr_client(personal=WorkflowGitProvider(merged=False))

    response = client.post(
        "/api/v1/niuu/repos/prs/7/merge",
        params={"repo_url": NIUU_REPO},
        json={"merge_method": "squash"},
    )

    assert response.status_code == 409
    assert "Failed to merge PR #7" in response.json()["detail"]


def test_ci_status_reads_the_branch_from_the_git_host() -> None:
    personal = WorkflowGitProvider()
    client, _ = _pr_client(personal=personal)

    response = client.get(
        "/api/v1/niuu/repos/prs/7/ci", params={"repo_url": NIUU_REPO, "branch": "feat/x"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "passing"}
    assert personal.calls == [("ci", NIUU_REPO, "feat/x")]


@pytest.mark.parametrize(
    ("method", "path", "params"),
    [
        ("get", "/api/v1/niuu/repos/prs", {"repo_url": NIUU_REPO}),
        ("get", "/api/v1/niuu/repos/prs/7", {"repo_url": NIUU_REPO}),
        ("post", "/api/v1/niuu/repos/prs/7/merge", {"repo_url": NIUU_REPO}),
        ("get", "/api/v1/niuu/repos/prs/7/ci", {"repo_url": NIUU_REPO, "branch": "dev"}),
    ],
)
def test_pr_routes_report_a_missing_repo_service(
    method: str, path: str, params: dict[str, str]
) -> None:
    app = FastAPI()
    app.include_router(create_repos_router(None))  # type: ignore[arg-type]
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id="regin", email="regin@example.test", tenant_id="default", roles=[]
    )

    response = getattr(TestClient(app), method)(path, params=params)

    assert response.status_code == 503
    assert response.json()["detail"] == "Repo service not available"

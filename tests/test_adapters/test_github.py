"""Tests for the GitHub git provider adapter."""

import pytest
import respx
from httpx import Response

from volundr.adapters.outbound.github import GitHubProvider
from volundr.domain.models import CIStatus, GitProviderType, PullRequestStatus
from volundr.domain.ports import GitAuthError, GitRepoNotFoundError


class TestGitHubProvider:
    """Tests for GitHubProvider."""

    @pytest.fixture
    def provider(self) -> GitHubProvider:
        """Create a GitHub provider."""
        return GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            token="test-token",
        )

    @pytest.fixture
    def provider_no_token(self) -> GitHubProvider:
        """Create a GitHub provider without token."""
        return GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            token=None,
        )

    @pytest.fixture
    def provider_enterprise(self) -> GitHubProvider:
        """Create a GitHub Enterprise provider."""
        return GitHubProvider(
            name="GitHub Enterprise",
            base_url="https://github.company.com/api/v3",
            token="enterprise-token",
        )

    def test_provider_type(self, provider: GitHubProvider):
        """Returns correct provider type."""
        assert provider.provider_type == GitProviderType.GITHUB

    def test_name(self, provider: GitHubProvider):
        """Returns correct provider name."""
        assert provider.name == "GitHub"

    def test_name_custom(self, provider_enterprise: GitHubProvider):
        """Returns custom provider name."""
        assert provider_enterprise.name == "GitHub Enterprise"

    @pytest.mark.parametrize(
        "url",
        [
            "github.com/org/repo",
            "https://github.com/org/repo",
            "git@github.com:org/repo.git",
        ],
    )
    def test_supports_github_urls(self, provider: GitHubProvider, url: str):
        """Supports GitHub URLs."""
        assert provider.supports(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "gitlab.com/org/repo",
            "https://bitbucket.org/org/repo",
            "not-a-valid-url",
        ],
    )
    def test_rejects_non_github_urls(self, provider: GitHubProvider, url: str):
        """Rejects non-GitHub URLs."""
        assert provider.supports(url) is False

    def test_enterprise_supports_own_urls(self, provider_enterprise: GitHubProvider):
        """GitHub Enterprise provider supports its own domain."""
        assert provider_enterprise.supports("https://github.company.com/org/repo") is True
        assert provider_enterprise.supports("git@github.company.com:org/repo.git") is True
        # Does not support github.com
        assert provider_enterprise.supports("https://github.com/org/repo") is False

    def test_parse_repo_valid_url(self, provider: GitHubProvider):
        """Parses valid GitHub URL into RepoInfo."""
        info = provider.parse_repo("https://github.com/anthropics/claude-code")

        assert info is not None
        assert info.provider == GitProviderType.GITHUB
        assert info.org == "anthropics"
        assert info.name == "claude-code"
        assert info.url == "https://github.com/anthropics/claude-code"

    def test_parse_repo_invalid_url(self, provider: GitHubProvider):
        """Returns None for invalid URLs."""
        info = provider.parse_repo("https://gitlab.com/org/repo")
        assert info is None

    def test_get_clone_url_with_token(self, provider: GitHubProvider):
        """Returns authenticated clone URL when token is set."""
        url = provider.get_clone_url("https://github.com/org/repo")

        assert url == "https://x-access-token:test-token@github.com/org/repo.git"

    def test_get_clone_url_without_token(self, provider_no_token: GitHubProvider):
        """Returns unauthenticated clone URL when no token."""
        url = provider_no_token.get_clone_url("https://github.com/org/repo")

        assert url == "https://github.com/org/repo.git"

    def test_get_clone_url_enterprise(self, provider_enterprise: GitHubProvider):
        """Returns authenticated clone URL for GitHub Enterprise."""
        url = provider_enterprise.get_clone_url("https://github.company.com/org/repo")

        assert url == "https://x-access-token:enterprise-token@github.company.com/org/repo.git"

    def test_get_clone_url_invalid(self, provider: GitHubProvider):
        """Returns None for invalid URLs."""
        url = provider.get_clone_url("https://gitlab.com/org/repo")
        assert url is None

    @pytest.mark.asyncio
    async def test_close(self, provider: GitHubProvider):
        """Close works without error."""
        await provider.close()
        # Can close multiple times
        await provider.close()


class TestGitHubProviderHTTP:
    """Tests for GitHubProvider HTTP operations."""

    @pytest.fixture
    def provider(self) -> GitHubProvider:
        """Create a GitHub provider."""
        return GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            token="test-token",
        )

    @pytest.mark.asyncio
    @respx.mock
    async def test_validate_repo_success(self, provider: GitHubProvider):
        """validate_repo returns True when repo exists."""
        respx.get("https://api.github.com/repos/org/repo").mock(
            return_value=Response(200, json={"id": 1, "name": "repo"})
        )

        result = await provider.validate_repo("https://github.com/org/repo")

        assert result is True
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_validate_repo_not_found(self, provider: GitHubProvider):
        """validate_repo returns False when repo doesn't exist."""
        respx.get("https://api.github.com/repos/org/repo").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )

        result = await provider.validate_repo("https://github.com/org/repo")

        assert result is False
        await provider.close()

    @pytest.mark.asyncio
    async def test_validate_repo_invalid_url(self, provider: GitHubProvider):
        """validate_repo returns False for invalid URLs."""
        result = await provider.validate_repo("https://gitlab.com/org/repo")

        assert result is False
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_validate_repo_server_error(self, provider: GitHubProvider):
        """validate_repo returns False on server error."""
        respx.get("https://api.github.com/repos/org/repo").mock(
            return_value=Response(500, json={"message": "Internal Server Error"})
        )

        result = await provider.validate_repo("https://github.com/org/repo")

        assert result is False
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_org(self, provider: GitHubProvider):
        """list_repos returns repos with default_branch and branches."""
        respx.get("https://api.github.com/orgs/myorg/repos").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "name": "repo1",
                        "clone_url": "https://github.com/myorg/repo1.git",
                        "html_url": "https://github.com/myorg/repo1",
                        "default_branch": "develop",
                    },
                    {
                        "name": "repo2",
                        "clone_url": "https://github.com/myorg/repo2.git",
                        "html_url": "https://github.com/myorg/repo2",
                        "default_branch": "main",
                    },
                ],
            )
        )
        respx.get("https://api.github.com/repos/myorg/repo1/branches").mock(
            return_value=Response(
                200,
                json=[{"name": "develop"}, {"name": "main"}, {"name": "feature/x"}],
            )
        )
        respx.get("https://api.github.com/repos/myorg/repo2/branches").mock(
            return_value=Response(
                200,
                json=[{"name": "main"}],
            )
        )

        repos = await provider.list_repos("myorg")

        assert len(repos) == 2
        assert repos[0].name == "repo1"
        assert repos[0].org == "myorg"
        assert repos[0].default_branch == "develop"
        assert repos[0].branches == ("develop", "main", "feature/x")
        assert repos[1].name == "repo2"
        assert repos[1].default_branch == "main"
        assert repos[1].branches == ("main",)
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_user_fallback_authenticated(self, provider: GitHubProvider):
        """list_repos uses /user/repos when authenticated and org 404s."""
        respx.get("https://api.github.com/orgs/myuser/repos").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )
        respx.get("https://api.github.com/user/repos").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "name": "repo1",
                        "clone_url": "https://github.com/myuser/repo1.git",
                        "html_url": "https://github.com/myuser/repo1",
                        "owner": {"login": "myuser"},
                    },
                    {
                        "name": "other-repo",
                        "clone_url": "https://github.com/other/other-repo.git",
                        "html_url": "https://github.com/other/other-repo",
                        "owner": {"login": "other"},
                    },
                ],
            )
        )

        repos = await provider.list_repos("myuser")

        assert len(repos) == 1
        assert repos[0].name == "repo1"
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_user_fallback_unauthenticated(self):
        """list_repos falls back to /users/{name}/repos without token."""
        provider = GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            token=None,
        )
        respx.get("https://api.github.com/orgs/myuser/repos").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )
        respx.get("https://api.github.com/users/myuser/repos").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "name": "repo1",
                        "clone_url": "https://github.com/myuser/repo1.git",
                        "html_url": "https://github.com/myuser/repo1",
                    },
                ],
            )
        )

        repos = await provider.list_repos("myuser")

        assert len(repos) == 1
        assert repos[0].name == "repo1"
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_empty(self, provider: GitHubProvider):
        """list_repos returns empty list when no repos found."""
        respx.get("https://api.github.com/orgs/empty/repos").mock(
            return_value=Response(200, json=[])
        )

        repos = await provider.list_repos("empty")

        assert repos == []
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_server_error(self, provider: GitHubProvider):
        """list_repos returns empty list on server error."""
        respx.get("https://api.github.com/orgs/error/repos").mock(
            return_value=Response(500, json={"message": "Internal Server Error"})
        )

        repos = await provider.list_repos("error")

        assert repos == []
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_paginates(self, provider: GitHubProvider):
        """list_repos follows Link header pagination to fetch all pages."""
        page2_url = "https://api.github.com/orgs/bigorg/repos?page=2"

        respx.get("https://api.github.com/orgs/bigorg/repos").mock(
            side_effect=[
                Response(
                    200,
                    json=[
                        {
                            "name": "repo1",
                            "clone_url": "https://github.com/bigorg/repo1.git",
                            "html_url": "https://github.com/bigorg/repo1",
                        },
                    ],
                    headers={"link": f'<{page2_url}>; rel="next"'},
                ),
                Response(
                    200,
                    json=[
                        {
                            "name": "repo2",
                            "clone_url": "https://github.com/bigorg/repo2.git",
                            "html_url": "https://github.com/bigorg/repo2",
                        },
                    ],
                ),
            ]
        )

        repos = await provider.list_repos("bigorg")

        assert len(repos) == 2
        assert repos[0].name == "repo1"
        assert repos[1].name == "repo2"
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_includes_type_all(self, provider: GitHubProvider):
        """list_repos passes type=all to include private repos."""
        route = respx.get("https://api.github.com/orgs/myorg/repos").mock(
            return_value=Response(200, json=[])
        )

        await provider.list_repos("myorg")

        assert route.called
        request = route.calls[0].request
        assert "type=all" in str(request.url)
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_branch_fetch_404_logs_warning(self, provider: GitHubProvider):
        """Branch fetch 404 in list_repos logs warning and returns empty branches."""
        respx.get("https://api.github.com/orgs/myorg/repos").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "name": "private-repo",
                        "clone_url": "https://github.com/myorg/private-repo.git",
                        "html_url": "https://github.com/myorg/private-repo",
                    },
                ],
            )
        )
        respx.get("https://api.github.com/repos/myorg/private-repo/branches").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )
        respx.get("https://api.github.com/user").mock(
            return_value=Response(
                200,
                json={"login": "testuser"},
                headers={"x-oauth-scopes": "public_repo"},
            )
        )

        repos = await provider.list_repos("myorg")

        assert len(repos) == 1
        assert repos[0].branches == ()
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_branch_fetch_401_logs_warning(self, provider: GitHubProvider):
        """Branch fetch 401 in list_repos logs warning with scope hint."""
        respx.get("https://api.github.com/orgs/myorg/repos").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "name": "repo1",
                        "clone_url": "https://github.com/myorg/repo1.git",
                        "html_url": "https://github.com/myorg/repo1",
                    },
                ],
            )
        )
        respx.get("https://api.github.com/repos/myorg/repo1/branches").mock(
            return_value=Response(401, json={"message": "Bad credentials"})
        )

        repos = await provider.list_repos("myorg")

        assert len(repos) == 1
        assert repos[0].branches == ()
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_user_fallback_expanded_affiliation(self, provider: GitHubProvider):
        """/user/repos fallback includes collaborator and org_member affiliation."""
        respx.get("https://api.github.com/orgs/myuser/repos").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )
        route = respx.get("https://api.github.com/user/repos").mock(
            return_value=Response(200, json=[])
        )

        await provider.list_repos("myuser")

        assert route.called
        request_url = str(route.calls[0].request.url)
        assert "collaborator" in request_url
        assert "organization_member" in request_url
        await provider.close()


class TestGitHubProviderWorkflow:
    """Tests for GitHubProvider workflow (PR/CI) operations."""

    @pytest.fixture
    def provider(self) -> GitHubProvider:
        """Create a GitHub provider."""
        return GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            token="test-token",
        )

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_branch(self, provider: GitHubProvider):
        """create_branch creates a ref from the source branch SHA."""
        respx.get("https://api.github.com/repos/user/repo/git/ref/heads/main").mock(
            return_value=Response(200, json={"object": {"sha": "abc123"}})
        )
        respx.post("https://api.github.com/repos/user/repo/git/refs").mock(
            return_value=Response(201, json={"ref": "refs/heads/feature/test"})
        )

        result = await provider.create_branch(
            "https://github.com/user/repo", "feature/test", "main"
        )

        assert result is True
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_branch_user_repo(self, provider: GitHubProvider):
        """create_branch works with user repos (no org)."""
        respx.get("https://api.github.com/repos/myuser/myrepo/git/ref/heads/main").mock(
            return_value=Response(200, json={"object": {"sha": "def456"}})
        )
        respx.post("https://api.github.com/repos/myuser/myrepo/git/refs").mock(
            return_value=Response(201, json={"ref": "refs/heads/feature/x"})
        )

        result = await provider.create_branch(
            "https://github.com/myuser/myrepo", "feature/x", "main"
        )

        assert result is True
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_branch_source_not_found(self, provider: GitHubProvider):
        """create_branch returns False when source branch doesn't exist."""
        respx.get("https://api.github.com/repos/user/repo/git/ref/heads/main").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )

        result = await provider.create_branch(
            "https://github.com/user/repo", "feature/test", "main"
        )

        assert result is False
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_pull_request(self, provider: GitHubProvider):
        """create_pull_request creates a PR."""
        respx.post("https://api.github.com/repos/user/repo/pulls").mock(
            return_value=Response(
                201,
                json={
                    "number": 42,
                    "title": "My PR",
                    "html_url": "https://github.com/user/repo/pull/42",
                    "state": "open",
                    "body": "Description",
                    "head": {"ref": "feature/test"},
                    "base": {"ref": "main"},
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z",
                },
            )
        )

        pr = await provider.create_pull_request(
            "https://github.com/user/repo",
            "My PR",
            "Description",
            "feature/test",
            "main",
        )

        assert pr.number == 42
        assert pr.status == PullRequestStatus.OPEN
        assert pr.provider == GitProviderType.GITHUB
        assert pr.source_branch == "feature/test"
        assert pr.target_branch == "main"
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_pull_request_user_repo(self, provider: GitHubProvider):
        """create_pull_request works for user repos (not just orgs)."""
        respx.post("https://api.github.com/repos/alice/personal-project/pulls").mock(
            return_value=Response(
                201,
                json={
                    "number": 7,
                    "title": "User PR",
                    "html_url": "https://github.com/alice/personal-project/pull/7",
                    "state": "open",
                    "body": "From user repo",
                    "head": {"ref": "fix/typo"},
                    "base": {"ref": "main"},
                    "created_at": "2025-06-01T00:00:00Z",
                    "updated_at": "2025-06-01T00:00:00Z",
                },
            )
        )

        pr = await provider.create_pull_request(
            "https://github.com/alice/personal-project",
            "User PR",
            "From user repo",
            "fix/typo",
            "main",
        )

        assert pr.number == 7
        assert pr.repo_url == "https://github.com/alice/personal-project"
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_pull_request_with_labels(self, provider: GitHubProvider):
        """create_pull_request adds labels if provided."""
        respx.post("https://api.github.com/repos/user/repo/pulls").mock(
            return_value=Response(
                201,
                json={
                    "number": 42,
                    "title": "My PR",
                    "html_url": "https://github.com/user/repo/pull/42",
                    "state": "open",
                    "head": {"ref": "feature/test"},
                    "base": {"ref": "main"},
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z",
                },
            )
        )
        label_route = respx.post("https://api.github.com/repos/user/repo/issues/42/labels").mock(
            return_value=Response(200, json=[])
        )

        await provider.create_pull_request(
            "https://github.com/user/repo",
            "My PR",
            "Description",
            "feature/test",
            "main",
            labels=["auto-merge"],
        )

        assert label_route.called
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_pull_request_failure(self, provider: GitHubProvider):
        """create_pull_request raises on API failure."""
        respx.post("https://api.github.com/repos/user/repo/pulls").mock(
            return_value=Response(422, json={"message": "Validation Failed"})
        )

        with pytest.raises(RuntimeError, match="Failed to create PR"):
            await provider.create_pull_request(
                "https://github.com/user/repo",
                "My PR",
                "Description",
                "feature/test",
                "main",
            )
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pull_request(self, provider: GitHubProvider):
        """get_pull_request returns a PR."""
        respx.get("https://api.github.com/repos/user/repo/pulls/42").mock(
            return_value=Response(
                200,
                json={
                    "number": 42,
                    "title": "My PR",
                    "html_url": "https://github.com/user/repo/pull/42",
                    "state": "open",
                    "head": {"ref": "feature/test"},
                    "base": {"ref": "main"},
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z",
                },
            )
        )

        pr = await provider.get_pull_request("https://github.com/user/repo", 42)

        assert pr is not None
        assert pr.number == 42
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pull_request_not_found(self, provider: GitHubProvider):
        """get_pull_request returns None when not found."""
        respx.get("https://api.github.com/repos/user/repo/pulls/999").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )

        pr = await provider.get_pull_request("https://github.com/user/repo", 999)

        assert pr is None
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pull_request_merged(self, provider: GitHubProvider):
        """get_pull_request recognizes merged PRs."""
        respx.get("https://api.github.com/repos/user/repo/pulls/42").mock(
            return_value=Response(
                200,
                json={
                    "number": 42,
                    "title": "Merged PR",
                    "html_url": "https://github.com/user/repo/pull/42",
                    "state": "closed",
                    "merged": True,
                    "head": {"ref": "feature/test"},
                    "base": {"ref": "main"},
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z",
                },
            )
        )

        pr = await provider.get_pull_request("https://github.com/user/repo", 42)

        assert pr is not None
        assert pr.status == PullRequestStatus.MERGED
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_pull_requests(self, provider: GitHubProvider):
        """list_pull_requests returns PRs."""
        respx.get("https://api.github.com/repos/user/repo/pulls").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "number": 1,
                        "title": "PR 1",
                        "html_url": "https://github.com/user/repo/pull/1",
                        "state": "open",
                        "head": {"ref": "feature/1"},
                        "base": {"ref": "main"},
                        "created_at": "2025-01-01T00:00:00Z",
                        "updated_at": "2025-01-01T00:00:00Z",
                    },
                    {
                        "number": 2,
                        "title": "PR 2",
                        "html_url": "https://github.com/user/repo/pull/2",
                        "state": "open",
                        "head": {"ref": "feature/2"},
                        "base": {"ref": "main"},
                        "created_at": "2025-01-01T00:00:00Z",
                        "updated_at": "2025-01-01T00:00:00Z",
                    },
                ],
            )
        )

        prs = await provider.list_pull_requests("https://github.com/user/repo", "open")

        assert len(prs) == 2
        assert prs[0].number == 1
        assert prs[1].number == 2
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_merge_pull_request_success(self, provider: GitHubProvider):
        """merge_pull_request returns True on success."""
        respx.put("https://api.github.com/repos/user/repo/pulls/42/merge").mock(
            return_value=Response(200, json={"merged": True})
        )

        result = await provider.merge_pull_request("https://github.com/user/repo", 42, "squash")

        assert result is True
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_merge_pull_request_failure(self, provider: GitHubProvider):
        """merge_pull_request returns False on failure."""
        respx.put("https://api.github.com/repos/user/repo/pulls/42/merge").mock(
            return_value=Response(405, json={"message": "Not mergeable"})
        )

        result = await provider.merge_pull_request("https://github.com/user/repo", 42, "squash")

        assert result is False
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_ci_status_success(self, provider: GitHubProvider):
        """get_ci_status returns PASSING for success state."""
        respx.get("https://api.github.com/repos/user/repo/commits/feature%2Ftest/status").mock(
            return_value=Response(200, json={"state": "success"})
        )

        status = await provider.get_ci_status("https://github.com/user/repo", "feature/test")

        assert status == CIStatus.PASSING
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_ci_status_failure(self, provider: GitHubProvider):
        """get_ci_status returns FAILING for failure state."""
        respx.get("https://api.github.com/repos/user/repo/commits/main/status").mock(
            return_value=Response(200, json={"state": "failure"})
        )

        status = await provider.get_ci_status("https://github.com/user/repo", "main")

        assert status == CIStatus.FAILING
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_ci_status_pending(self, provider: GitHubProvider):
        """get_ci_status returns PENDING for pending state."""
        respx.get("https://api.github.com/repos/user/repo/commits/main/status").mock(
            return_value=Response(200, json={"state": "pending"})
        )

        status = await provider.get_ci_status("https://github.com/user/repo", "main")

        assert status == CIStatus.PENDING
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_ci_status_unknown_on_error(self, provider: GitHubProvider):
        """get_ci_status returns UNKNOWN on API error."""
        respx.get("https://api.github.com/repos/user/repo/commits/main/status").mock(
            return_value=Response(500, json={})
        )

        status = await provider.get_ci_status("https://github.com/user/repo", "main")

        assert status == CIStatus.UNKNOWN
        await provider.close()

    def test_create_branch_invalid_url(self, provider: GitHubProvider):
        """create_branch returns False for unsupported URLs."""
        import asyncio

        result = asyncio.run(provider.create_branch("https://gitlab.com/org/repo", "test", "main"))
        assert result is False


class TestGitHubProviderConstructor:
    """Tests for GitHubProvider constructor edge cases."""

    def test_extra_kwargs_ignored(self):
        """Extra kwargs from dynamic adapter pattern don't crash."""
        provider = GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            token="tok",
            orgs=("org1",),
            some_extra_key="value",
            another="123",
        )
        assert provider.name == "GitHub"
        assert provider.orgs == ("org1",)

    def test_orgs_from_comma_separated_string(self):
        """Orgs can be provided as a comma-separated string."""
        provider = GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            orgs="niuulabs, anthropic, openai",
        )
        assert provider.orgs == ("niuulabs", "anthropic", "openai")

    def test_orgs_from_string_strips_whitespace(self):
        """Whitespace is stripped from comma-separated orgs."""
        provider = GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            orgs="  org1 ,  , org2  ",
        )
        assert provider.orgs == ("org1", "org2")

    def test_orgs_from_empty_string(self):
        """Empty string produces empty tuple."""
        provider = GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            orgs="",
        )
        assert provider.orgs == ()

    def test_orgs_from_list(self):
        """Orgs can be provided as a list."""
        provider = GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            orgs=["a", "b"],
        )
        assert provider.orgs == ("a", "b")

    def test_orgs_from_tuple(self):
        """Orgs can be provided as a tuple (original behavior)."""
        provider = GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            orgs=("x", "y"),
        )
        assert provider.orgs == ("x", "y")


class TestGitHubBranchErrors:
    """Tests for branch listing error handling and diagnostics."""

    @pytest.fixture
    def provider(self) -> GitHubProvider:
        """Create a GitHub provider."""
        return GitHubProvider(
            name="GitHub",
            base_url="https://api.github.com",
            token="test-token",
        )

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_branches_404_includes_scope_hint(self, provider: GitHubProvider):
        """list_branches 404 error includes token scope hint."""
        respx.get("https://api.github.com/repos/org/private-repo/branches").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )

        with pytest.raises(GitRepoNotFoundError, match="contents:read"):
            await provider.list_branches("https://github.com/org/private-repo")
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_branches_401_includes_scope_hint(self, provider: GitHubProvider):
        """list_branches 401 error includes token scope hint."""
        respx.get("https://api.github.com/repos/org/repo/branches").mock(
            return_value=Response(401, json={"message": "Bad credentials"})
        )

        with pytest.raises(GitAuthError, match="contents:read"):
            await provider.list_branches("https://github.com/org/repo")
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_branches_403_includes_scope_hint(self, provider: GitHubProvider):
        """list_branches 403 error includes token scope hint."""
        respx.get("https://api.github.com/repos/org/repo/branches").mock(
            return_value=Response(403, json={"message": "Forbidden"})
        )

        with pytest.raises(GitAuthError, match="repo.*scope"):
            await provider.list_branches("https://github.com/org/repo")
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_check_token_scopes_returns_scopes(self, provider: GitHubProvider):
        """Token scope check returns scopes from X-OAuth-Scopes header."""
        respx.get("https://api.github.com/user").mock(
            return_value=Response(
                200,
                json={"login": "testuser"},
                headers={"x-oauth-scopes": "public_repo, read:org"},
            )
        )

        client = await provider._get_client()
        scopes = await provider._check_token_scopes(client)

        assert scopes == ["public_repo", "read:org"]
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_check_token_scopes_only_called_once(self, provider: GitHubProvider):
        """Token scope check is only performed once."""
        route = respx.get("https://api.github.com/user").mock(
            return_value=Response(
                200,
                json={"login": "testuser"},
                headers={"x-oauth-scopes": "repo"},
            )
        )

        client = await provider._get_client()
        await provider._check_token_scopes(client)
        await provider._check_token_scopes(client)

        assert route.call_count == 1
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_check_token_scopes_no_header(self, provider: GitHubProvider):
        """Token scope check handles missing X-OAuth-Scopes header."""
        respx.get("https://api.github.com/user").mock(
            return_value=Response(200, json={"login": "testuser"})
        )

        client = await provider._get_client()
        scopes = await provider._check_token_scopes(client)

        assert scopes == []
        await provider.close()


class TestGitHubEverythingTheTokenReaches:
    """An account added without organisations lists all it can reach."""

    @pytest.fixture
    def provider(self) -> GitHubProvider:
        return GitHubProvider(name="github-work", base_url="https://api.github.com", token="t")

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_org_lists_own_and_member_repositories(self, provider) -> None:
        listing = respx.get("https://api.github.com/user/repos").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "name": "volundr",
                        "owner": {"login": "niuulabs"},
                        "html_url": "https://github.com/niuulabs/volundr",
                        "default_branch": "dev",
                    },
                    {
                        "name": "dotfiles",
                        "owner": {"login": "jve"},
                        "html_url": "https://github.com/jve/dotfiles",
                        "default_branch": "main",
                    },
                ],
            )
        )
        respx.get(url__regex=r"https://api\.github\.com/repos/.*/branches").mock(
            return_value=Response(200, json=[{"name": "main"}])
        )

        repos = await provider.list_repos("")

        sent = listing.calls.last.request.url.params
        assert sent["affiliation"] == "owner,collaborator,organization_member"
        assert [(r.org, r.name) for r in repos] == [("niuulabs", "volundr"), ("jve", "dotfiles")]
        assert repos[0].clone_url == "https://github.com/niuulabs/volundr.git"
        assert repos[1].branches == ("main",)

    @pytest.mark.asyncio
    async def test_empty_org_without_a_token_is_refused(self) -> None:
        provider = GitHubProvider(name="anon", base_url="https://api.github.com", token="")
        with pytest.raises(ValueError, match="needs a token"):
            await provider.list_repos("")

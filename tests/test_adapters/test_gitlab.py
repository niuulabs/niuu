"""Tests for the GitLab git provider adapter."""

import pytest
import respx
from httpx import Response

from volundr.adapters.outbound.gitlab import GitLabProvider
from volundr.domain.models import CIStatus, GitProviderType, PullRequestStatus


class TestGitLabProvider:
    """Tests for GitLabProvider."""

    @pytest.fixture
    def provider(self) -> GitLabProvider:
        """Create a GitLab provider for gitlab.com."""
        return GitLabProvider(
            name="GitLab",
            base_url="https://gitlab.com",
            token="test-token",
        )

    @pytest.fixture
    def provider_no_token(self) -> GitLabProvider:
        """Create a GitLab provider without token."""
        return GitLabProvider(
            name="GitLab",
            base_url="https://gitlab.com",
            token=None,
        )

    @pytest.fixture
    def self_hosted_provider(self) -> GitLabProvider:
        """Create a self-hosted GitLab provider."""
        return GitLabProvider(
            name="Internal GitLab",
            base_url="https://git.company.com",
            token="internal-token",
        )

    def test_provider_type(self, provider: GitLabProvider):
        """Returns correct provider type."""
        assert provider.provider_type == GitProviderType.GITLAB

    def test_name(self, provider: GitLabProvider):
        """Returns correct provider name."""
        assert provider.name == "GitLab"

    def test_self_hosted_name(self, self_hosted_provider: GitLabProvider):
        """Returns custom name for self-hosted."""
        assert self_hosted_provider.name == "Internal GitLab"

    def test_host(self, provider: GitLabProvider):
        """Returns correct host."""
        assert provider.host == "gitlab.com"

    def test_self_hosted_host(self, self_hosted_provider: GitLabProvider):
        """Returns correct host for self-hosted."""
        assert self_hosted_provider.host == "git.company.com"

    @pytest.mark.parametrize(
        "url",
        [
            "gitlab.com/org/repo",
            "https://gitlab.com/org/repo",
            "https://gitlab.com/org/repo.git",
            "git@gitlab.com:org/repo.git",
        ],
    )
    def test_supports_gitlab_urls(self, provider: GitLabProvider, url: str):
        """Supports GitLab.com URLs."""
        assert provider.supports(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "git.company.com/org/repo",
            "https://git.company.com/org/repo",
        ],
    )
    def test_supports_self_hosted_urls(self, self_hosted_provider: GitLabProvider, url: str):
        """Supports self-hosted GitLab URLs."""
        assert self_hosted_provider.supports(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "github.com/org/repo",
            "https://github.com/org/repo",
            "not-a-valid-url",
        ],
    )
    def test_rejects_non_gitlab_urls(self, provider: GitLabProvider, url: str):
        """Rejects non-GitLab URLs."""
        assert provider.supports(url) is False

    def test_self_hosted_rejects_gitlab_com(self, self_hosted_provider: GitLabProvider):
        """Self-hosted provider rejects gitlab.com URLs."""
        assert self_hosted_provider.supports("https://gitlab.com/org/repo") is False

    def test_parse_repo_valid_url(self, provider: GitLabProvider):
        """Parses valid GitLab URL into RepoInfo."""
        info = provider.parse_repo("https://gitlab.com/mygroup/myproject")

        assert info is not None
        assert info.provider == GitProviderType.GITLAB
        assert info.org == "mygroup"
        assert info.name == "myproject"
        assert info.url == "https://gitlab.com/mygroup/myproject"

    def test_parse_repo_invalid_url(self, provider: GitLabProvider):
        """Returns None for invalid URLs."""
        info = provider.parse_repo("https://github.com/org/repo")
        assert info is None

    def test_get_clone_url_with_token(self, provider: GitLabProvider):
        """Returns authenticated clone URL when token is set."""
        url = provider.get_clone_url("https://gitlab.com/org/repo")

        assert url == "https://oauth2:test-token@gitlab.com/org/repo.git"

    def test_get_clone_url_without_token(self, provider_no_token: GitLabProvider):
        """Returns unauthenticated clone URL when no token."""
        url = provider_no_token.get_clone_url("https://gitlab.com/org/repo")

        assert url == "https://gitlab.com/org/repo.git"

    def test_get_clone_url_self_hosted(self, self_hosted_provider: GitLabProvider):
        """Returns authenticated clone URL for self-hosted."""
        url = self_hosted_provider.get_clone_url("https://git.company.com/team/project")

        assert url == "https://oauth2:internal-token@git.company.com/team/project.git"

    def test_get_clone_url_invalid(self, provider: GitLabProvider):
        """Returns None for invalid URLs."""
        url = provider.get_clone_url("https://github.com/org/repo")
        assert url is None

    @pytest.mark.asyncio
    async def test_close(self, provider: GitLabProvider):
        """Close works without error."""
        await provider.close()
        # Can close multiple times
        await provider.close()


class TestMultipleGitLabInstances:
    """Tests for multiple GitLab instances."""

    def test_separate_instances_support_different_hosts(self):
        """Multiple instances support their own hosts."""
        gitlab_com = GitLabProvider(
            name="GitLab.com",
            base_url="https://gitlab.com",
            token="token1",
        )
        internal = GitLabProvider(
            name="Internal",
            base_url="https://git.company.com",
            token="token2",
        )

        # gitlab.com provider
        assert gitlab_com.supports("https://gitlab.com/org/repo") is True
        assert gitlab_com.supports("https://git.company.com/org/repo") is False

        # internal provider
        assert internal.supports("https://git.company.com/org/repo") is True
        assert internal.supports("https://gitlab.com/org/repo") is False

    def test_clone_urls_use_correct_tokens(self):
        """Each instance uses its own token in clone URLs."""
        gitlab_com = GitLabProvider(
            name="GitLab.com",
            base_url="https://gitlab.com",
            token="public-token",
        )
        internal = GitLabProvider(
            name="Internal",
            base_url="https://git.company.com",
            token="private-token",
        )

        url1 = gitlab_com.get_clone_url("https://gitlab.com/org/repo")
        url2 = internal.get_clone_url("https://git.company.com/team/project")

        assert "public-token" in url1
        assert "private-token" in url2
        assert "gitlab.com" in url1
        assert "git.company.com" in url2


class TestGitLabProviderHTTP:
    """Tests for GitLabProvider HTTP operations."""

    @pytest.fixture
    def provider(self) -> GitLabProvider:
        """Create a GitLab provider."""
        return GitLabProvider(
            name="GitLab",
            base_url="https://gitlab.com",
            token="test-token",
        )

    @pytest.mark.asyncio
    @respx.mock
    async def test_validate_repo_success(self, provider: GitLabProvider):
        """validate_repo returns True when repo exists."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo").mock(
            return_value=Response(200, json={"id": 1, "name": "repo"})
        )

        result = await provider.validate_repo("https://gitlab.com/org/repo")

        assert result is True
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_validate_repo_not_found(self, provider: GitLabProvider):
        """validate_repo returns False when repo doesn't exist."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )

        result = await provider.validate_repo("https://gitlab.com/org/repo")

        assert result is False
        await provider.close()

    @pytest.mark.asyncio
    async def test_validate_repo_invalid_url(self, provider: GitLabProvider):
        """validate_repo returns False for invalid URLs."""
        result = await provider.validate_repo("https://github.com/org/repo")

        assert result is False
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_validate_repo_server_error(self, provider: GitLabProvider):
        """validate_repo returns False on server error."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo").mock(
            return_value=Response(500, json={"message": "Internal Server Error"})
        )

        result = await provider.validate_repo("https://gitlab.com/org/repo")

        assert result is False
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_group(self, provider: GitLabProvider):
        """list_repos returns repos with default_branch and branches."""
        respx.get("https://gitlab.com/api/v4/groups/mygroup/projects").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "path": "repo1",
                        "namespace": {"path": "mygroup"},
                        "http_url_to_repo": "https://gitlab.com/mygroup/repo1.git",
                        "web_url": "https://gitlab.com/mygroup/repo1",
                        "default_branch": "develop",
                    },
                    {
                        "path": "repo2",
                        "namespace": {"path": "mygroup"},
                        "http_url_to_repo": "https://gitlab.com/mygroup/repo2.git",
                        "web_url": "https://gitlab.com/mygroup/repo2",
                        "default_branch": "main",
                    },
                ],
            )
        )
        respx.get("https://gitlab.com/api/v4/projects/mygroup%2Frepo1/repository/branches").mock(
            return_value=Response(
                200,
                json=[{"name": "develop"}, {"name": "main"}, {"name": "feature/y"}],
            )
        )
        respx.get("https://gitlab.com/api/v4/projects/mygroup%2Frepo2/repository/branches").mock(
            return_value=Response(
                200,
                json=[{"name": "main"}],
            )
        )

        repos = await provider.list_repos("mygroup")

        assert len(repos) == 2
        assert repos[0].name == "repo1"
        assert repos[0].org == "mygroup"
        assert repos[0].default_branch == "develop"
        assert repos[0].branches == ("develop", "main", "feature/y")
        assert repos[1].name == "repo2"
        assert repos[1].default_branch == "main"
        assert repos[1].branches == ("main",)
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_user_fallback(self, provider: GitLabProvider):
        """list_repos falls back to user endpoint when group not found."""
        respx.get("https://gitlab.com/api/v4/groups/myuser/projects").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )
        respx.get("https://gitlab.com/api/v4/users/myuser/projects").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "path": "repo1",
                        "namespace": {"path": "myuser"},
                        "http_url_to_repo": "https://gitlab.com/myuser/repo1.git",
                        "web_url": "https://gitlab.com/myuser/repo1",
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
    async def test_list_repos_empty(self, provider: GitLabProvider):
        """list_repos returns empty list when no repos found."""
        respx.get("https://gitlab.com/api/v4/groups/empty/projects").mock(
            return_value=Response(200, json=[])
        )

        repos = await provider.list_repos("empty")

        assert repos == []
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_server_error(self, provider: GitLabProvider):
        """list_repos returns empty list on server error."""
        respx.get("https://gitlab.com/api/v4/groups/error/projects").mock(
            return_value=Response(500, json={"message": "Internal Server Error"})
        )

        repos = await provider.list_repos("error")

        assert repos == []
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_repos_paginates(self, provider: GitLabProvider):
        """list_repos follows x-next-page header pagination to fetch all pages."""
        respx.get("https://gitlab.com/api/v4/groups/biggroup/projects").mock(
            side_effect=[
                Response(
                    200,
                    json=[
                        {
                            "path": "repo1",
                            "namespace": {"path": "biggroup"},
                            "http_url_to_repo": "https://gitlab.com/biggroup/repo1.git",
                            "web_url": "https://gitlab.com/biggroup/repo1",
                        },
                    ],
                    headers={"x-next-page": "2"},
                ),
                Response(
                    200,
                    json=[
                        {
                            "path": "repo2",
                            "namespace": {"path": "biggroup"},
                            "http_url_to_repo": "https://gitlab.com/biggroup/repo2.git",
                            "web_url": "https://gitlab.com/biggroup/repo2",
                        },
                    ],
                    headers={},
                ),
            ]
        )

        repos = await provider.list_repos("biggroup")

        assert len(repos) == 2
        assert repos[0].name == "repo1"
        assert repos[1].name == "repo2"
        await provider.close()


class TestGitLabProviderWorkflow:
    """Tests for GitLabProvider workflow (MR/pipeline) operations."""

    @pytest.fixture
    def provider(self) -> GitLabProvider:
        """Create a GitLab provider."""
        return GitLabProvider(
            name="GitLab",
            base_url="https://gitlab.com",
            token="test-token",
        )

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_branch(self, provider: GitLabProvider):
        """create_branch creates a branch via the API."""
        respx.post("https://gitlab.com/api/v4/projects/org%2Frepo/repository/branches").mock(
            return_value=Response(201, json={"name": "feature/test"})
        )

        result = await provider.create_branch("https://gitlab.com/org/repo", "feature/test", "main")

        assert result is True
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_branch_failure(self, provider: GitLabProvider):
        """create_branch returns False on failure."""
        respx.post("https://gitlab.com/api/v4/projects/org%2Frepo/repository/branches").mock(
            return_value=Response(400, json={"message": "Branch already exists"})
        )

        result = await provider.create_branch("https://gitlab.com/org/repo", "feature/test", "main")

        assert result is False
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_pull_request(self, provider: GitLabProvider):
        """create_pull_request creates a merge request."""
        respx.post("https://gitlab.com/api/v4/projects/org%2Frepo/merge_requests").mock(
            return_value=Response(
                201,
                json={
                    "iid": 10,
                    "title": "My MR",
                    "web_url": "https://gitlab.com/org/repo/-/merge_requests/10",
                    "state": "opened",
                    "description": "Test MR",
                    "source_branch": "feature/test",
                    "target_branch": "main",
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z",
                },
            )
        )

        pr = await provider.create_pull_request(
            "https://gitlab.com/org/repo",
            "My MR",
            "Test MR",
            "feature/test",
            "main",
        )

        assert pr.number == 10
        assert pr.status == PullRequestStatus.OPEN
        assert pr.provider == GitProviderType.GITLAB
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_pull_request_with_labels(self, provider: GitLabProvider):
        """create_pull_request sends labels as comma-separated string."""
        route = respx.post("https://gitlab.com/api/v4/projects/org%2Frepo/merge_requests").mock(
            return_value=Response(
                201,
                json={
                    "iid": 10,
                    "title": "MR",
                    "web_url": "https://gitlab.com/org/repo/-/merge_requests/10",
                    "state": "opened",
                    "source_branch": "feature/test",
                    "target_branch": "main",
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z",
                },
            )
        )

        await provider.create_pull_request(
            "https://gitlab.com/org/repo",
            "MR",
            "Desc",
            "feature/test",
            "main",
            labels=["auto-merge", "bot"],
        )

        assert route.called
        import json

        body = json.loads(route.calls[0].request.content)
        assert body["labels"] == "auto-merge,bot"
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_create_pull_request_failure(self, provider: GitLabProvider):
        """create_pull_request raises on API failure."""
        respx.post("https://gitlab.com/api/v4/projects/org%2Frepo/merge_requests").mock(
            return_value=Response(422, json={"message": "Validation error"})
        )

        with pytest.raises(RuntimeError, match="Failed to create MR"):
            await provider.create_pull_request(
                "https://gitlab.com/org/repo",
                "MR",
                "Desc",
                "feature/test",
                "main",
            )
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pull_request(self, provider: GitLabProvider):
        """get_pull_request returns a MR by IID."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo/merge_requests/10").mock(
            return_value=Response(
                200,
                json={
                    "iid": 10,
                    "title": "My MR",
                    "web_url": "https://gitlab.com/org/repo/-/merge_requests/10",
                    "state": "opened",
                    "source_branch": "feature/test",
                    "target_branch": "main",
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z",
                },
            )
        )

        pr = await provider.get_pull_request("https://gitlab.com/org/repo", 10)

        assert pr is not None
        assert pr.number == 10
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pull_request_not_found(self, provider: GitLabProvider):
        """get_pull_request returns None when not found."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo/merge_requests/999").mock(
            return_value=Response(404, json={"message": "Not Found"})
        )

        pr = await provider.get_pull_request("https://gitlab.com/org/repo", 999)

        assert pr is None
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_pull_request_merged(self, provider: GitLabProvider):
        """get_pull_request recognizes merged MRs."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo/merge_requests/10").mock(
            return_value=Response(
                200,
                json={
                    "iid": 10,
                    "title": "Merged MR",
                    "web_url": "https://gitlab.com/org/repo/-/merge_requests/10",
                    "state": "merged",
                    "source_branch": "feature/test",
                    "target_branch": "main",
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z",
                },
            )
        )

        pr = await provider.get_pull_request("https://gitlab.com/org/repo", 10)

        assert pr is not None
        assert pr.status == PullRequestStatus.MERGED
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_list_pull_requests(self, provider: GitLabProvider):
        """list_pull_requests returns MRs."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo/merge_requests").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "iid": 1,
                        "title": "MR 1",
                        "web_url": "https://gitlab.com/org/repo/-/merge_requests/1",
                        "state": "opened",
                        "source_branch": "feature/1",
                        "target_branch": "main",
                        "created_at": "2025-01-01T00:00:00Z",
                        "updated_at": "2025-01-01T00:00:00Z",
                    },
                ],
            )
        )

        prs = await provider.list_pull_requests("https://gitlab.com/org/repo", "open")

        assert len(prs) == 1
        assert prs[0].number == 1
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_merge_pull_request_success(self, provider: GitLabProvider):
        """merge_pull_request returns True on success."""
        respx.put("https://gitlab.com/api/v4/projects/org%2Frepo/merge_requests/10/merge").mock(
            return_value=Response(200, json={"state": "merged"})
        )

        result = await provider.merge_pull_request("https://gitlab.com/org/repo", 10, "squash")

        assert result is True
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_merge_pull_request_failure(self, provider: GitLabProvider):
        """merge_pull_request returns False on failure."""
        respx.put("https://gitlab.com/api/v4/projects/org%2Frepo/merge_requests/10/merge").mock(
            return_value=Response(405, json={"message": "Not mergeable"})
        )

        result = await provider.merge_pull_request("https://gitlab.com/org/repo", 10, "squash")

        assert result is False
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_ci_status_success(self, provider: GitLabProvider):
        """get_ci_status returns PASSING for success pipeline."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo/pipelines").mock(
            return_value=Response(
                200,
                json=[{"status": "success"}],
            )
        )

        status = await provider.get_ci_status("https://gitlab.com/org/repo", "main")

        assert status == CIStatus.PASSING
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_ci_status_failed(self, provider: GitLabProvider):
        """get_ci_status returns FAILING for failed pipeline."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo/pipelines").mock(
            return_value=Response(
                200,
                json=[{"status": "failed"}],
            )
        )

        status = await provider.get_ci_status("https://gitlab.com/org/repo", "main")

        assert status == CIStatus.FAILING
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_ci_status_running(self, provider: GitLabProvider):
        """get_ci_status returns PENDING for running pipeline."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo/pipelines").mock(
            return_value=Response(
                200,
                json=[{"status": "running"}],
            )
        )

        status = await provider.get_ci_status("https://gitlab.com/org/repo", "main")

        assert status == CIStatus.PENDING
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_ci_status_no_pipelines(self, provider: GitLabProvider):
        """get_ci_status returns UNKNOWN when no pipelines exist."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo/pipelines").mock(
            return_value=Response(200, json=[])
        )

        status = await provider.get_ci_status("https://gitlab.com/org/repo", "main")

        assert status == CIStatus.UNKNOWN
        await provider.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_ci_status_api_error(self, provider: GitLabProvider):
        """get_ci_status returns UNKNOWN on API error."""
        respx.get("https://gitlab.com/api/v4/projects/org%2Frepo/pipelines").mock(
            return_value=Response(500, json={})
        )

        status = await provider.get_ci_status("https://gitlab.com/org/repo", "main")

        assert status == CIStatus.UNKNOWN
        await provider.close()


class TestGitLabEverythingTheTokenReaches:
    """An account added without a group lists every project it is a member of."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_group_lists_membership_projects(self) -> None:
        provider = GitLabProvider(name="gitlab-work", base_url="https://gitlab.com", token="t")
        listing = respx.get("https://gitlab.com/api/v4/projects").mock(
            return_value=Response(
                200,
                json=[
                    {
                        "path": "skuld",
                        "namespace": {"path": "niuulabs"},
                        "web_url": "https://gitlab.com/niuulabs/skuld",
                        "default_branch": "main",
                    }
                ],
                headers={"x-next-page": ""},
            )
        )
        respx.get(url__regex=r"https://gitlab\.com/api/v4/projects/.*/repository/branches").mock(
            return_value=Response(200, json=[{"name": "main"}])
        )

        repos = await provider.list_repos("")

        assert listing.calls.last.request.url.params["membership"] == "true"
        assert [(r.org, r.name) for r in repos] == [("niuulabs", "skuld")]

    @pytest.mark.asyncio
    async def test_empty_group_without_a_token_is_refused(self) -> None:
        provider = GitLabProvider(name="anon", base_url="https://gitlab.com", token="")
        with pytest.raises(ValueError, match="needs a token"):
            await provider.list_repos("")

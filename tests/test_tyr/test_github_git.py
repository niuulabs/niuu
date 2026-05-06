"""Tests for GitHubGitAdapter with respx-mocked httpx calls."""

from __future__ import annotations

import httpx
import pytest
import respx

from tyr.adapters.github_git import GitHubGitAdapter

REPO = "org/repo"
GH_API = "https://api.github.com"


@pytest.fixture
def adapter() -> GitHubGitAdapter:
    return GitHubGitAdapter(token="test-token", timeout=5.0)


# -------------------------------------------------------------------
# Constructor
# -------------------------------------------------------------------


class TestConstructor:
    def test_stores_token(self):
        adapter = GitHubGitAdapter(token="tok")
        assert adapter._token == "tok"

    def test_default_timeout(self):
        adapter = GitHubGitAdapter(token="tok")
        assert adapter._timeout == 30.0

    def test_custom_timeout(self):
        adapter = GitHubGitAdapter(token="tok", timeout=10.0)
        assert adapter._timeout == 10.0

    def test_headers(self):
        adapter = GitHubGitAdapter(token="tok")
        headers = adapter._headers()
        assert headers["Authorization"] == "Bearer tok"
        assert "application/vnd.github+json" in headers["Accept"]

    def test_headers_omit_authorization_when_token_missing(self):
        adapter = GitHubGitAdapter(token="")
        headers = adapter._headers()
        assert "Authorization" not in headers
        assert "application/vnd.github+json" in headers["Accept"]

    def test_owner_repo_split(self):
        adapter = GitHubGitAdapter(token="tok")
        owner, name = adapter._owner_repo("myorg/myrepo")
        assert owner == "myorg"
        assert name == "myrepo"

    def test_owner_repo_split_from_https_url(self):
        adapter = GitHubGitAdapter(token="tok")
        owner, name = adapter._owner_repo("https://github.com/myorg/myrepo.git")
        assert owner == "myorg"
        assert name == "myrepo"

    def test_creates_shared_client(self):
        adapter = GitHubGitAdapter(token="tok")
        assert adapter._client is not None
        assert isinstance(adapter._client, httpx.AsyncClient)


# -------------------------------------------------------------------
# create_branch
# -------------------------------------------------------------------


class TestCreateBranch:
    @pytest.mark.asyncio
    @respx.mock
    async def test_creates_branch(self, adapter: GitHubGitAdapter):
        respx.get(f"{GH_API}/repos/org/repo/git/ref/heads/main").mock(
            return_value=httpx.Response(
                200,
                json={"object": {"sha": "abc123"}},
            )
        )
        respx.post(f"{GH_API}/repos/org/repo/git/refs").mock(
            return_value=httpx.Response(201, json={"ref": "refs/heads/new-branch"})
        )

        await adapter.create_branch(REPO, "new-branch", "main")

        create_call = respx.calls[1]
        import json

        body = json.loads(create_call.request.content)
        assert body["ref"] == "refs/heads/new-branch"
        assert body["sha"] == "abc123"

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_base_not_found(self, adapter: GitHubGitAdapter):
        respx.get(f"{GH_API}/repos/org/repo/git/ref/heads/missing").mock(
            return_value=httpx.Response(404, text="not found")
        )

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.create_branch(REPO, "new-branch", "missing")


# -------------------------------------------------------------------
# merge_branch
# -------------------------------------------------------------------


class TestMergeBranch:
    @pytest.mark.asyncio
    @respx.mock
    async def test_merges_branch(self, adapter: GitHubGitAdapter):
        route = respx.post(f"{GH_API}/repos/org/repo/merges").mock(
            return_value=httpx.Response(201, json={"sha": "merged-sha"})
        )

        await adapter.merge_branch(REPO, "feature", "main")

        import json

        body = json.loads(route.calls[0].request.content)
        assert body["base"] == "main"
        assert body["head"] == "feature"

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_conflict(self, adapter: GitHubGitAdapter):
        respx.post(f"{GH_API}/repos/org/repo/merges").mock(
            return_value=httpx.Response(409, text="Merge conflict")
        )

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.merge_branch(REPO, "conflicting", "main")


# -------------------------------------------------------------------
# delete_branch
# -------------------------------------------------------------------


class TestDeleteBranch:
    @pytest.mark.asyncio
    @respx.mock
    async def test_deletes_branch(self, adapter: GitHubGitAdapter):
        respx.delete(f"{GH_API}/repos/org/repo/git/refs/heads/old-branch").mock(
            return_value=httpx.Response(204)
        )

        await adapter.delete_branch(REPO, "old-branch")

    @pytest.mark.asyncio
    @respx.mock
    async def test_ignores_already_deleted(self, adapter: GitHubGitAdapter):
        respx.delete(f"{GH_API}/repos/org/repo/git/refs/heads/gone").mock(
            return_value=httpx.Response(404)
        )

        # Should not raise
        await adapter.delete_branch(REPO, "gone")

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_server_error(self, adapter: GitHubGitAdapter):
        respx.delete(f"{GH_API}/repos/org/repo/git/refs/heads/branch").mock(
            return_value=httpx.Response(500, text="error")
        )

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.delete_branch(REPO, "branch")


# -------------------------------------------------------------------
# create_pr
# -------------------------------------------------------------------


class TestCreatePR:
    @pytest.mark.asyncio
    @respx.mock
    async def test_creates_pr(self, adapter: GitHubGitAdapter):
        route = respx.post(f"{GH_API}/repos/org/repo/pulls").mock(
            return_value=httpx.Response(201, json={"number": 42})
        )

        pr_id = await adapter.create_pr(REPO, "feature", "main", "My PR")

        assert pr_id == "42"
        import json

        body = json.loads(route.calls[0].request.content)
        assert body["title"] == "My PR"
        assert body["head"] == "feature"
        assert body["base"] == "main"

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_error(self, adapter: GitHubGitAdapter):
        respx.post(f"{GH_API}/repos/org/repo/pulls").mock(
            return_value=httpx.Response(422, text="validation failed")
        )

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.create_pr(REPO, "feature", "main", "Bad PR")


# -------------------------------------------------------------------
# get_pr_status
# -------------------------------------------------------------------


class TestGetPRStatus:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_status(self, adapter: GitHubGitAdapter):
        pr_url = "https://api.github.com/repos/org/repo/pulls/42"
        respx.get(pr_url).mock(
            return_value=httpx.Response(
                200,
                json={
                    "state": "open",
                    "mergeable": True,
                    "html_url": "https://github.com/org/repo/pull/42",
                },
            )
        )

        status = await adapter.get_pr_status(pr_url)

        assert status.pr_id == pr_url
        assert status.url == "https://github.com/org/repo/pull/42"
        assert status.state == "open"
        assert status.mergeable is True
        assert status.ci_passed is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_error(self, adapter: GitHubGitAdapter):
        pr_url = "https://api.github.com/repos/org/repo/pulls/999"
        respx.get(pr_url).mock(return_value=httpx.Response(404, text="not found"))

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.get_pr_status(pr_url)


# -------------------------------------------------------------------
# get_pr_changed_files
# -------------------------------------------------------------------


class TestGetPRChangedFiles:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_file_list(self, adapter: GitHubGitAdapter):
        pr_url = "https://api.github.com/repos/org/repo/pulls/42"
        respx.get(f"{pr_url}/files").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"filename": "src/main.py", "status": "modified"},
                    {"filename": "tests/test_main.py", "status": "added"},
                ],
            )
        )

        files = await adapter.get_pr_changed_files(pr_url)

        assert files == ["src/main.py", "tests/test_main.py"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_file_list(self, adapter: GitHubGitAdapter):
        pr_url = "https://api.github.com/repos/org/repo/pulls/42"
        respx.get(f"{pr_url}/files").mock(return_value=httpx.Response(200, json=[]))

        files = await adapter.get_pr_changed_files(pr_url)

        assert files == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_error(self, adapter: GitHubGitAdapter):
        pr_url = "https://api.github.com/repos/org/repo/pulls/999"
        respx.get(f"{pr_url}/files").mock(return_value=httpx.Response(404, text="not found"))

        with pytest.raises(httpx.HTTPStatusError):
            await adapter.get_pr_changed_files(pr_url)

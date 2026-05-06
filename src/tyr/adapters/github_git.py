"""GitHub Git adapter — calls the GitHub API for branch and PR operations."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from tyr.domain.models import PRStatus
from tyr.ports.git import GitPort

logger = logging.getLogger(__name__)


class GitHubGitAdapter(GitPort):
    """Implements GitPort via the GitHub REST API."""

    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self._token = token
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=self._timeout)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _owner_repo(self, repo: str) -> tuple[str, str]:
        """Split a GitHub repo ref into ``(owner, repo)``.

        Accepts either ``owner/repo`` or ``https://github.com/owner/repo(.git)``.
        """
        if repo.startswith("http://") or repo.startswith("https://"):
            parsed = urlparse(repo)
            path = parsed.path.strip("/")
            if path.endswith(".git"):
                path = path[:-4]
            owner, name = path.split("/", 1)
            return owner, name

        owner, name = repo.split("/", 1)
        return owner, name

    async def create_branch(self, repo: str, branch: str, base: str) -> None:
        owner, name = self._owner_repo(repo)
        # Get base branch SHA
        resp = await self._client.get(
            f"https://api.github.com/repos/{owner}/{name}/git/ref/heads/{base}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        sha = resp.json()["object"]["sha"]

        # Create new branch
        resp = await self._client.post(
            f"https://api.github.com/repos/{owner}/{name}/git/refs",
            headers=self._headers(),
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        resp.raise_for_status()

    async def merge_branch(self, repo: str, source: str, target: str) -> None:
        owner, name = self._owner_repo(repo)
        resp = await self._client.post(
            f"https://api.github.com/repos/{owner}/{name}/merges",
            headers=self._headers(),
            json={
                "base": target,
                "head": source,
                "commit_message": f"Merge {source} into {target}",
            },
        )
        resp.raise_for_status()

    async def delete_branch(self, repo: str, branch: str) -> None:
        owner, name = self._owner_repo(repo)
        resp = await self._client.delete(
            f"https://api.github.com/repos/{owner}/{name}/git/refs/heads/{branch}",
            headers=self._headers(),
        )
        if resp.status_code != 404:
            resp.raise_for_status()

    async def create_pr(self, repo: str, source: str, target: str, title: str) -> str:
        owner, name = self._owner_repo(repo)
        resp = await self._client.post(
            f"https://api.github.com/repos/{owner}/{name}/pulls",
            headers=self._headers(),
            json={"title": title, "head": source, "base": target},
        )
        resp.raise_for_status()
        return str(resp.json()["number"])

    async def get_pr_status(self, pr_id: str) -> PRStatus:
        # pr_id expected as "owner/repo#number" or just a URL
        resp = await self._client.get(
            pr_id,
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        return PRStatus(
            pr_id=pr_id,
            url=data.get("html_url", ""),
            state=data["state"],
            mergeable=data.get("mergeable", False),
            ci_passed=None,
        )

    async def get_pr_changed_files(self, pr_id: str) -> list[str]:
        resp = await self._client.get(
            f"{pr_id}/files",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return [f["filename"] for f in resp.json()]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

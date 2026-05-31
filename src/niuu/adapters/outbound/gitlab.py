"""GitLab git provider adapter."""

import asyncio
import logging
import re
from dataclasses import replace
from datetime import datetime
from urllib.parse import quote_plus, urlparse

import httpx

from niuu.domain.models import (
    CIStatus,
    GitProviderType,
    PullRequest,
    PullRequestStatus,
    RepoInfo,
)
from niuu.ports.git import (
    GitAuthError,
    GitProvider,
    GitRepoNotFoundError,
    GitWorkflowProvider,
)

logger = logging.getLogger(__name__)


class GitLabProvider(GitProvider, GitWorkflowProvider):
    """GitLab git provider implementation.

    Supports GitLab.com and self-hosted GitLab instances.
    Each instance of this class represents one GitLab server.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        token: str | None = None,
        orgs: tuple[str, ...] = (),
    ):
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._orgs = orgs
        self._client: httpx.AsyncClient | None = None

        # Extract host from base URL for matching
        parsed = urlparse(self._base_url)
        self._host = parsed.netloc or parsed.path

        # Build URL patterns for this instance
        host_escaped = re.escape(self._host)
        self._patterns = [
            re.compile(rf"^(?:https?://)?{host_escaped}/([^/]+)/([^/]+?)(?:\.git)?/?$"),
            re.compile(rf"^git@{host_escaped}:([^/]+)/([^/]+?)(?:\.git)?$"),
        ]

        logger.debug(
            "GitLabProvider initialized: name=%s, base_url=%s, host=%s, "
            "token_configured=%s, patterns=%s",
            self._name,
            self._base_url,
            self._host,
            bool(self._token),
            [p.pattern for p in self._patterns],
        )

    @property
    def provider_type(self) -> GitProviderType:
        """Return the provider type."""
        return GitProviderType.GITLAB

    @property
    def name(self) -> str:
        """Return provider name."""
        return self._name

    @property
    def base_url(self) -> str:
        """Return the base URL for this provider instance."""
        return self._base_url

    @property
    def orgs(self) -> tuple[str, ...]:
        """Return configured organizations/groups."""
        return self._orgs

    @property
    def host(self) -> str:
        """Return the GitLab host."""
        return self._host

    def supports(self, repo_url: str) -> bool:
        """Check if this provider supports the given URL."""
        result = self._parse_url(repo_url) is not None
        logger.debug(
            "GitLabProvider[%s].supports(%s) = %s (host=%s)",
            self._name,
            repo_url,
            result,
            self._host,
        )
        return result

    def _parse_url(self, repo_url: str) -> tuple[str, str] | None:
        """Parse a GitLab URL into (org, repo) tuple."""
        for i, pattern in enumerate(self._patterns):
            match = pattern.match(repo_url)
            if match:
                org, repo = match.group(1), match.group(2)
                logger.debug(
                    "GitLabProvider[%s]: URL %s matched pattern %d (%s), extracted org=%s, repo=%s",
                    self._name,
                    repo_url,
                    i,
                    pattern.pattern,
                    org,
                    repo,
                )
                return (org, repo)
        logger.debug(
            "GitLabProvider[%s]: URL %s did not match any pattern for host %s",
            self._name,
            repo_url,
            self._host,
        )
        return None

    def parse_repo(self, repo_url: str) -> RepoInfo | None:
        """Parse a repository URL into RepoInfo."""
        parsed = self._parse_url(repo_url)
        if parsed is None:
            return None

        org, repo = parsed
        return RepoInfo(
            provider=GitProviderType.GITLAB,
            org=org,
            name=repo,
            clone_url=f"https://{self._host}/{org}/{repo}.git",
            url=f"https://{self._host}/{org}/{repo}",
        )

    def get_clone_url(self, repo_url: str) -> str | None:
        """Get authenticated clone URL."""
        parsed = self._parse_url(repo_url)
        if parsed is None:
            return None

        org, repo = parsed

        if self._token:
            return f"https://oauth2:{self._token}@{self._host}/{org}/{repo}.git"

        return f"https://{self._host}/{org}/{repo}.git"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self._token:
                headers["PRIVATE-TOKEN"] = self._token

            self._client = httpx.AsyncClient(
                base_url=f"https://{self._host}/api/v4",
                headers=headers,
                timeout=30.0,
            )
        return self._client

    async def validate_repo(self, repo_url: str) -> bool:
        """Validate repository exists and is accessible."""
        logger.debug(
            "GitLabProvider[%s]: validating repo URL: %s",
            self._name,
            repo_url,
        )
        parsed = self._parse_url(repo_url)
        if parsed is None:
            logger.warning(
                "GitLabProvider[%s]: cannot validate repo, URL parsing failed: %s",
                self._name,
                repo_url,
            )
            return False

        org, repo = parsed
        project_path = quote_plus(f"{org}/{repo}")
        client = await self._get_client()
        api_endpoint = f"/projects/{project_path}"

        try:
            logger.debug(
                "GitLabProvider[%s]: making API request to https://%s/api/v4%s",
                self._name,
                self._host,
                api_endpoint,
            )
            response = await client.get(api_endpoint)
            is_valid = response.status_code == 200
            logger.info(
                "GitLabProvider[%s]: repo validation for %s/%s: status=%d, valid=%s",
                self._name,
                org,
                repo,
                response.status_code,
                is_valid,
            )
            if not is_valid:
                logger.debug(
                    "GitLabProvider[%s]: API response body: %s",
                    self._name,
                    response.text[:500] if response.text else "(empty)",
                )
            return is_valid
        except httpx.HTTPError as e:
            logger.error(
                "GitLabProvider[%s]: HTTP error validating repo %s/%s: %s",
                self._name,
                org,
                repo,
                str(e),
            )
            return False

    async def list_repos(self, org: str) -> list[RepoInfo]:
        """List repositories in a group."""
        client = await self._get_client()
        repos: list[RepoInfo] = []
        group_path = quote_plus(org)

        try:
            # Try group endpoint first
            page = 1
            url = f"/groups/{group_path}/projects"
            params: dict[str, str | int | bool] = {
                "per_page": 100,
                "include_subgroups": True,
                "page": page,
            }

            logger.info(
                "GitLabProvider[%s]: listing repos for org=%s, url=%s, authenticated=%s",
                self._name,
                org,
                url,
                bool(self._token),
            )

            response = await client.get(url, params=params)

            # Fall back to user projects if group not found
            if response.status_code == 404:
                logger.debug(
                    "GitLabProvider[%s]: group endpoint 404 for %s, trying user endpoint",
                    self._name,
                    org,
                )
                page = 1
                url = f"/users/{org}/projects"
                params = {"per_page": 100, "page": page}
                response = await client.get(url, params=params)

            # Paginate through all results
            while True:
                if response.status_code != 200:
                    logger.warning(
                        "GitLabProvider[%s]: list_repos got status %d for org=%s url=%s: %s",
                        self._name,
                        response.status_code,
                        org,
                        url,
                        response.text[:500] if response.text else "(empty)",
                    )
                    break

                page_data = response.json()
                if not page_data:
                    break

                for project in page_data:
                    repo_name = project["path"]
                    namespace = project.get("namespace", {}).get("path", org)
                    repos.append(
                        RepoInfo(
                            provider=GitProviderType.GITLAB,
                            org=namespace,
                            name=repo_name,
                            clone_url=f"https://{self._host}/{namespace}/{repo_name}.git",
                            url=project["web_url"],
                            default_branch=project.get("default_branch", "main"),
                        )
                    )

                # Check for next page via header or stop if partial page
                next_page = response.headers.get("x-next-page", "")
                if not next_page:
                    break

                page = int(next_page)
                params["page"] = page
                response = await client.get(url, params=params)

            # Fetch branches concurrently for all repos
            branch_lists = await asyncio.gather(
                *(self._fetch_branches(client, r.org, r.name) for r in repos),
                return_exceptions=True,
            )
            repos = [
                replace(r, branches=tuple(bl) if isinstance(bl, list) else ())
                for r, bl in zip(repos, branch_lists)
            ]

            logger.info(
                "GitLabProvider[%s]: listed %d repos for org=%s",
                self._name,
                len(repos),
                org,
            )

        except httpx.HTTPError as e:
            logger.error(
                "GitLabProvider[%s]: HTTP error listing repos for org=%s: %s",
                self._name,
                org,
                str(e),
            )

        return repos

    async def _fetch_branches(
        self, client: httpx.AsyncClient, namespace: str, repo: str
    ) -> list[str]:
        """Fetch all branch names for a repository."""
        branches: list[str] = []
        project_path = quote_plus(f"{namespace}/{repo}")
        page = 1
        params: dict[str, str | int] = {"per_page": 100, "page": page}

        while True:
            response = await client.get(
                f"/projects/{project_path}/repository/branches", params=params
            )
            if response.status_code != 200:
                logger.debug(
                    "GitLabProvider[%s]: failed to fetch branches for %s/%s: %d",
                    self._name,
                    namespace,
                    repo,
                    response.status_code,
                )
                break

            page_data = response.json()
            if not page_data:
                break

            for branch in page_data:
                branches.append(branch["name"])

            next_page = response.headers.get("x-next-page", "")
            if not next_page:
                break

            page = int(next_page)
            params["page"] = page

        return branches

    async def list_branches(self, repo_url: str) -> list[str]:
        """List all branches for a specific repository with proper auth."""
        parsed = self._parse_url(repo_url)
        if parsed is None:
            raise GitRepoNotFoundError(f"Cannot parse repository URL: {repo_url}")

        namespace, repo = parsed
        client = await self._get_client()
        project_path = quote_plus(f"{namespace}/{repo}")

        branches: list[str] = []
        page = 1
        params: dict[str, str | int] = {"per_page": 100, "page": page}

        while True:
            response = await client.get(
                f"/projects/{project_path}/repository/branches", params=params
            )

            if response.status_code in (401, 403):
                raise GitAuthError(
                    f"Authentication failed for {namespace}/{repo}: "
                    f"HTTP {response.status_code}. "
                    f"Check your GitLab token has read_repository access."
                )

            if response.status_code == 404:
                raise GitRepoNotFoundError(
                    f"Repository not found: {namespace}/{repo}. "
                    f"It may not exist or your token lacks access."
                )

            if response.status_code != 200:
                logger.warning(
                    "GitLabProvider[%s]: unexpected status %d listing branches for %s/%s",
                    self._name,
                    response.status_code,
                    namespace,
                    repo,
                )
                break

            page_data = response.json()
            if not page_data:
                break

            for branch in page_data:
                branches.append(branch["name"])

            next_page = response.headers.get("x-next-page", "")
            if not next_page:
                break

            page = int(next_page)
            params["page"] = page

        logger.info(
            "GitLabProvider[%s]: listed %d branches for %s/%s",
            self._name,
            len(branches),
            namespace,
            repo,
        )
        return branches

    # --- GitWorkflowProvider methods ---

    def _project_api_path(self, repo_url: str) -> str | None:
        """Get the /projects/{encoded_path} API path for a URL."""
        parsed = self._parse_url(repo_url)
        if parsed is None:
            return None
        org, repo = parsed
        return f"/projects/{quote_plus(f'{org}/{repo}')}"

    def _to_pull_request(self, data: dict, repo_url: str) -> PullRequest:
        """Map a GitLab MR JSON response to a domain PullRequest."""
        state = data.get("state", "opened")
        match state:
            case "merged":
                pr_status = PullRequestStatus.MERGED
            case "closed":
                pr_status = PullRequestStatus.CLOSED
            case _:
                pr_status = PullRequestStatus.OPEN

        created_at = None
        if data.get("created_at"):
            created_at = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
        updated_at = None
        if data.get("updated_at"):
            updated_at = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))

        return PullRequest(
            number=data["iid"],
            title=data["title"],
            url=data["web_url"],
            repo_url=repo_url,
            provider=GitProviderType.GITLAB,
            source_branch=data.get("source_branch", ""),
            target_branch=data.get("target_branch", ""),
            status=pr_status,
            description=data.get("description"),
            created_at=created_at,
            updated_at=updated_at,
        )

    async def create_branch(
        self,
        repo_url: str,
        branch_name: str,
        from_branch: str = "main",
    ) -> bool:
        """Create a branch via the GitLab branches API."""
        path = self._project_api_path(repo_url)
        if path is None:
            return False

        client = await self._get_client()

        try:
            resp = await client.post(
                f"{path}/repository/branches",
                json={"branch": branch_name, "ref": from_branch},
            )
            if resp.status_code in (200, 201):
                logger.info(
                    "GitLabProvider[%s]: created branch %s from %s",
                    self._name,
                    branch_name,
                    from_branch,
                )
                return True

            logger.error(
                "GitLabProvider[%s]: failed to create branch %s: %d %s",
                self._name,
                branch_name,
                resp.status_code,
                resp.text[:300],
            )
            return False
        except httpx.HTTPError as e:
            logger.error(
                "GitLabProvider[%s]: HTTP error creating branch: %s",
                self._name,
                str(e),
            )
            return False

    async def create_pull_request(
        self,
        repo_url: str,
        title: str,
        description: str,
        source_branch: str,
        target_branch: str,
        labels: list[str] | None = None,
    ) -> PullRequest:
        """Create a merge request via the GitLab MR API."""
        path = self._project_api_path(repo_url)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {repo_url}")

        client = await self._get_client()
        body: dict = {
            "title": title,
            "description": description,
            "source_branch": source_branch,
            "target_branch": target_branch,
        }
        if labels:
            body["labels"] = ",".join(labels)

        resp = await client.post(f"{path}/merge_requests", json=body)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create MR: {resp.status_code} {resp.text[:300]}")

        mr_data = resp.json()
        logger.info(
            "GitLabProvider[%s]: created MR !%d for %s",
            self._name,
            mr_data["iid"],
            repo_url,
        )
        return self._to_pull_request(mr_data, repo_url)

    async def get_pull_request(self, repo_url: str, pr_number: int) -> PullRequest | None:
        """Get a merge request by IID."""
        path = self._project_api_path(repo_url)
        if path is None:
            return None

        client = await self._get_client()
        resp = await client.get(f"{path}/merge_requests/{pr_number}")
        if resp.status_code != 200:
            return None

        return self._to_pull_request(resp.json(), repo_url)

    async def list_pull_requests(self, repo_url: str, status: str = "open") -> list[PullRequest]:
        """List merge requests for a project."""
        path = self._project_api_path(repo_url)
        if path is None:
            return []

        client = await self._get_client()
        # GitLab uses "state": opened, closed, merged, all
        gl_state = "opened" if status == "open" else status
        params: dict[str, str | int] = {
            "state": gl_state,
            "per_page": 100,
        }
        resp = await client.get(f"{path}/merge_requests", params=params)
        if resp.status_code != 200:
            return []

        return [self._to_pull_request(mr, repo_url) for mr in resp.json()]

    async def merge_pull_request(
        self,
        repo_url: str,
        pr_number: int,
        merge_method: str = "squash",
    ) -> bool:
        """Merge a merge request."""
        path = self._project_api_path(repo_url)
        if path is None:
            return False

        client = await self._get_client()
        params: dict[str, str | bool] = {}
        if merge_method == "squash":
            params["squash"] = True

        resp = await client.put(
            f"{path}/merge_requests/{pr_number}/merge",
            json=params,
        )
        if resp.status_code == 200:
            logger.info(
                "GitLabProvider[%s]: merged MR !%d (%s)",
                self._name,
                pr_number,
                merge_method,
            )
            return True

        logger.error(
            "GitLabProvider[%s]: failed to merge MR !%d: %d %s",
            self._name,
            pr_number,
            resp.status_code,
            resp.text[:300],
        )
        return False

    async def get_ci_status(self, repo_url: str, branch: str) -> CIStatus:
        """Get pipeline status for a branch."""
        path = self._project_api_path(repo_url)
        if path is None:
            return CIStatus.UNKNOWN

        client = await self._get_client()
        resp = await client.get(
            f"{path}/pipelines",
            params={"ref": branch, "per_page": 1},
        )
        if resp.status_code != 200:
            return CIStatus.UNKNOWN

        pipelines = resp.json()
        if not pipelines:
            return CIStatus.UNKNOWN

        status = pipelines[0].get("status", "")
        match status:
            case "success":
                return CIStatus.PASSING
            case "failed":
                return CIStatus.FAILING
            case "pending" | "running" | "created":
                return CIStatus.PENDING
            case _:
                return CIStatus.UNKNOWN
        raise AssertionError("Unreachable get_ci_status fallthrough")

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

"""GitHub git provider adapter."""

import asyncio
import logging
import re
from dataclasses import replace
from datetime import datetime
from urllib.parse import urlparse

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


class GitHubProvider(GitProvider, GitWorkflowProvider):
    """GitHub git provider implementation.

    Supports GitHub.com and GitHub Enterprise instances.
    Each instance of this class represents one GitHub server.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        token: str | None = None,
        orgs: tuple[str, ...] | list[str] | str = (),
        **_extra: object,
    ):
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._token = token
        if isinstance(orgs, str):
            self._orgs = tuple(o.strip() for o in orgs.split(",") if o.strip())
        else:
            self._orgs = tuple(orgs)
        self._client: httpx.AsyncClient | None = None
        self._token_scopes_checked: bool = False

        # Extract host from base URL for matching
        # For API URLs like https://api.github.com or https://github.company.com/api/v3
        parsed = urlparse(self._base_url)
        api_host = parsed.netloc

        # Determine the web host (for clone URLs)
        # api.github.com -> github.com
        # github.company.com/api/v3 -> github.company.com
        if api_host.startswith("api."):
            self._web_host = api_host[4:]  # Remove "api." prefix
        else:
            self._web_host = api_host

        # Build URL patterns for this instance
        host_escaped = re.escape(self._web_host)
        self._patterns = [
            re.compile(rf"^(?:https?://)?{host_escaped}/([^/]+)/([^/]+?)(?:\.git)?/?$"),
            re.compile(rf"^git@{host_escaped}:([^/]+)/([^/]+?)(?:\.git)?$"),
            # Bare shorthand: "org/repo" (no host, no protocol)
            re.compile(r"^([^/:@.]+)/([^/:@.]+?)(?:\.git)?$"),
        ]

        logger.debug(
            "GitHubProvider initialized: name=%s, api_url=%s, web_host=%s, "
            "token_configured=%s, patterns=%s",
            self._name,
            self._base_url,
            self._web_host,
            bool(self._token),
            [p.pattern for p in self._patterns],
        )

    @property
    def provider_type(self) -> GitProviderType:
        """Return the provider type."""
        return GitProviderType.GITHUB

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
        """Return configured organizations."""
        return self._orgs

    def supports(self, repo_url: str) -> bool:
        """Check if this provider supports the given URL."""
        result = self._parse_url(repo_url) is not None
        logger.debug(
            "GitHubProvider[%s].supports(%s) = %s (web_host=%s)",
            self._name,
            repo_url,
            result,
            self._web_host,
        )
        return result

    def _parse_url(self, repo_url: str) -> tuple[str, str] | None:
        """Parse a GitHub URL into (org, repo) tuple."""
        for i, pattern in enumerate(self._patterns):
            match = pattern.match(repo_url)
            if match:
                org, repo = match.group(1), match.group(2)
                logger.debug(
                    "GitHubProvider[%s]: URL %s matched pattern %d (%s), extracted org=%s, repo=%s",
                    self._name,
                    repo_url,
                    i,
                    pattern.pattern,
                    org,
                    repo,
                )
                return (org, repo)
        logger.debug(
            "GitHubProvider[%s]: URL %s did not match any pattern for host %s",
            self._name,
            repo_url,
            self._web_host,
        )
        return None

    def parse_repo(self, repo_url: str) -> RepoInfo | None:
        """Parse a repository URL into RepoInfo."""
        parsed = self._parse_url(repo_url)
        if parsed is None:
            return None

        org, repo = parsed
        return RepoInfo(
            provider=GitProviderType.GITHUB,
            org=org,
            name=repo,
            clone_url=f"https://{self._web_host}/{org}/{repo}.git",
            url=f"https://{self._web_host}/{org}/{repo}",
        )

    def get_clone_url(self, repo_url: str) -> str | None:
        """Get authenticated clone URL."""
        parsed = self._parse_url(repo_url)
        if parsed is None:
            return None

        org, repo = parsed

        if self._token:
            return f"https://x-access-token:{self._token}@{self._web_host}/{org}/{repo}.git"

        return f"https://{self._web_host}/{org}/{repo}.git"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            headers = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"

            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=30.0,
            )
        return self._client

    async def validate_repo(self, repo_url: str) -> bool:
        """Validate repository exists and is accessible."""
        logger.debug(
            "GitHubProvider[%s]: validating repo URL: %s",
            self._name,
            repo_url,
        )
        parsed = self._parse_url(repo_url)
        if parsed is None:
            logger.warning(
                "GitHubProvider[%s]: cannot validate repo, URL parsing failed: %s",
                self._name,
                repo_url,
            )
            return False

        org, repo = parsed
        client = await self._get_client()
        api_endpoint = f"/repos/{org}/{repo}"

        try:
            logger.debug(
                "GitHubProvider[%s]: making API request to %s%s",
                self._name,
                self._base_url,
                api_endpoint,
            )
            response = await client.get(api_endpoint)
            is_valid = response.status_code == 200
            logger.info(
                "GitHubProvider[%s]: repo validation for %s/%s: status=%d, valid=%s",
                self._name,
                org,
                repo,
                response.status_code,
                is_valid,
            )
            if not is_valid:
                logger.debug(
                    "GitHubProvider[%s]: API response body: %s",
                    self._name,
                    response.text[:500] if response.text else "(empty)",
                )
            return is_valid
        except httpx.HTTPError as e:
            logger.error(
                "GitHubProvider[%s]: HTTP error validating repo %s/%s: %s",
                self._name,
                org,
                repo,
                str(e),
            )
            return False

    async def list_repos(self, org: str) -> list[RepoInfo]:
        """List repositories in an organization or user account.

        Tries the org endpoint first.  When that 404s the behaviour depends on
        whether we have a token:

        * **Authenticated** -- uses ``/user/repos`` (visibility=all,
          affiliation=owner) which includes private repos, then filters by
          ``owner.login`` matching *org* so we only return repos belonging to
          the requested account.
        * **Unauthenticated** -- falls back to ``/users/{org}/repos`` which
          only returns public repos.
        """
        client = await self._get_client()
        repos: list[RepoInfo] = []
        # When using /user/repos we must filter by owner since it returns
        # *all* repos owned by the authenticated user.
        filter_owner: bool = False

        try:
            # Try org endpoint first, type=all includes public+private+forks
            url: str | None = f"/orgs/{org}/repos"
            params: dict[str, str | int] = {"per_page": 100, "type": "all"}

            logger.info(
                "GitHubProvider[%s]: listing repos for org=%s, url=%s, authenticated=%s",
                self._name,
                org,
                url,
                bool(self._token),
            )

            response = await client.get(url, params=params)

            # Fall back to user endpoint if org not found
            if response.status_code == 404:
                if self._token:
                    # Use authenticated /user/repos endpoint to include private repos.
                    # /users/{name}/repos only returns public repos even with a token.
                    logger.debug(
                        "GitHubProvider[%s]: org endpoint 404 for %s, "
                        "trying authenticated /user/repos",
                        self._name,
                        org,
                    )
                    url = "/user/repos"
                    params = {
                        "per_page": 100,
                        "visibility": "all",
                        "affiliation": "owner,collaborator,organization_member",
                    }
                    filter_owner = True
                else:
                    logger.debug(
                        "GitHubProvider[%s]: org endpoint 404 for %s, "
                        "trying /users/%s/repos (unauthenticated, public only)",
                        self._name,
                        org,
                        org,
                    )
                    url = f"/users/{org}/repos"
                    params = {"per_page": 100, "type": "all"}
                response = await client.get(url, params=params)

            # Paginate through all results
            while url is not None:
                if response.status_code != 200:
                    logger.warning(
                        "GitHubProvider[%s]: list_repos got status %d for org=%s url=%s: %s",
                        self._name,
                        response.status_code,
                        org,
                        url,
                        response.text[:500] if response.text else "(empty)",
                    )
                    break

                for repo_data in response.json():
                    # /user/repos returns all owned repos; skip repos whose
                    # owner doesn't match the requested org/user.
                    if filter_owner:
                        owner_login = repo_data.get("owner", {}).get("login", "")
                        if owner_login.lower() != org.lower():
                            continue

                    repo_name = repo_data["name"]
                    repos.append(
                        RepoInfo(
                            provider=GitProviderType.GITHUB,
                            org=org,
                            name=repo_name,
                            clone_url=f"https://{self._web_host}/{org}/{repo_name}.git",
                            url=repo_data["html_url"],
                            default_branch=repo_data.get("default_branch", "main"),
                        )
                    )

                url = self._next_link(response)
                if url is not None:
                    response = await client.get(url)

            # Fetch branches concurrently for all repos
            branch_lists = await asyncio.gather(
                *(self._fetch_branches(client, org, r.name) for r in repos),
                return_exceptions=True,
            )
            updated_repos: list[RepoInfo] = []
            for r, bl in zip(repos, branch_lists):
                if isinstance(bl, Exception):
                    logger.warning(
                        "GitHubProvider[%s]: failed to fetch branches for %s/%s: %s",
                        self._name,
                        org,
                        r.name,
                        bl,
                    )
                    updated_repos.append(replace(r, branches=()))
                else:
                    updated_repos.append(replace(r, branches=tuple(bl)))
            repos = updated_repos

            logger.info(
                "GitHubProvider[%s]: listed %d repos for org=%s",
                self._name,
                len(repos),
                org,
            )

        except httpx.HTTPError as e:
            logger.error(
                "GitHubProvider[%s]: HTTP error listing repos for org=%s: %s",
                self._name,
                org,
                str(e),
            )

        return repos

    async def _fetch_branches(self, client: httpx.AsyncClient, org: str, repo: str) -> list[str]:
        """Fetch branch names for a repository (single page, max 100)."""
        response = await client.get(
            f"/repos/{org}/{repo}/branches",
            params={"per_page": 100},
        )

        if response.status_code in (401, 403):
            logger.warning(
                "GitHubProvider[%s]: auth error fetching branches for %s/%s: "
                "HTTP %d. Token may be missing the 'repo' scope (classic PAT) "
                "or 'contents:read' permission (fine-grained PAT).",
                self._name,
                org,
                repo,
                response.status_code,
            )
            return []

        if response.status_code == 404:
            logger.warning(
                "GitHubProvider[%s]: 404 fetching branches for %s/%s. "
                "The repo may be private and the token lacks access. "
                "Ensure the token has the 'repo' scope (classic PAT) "
                "or 'contents:read' permission (fine-grained PAT).",
                self._name,
                org,
                repo,
            )
            await self._check_token_scopes(client)
            return []

        if response.status_code != 200:
            logger.warning(
                "GitHubProvider[%s]: unexpected status %d fetching branches for %s/%s",
                self._name,
                response.status_code,
                org,
                repo,
            )
            return []

        return [branch["name"] for branch in response.json()]

    async def _check_token_scopes(self, client: httpx.AsyncClient) -> list[str]:
        """Check token scopes by inspecting X-OAuth-Scopes header from /user.

        Called once on first 404 to help diagnose missing permissions.
        Returns the list of scopes (empty if unavailable).
        """
        if self._token_scopes_checked:
            return []
        self._token_scopes_checked = True

        try:
            response = await client.get("/user")
            scopes_header = response.headers.get("x-oauth-scopes", "")
            scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]

            if scopes:
                logger.info(
                    "GitHubProvider[%s]: token scopes received (count=%d, has_repo_scope=%s)",
                    self._name,
                    len(scopes),
                    "repo" in scopes,
                )
                if "repo" not in scopes:
                    logger.warning(
                        "GitHubProvider[%s]: token is missing the 'repo' scope. "
                        "Private repository access requires the 'repo' scope for classic PATs.",
                        self._name,
                    )
            else:
                logger.info(
                    "GitHubProvider[%s]: no X-OAuth-Scopes header returned "
                    "(token may be a fine-grained PAT or GitHub App token)",
                    self._name,
                )

            return scopes
        except httpx.HTTPError as e:
            logger.debug(
                "GitHubProvider[%s]: failed to check token scopes: %s",
                self._name,
                e,
            )
            return []

    def _next_link(self, response: httpx.Response) -> str | None:
        """Extract the next page URL from the Link header.

        GitHub returns absolute URLs in Link headers. We strip the base URL
        prefix so httpx resolves them correctly against the client's base_url.
        """
        link_header = response.headers.get("link", "")
        for part in link_header.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                # Strip the base URL to get a relative path with query params
                if url.startswith(self._base_url):
                    url = url[len(self._base_url) :]
                return url
        return None

    async def list_branches(self, repo_url: str) -> list[str]:
        """List all branches for a specific repository with proper auth."""
        parsed = self._parse_url(repo_url)
        if parsed is None:
            raise GitRepoNotFoundError(f"Cannot parse repository URL: {repo_url}")

        org, repo = parsed
        client = await self._get_client()

        response = await client.get(
            f"/repos/{org}/{repo}/branches",
            params={"per_page": 100},
        )

        if response.status_code in (401, 403):
            raise GitAuthError(
                f"Authentication failed for {org}/{repo}: "
                f"HTTP {response.status_code}. "
                f"Ensure your token has the 'repo' scope (classic PAT) "
                f"or 'contents:read' permission (fine-grained PAT)."
            )

        if response.status_code == 404:
            raise GitRepoNotFoundError(
                f"Repository not found or not accessible: {org}/{repo}. "
                f"For private repos, ensure your token has the 'repo' scope "
                f"(classic PAT) or 'contents:read' permission (fine-grained PAT)."
            )

        if response.status_code != 200:
            logger.warning(
                "GitHubProvider[%s]: unexpected status %d listing branches for %s/%s",
                self._name,
                response.status_code,
                org,
                repo,
            )
            return []

        branches = [branch["name"] for branch in response.json()]

        logger.info(
            "GitHubProvider[%s]: listed %d branches for %s/%s",
            self._name,
            len(branches),
            org,
            repo,
        )
        return branches

    # --- GitWorkflowProvider methods ---

    def _repo_api_path(self, repo_url: str) -> str | None:
        """Get the /repos/{owner}/{repo} API path for a URL."""
        parsed = self._parse_url(repo_url)
        if parsed is None:
            return None
        owner, repo = parsed
        return f"/repos/{owner}/{repo}"

    def _to_pull_request(self, data: dict, repo_url: str) -> PullRequest:
        """Map a GitHub PR JSON response to a domain PullRequest."""
        state = data.get("state", "open")
        if data.get("merged"):
            pr_status = PullRequestStatus.MERGED
        elif state == "closed":
            pr_status = PullRequestStatus.CLOSED
        else:
            pr_status = PullRequestStatus.OPEN

        created_at = None
        if data.get("created_at"):
            created_at = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
        updated_at = None
        if data.get("updated_at"):
            updated_at = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))

        return PullRequest(
            number=data["number"],
            title=data["title"],
            url=data["html_url"],
            repo_url=repo_url,
            provider=GitProviderType.GITHUB,
            source_branch=data.get("head", {}).get("ref", ""),
            target_branch=data.get("base", {}).get("ref", ""),
            status=pr_status,
            description=data.get("body"),
            created_at=created_at,
            updated_at=updated_at,
        )

    async def create_branch(
        self,
        repo_url: str,
        branch_name: str,
        from_branch: str = "main",
    ) -> bool:
        """Create a branch via the GitHub refs API."""
        path = self._repo_api_path(repo_url)
        if path is None:
            return False

        client = await self._get_client()

        try:
            # Get the SHA of the source branch
            ref_resp = await client.get(f"{path}/git/ref/heads/{from_branch}")
            if ref_resp.status_code != 200:
                logger.error(
                    "GitHubProvider[%s]: failed to get ref for %s: %d",
                    self._name,
                    from_branch,
                    ref_resp.status_code,
                )
                return False

            sha = ref_resp.json()["object"]["sha"]

            # Create the new branch
            create_resp = await client.post(
                f"{path}/git/refs",
                json={"ref": f"refs/heads/{branch_name}", "sha": sha},
            )
            if create_resp.status_code in (200, 201):
                logger.info(
                    "GitHubProvider[%s]: created branch %s from %s",
                    self._name,
                    branch_name,
                    from_branch,
                )
                return True

            logger.error(
                "GitHubProvider[%s]: failed to create branch %s: %d %s",
                self._name,
                branch_name,
                create_resp.status_code,
                create_resp.text[:300],
            )
            return False
        except httpx.HTTPError as e:
            logger.error(
                "GitHubProvider[%s]: HTTP error creating branch: %s",
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
        """Create a PR via the GitHub pulls API."""
        path = self._repo_api_path(repo_url)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {repo_url}")

        client = await self._get_client()
        body: dict = {
            "title": title,
            "body": description,
            "head": source_branch,
            "base": target_branch,
        }

        resp = await client.post(f"{path}/pulls", json=body)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create PR: {resp.status_code} {resp.text[:300]}")

        pr_data = resp.json()

        # Add labels if requested
        if labels and pr_data.get("number"):
            await client.post(
                f"{path}/issues/{pr_data['number']}/labels",
                json={"labels": labels},
            )

        logger.info(
            "GitHubProvider[%s]: created PR #%d for %s",
            self._name,
            pr_data["number"],
            repo_url,
        )
        return self._to_pull_request(pr_data, repo_url)

    async def get_pull_request(self, repo_url: str, pr_number: int) -> PullRequest | None:
        """Get a PR by number."""
        path = self._repo_api_path(repo_url)
        if path is None:
            return None

        client = await self._get_client()
        resp = await client.get(f"{path}/pulls/{pr_number}")
        if resp.status_code != 200:
            return None

        return self._to_pull_request(resp.json(), repo_url)

    async def list_pull_requests(self, repo_url: str, status: str = "open") -> list[PullRequest]:
        """List PRs for a repo."""
        path = self._repo_api_path(repo_url)
        if path is None:
            return []

        client = await self._get_client()
        # GitHub uses "state" param: open, closed, all
        params: dict[str, str | int] = {
            "state": status,
            "per_page": 100,
        }
        resp = await client.get(f"{path}/pulls", params=params)
        if resp.status_code != 200:
            return []

        return [self._to_pull_request(pr, repo_url) for pr in resp.json()]

    async def merge_pull_request(
        self,
        repo_url: str,
        pr_number: int,
        merge_method: str = "squash",
    ) -> bool:
        """Merge a PR."""
        path = self._repo_api_path(repo_url)
        if path is None:
            return False

        client = await self._get_client()
        resp = await client.put(
            f"{path}/pulls/{pr_number}/merge",
            json={"merge_method": merge_method},
        )
        if resp.status_code == 200:
            logger.info(
                "GitHubProvider[%s]: merged PR #%d (%s)",
                self._name,
                pr_number,
                merge_method,
            )
            return True

        logger.error(
            "GitHubProvider[%s]: failed to merge PR #%d: %d %s",
            self._name,
            pr_number,
            resp.status_code,
            resp.text[:300],
        )
        return False

    async def get_ci_status(self, repo_url: str, branch: str) -> CIStatus:
        """Get combined CI status for a branch."""
        path = self._repo_api_path(repo_url)
        if path is None:
            return CIStatus.UNKNOWN

        client = await self._get_client()
        resp = await client.get(f"{path}/commits/{branch}/status")
        if resp.status_code != 200:
            return CIStatus.UNKNOWN

        state = resp.json().get("state", "")
        match state:
            case "success":
                return CIStatus.PASSING
            case "failure" | "error":
                return CIStatus.FAILING
            case "pending":
                return CIStatus.PENDING
            case _:
                return CIStatus.UNKNOWN
        raise AssertionError("Unreachable get_ci_status fallthrough")

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

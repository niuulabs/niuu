"""GitHub git provider adapter."""

import logging
import re
from datetime import UTC, datetime
from urllib.parse import quote, urlparse
from uuid import NAMESPACE_URL, uuid5

import httpx

from niuu.adapters.outbound.git_branch_publisher import AuthenticatedGitBranchPublisher
from niuu.domain.delivery import (
    BranchPublicationReceipt,
    BranchPublicationRequest,
    CheckConclusion,
    CheckReceipt,
    CheckRecord,
    MergeReceipt,
    MergeRequest,
    PublicationSource,
    PublicationState,
    ResolvedRef,
    ReviewCandidate,
    ReviewPublication,
    ReviewRequest,
)
from niuu.domain.models import (
    CIStatus,
    GitProviderType,
    PullRequest,
    PullRequestStatus,
    RepoInfo,
)
from niuu.ports.delivery import DeliveryForgeProvider
from niuu.ports.git import (
    GitAuthError,
    GitProvider,
    GitRepoNotFoundError,
    GitWorkflowProvider,
)

logger = logging.getLogger(__name__)


class GitHubProvider(GitProvider, GitWorkflowProvider, DeliveryForgeProvider):
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
        branch_publisher: AuthenticatedGitBranchPublisher | None = None,
        **_extra: object,
    ):
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._branch_publisher = branch_publisher or AuthenticatedGitBranchPublisher()
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
            if org:
                # Try org endpoint first, type=all includes public+private+forks
                url: str | None = f"/orgs/{org}/repos"
                params: dict[str, str | int] = {"per_page": 100, "type": "all"}
            else:
                # No organisation named: everything the token can reach, own
                # repositories and those of every organisation it belongs to.
                if not self._token:
                    raise ValueError(
                        f"GitHubProvider[{self._name}]: listing repositories without an "
                        "organisation needs a token"
                    )
                url = "/user/repos"
                params = {
                    "per_page": 100,
                    "visibility": "all",
                    "affiliation": "owner,collaborator,organization_member",
                }

            logger.info(
                "GitHubProvider[%s]: listing repos for org=%s, url=%s, authenticated=%s",
                self._name,
                org or "<all>",
                url,
                bool(self._token),
            )

            response = await client.get(url, params=params)

            # Fall back to user endpoint if org not found
            if org and response.status_code == 404:
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
                    owner = str(repo_data.get("owner", {}).get("login") or org)
                    repos.append(
                        RepoInfo(
                            provider=GitProviderType.GITHUB,
                            org=owner,
                            name=repo_name,
                            clone_url=f"https://{self._web_host}/{owner}/{repo_name}.git",
                            url=repo_data["html_url"],
                            default_branch=repo_data.get("default_branch", "main"),
                        )
                    )

                url = self._next_link(response)
                if url is not None:
                    response = await client.get(url)

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

        response.raise_for_status()

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

    async def resolve_ref(self, repository: str, ref: str) -> ResolvedRef:
        path = self._repo_api_path(repository)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {repository}")
        client = await self._get_client()
        response = await client.get(f"{path}/commits/{ref}")
        if response.status_code != 200:
            raise RuntimeError(f"Cannot resolve GitHub ref {ref!r}: HTTP {response.status_code}")
        sha = str(response.json().get("sha") or "")
        if not sha:
            raise RuntimeError("GitHub commit response omitted its immutable SHA")
        return ResolvedRef(
            provider=self._name,
            repository=repository,
            ref=ref,
            sha=sha,
            observed_at=datetime.now(UTC),
        )

    async def ensure_review(self, request: ReviewRequest) -> ReviewPublication:
        """Create or recover one campaign PR without duplicating ambiguous requests."""
        resolved = await self.resolve_ref(request.repository, request.source_branch)
        if resolved.sha != request.expected_head_sha:
            raise RuntimeError("GitHub source branch moved before PR publication")
        path = self._repo_api_path(request.repository)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {request.repository}")
        client = await self._get_client()
        marker = f"<!-- niuu-campaign:{request.campaign_id} -->"
        description = f"{request.description.rstrip()}\n\n{marker}".strip()
        listing = await client.get(f"{path}/pulls", params={"state": "open", "per_page": 100})
        if listing.status_code != 200:
            raise RuntimeError(f"Cannot reconcile GitHub PRs: HTTP {listing.status_code}")
        matches = [
            item
            for item in listing.json()
            if marker in str(item.get("body") or "")
            and item.get("head", {}).get("ref") == request.source_branch
            and item.get("base", {}).get("ref") == request.target_branch
        ]
        if len(matches) > 1:
            raise RuntimeError("Multiple open GitHub PRs claim the same campaign")
        created = not matches
        if matches:
            data = matches[0]
            response = await client.patch(
                f"{path}/pulls/{data['number']}",
                json={"title": request.title, "body": description},
            )
        else:
            response = await client.post(
                f"{path}/pulls",
                json={
                    "title": request.title,
                    "body": description,
                    "head": request.source_branch,
                    "base": request.target_branch,
                },
            )
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"GitHub PR publication failed: HTTP {response.status_code} {response.text[:300]}"
            )
        data = response.json()
        candidate_sha = str(data.get("head", {}).get("sha") or "")
        if candidate_sha != request.expected_head_sha:
            raise RuntimeError("Published GitHub PR does not reference the expected candidate")
        if request.labels:
            labels = await client.post(
                f"{path}/issues/{data['number']}/labels",
                json={"labels": list(request.labels)},
            )
            if labels.status_code not in (200, 201):
                raise RuntimeError(f"GitHub PR labels failed: HTTP {labels.status_code}")
        return ReviewPublication(
            receipt_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"github-review:{request.repository}:{request.campaign_id}:{data['number']}",
                )
            ),
            campaign_id=request.campaign_id,
            provider=self._name,
            repository=request.repository,
            review_number=int(data["number"]),
            url=str(data["html_url"]),
            candidate_sha=candidate_sha,
            source_branch=request.source_branch,
            target_branch=request.target_branch,
            created=created,
        )

    async def inspect_delivery_candidate(
        self,
        repo_url: str,
        review_number: int,
        required_checks: tuple[str, ...],
    ) -> tuple[ReviewCandidate, CheckReceipt]:
        """Read exact PR identities and check conclusions from GitHub."""
        path = self._repo_api_path(repo_url)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {repo_url}")
        client = await self._get_client()
        response = await client.get(f"{path}/pulls/{review_number}")
        if response.status_code != 200:
            raise RuntimeError(
                f"Cannot inspect GitHub PR #{review_number}: HTTP {response.status_code}"
            )
        data = response.json()
        candidate_sha = str(data.get("head", {}).get("sha") or "")
        tested_base_sha = str(data.get("base", {}).get("sha") or "")
        source_branch = str(data.get("head", {}).get("ref") or "")
        target_branch = str(data.get("base", {}).get("ref") or "")
        if not candidate_sha or not tested_base_sha or not source_branch or not target_branch:
            raise RuntimeError("GitHub PR response omitted immutable candidate identities")
        ref_response = await client.get(f"{path}/git/ref/heads/{target_branch}")
        if ref_response.status_code != 200:
            raise RuntimeError(f"Cannot inspect GitHub target ref: HTTP {ref_response.status_code}")
        current_target_sha = str(ref_response.json().get("object", {}).get("sha") or "")
        if not current_target_sha:
            raise RuntimeError("GitHub target ref response omitted its commit SHA")

        checks_by_name: dict[str, CheckRecord] = {}
        runs_response = await client.get(
            f"{path}/commits/{candidate_sha}/check-runs", params={"per_page": 100}
        )
        if runs_response.status_code != 200:
            raise RuntimeError(
                f"Cannot inspect GitHub check runs: HTTP {runs_response.status_code}"
            )
        for run in runs_response.json().get("check_runs", []):
            name = str(run.get("name") or "unnamed-check")
            checks_by_name.setdefault(name, self._github_check(run))

        statuses_response = await client.get(f"{path}/commits/{candidate_sha}/status")
        if statuses_response.status_code != 200:
            raise RuntimeError(
                f"Cannot inspect GitHub commit statuses: HTTP {statuses_response.status_code}"
            )
        for status in statuses_response.json().get("statuses", []):
            name = str(status.get("context") or "unnamed-status")
            checks_by_name.setdefault(name, self._github_status(status))
        for missing in sorted(set(required_checks) - checks_by_name.keys()):
            checks_by_name[missing] = CheckRecord(name=missing, conclusion=CheckConclusion.UNKNOWN)
        checks = tuple(checks_by_name.values())
        observed_at = datetime.now(UTC)
        receipt = CheckReceipt(
            receipt_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"github-checks:{repo_url}:{review_number}:{candidate_sha}:{observed_at.isoformat()}",
                )
            ),
            provider=self._name,
            repository=repo_url,
            review_number=review_number,
            candidate_sha=candidate_sha,
            tested_base_sha=tested_base_sha,
            checks=checks,
            observed_at=observed_at,
        )
        candidate = ReviewCandidate(
            provider=self._name,
            repository=repo_url,
            review_number=review_number,
            source_branch=source_branch,
            target_branch=target_branch,
            candidate_sha=candidate_sha,
            tested_base_sha=tested_base_sha,
            current_target_sha=current_target_sha,
            mergeable=data.get("mergeable") is True and data.get("state") == "open",
            checks=checks,
            serialized_publication=True,
        )
        return candidate, receipt

    @staticmethod
    def _github_check(run: dict) -> CheckRecord:
        status = str(run.get("status") or "")
        conclusion_value = str(run.get("conclusion") or "")
        if status != "completed":
            conclusion = CheckConclusion.PENDING
        else:
            match conclusion_value:
                case "success":
                    conclusion = CheckConclusion.PASSING
                case "failure" | "timed_out" | "action_required" | "startup_failure":
                    conclusion = CheckConclusion.FAILING
                case "cancelled":
                    conclusion = CheckConclusion.CANCELED
                case "skipped" | "neutral" | "stale":
                    conclusion = CheckConclusion.SKIPPED
                case _:
                    conclusion = CheckConclusion.UNKNOWN
        return CheckRecord(
            name=str(run.get("name") or "unnamed-check"),
            conclusion=conclusion,
            details_url=run.get("details_url"),
        )

    @staticmethod
    def _github_status(status: dict) -> CheckRecord:
        match str(status.get("state") or ""):
            case "success":
                conclusion = CheckConclusion.PASSING
            case "failure" | "error":
                conclusion = CheckConclusion.FAILING
            case "pending":
                conclusion = CheckConclusion.PENDING
            case _:
                conclusion = CheckConclusion.UNKNOWN
        return CheckRecord(
            name=str(status.get("context") or "unnamed-status"),
            conclusion=conclusion,
            details_url=status.get("target_url"),
        )

    async def publish_branch(
        self,
        source: PublicationSource,
        request: BranchPublicationRequest,
    ) -> BranchPublicationReceipt:
        if (
            source.repository != request.repository
            or source.candidate_sha != request.expected_head_sha
        ):
            raise RuntimeError("Publication source does not match the requested repository and SHA")
        if not self._token:
            raise RuntimeError("GitHub branch publication requires a configured credential")
        parsed = self._parse_url(request.repository)
        path = self._repo_api_path(request.repository)
        if parsed is None or path is None:
            raise ValueError(f"Unsupported repo URL: {request.repository}")
        client = await self._get_client()
        branch_ref = quote(request.branch, safe="")
        before = await client.get(f"{path}/git/ref/heads/{branch_ref}")
        if before.status_code == 200:
            previous = str(before.json().get("object", {}).get("sha") or "")
            if previous != request.expected_remote_sha:
                raise RuntimeError("GitHub branch moved before publication")
        elif before.status_code == 404:
            previous = None
            if request.expected_remote_sha is not None:
                raise RuntimeError("Expected GitHub branch is missing")
        else:
            raise RuntimeError(
                f"Cannot inspect GitHub publication branch: HTTP {before.status_code}"
            )
        org, repo = parsed
        await self._branch_publisher.publish(
            source_repository=source.repository_path,
            source_sha=source.candidate_sha,
            remote_url=f"https://{self._web_host}/{org}/{repo}.git",
            branch=request.branch,
            expected_remote_sha=request.expected_remote_sha,
            username="x-access-token",
            token=self._token,
        )
        after = await client.get(f"{path}/git/ref/heads/{branch_ref}")
        resulting = (
            str(after.json().get("object", {}).get("sha") or "") if after.status_code == 200 else ""
        )
        if resulting != request.expected_head_sha:
            raise RuntimeError("GitHub did not expose the exact published commit")
        return BranchPublicationReceipt(
            receipt_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"github-branch:{request.repository}:{request.campaign_id}:{request.branch}:{request.expected_head_sha}",
                )
            ),
            campaign_id=request.campaign_id,
            provider=self._name,
            repository=request.repository,
            branch=request.branch,
            source_sha=request.expected_head_sha,
            previous_remote_sha=previous,
            resulting_remote_sha=resulting,
            published_at=datetime.now(UTC),
        )

    async def conditional_merge(self, request: MergeRequest) -> MergeReceipt:
        """Enqueue an exact PR head through GitHub's serialized merge queue."""
        candidate, _ = await self.inspect_delivery_candidate(
            request.repository, request.review_number, ()
        )
        if candidate.candidate_sha != request.expected_head_sha:
            raise RuntimeError("GitHub PR head moved after evidence was accepted")
        if candidate.current_target_sha != request.expected_base_sha:
            raise RuntimeError("GitHub target branch moved; test a new merge candidate")
        if candidate.tested_base_sha != request.expected_base_sha:
            raise RuntimeError("GitHub PR candidate was not tested from the expected target")
        if candidate.target_branch != request.expected_target_branch:
            raise RuntimeError("GitHub PR target branch differs from the requested target")
        if not candidate.mergeable:
            raise RuntimeError("GitHub reports that the PR is not mergeable")

        path = self._repo_api_path(request.repository)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {request.repository}")
        client = await self._get_client()
        rules_response = await client.get(
            f"{path}/rules/branches/{quote(candidate.target_branch, safe='')}"
        )
        if rules_response.status_code != 200:
            raise RuntimeError(
                f"Cannot verify GitHub merge-queue rules: HTTP {rules_response.status_code}"
            )
        rules = rules_response.json()
        merge_queue = next(
            (rule for rule in rules if rule.get("type") == "merge_queue"),
            None,
        )
        required_checks = next(
            (rule for rule in rules if rule.get("type") == "required_status_checks"),
            None,
        )
        check_parameters = (required_checks or {}).get("parameters") or {}
        if merge_queue is None:
            raise RuntimeError(
                "GitHub target has no enforced merge-queue rule; conditional publication refused"
            )
        if (
            required_checks is None
            or check_parameters.get("strict_required_status_checks_policy") is not True
            or not check_parameters.get("required_status_checks")
        ):
            raise RuntimeError(
                "GitHub target lacks strict required status checks; conditional publication refused"
            )
        response = await client.put(
            f"{path}/pulls/{request.review_number}/merge-async",
            headers={"X-GitHub-Api-Version": "2026-03-10"},
            json={
                "sha": request.expected_head_sha,
                "merge_method": request.method,
                "merge_action": "merge_queue",
            },
        )
        if response.status_code not in (200, 202, 409):
            raise RuntimeError(
                "GitHub merge queue rejected conditional publication: "
                f"HTTP {response.status_code} {response.text[:300]}"
            )
        data = response.json()
        details = data.get("details") or {}
        operation_id = (
            str(
                data.get("uuid")
                or data.get("id")
                or (details.get("uuid") if isinstance(details, dict) else "")
                or ""
            )
            or None
        )
        if response.status_code == 200 and data.get("status") == "merged":
            if not operation_id:
                raise RuntimeError("GitHub merged response omitted required operation ID")
            return await self.reconcile_merge(
                request.model_copy(update={"provider_operation_id": operation_id})
            )
        if not operation_id:
            raise RuntimeError("GitHub merge queue response omitted its operation ID")
        return MergeReceipt(
            receipt_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"github-merge:{request.repository}:{request.review_number}:{request.expected_head_sha}",
                )
            ),
            campaign_id=request.campaign_id,
            provider=self._name,
            repository=request.repository,
            review_number=request.review_number,
            source_sha=request.expected_head_sha,
            base_sha=request.expected_base_sha,
            target_branch=request.expected_target_branch,
            method=request.method,
            state=PublicationState.QUEUED,
            provider_operation_id=operation_id,
        )

    async def reconcile_merge(self, request: MergeRequest) -> MergeReceipt:
        """Reconcile one durable asynchronous merge operation without mutating it."""
        if not request.provider_operation_id:
            raise RuntimeError(
                "GitHub asynchronous merge reconciliation requires its provider operation ID"
            )
        return await self._reconcile_merge(request)

    async def _reconcile_merge(self, request: MergeRequest) -> MergeReceipt:
        path = self._repo_api_path(request.repository)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {request.repository}")
        client = await self._get_client()
        response = await client.get(f"{path}/pulls/{request.review_number}")
        if response.status_code != 200:
            raise RuntimeError(f"Cannot reconcile GitHub PR: HTTP {response.status_code}")
        data = response.json()
        source_sha = str(data.get("head", {}).get("sha") or "")
        base_sha = str(data.get("base", {}).get("sha") or "")
        target_branch = str(data.get("base", {}).get("ref") or "")
        if not source_sha or not base_sha or not target_branch:
            raise RuntimeError("GitHub PR omitted its source or target identity")
        merged = data.get("merged") is True
        receipt = self._github_merge_receipt(
            request,
            state=PublicationState.FAILED,
            source_sha=source_sha,
            base_sha=request.expected_base_sha if merged else base_sha,
            target_branch=target_branch,
        )
        if (
            source_sha != request.expected_head_sha
            or target_branch != request.expected_target_branch
            or (not merged and base_sha != request.expected_base_sha)
        ):
            return receipt
        if not merged and str(data.get("state") or "").casefold() == "closed":
            return receipt
        operation = await client.get(
            f"{path}/pulls/{request.review_number}/merge-async/"
            f"{quote(request.provider_operation_id, safe='')}",
            headers={"X-GitHub-Api-Version": "2026-03-10"},
        )
        if operation.status_code != 200:
            raise RuntimeError(
                "Cannot reconcile GitHub asynchronous merge operation: "
                f"HTTP {operation.status_code}"
            )
        operation_data = operation.json()
        operation_status = str(operation_data.get("status") or "").casefold()
        details = operation_data.get("details") or {}
        if not isinstance(details, dict):
            raise RuntimeError("GitHub asynchronous merge result omitted operation details")
        operation_uuid = str(details.get("uuid") or "")
        operation_head = str(details.get("expected_head_sha") or "")
        operation_method = str(details.get("merge_method") or "")
        if operation_uuid and operation_uuid != request.provider_operation_id:
            return receipt
        if operation_head and operation_head != request.expected_head_sha:
            return receipt
        if operation_method and operation_method != request.method:
            return receipt
        if operation_status == "pending":
            return receipt.model_copy(update={"state": PublicationState.QUEUED})
        if not operation_status:
            raise RuntimeError("GitHub asynchronous merge result omitted its status")
        if operation_status != "merged":
            return receipt

        if not merged:
            return receipt.model_copy(update={"state": PublicationState.QUEUED})

        result_sha = str(details.get("sha") or data.get("merge_commit_sha") or "")
        if not result_sha:
            raise RuntimeError("GitHub merged operation omitted its resulting commit identity")
        result_base_sha = await self._github_merge_base_sha(
            client,
            path,
            request,
            result_sha=result_sha,
        )
        if result_base_sha != request.expected_base_sha:
            return receipt.model_copy(update={"base_sha": result_base_sha})
        ref_response = await client.get(f"{path}/git/ref/heads/{target_branch}")
        if ref_response.status_code != 200:
            raise RuntimeError("Cannot verify GitHub canonical target branch")
        canonical_sha = str(ref_response.json().get("object", {}).get("sha") or "")
        if canonical_sha != result_sha:
            raise RuntimeError(
                "GitHub target advanced beyond the merge result; ancestry proof is unavailable"
            )
        return self._github_merge_receipt(
            request,
            state=PublicationState.MERGED,
            source_sha=source_sha,
            base_sha=request.expected_base_sha,
            target_branch=target_branch,
            result_sha=result_sha,
            canonical_target_sha=canonical_sha,
            verified_at=datetime.now(UTC),
        )

    async def _github_merge_base_sha(
        self,
        client: httpx.AsyncClient,
        path: str,
        request: MergeRequest,
        *,
        result_sha: str,
    ) -> str:
        """Return the provider-proven base from which the merged result was built."""
        commit_count = 1
        if request.method == "rebase":
            commit_count = 0
            page = 1
            while True:
                commits_response = await client.get(
                    f"{path}/pulls/{request.review_number}/commits",
                    params={"per_page": 100, "page": page},
                )
                if commits_response.status_code != 200:
                    raise RuntimeError("Cannot verify GitHub rebased commit range")
                commits = commits_response.json()
                if not isinstance(commits, list) or any(
                    not isinstance(commit, dict) for commit in commits
                ):
                    raise RuntimeError("GitHub pull request commits response is invalid")
                commit_count += len(commits)
                if len(commits) < 100:
                    break
                page += 1
            if commit_count == 0:
                raise RuntimeError("GitHub rebased pull request has no commits")

        current_sha = result_sha
        for _ in range(commit_count):
            commit_response = await client.get(f"{path}/git/commits/{current_sha}")
            if commit_response.status_code != 200:
                raise RuntimeError("Cannot verify GitHub merge result ancestry")
            commit = commit_response.json()
            parents = commit.get("parents") or []
            parent_shas = [
                str(parent.get("sha") or "") for parent in parents if isinstance(parent, dict)
            ]
            if request.method == "merge":
                if len(parent_shas) != 2 or parent_shas[1] != request.expected_head_sha:
                    raise RuntimeError("GitHub merge result was not built from the expected head")
                return parent_shas[0]
            if len(parent_shas) != 1 or not parent_shas[0]:
                raise RuntimeError(
                    f"GitHub {request.method} result has an unexpected parent structure"
                )
            current_sha = parent_shas[0]
            if request.method == "squash":
                return current_sha
        return current_sha

    def _github_merge_receipt(
        self,
        request: MergeRequest,
        *,
        state: PublicationState,
        source_sha: str,
        base_sha: str,
        target_branch: str,
        result_sha: str | None = None,
        canonical_target_sha: str | None = None,
        verified_at: datetime | None = None,
    ) -> MergeReceipt:
        return MergeReceipt(
            receipt_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"github-merge:{request.repository}:{request.review_number}:{request.expected_head_sha}",
                )
            ),
            campaign_id=request.campaign_id,
            provider=self._name,
            repository=request.repository,
            review_number=request.review_number,
            source_sha=source_sha,
            base_sha=base_sha,
            target_branch=target_branch,
            result_sha=result_sha,
            canonical_target_sha=canonical_target_sha,
            method=request.method,
            state=state,
            provider_operation_id=request.provider_operation_id,
            verified_at=verified_at,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

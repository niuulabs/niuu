"""GitLab git provider adapter."""

import logging
import re
from datetime import UTC, datetime
from urllib.parse import quote_plus, urlparse
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


class _MergeTrainEntryNotFoundError(RuntimeError):
    """The provider response contains no entry for the requested merge operation."""


def _select_merge_train_entry(
    payload: object,
    request: MergeRequest,
    *,
    operation_id: str | None,
    operation: str,
) -> dict[str, object]:
    """Select one exact merge-train entry from GitLab's object or array response.

    GitLab documents an array for enqueue and an object for the per-MR lookup.
    Some deployed versions return either shape. The enqueue request's ``sha`` and
    the preceding candidate inspection bind the head even when GitLab omits it
    from the response; any explicit response head must still agree.
    """
    if isinstance(payload, dict):
        entries = [payload]
    elif isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        entries = payload
    else:
        raise RuntimeError(f"GitLab merge train {operation} response is invalid")

    matches: list[dict[str, object]] = []
    missing_operation_id = False
    for entry in entries:
        merge_request = entry.get("merge_request")
        if not isinstance(merge_request, dict):
            continue
        if str(merge_request.get("iid") or "") != str(request.review_number):
            continue
        if str(entry.get("target_branch") or "") != request.expected_target_branch:
            continue

        explicit_heads = {
            str(value)
            for value in (
                entry.get("head_sha"),
                merge_request.get("sha"),
                (
                    merge_request.get("diff_refs", {}).get("head_sha")
                    if isinstance(merge_request.get("diff_refs"), dict)
                    else None
                ),
            )
            if value
        }
        if explicit_heads and explicit_heads != {request.expected_head_sha}:
            continue

        entry_id = entry.get("id")
        if isinstance(entry_id, bool) or not isinstance(entry_id, (int, str)) or not str(entry_id):
            missing_operation_id = True
            continue
        if operation_id is not None and str(entry_id) != operation_id:
            continue
        matches.append(entry)

    if operation_id is None and missing_operation_id:
        raise RuntimeError(f"GitLab merge train {operation} omitted its operation ID")
    if len(matches) > 1:
        raise RuntimeError(f"GitLab merge train {operation} response is ambiguous for the exact MR")
    if not matches:
        if missing_operation_id:
            raise RuntimeError(f"GitLab merge train {operation} omitted its operation ID")
        raise _MergeTrainEntryNotFoundError(
            f"GitLab merge train {operation} response omitted the exact MR operation"
        )
    return matches[0]


class GitLabProvider(GitProvider, GitWorkflowProvider, DeliveryForgeProvider):
    """GitLab git provider implementation.

    Supports GitLab.com and self-hosted GitLab instances.
    Each instance of this class represents one GitLab server.
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        token: str | None = None,
        orgs: tuple[str, ...] | list[str] | str = (),
        groups: tuple[str, ...] | list[str] | str | None = None,
        branch_publisher: AuthenticatedGitBranchPublisher | None = None,
        **_extra: object,
    ):
        self._name = name
        self._base_url = (base_url if "://" in base_url else f"https://{base_url}").rstrip("/")
        self._token = token
        self._branch_publisher = branch_publisher or AuthenticatedGitBranchPublisher()
        if groups is not None:
            orgs = groups
        if isinstance(orgs, str):
            self._orgs = tuple(o.strip() for o in orgs.split(",") if o.strip())
        else:
            self._orgs = tuple(orgs)
        self._client: httpx.AsyncClient | None = None

        # Extract host from base URL for matching
        parsed = urlparse(self._base_url)
        self._host = parsed.netloc or parsed.path

        # Build URL patterns for this instance
        host_escaped = re.escape(self._host)
        self._patterns = [
            re.compile(
                rf"^(?:https?://)?{host_escaped}/([^/?#]+(?:/[^/?#]+)*)/([^/?#]+?)(?:\.git)?/?$"
            ),
            re.compile(rf"^git@{host_escaped}:([^/?#]+(?:/[^/?#]+)*)/([^/?#]+?)(?:\.git)?$"),
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
            clone_url=f"{self._base_url}/{org}/{repo}.git",
            url=f"{self._base_url}/{org}/{repo}",
        )

    def get_clone_url(self, repo_url: str) -> str | None:
        """Get authenticated clone URL."""
        parsed = self._parse_url(repo_url)
        if parsed is None:
            return None

        org, repo = parsed

        if self._token:
            scheme, host = self._base_url.split("://", 1)
            return f"{scheme}://oauth2:{self._token}@{host}/{org}/{repo}.git"

        return f"{self._base_url}/{org}/{repo}.git"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self._token:
                headers["PRIVATE-TOKEN"] = self._token

            self._client = httpx.AsyncClient(
                base_url=f"{self._base_url}/api/v4",
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
            page = 1
            if org:
                # Try group endpoint first
                url = f"/groups/{group_path}/projects"
                params: dict[str, str | int | bool] = {
                    "per_page": 100,
                    "include_subgroups": True,
                    "page": page,
                }
            else:
                # No group named: every project the token is a member of.
                if not self._token:
                    raise ValueError(
                        f"GitLabProvider[{self._name}]: listing projects without a group "
                        "needs a token"
                    )
                url = "/projects"
                params = {"per_page": 100, "membership": True, "page": page}

            logger.info(
                "GitLabProvider[%s]: listing repos for org=%s, url=%s, authenticated=%s",
                self._name,
                org,
                url,
                bool(self._token),
            )

            response = await client.get(url, params=params)

            # Fall back to user projects if group not found
            if org and response.status_code == 404:
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
                    full_path = project.get("path_with_namespace")
                    namespace = (
                        full_path.rsplit("/", 1)[0]
                        if full_path
                        else project.get("namespace", {}).get("full_path")
                        or project.get("namespace", {}).get("path", org)
                    )
                    repos.append(
                        RepoInfo(
                            provider=GitProviderType.GITLAB,
                            org=namespace,
                            name=repo_name,
                            clone_url=project.get("http_url_to_repo")
                            or f"{self._base_url}/{namespace}/{repo_name}.git",
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

            response.raise_for_status()

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

    async def inspect_delivery_candidate(
        self,
        repo_url: str,
        review_number: int,
        required_checks: tuple[str, ...],
    ) -> tuple[ReviewCandidate, CheckReceipt]:
        """Read exact MR head/base and job conclusions from GitLab."""
        path = self._project_api_path(repo_url)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {repo_url}")
        client = await self._get_client()
        response = await client.get(f"{path}/merge_requests/{review_number}")
        if response.status_code != 200:
            raise RuntimeError(
                f"Cannot inspect GitLab MR !{review_number}: HTTP {response.status_code}"
            )
        data = response.json()
        candidate_sha = str(data.get("sha") or data.get("diff_refs", {}).get("head_sha") or "")
        tested_base_sha = str(data.get("diff_refs", {}).get("start_sha") or "")
        target_branch = str(data.get("target_branch") or "")
        source_branch = str(data.get("source_branch") or "")
        if not candidate_sha or not tested_base_sha or not target_branch or not source_branch:
            raise RuntimeError("GitLab MR response omitted immutable candidate identities")

        branch_response = await client.get(
            f"{path}/repository/branches/{quote_plus(target_branch)}"
        )
        if branch_response.status_code != 200:
            raise RuntimeError(
                f"Cannot inspect GitLab target branch: HTTP {branch_response.status_code}"
            )
        current_target_sha = str(branch_response.json().get("commit", {}).get("id") or "")
        if not current_target_sha:
            raise RuntimeError("GitLab target branch response omitted its commit SHA")

        checks: list[CheckRecord] = []
        pipeline = data.get("head_pipeline") or {}
        if pipeline and str(pipeline.get("sha") or "") != candidate_sha:
            await self._verify_merged_results_pipeline(
                client,
                path,
                pipeline,
                review_number=review_number,
                candidate_sha=candidate_sha,
                tested_base_sha=tested_base_sha,
            )
        pipeline_id = pipeline.get("id")
        if pipeline_id is not None:
            checks.extend(await self._delivery_pipeline_checks(client, path, pipeline_id))

        found = {check.name for check in checks}
        for missing in sorted(set(required_checks) - found):
            checks.append(CheckRecord(name=missing, conclusion=CheckConclusion.UNKNOWN))

        observed_at = datetime.now(UTC)
        receipt = CheckReceipt(
            receipt_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"gitlab-checks:{repo_url}:{review_number}:{candidate_sha}:{observed_at.isoformat()}",
                )
            ),
            provider=self._name,
            repository=repo_url,
            review_number=review_number,
            candidate_sha=candidate_sha,
            tested_base_sha=tested_base_sha,
            checks=tuple(checks),
            observed_at=observed_at,
        )
        merge_status = str(data.get("detailed_merge_status") or data.get("merge_status") or "")
        candidate = ReviewCandidate(
            provider=self._name,
            repository=repo_url,
            review_number=review_number,
            source_branch=source_branch,
            target_branch=target_branch,
            candidate_sha=candidate_sha,
            tested_base_sha=tested_base_sha,
            current_target_sha=current_target_sha,
            mergeable=merge_status in {"mergeable", "can_be_merged"},
            checks=tuple(checks),
            serialized_publication=True,
        )
        return candidate, receipt

    @staticmethod
    async def _verify_merged_results_pipeline(
        client: httpx.AsyncClient,
        project_path: str,
        pipeline: dict,
        *,
        review_number: int,
        candidate_sha: str,
        tested_base_sha: str,
    ) -> None:
        """Bind GitLab's temporary merge commit to the exact MR source and base."""
        pipeline_id = pipeline.get("id")
        pipeline_sha = str(pipeline.get("sha") or "")
        if not pipeline_id or not pipeline_sha:
            raise RuntimeError("GitLab head pipeline omitted its immutable identity")
        response = await client.get(f"{project_path}/pipelines/{pipeline_id}")
        if response.status_code != 200:
            raise RuntimeError(
                f"Cannot inspect GitLab merged-results pipeline: HTTP {response.status_code}"
            )
        details = response.json()
        if (
            details.get("id") != pipeline_id
            or details.get("sha") != pipeline_sha
            or details.get("source") != "merge_request_event"
            or details.get("ref") != f"refs/merge-requests/{review_number}/merge"
        ):
            raise RuntimeError("GitLab head pipeline is stale for the MR candidate")
        commit_response = await client.get(
            f"{project_path}/repository/commits/{quote_plus(pipeline_sha)}"
        )
        if commit_response.status_code != 200:
            raise RuntimeError(
                f"Cannot inspect GitLab merged-results commit: HTTP {commit_response.status_code}"
            )
        commit = commit_response.json()
        if commit.get("id") != pipeline_sha or commit.get("parent_ids") != [
            tested_base_sha,
            candidate_sha,
        ]:
            raise RuntimeError("GitLab merged-results pipeline has a stale source or target")

    async def _delivery_pipeline_checks(
        self,
        client: httpx.AsyncClient,
        project_path: str,
        pipeline_id: int,
    ) -> list[CheckRecord]:
        checks: list[CheckRecord] = []
        page = 1
        while True:
            response = await client.get(
                f"{project_path}/pipelines/{pipeline_id}/jobs",
                params={"per_page": 100, "include_retried": False, "page": page},
            )
            if response.status_code != 200:
                raise RuntimeError(
                    f"Cannot inspect GitLab pipeline jobs: HTTP {response.status_code}"
                )
            checks.extend(self._gitlab_check(job) for job in response.json())
            next_page = response.headers.get("x-next-page", "")
            if not next_page:
                return checks
            if not next_page.isascii() or not next_page.isdecimal() or int(next_page) <= page:
                raise RuntimeError("GitLab pipeline job pagination did not advance")
            page = int(next_page)

    async def resolve_ref(self, repository: str, ref: str) -> ResolvedRef:
        path = self._project_api_path(repository)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {repository}")
        client = await self._get_client()
        response = await client.get(f"{path}/repository/commits/{quote_plus(ref)}")
        if response.status_code != 200:
            raise RuntimeError(f"Cannot resolve GitLab ref {ref!r}: HTTP {response.status_code}")
        sha = str(response.json().get("id") or "")
        if not sha:
            raise RuntimeError("GitLab commit response omitted its immutable SHA")
        return ResolvedRef(
            provider=self._name,
            repository=repository,
            ref=ref,
            sha=sha,
            observed_at=datetime.now(UTC),
        )

    async def ensure_review(self, request: ReviewRequest) -> ReviewPublication:
        """Create or recover one campaign MR without duplicating ambiguous requests."""
        resolved = await self.resolve_ref(request.repository, request.source_branch)
        if resolved.sha != request.expected_head_sha:
            raise RuntimeError("GitLab source branch moved before MR publication")
        path = self._project_api_path(request.repository)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {request.repository}")
        client = await self._get_client()
        marker = f"<!-- niuu-campaign:{request.campaign_id} -->"
        description = f"{request.description.rstrip()}\n\n{marker}".strip()
        listing = await client.get(
            f"{path}/merge_requests",
            params={
                "state": "opened",
                "source_branch": request.source_branch,
                "target_branch": request.target_branch,
                "per_page": 100,
            },
        )
        if listing.status_code != 200:
            raise RuntimeError(f"Cannot reconcile GitLab MRs: HTTP {listing.status_code}")
        matches = [item for item in listing.json() if marker in str(item.get("description") or "")]
        if len(matches) > 1:
            raise RuntimeError("Multiple open GitLab MRs claim the same campaign")
        created = not matches
        if matches:
            data = matches[0]
            response = await client.put(
                f"{path}/merge_requests/{data['iid']}",
                json={
                    "title": request.title,
                    "description": description,
                    "labels": ",".join(request.labels),
                },
            )
        else:
            response = await client.post(
                f"{path}/merge_requests",
                json={
                    "title": request.title,
                    "description": description,
                    "source_branch": request.source_branch,
                    "target_branch": request.target_branch,
                    "labels": ",".join(request.labels),
                },
            )
        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"GitLab MR publication failed: HTTP {response.status_code} {response.text[:300]}"
            )
        data = response.json()
        candidate_sha = str(data.get("sha") or data.get("diff_refs", {}).get("head_sha") or "")
        if candidate_sha != request.expected_head_sha:
            raise RuntimeError("Published GitLab MR does not reference the expected candidate")
        return ReviewPublication(
            receipt_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"gitlab-review:{request.repository}:{request.campaign_id}:{data['iid']}",
                )
            ),
            campaign_id=request.campaign_id,
            provider=self._name,
            repository=request.repository,
            review_number=int(data["iid"]),
            url=str(data["web_url"]),
            candidate_sha=candidate_sha,
            source_branch=request.source_branch,
            target_branch=request.target_branch,
            created=created,
        )

    @staticmethod
    def _gitlab_check(job: dict) -> CheckRecord:
        status = str(job.get("status") or "")
        match status:
            case "success":
                conclusion = CheckConclusion.PASSING
            case "failed":
                conclusion = CheckConclusion.FAILING
            case "pending" | "running" | "created" | "preparing" | "waiting_for_resource":
                conclusion = CheckConclusion.PENDING
            case "canceled":
                conclusion = CheckConclusion.CANCELED
            case "skipped" | "manual":
                conclusion = CheckConclusion.SKIPPED
            case _:
                conclusion = CheckConclusion.UNKNOWN
        return CheckRecord(
            name=str(job.get("name") or "unnamed-job"),
            conclusion=conclusion,
            details_url=job.get("web_url"),
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
            raise RuntimeError("GitLab branch publication requires a configured credential")
        parsed = self._parse_url(request.repository)
        path = self._project_api_path(request.repository)
        if parsed is None or path is None:
            raise ValueError(f"Unsupported repo URL: {request.repository}")
        client = await self._get_client()
        branch_path = f"{path}/repository/branches/{quote_plus(request.branch)}"
        before = await client.get(branch_path)
        if before.status_code == 200:
            previous = str(before.json().get("commit", {}).get("id") or "")
            if previous != request.expected_remote_sha:
                raise RuntimeError("GitLab branch moved before publication")
        elif before.status_code == 404:
            previous = None
            if request.expected_remote_sha is not None:
                raise RuntimeError("Expected GitLab branch is missing")
        else:
            raise RuntimeError(
                f"Cannot inspect GitLab publication branch: HTTP {before.status_code}"
            )
        group, repo = parsed
        await self._branch_publisher.publish(
            source_repository=source.repository_path,
            source_sha=source.candidate_sha,
            remote_url=f"{self._base_url}/{group}/{repo}.git",
            branch=request.branch,
            expected_remote_sha=request.expected_remote_sha,
            username="oauth2",
            token=self._token,
        )
        after = await client.get(branch_path)
        resulting = (
            str(after.json().get("commit", {}).get("id") or "") if after.status_code == 200 else ""
        )
        if resulting != request.expected_head_sha:
            raise RuntimeError("GitLab did not expose the exact published commit")
        return BranchPublicationReceipt(
            receipt_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"gitlab-branch:{request.repository}:{request.campaign_id}:{request.branch}:{request.expected_head_sha}",
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
        """Place an exact MR head on GitLab's serialized merge train."""
        if request.method == "rebase":
            raise RuntimeError("GitLab merge trains cannot guarantee a requested rebase method")
        path = self._project_api_path(request.repository)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {request.repository}")
        client = await self._get_client()

        # A previous enqueue can succeed remotely while its response is lost or
        # cannot be parsed. Recover that exact operation before considering a
        # second POST. Reconciliation independently proves the current MR head,
        # target, result ancestry, method, and canonical target.
        existing_train = await client.get(
            f"{path}/merge_trains/merge_requests/{request.review_number}"
        )
        if existing_train.status_code == 200:
            train = _select_merge_train_entry(
                existing_train.json(),
                request,
                operation_id=None,
                operation="recovery",
            )
            return await self.reconcile_merge(
                request.model_copy(update={"provider_operation_id": str(train["id"])})
            )
        if existing_train.status_code != 404:
            raise RuntimeError(
                "Cannot check for an existing GitLab merge train: "
                f"HTTP {existing_train.status_code}"
            )

        candidate, _ = await self.inspect_delivery_candidate(
            request.repository, request.review_number, ()
        )
        if candidate.candidate_sha != request.expected_head_sha:
            raise RuntimeError("GitLab MR head moved after evidence was accepted")
        if candidate.current_target_sha != request.expected_base_sha:
            raise RuntimeError("GitLab target branch moved; test a new merge candidate")
        if candidate.tested_base_sha != request.expected_base_sha:
            raise RuntimeError("GitLab MR candidate was not tested from the expected target")
        if candidate.target_branch != request.expected_target_branch:
            raise RuntimeError("GitLab MR target branch differs from the requested target")
        if not candidate.mergeable:
            raise RuntimeError("GitLab reports that the MR is not mergeable")

        project_response = await client.get(path)
        if project_response.status_code != 200:
            raise RuntimeError(
                f"Cannot verify GitLab merge-train safety: HTTP {project_response.status_code}"
            )
        project = project_response.json()
        missing_capabilities: list[str] = []
        if project.get("merge_trains_enabled") is not True:
            missing_capabilities.append("merge trains are not enabled")
        if project.get("merge_pipelines_enabled") is not True:
            missing_capabilities.append("merged-results pipelines are not enabled")
        if project.get("only_allow_merge_if_pipeline_succeeds") is not True:
            missing_capabilities.append("successful pipelines are not required for merge")
        if missing_capabilities:
            raise RuntimeError(
                "GitLab project cannot guarantee conditional publication: "
                + "; ".join(missing_capabilities)
            )
        response = await client.post(
            f"{path}/merge_trains/merge_requests/{request.review_number}",
            json={
                "sha": request.expected_head_sha,
                "squash": request.method == "squash",
                "auto_merge": False,
            },
        )
        if response.status_code not in (201, 202):
            raise RuntimeError(
                "GitLab merge train rejected conditional publication: "
                f"HTTP {response.status_code} {response.text[:300]}"
            )
        train = _select_merge_train_entry(
            response.json(),
            request,
            operation_id=None,
            operation="enqueue",
        )
        operation_id = str(train["id"])
        return MergeReceipt(
            receipt_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"gitlab-merge:{request.repository}:{request.review_number}:{request.expected_head_sha}",
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
        """Reconcile the exact merge-train entry without mutating remote state."""
        if request.method == "rebase":
            raise RuntimeError("GitLab merge trains cannot guarantee a requested rebase method")
        path = self._project_api_path(request.repository)
        if path is None:
            raise ValueError(f"Unsupported repo URL: {request.repository}")
        client = await self._get_client()
        response = await client.get(f"{path}/merge_requests/{request.review_number}")
        if response.status_code != 200:
            raise RuntimeError(f"Cannot reconcile GitLab MR: HTTP {response.status_code}")
        data = response.json()
        source_sha = str(data.get("sha") or "")
        target_branch = str(data.get("target_branch") or "")
        if not source_sha or not target_branch:
            raise RuntimeError("GitLab MR omitted its source or target identity")
        branch_response = await client.get(
            f"{path}/repository/branches/{quote_plus(target_branch)}"
        )
        if branch_response.status_code != 200:
            raise RuntimeError("Cannot verify GitLab canonical target branch")
        canonical_sha = str(branch_response.json().get("commit", {}).get("id") or "")
        if not canonical_sha:
            raise RuntimeError("GitLab target branch omitted its commit identity")
        merged = data.get("state") == "merged"
        receipt = self._gitlab_merge_receipt(
            request,
            state=PublicationState.FAILED,
            source_sha=source_sha,
            base_sha=request.expected_base_sha if merged else canonical_sha,
            target_branch=target_branch,
        )
        if (
            source_sha != request.expected_head_sha
            or target_branch != request.expected_target_branch
            or (not merged and canonical_sha != request.expected_base_sha)
        ):
            return receipt
        if not merged and str(data.get("state") or "").casefold() == "closed":
            return receipt
        train_response = await client.get(
            f"{path}/merge_trains/merge_requests/{request.review_number}"
        )
        if train_response.status_code == 404:
            if not request.provider_operation_id:
                raise RuntimeError(
                    "GitLab merge train operation ID is unavailable for reconciliation"
                )
            if merged:
                raise RuntimeError("GitLab completed merge train entry is unavailable")
            pipeline = data.get("head_pipeline") or {}
            pipeline_status = (
                str(pipeline.get("status") or "").casefold() if isinstance(pipeline, dict) else ""
            )
            if pipeline_status in {"failed", "canceled", "skipped"}:
                return receipt
            if pipeline_status in {
                "created",
                "waiting_for_resource",
                "preparing",
                "pending",
                "running",
                "manual",
                "scheduled",
            }:
                return receipt.model_copy(update={"state": PublicationState.QUEUED})
            raise RuntimeError(
                "GitLab merge train entry is unavailable and the MR has no terminal pipeline"
            )
        if train_response.status_code != 200:
            raise RuntimeError(
                f"Cannot reconcile GitLab merge train: HTTP {train_response.status_code}"
            )
        if request.provider_operation_id is None:
            train = _select_merge_train_entry(
                train_response.json(),
                request,
                operation_id=None,
                operation="reconcile recovery",
            )
            request = request.model_copy(update={"provider_operation_id": str(train["id"])})
            receipt = receipt.model_copy(
                update={"provider_operation_id": request.provider_operation_id}
            )
        else:
            try:
                train = _select_merge_train_entry(
                    train_response.json(),
                    request,
                    operation_id=request.provider_operation_id,
                    operation="reconcile",
                )
            except _MergeTrainEntryNotFoundError:
                return receipt
        pipeline = train.get("pipeline") or {}
        pipeline_status = (
            str(pipeline.get("status") or "").casefold() if isinstance(pipeline, dict) else ""
        )
        if pipeline_status in {"failed", "canceled", "skipped"}:
            return receipt
        train_status = str(train.get("status") or "").casefold()
        if train_status == "skip_merged":
            return receipt
        if train_status not in {"idle", "fresh", "stale", "merging", "merged"}:
            raise RuntimeError(f"GitLab returned unknown merge train status {train_status!r}")
        if not merged or train_status != "merged" or pipeline_status != "success":
            return receipt.model_copy(update={"state": PublicationState.QUEUED})

        squash_sha = str(data.get("squash_commit_sha") or "")
        if bool(squash_sha) != (request.method == "squash"):
            raise RuntimeError("GitLab merged MR method differs from the requested method")
        # Squash with a merge-commit project creates two commits. The final merge
        # commit, when present, is the target tip; the squash commit is its parent.
        result_sha = str(data.get("merge_commit_sha") or squash_sha or "")
        if not result_sha or not target_branch:
            raise RuntimeError("GitLab merged MR omitted its resulting commit identity")
        if target_branch != request.expected_target_branch:
            return self._gitlab_merge_receipt(
                request,
                state=PublicationState.FAILED,
                source_sha=source_sha,
                base_sha=request.expected_base_sha,
                target_branch=target_branch,
            )
        if canonical_sha != result_sha:
            raise RuntimeError(
                "GitLab target advanced beyond the merge result; ancestry proof is unavailable"
            )
        result_base_sha = await self._gitlab_merge_base_sha(
            client,
            path,
            request,
            result_sha=result_sha,
            squash_sha=squash_sha,
            has_merge_commit=bool(data.get("merge_commit_sha")),
        )
        if result_base_sha != request.expected_base_sha:
            return receipt.model_copy(update={"base_sha": result_base_sha})
        verified_at = datetime.now(UTC)
        return self._gitlab_merge_receipt(
            request,
            state=PublicationState.MERGED,
            source_sha=source_sha,
            base_sha=request.expected_base_sha,
            target_branch=target_branch,
            result_sha=result_sha,
            canonical_target_sha=canonical_sha,
            verified_at=verified_at,
        )

    async def _gitlab_merge_base_sha(
        self,
        client: httpx.AsyncClient,
        path: str,
        request: MergeRequest,
        *,
        result_sha: str,
        squash_sha: str,
        has_merge_commit: bool,
    ) -> str:
        """Return the provider-proven base from which the merged result was built."""
        commit_response = await client.get(f"{path}/repository/commits/{quote_plus(result_sha)}")
        if commit_response.status_code != 200:
            raise RuntimeError("Cannot verify GitLab merge result ancestry")
        commit = commit_response.json()
        parent_ids = commit.get("parent_ids") or []
        if not isinstance(parent_ids, list):
            raise RuntimeError("GitLab merge result parents are invalid")
        parents = [str(parent) for parent in parent_ids]
        if request.method == "squash":
            expected_parents = (
                [request.expected_base_sha, squash_sha]
                if has_merge_commit
                else [request.expected_base_sha]
            )
        else:
            expected_parents = [request.expected_base_sha, request.expected_head_sha]
        if len(parents) != len(expected_parents) or parents[1:] != expected_parents[1:]:
            raise RuntimeError("GitLab merge result was not built from the expected head")
        return parents[0]

    def _gitlab_merge_receipt(
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
                    f"gitlab-merge:{request.repository}:{request.review_number}:{request.expected_head_sha}",
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

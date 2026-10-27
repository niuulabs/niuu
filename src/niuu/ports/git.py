"""Port interfaces for git provider operations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.models import (
    CIStatus,
    CommitDetail,
    GitProviderType,
    ProvenanceAttestation,
    PullRequest,
    RefChecks,
    RefComparison,
    RepoInfo,
)


class GitProvider(ABC):
    """Port for git repository operations.

    Each instance represents a single git provider endpoint (e.g., github.com,
    gitlab.com, or a self-hosted GitLab instance). Multiple instances can be
    registered to support multiple providers/instances simultaneously.
    """

    @property
    @abstractmethod
    def provider_type(self) -> GitProviderType:
        """Return the type of this git provider."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return a human-readable name for this provider instance."""

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Return the base URL for this provider instance."""

    @property
    @abstractmethod
    def orgs(self) -> tuple[str, ...]:
        """Return the configured organizations/groups for this provider."""

    @abstractmethod
    def supports(self, repo_url: str) -> bool:
        """Check if this provider can handle the given repository URL.

        Args:
            repo_url: Repository URL or shorthand (e.g., 'github.com/org/repo').

        Returns:
            True if this provider can handle the URL.
        """

    @abstractmethod
    async def validate_repo(self, repo_url: str) -> bool:
        """Validate that a repository exists and is accessible.

        Args:
            repo_url: Repository URL or shorthand.

        Returns:
            True if the repository exists and is accessible.
        """

    @abstractmethod
    def parse_repo(self, repo_url: str) -> RepoInfo | None:
        """Parse a repository URL into structured information.

        Args:
            repo_url: Repository URL or shorthand.

        Returns:
            RepoInfo if the URL can be parsed, None otherwise.
        """

    @abstractmethod
    def get_clone_url(self, repo_url: str) -> str | None:
        """Get an authenticated clone URL for a repository.

        Args:
            repo_url: Repository URL or shorthand.

        Returns:
            Authenticated clone URL, or None if not supported.
        """

    @abstractmethod
    async def list_repos(self, org: str) -> list[RepoInfo]:
        """List all repositories in an organization/group.

        Args:
            org: Organization or group name.

        Returns:
            List of repositories in the organization.
        """

    @abstractmethod
    async def list_branches(self, repo_url: str) -> list[str]:
        """List all branches for a specific repository.

        Args:
            repo_url: Repository URL or shorthand.

        Returns:
            List of branch names.
        """

    @abstractmethod
    async def get_commit(self, repo_url: str, ref: str) -> CommitDetail:
        """Read one commit: identity, message and every changed file with its patch.

        Args:
            repo_url: Repository URL or shorthand.
            ref: Commit SHA, branch or tag.

        Returns:
            The commit with all its changed files. A patch the provider
            omitted is ``None`` with ``patch_omitted_reason`` set.

        Raises:
            GitRepoNotFoundError: The repository or the ref does not exist.
            GitAuthError: The credentials cannot read the repository.
        """

    @abstractmethod
    async def compare_refs(self, repo_url: str, base: str, head: str) -> RefComparison:
        """Compare ``base...head``: ahead/behind counts, commits and changed files.

        Args:
            repo_url: Repository URL or shorthand.
            base: Base commit SHA, branch or tag.
            head: Head commit SHA, branch or tag.

        Raises:
            GitRepoNotFoundError: The repository or either ref does not exist.
            GitAuthError: The credentials cannot read the repository.
        """

    @abstractmethod
    async def get_ref_checks(self, repo_url: str, ref: str) -> RefChecks:
        """Read CI / check results for a branch, tag or commit, with no PR needed.

        Args:
            repo_url: Repository URL or shorthand.
            ref: Commit SHA, branch or tag.

        Returns:
            The checks attached to the commit ``ref`` resolves to. A commit
            nothing has run on yields an empty ``checks`` tuple.

        Raises:
            GitRepoNotFoundError: The repository or the ref does not exist.
            GitAuthError: The credentials cannot read the repository.
        """

    @abstractmethod
    async def get_image_attestations(
        self, repo_url: str, subject_digest: str
    ) -> tuple[ProvenanceAttestation, ...]:
        """Fetch the build-provenance attestations the provider holds for an image digest.

        The bundles are returned exactly as stored and are **not verified**:
        verification is the job of a provenance verifier, never of the provider.

        Args:
            repo_url: Repository URL or shorthand the image was built from.
            subject_digest: Manifest digest as ``sha256:<64 hex>``.

        Returns:
            At least one attestation.

        Raises:
            AttestationNotFoundError: The provider holds no build provenance for
                the digest in this repository.
            GitOperationUnsupportedError: The provider has no attestation API.
            GitRepoNotFoundError: The repository does not exist or is not visible.
            GitAuthError: The credentials cannot read the repository.
            GitProviderResponseError: The provider answered something unusable.
        """


class GitWorkflowProvider(ABC):
    """Port for git workflow operations (branches, PRs, CI status).

    Extends the basic GitProvider with write operations for PR-based
    workflows. GitHub/GitLab are the source of truth — no local state.
    """

    @abstractmethod
    async def create_branch(
        self,
        repo_url: str,
        branch_name: str,
        from_branch: str = "main",
    ) -> bool:
        """Create a new branch from an existing branch."""

    @abstractmethod
    async def create_pull_request(
        self,
        repo_url: str,
        title: str,
        description: str,
        source_branch: str,
        target_branch: str,
        labels: list[str] | None = None,
    ) -> PullRequest:
        """Create a pull request / merge request."""

    @abstractmethod
    async def get_pull_request(self, repo_url: str, pr_number: int) -> PullRequest | None:
        """Get a pull request by number."""

    @abstractmethod
    async def list_pull_requests(self, repo_url: str, status: str = "open") -> list[PullRequest]:
        """List pull requests for a repository."""

    @abstractmethod
    async def merge_pull_request(
        self,
        repo_url: str,
        pr_number: int,
        merge_method: str = "squash",
    ) -> bool:
        """Merge a pull request."""

    @abstractmethod
    async def get_ci_status(self, repo_url: str, branch: str) -> CIStatus:
        """Get the CI status for a branch."""


class GitAuthError(Exception):
    """Raised when git provider authentication fails."""


class GitRepoNotFoundError(Exception):
    """Raised when a git repository is not found."""


class GitProviderResponseError(RuntimeError):
    """Raised when a git provider answers a read with something unusable.

    For example an incomplete comparison, a missing identity field or a
    pagination that never advances. The caller must treat the read as failed;
    the message names what the provider got wrong.
    """


class AttestationNotFoundError(GitRepoNotFoundError):
    """Raised when a provider holds no build provenance for a digest.

    A subtype of :class:`GitRepoNotFoundError` because GitHub answers an
    unknown digest and an unreadable repository with the same HTTP 404; the
    message says which of the causes apply.
    """


class GitOperationUnsupportedError(NotImplementedError):
    """Raised when a provider has no equivalent of the requested operation.

    The honest answer for a provider that lacks a feature (for example GitLab
    has no GitHub attestation API); never a stand-in result.
    """

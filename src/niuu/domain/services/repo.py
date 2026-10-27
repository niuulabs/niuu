"""Domain service for repository and provider management."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from niuu.domain.models import (
    ChangedFile,
    CIStatus,
    CommitDetail,
    GitProviderType,
    ImageProvenance,
    PullRequest,
    RefChecks,
    RefComparison,
    RepoInfo,
    VerifiedProvenance,
    normalize_image_digest,
)
from niuu.ports.git import GitProvider, GitWorkflowProvider
from niuu.ports.provenance import (
    ProvenanceNotConfiguredError,
    ProvenanceVerificationError,
    ProvenanceVerifier,
)

if TYPE_CHECKING:
    from niuu.adapters.outbound.git_registry import GitProviderRegistry
    from niuu.ports.user_integration import UserIntegrationPort

logger = logging.getLogger(__name__)


def _path_matches(requested: str, file: ChangedFile) -> bool:
    """A requested path names a file exactly (also by its old name) or a directory."""
    wanted = requested.strip().lstrip("/")
    if not wanted:
        return False
    prefix = wanted.rstrip("/") + "/"
    candidates = (file.path, file.previous_path or "")
    return any(c == wanted or c.startswith(prefix) for c in candidates)


def _shape_files(
    files: tuple[ChangedFile, ...],
    paths: Sequence[str],
    include_patch: bool,
) -> tuple[tuple[ChangedFile, ...], tuple[str, ...]]:
    """Narrow *files* to *paths* and drop patches the caller did not ask for.

    Returns the shaped files and the requested paths that matched no file, so a
    mistyped path is reported instead of reading as "this change did not touch it".
    """
    selected = files
    unmatched: tuple[str, ...] = ()
    if paths:
        selected = tuple(f for f in files if any(_path_matches(p, f) for p in paths))
        unmatched = tuple(p for p in paths if not any(_path_matches(p, f) for f in files))
    if include_patch:
        return selected, unmatched
    return tuple(replace(f, patch=None, patch_omitted_reason=None) for f in selected), unmatched


@dataclass(frozen=True)
class ProviderInfo:
    """Summary of a configured git provider."""

    name: str
    type: GitProviderType
    orgs: tuple[str, ...]


class RepoService:
    """Service for listing providers and their repositories.

    When a ``user_integration`` service is available, repo and branch
    listing is user-scoped: shared/org-level providers are combined
    with the user's own integration connections (credentials resolved
    on-the-fly, never cached).  Without it, falls back to shared only.
    """

    def __init__(
        self,
        git_registry: GitProviderRegistry,
        user_integration: UserIntegrationPort | None = None,
        provenance_verifier: ProvenanceVerifier | None = None,
    ):
        self._git_registry = git_registry
        self._user_integration = user_integration
        self._provenance_verifier = provenance_verifier

    def list_providers(self) -> list[ProviderInfo]:
        """List all configured git providers."""
        return [
            ProviderInfo(
                name=p.name,
                type=p.provider_type,
                orgs=p.orgs,
            )
            for p in self._git_registry.providers
        ]

    async def list_repos(
        self,
        user_id: str | None = None,
    ) -> dict[str, list[RepoInfo]]:
        """List repositories, optionally scoped to a user.

        When *user_id* is provided and a ``UserIntegrationService`` is
        available, includes repos from the user's personal integration
        connections in addition to the shared/org-level providers.
        """
        if user_id and self._user_integration:
            return await self._list_repos_for_user(user_id)
        return await self._git_registry.list_configured_repos()

    async def list_branches(
        self,
        repo_url: str,
        user_id: str | None = None,
    ) -> list[str]:
        """List branches, preferring the user's credentials if available."""
        if user_id and self._user_integration:
            provider = await self._user_integration.find_git_provider_for(
                repo_url,
                user_id,
            )
            if provider:
                return await provider.list_branches(repo_url)

        return await self._git_registry.list_branches(repo_url)

    async def list_pull_requests(
        self,
        repo_url: str,
        status: str = "open",
        user_id: str | None = None,
    ) -> list[PullRequest]:
        """List pull requests straight from the Git host."""
        provider = await self._workflow_provider(repo_url, user_id)
        return await provider.list_pull_requests(repo_url, status)

    async def get_pull_request(
        self,
        repo_url: str,
        pr_number: int,
        user_id: str | None = None,
    ) -> PullRequest | None:
        """Get one pull request; ``None`` when the host has no such number."""
        provider = await self._workflow_provider(repo_url, user_id)
        return await provider.get_pull_request(repo_url, pr_number)

    async def merge_pull_request(
        self,
        repo_url: str,
        pr_number: int,
        merge_method: str,
        user_id: str | None = None,
    ) -> bool:
        """Merge a pull request; ``False`` when the host refused the merge."""
        provider = await self._workflow_provider(repo_url, user_id)
        return await provider.merge_pull_request(repo_url, pr_number, merge_method)

    async def get_ci_status(
        self,
        repo_url: str,
        branch: str,
        user_id: str | None = None,
    ) -> CIStatus:
        """CI status of a branch as the Git host reports it."""
        provider = await self._workflow_provider(repo_url, user_id)
        return await provider.get_ci_status(repo_url, branch)

    async def get_commit(
        self,
        repo_url: str,
        ref: str,
        paths: Sequence[str] = (),
        include_patch: bool = False,
        user_id: str | None = None,
    ) -> CommitDetail:
        """Read one commit, with the files narrowed to *paths* and patches only on request."""
        provider = await self._user_git_provider(repo_url, user_id)
        if provider is not None:
            commit = await provider.get_commit(repo_url, ref)
        else:
            commit = await self._git_registry.get_commit(repo_url, ref)
        files, unmatched = _shape_files(commit.files, paths, include_patch)
        return replace(
            commit,
            files=files,
            files_total=len(commit.files),
            unmatched_paths=unmatched,
            patch_included=include_patch,
        )

    async def compare_refs(
        self,
        repo_url: str,
        base: str,
        head: str,
        paths: Sequence[str] = (),
        include_patch: bool = False,
        user_id: str | None = None,
    ) -> RefComparison:
        """Compare ``base...head``, shaped like :meth:`get_commit`."""
        provider = await self._user_git_provider(repo_url, user_id)
        if provider is not None:
            comparison = await provider.compare_refs(repo_url, base, head)
        else:
            comparison = await self._git_registry.compare_refs(repo_url, base, head)
        files, unmatched = _shape_files(comparison.files, paths, include_patch)
        return replace(
            comparison,
            files=files,
            files_total=len(comparison.files),
            unmatched_paths=unmatched,
            patch_included=include_patch,
        )

    async def get_ref_checks(
        self,
        repo_url: str,
        ref: str,
        user_id: str | None = None,
    ) -> RefChecks:
        """CI / check results for a branch, tag or commit, as the provider reports them."""
        provider = await self._user_git_provider(repo_url, user_id)
        if provider is not None:
            return await provider.get_ref_checks(repo_url, ref)
        return await self._git_registry.get_ref_checks(repo_url, ref)

    async def verify_image_provenance(
        self,
        repo_url: str,
        digest: str,
        user_id: str | None = None,
    ) -> ImageProvenance:
        """Verified build provenance of an image digest built from *repo_url*.

        The provider supplies the attestation bundles; the configured verifier
        decides which of them prove anything. Only verified attestations are
        returned, and each refused one is reported by reason, never dropped
        silently.

        Raises:
            InvalidImageDigestError: *digest* is not ``sha256:`` plus 64 hex.
            ProvenanceNotConfiguredError: Verification is turned off.
            ValueError: No connected Git host serves *repo_url*.
            AttestationNotFoundError: The provider holds no provenance for the digest.
            GitOperationUnsupportedError: The provider has no attestation API.
            ProvenanceVerificationError: Attestations exist but none verified.
            ProvenanceVerifierUnavailableError: Verification could not run.
        """
        if self._provenance_verifier is None:
            raise ProvenanceNotConfiguredError(
                "Image provenance verification is turned off (git.provenance.enabled is false). "
                "Set it to true, with the sigstore extra installed, to verify image digests."
            )
        subject_digest = normalize_image_digest(digest)
        provider = await self._user_git_provider(repo_url, user_id)
        if provider is not None:
            info = provider.parse_repo(repo_url)
            attestations = await provider.get_image_attestations(repo_url, subject_digest)
        else:
            info = self._git_registry.parse_repo(repo_url)
            attestations = await self._git_registry.get_image_attestations(repo_url, subject_digest)
        if info is None:
            raise ValueError(f"Cannot parse repository URL: {repo_url}")

        verified: list[VerifiedProvenance] = []
        rejected: list[str] = []
        for attestation in attestations:
            try:
                verified.append(
                    await self._provenance_verifier.verify(
                        attestation.bundle,
                        subject_digest=subject_digest,
                        repository=f"{info.org}/{info.name}",
                    )
                )
            except ProvenanceVerificationError as exc:
                rejected.append(exc.reason)
        if not verified:
            raise ProvenanceVerificationError(
                f"None of the {len(attestations)} attestation(s) for {subject_digest} in "
                f"{repo_url} verified: " + "; ".join(rejected)
            )
        return ImageProvenance(
            repo_url=repo_url,
            digest=subject_digest,
            attestations=tuple(verified),
            rejected=tuple(rejected),
        )

    async def _user_git_provider(
        self,
        repo_url: str,
        user_id: str | None,
    ) -> GitProvider | None:
        """The caller's own connected provider for *repo_url*, when they have one."""
        if not user_id or not self._user_integration:
            return None
        return await self._user_integration.find_git_provider_for(repo_url, user_id)

    async def _workflow_provider(
        self,
        repo_url: str,
        user_id: str | None,
    ) -> GitWorkflowProvider:
        """Resolve the provider for *repo_url* the same way branch listing does.

        Raises:
            ValueError: no connected Git host serves the URL, or the host
                serving it has no pull-request workflow support.
        """
        provider = None
        if user_id and self._user_integration:
            provider = await self._user_integration.find_git_provider_for(repo_url, user_id)
        if provider is None:
            provider = self._git_registry.get_provider(repo_url)
        if provider is None:
            raise ValueError(f"No git provider found for: {repo_url}")
        if not isinstance(provider, GitWorkflowProvider):
            raise ValueError(f"Provider {provider.name} does not support pull requests")
        return provider

    async def _list_repos_for_user(
        self,
        user_id: str,
    ) -> dict[str, list[RepoInfo]]:
        """Query shared + user providers concurrently, group by provider."""
        providers = await self._user_integration.get_git_providers(user_id)

        sem = asyncio.Semaphore(10)

        async def _capped(coro):
            async with sem:
                return await coro

        shared = {id(provider) for provider in self._git_registry.providers}
        tasks = []
        task_labels: list[str] = []
        for provider in providers:
            # A personal account added without organisations still has
            # repositories: everything its token can reach. Shared providers
            # from config keep needing explicit orgs.
            orgs: tuple[str, ...] = tuple(provider.orgs)
            if not orgs and id(provider) not in shared:
                orgs = ("",)
            for org in orgs:
                tasks.append(_capped(provider.list_repos(org)))
                task_labels.append(provider.name)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        by_provider: dict[str, list[RepoInfo]] = {}
        seen_urls: set[str] = set()
        for label, result in zip(task_labels, results):
            if isinstance(result, BaseException):
                logger.warning("Provider %s error: %s", label, result)
                continue
            for repo in result:
                if repo.url in seen_urls:
                    continue
                seen_urls.add(repo.url)
                by_provider.setdefault(label, []).append(repo)

        return by_provider

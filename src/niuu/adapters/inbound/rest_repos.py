"""Shared repos REST endpoint — /api/v1/niuu/repos.

Serves repository listings, branches and pull requests for all services
(Volundr, Ting, Ravn residents, etc.). The Git host is the source of truth,
so nothing here depends on which Forge instance a caller happens to reach.
The RepoService is injected by the hosting application.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from niuu.adapters.inbound.auth import extract_principal
from niuu.domain.models import (
    ChangedFile,
    CommitDetail,
    CommitSummary,
    ImageProvenance,
    InvalidImageDigestError,
    Principal,
    PullRequest,
    RefChecks,
    RefComparison,
    VerifiedProvenance,
)
from niuu.ports.git import (
    AttestationNotFoundError,
    GitAuthError,
    GitOperationUnsupportedError,
    GitProviderResponseError,
    GitRepoNotFoundError,
)
from niuu.ports.provenance import (
    ProvenanceNotConfiguredError,
    ProvenanceVerificationError,
    ProvenanceVerifierUnavailableError,
)

if TYPE_CHECKING:
    from niuu.domain.services.repo import RepoService


class RepoResponse(BaseModel):
    provider: str
    org: str
    name: str
    url: str
    clone_url: str
    default_branch: str
    branches: list[str] = []


class PullRequestResponse(BaseModel):
    """Response model for a pull request."""

    number: int = Field(description="PR number")
    title: str = Field(description="PR title")
    url: str = Field(description="Web URL for the pull request")
    repo_url: str = Field(description="Repository URL")
    provider: str = Field(description="Git provider (github, gitlab)")
    source_branch: str = Field(description="Source branch name")
    target_branch: str = Field(description="Target branch name")
    status: str = Field(description="PR status (open, merged, closed)")
    description: str | None = Field(
        default=None,
        description="PR body/description",
    )
    ci_status: str | None = Field(
        default=None,
        description="CI pipeline status (passing, failing, pending)",
    )
    review_status: str | None = Field(
        default=None,
        description="Review status (approved, changes_requested, pending)",
    )
    created_at: str | None = Field(
        default=None,
        description="ISO 8601 creation timestamp",
    )
    updated_at: str | None = Field(
        default=None,
        description="ISO 8601 last update timestamp",
    )

    @classmethod
    def from_pull_request(cls, pr: PullRequest) -> PullRequestResponse:
        """Create response from domain model."""
        return cls(
            number=pr.number,
            title=pr.title,
            url=pr.url,
            repo_url=pr.repo_url,
            provider=pr.provider.value,
            source_branch=pr.source_branch,
            target_branch=pr.target_branch,
            status=pr.status.value,
            description=pr.description,
            ci_status=pr.ci_status.value if pr.ci_status else None,
            review_status=pr.review_status.value if pr.review_status else None,
            created_at=pr.created_at.isoformat() if pr.created_at else None,
            updated_at=pr.updated_at.isoformat() if pr.updated_at else None,
        )


class PRMergeRequest(BaseModel):
    """Request to merge a PR."""

    merge_method: Literal["merge", "squash", "rebase"] = Field(
        default="squash",
        description="Merge method: merge, squash, or rebase",
    )


class CIStatusResponse(BaseModel):
    """Response model for CI status."""

    status: str = Field(
        description="CI status (passing, failing, pending, unknown)",
    )


class MergeResultResponse(BaseModel):
    """Response model for merge result."""

    merged: bool = Field(description="Whether the PR was successfully merged")


class ChangedFileResponse(BaseModel):
    """One changed file; ``patch`` is present only when patches were requested."""

    path: str
    status: str = Field(description="added, modified, removed, renamed, copied or changed")
    additions: int | None = Field(default=None, description="Added lines; null when unknown")
    deletions: int | None = Field(default=None, description="Removed lines; null when unknown")
    previous_path: str | None = None
    patch: str | None = Field(default=None, description="Unified diff hunks for this file")
    patch_omitted_reason: str | None = Field(
        default=None,
        description="Why a requested patch is absent (binary, too large, no line changes)",
    )

    @classmethod
    def from_file(cls, file: ChangedFile) -> ChangedFileResponse:
        return cls(
            path=file.path,
            status=file.status,
            additions=file.additions,
            deletions=file.deletions,
            previous_path=file.previous_path,
            patch=file.patch,
            patch_omitted_reason=file.patch_omitted_reason,
        )


class CommitSummaryResponse(BaseModel):
    sha: str
    subject: str
    author_name: str
    authored_at: str | None = None
    url: str

    @classmethod
    def from_summary(cls, commit: CommitSummary) -> CommitSummaryResponse:
        return cls(
            sha=commit.sha,
            subject=commit.subject,
            author_name=commit.author_name,
            authored_at=commit.authored_at.isoformat() if commit.authored_at else None,
            url=commit.url,
        )


class CommitResponse(BaseModel):
    """A commit with its changed files, shaped by the ``path`` / ``include_patch`` filters."""

    repo_url: str
    sha: str
    url: str
    parents: list[str]
    author_name: str
    author_email: str
    authored_at: str | None = None
    message: str
    additions: int | None = None
    deletions: int | None = None
    files_total: int = Field(description="Files the commit changed, before any path filter")
    files: list[ChangedFileResponse]
    unmatched_paths: list[str] = Field(
        description="Requested paths that matched none of the changed files"
    )
    patch_included: bool = Field(description="Whether patches were requested")

    @classmethod
    def from_commit(cls, commit: CommitDetail) -> CommitResponse:
        return cls(
            repo_url=commit.repo_url,
            sha=commit.sha,
            url=commit.url,
            parents=list(commit.parents),
            author_name=commit.author_name,
            author_email=commit.author_email,
            authored_at=commit.authored_at.isoformat() if commit.authored_at else None,
            message=commit.message,
            additions=commit.additions,
            deletions=commit.deletions,
            files_total=commit.files_total,
            files=[ChangedFileResponse.from_file(f) for f in commit.files],
            unmatched_paths=list(commit.unmatched_paths),
            patch_included=commit.patch_included,
        )


class ComparisonResponse(BaseModel):
    """``base...head``: ahead/behind, commit subjects and the changed files."""

    repo_url: str
    base: str
    head: str
    status: str = Field(description="ahead, behind, diverged or identical")
    ahead_by: int = Field(description="Commits on head that base lacks")
    behind_by: int = Field(description="Commits on base that head lacks")
    merge_base_sha: str
    url: str
    commits: list[CommitSummaryResponse]
    files_total: int = Field(description="Files changed, before any path filter")
    files: list[ChangedFileResponse]
    unmatched_paths: list[str] = Field(
        description="Requested paths that matched none of the changed files"
    )
    patch_included: bool = Field(description="Whether patches were requested")

    @classmethod
    def from_comparison(cls, comparison: RefComparison) -> ComparisonResponse:
        return cls(
            repo_url=comparison.repo_url,
            base=comparison.base,
            head=comparison.head,
            status=comparison.status,
            ahead_by=comparison.ahead_by,
            behind_by=comparison.behind_by,
            merge_base_sha=comparison.merge_base_sha,
            url=comparison.url,
            commits=[CommitSummaryResponse.from_summary(c) for c in comparison.commits],
            files_total=comparison.files_total,
            files=[ChangedFileResponse.from_file(f) for f in comparison.files],
            unmatched_paths=list(comparison.unmatched_paths),
            patch_included=comparison.patch_included,
        )


class CheckResponse(BaseModel):
    name: str
    conclusion: str = Field(description="passing, failing, pending, canceled, skipped or unknown")
    details_url: str | None = None


class RefChecksResponse(BaseModel):
    """CI results for a ref, bound to the exact commit the provider resolved it to."""

    repo_url: str
    ref: str
    sha: str
    status: str = Field(description="Aggregate: passing, failing, pending or unknown")
    counts: dict[str, int] = Field(description="Number of checks per conclusion")
    checks: list[CheckResponse] = Field(description="Worst first, then by name")

    @classmethod
    def from_checks(cls, result: RefChecks) -> RefChecksResponse:
        return cls(
            repo_url=result.repo_url,
            ref=result.ref,
            sha=result.sha,
            status=result.status.value,
            counts=result.counts(),
            checks=[
                CheckResponse(name=c.name, conclusion=c.conclusion.value, details_url=c.details_url)
                for c in result.checks
            ],
        )


class VerifiedProvenanceResponse(BaseModel):
    """What one verified attestation proves; every field comes from the signed material."""

    subject_name: str = Field(description="Image repository the attestation names for the digest")
    subject_digest: str = Field(description="sha256:<hex> manifest digest that was attested")
    source_repository: str = Field(description="Repository the build ran from (certificate)")
    commit_sha: str = Field(description="Commit the build ran at (certificate)")
    ref: str = Field(description="Git ref the build ran for, e.g. refs/heads/dev (certificate)")
    workflow: str = Field(description="Signing workflow file and ref (certificate build signer)")
    run_url: str = Field(description="URL of the workflow run that built and signed the image")

    @classmethod
    def from_provenance(cls, provenance: VerifiedProvenance) -> VerifiedProvenanceResponse:
        return cls(
            subject_name=provenance.subject_name,
            subject_digest=provenance.subject_digest,
            source_repository=provenance.source_repository,
            commit_sha=provenance.commit_sha,
            ref=provenance.ref,
            workflow=provenance.workflow,
            run_url=provenance.run_url,
        )


class ImageProvenanceResponse(BaseModel):
    """A digest whose build provenance verified. Anything unverified is an error, not this."""

    repo_url: str
    digest: str
    verified: Literal[True] = Field(
        default=True, description="Always true: unverified provenance is never returned"
    )
    attestations: list[VerifiedProvenanceResponse] = Field(
        description="Every attestation that verified (at least one)"
    )
    rejected: list[str] = Field(
        description="Why each attestation the host returned but verification refused was refused"
    )

    @classmethod
    def from_provenance(cls, provenance: ImageProvenance) -> ImageProvenanceResponse:
        return cls(
            repo_url=provenance.repo_url,
            digest=provenance.digest,
            attestations=[
                VerifiedProvenanceResponse.from_provenance(a) for a in provenance.attestations
            ],
            rejected=list(provenance.rejected),
        )


@contextmanager
def _git_host_errors(repo_url: str) -> Iterator[None]:
    """Translate Git-host failures on pull-request routes into typed HTTP errors.

    Every outcome names its cause so a caller (often an agent) can tell a
    wrong address from missing credentials from a host outage, instead of
    reading a bare 404 as "this platform has no such feature".
    """
    try:
        yield
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No connected Git host serves {repo_url!r}: {exc}. Connect the "
                "account that owns this repository, or pass its full https URL."
            ),
        ) from exc
    except GitRepoNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except GitAuthError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the Git provider for pull requests; try again shortly.",
        ) from exc


@contextmanager
def _git_read_errors(repo_url: str) -> Iterator[None]:
    """Translate failures on commit / compare / checks reads into typed HTTP errors.

    Same mapping as branch listing: 404 for an unknown repository or ref, 403
    for credentials the host refused, 502 when the host failed or answered
    something unusable (including a rate limit), with the cause in ``detail``.
    """
    try:
        yield
    except (ValueError, GitRepoNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No connected Git host serves {repo_url!r}, or it has no such ref: {exc}",
        ) from exc
    except GitAuthError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (GitProviderResponseError, httpx.HTTPError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The Git provider could not serve this read: {exc}",
        ) from exc


@contextmanager
def _provenance_errors(repo_url: str) -> Iterator[None]:
    """Translate image-provenance failures into typed HTTP errors.

    Each outcome is distinct so a caller can tell "there is no attestation" (404)
    from "there is one and it failed verification" (422), and both from a host
    outage (502), a verifier that could not run (503) and an operator decision
    to run without verification (503). Host errors share ``_git_read_errors``.
    """
    with _git_read_errors(repo_url):
        try:
            yield
        except InvalidImageDigestError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        except AttestationNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except GitOperationUnsupportedError as exc:
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
            ) from exc
        except ProvenanceVerificationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Provenance verification failed: {exc.reason}",
            ) from exc
        except (ProvenanceNotConfiguredError, ProvenanceVerifierUnavailableError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc


def create_repos_router(repo_service: RepoService) -> APIRouter:
    """Create the shared repos router at /api/v1/niuu."""
    router = APIRouter(prefix="/api/v1/niuu", tags=["Shared"])

    def _repo_service() -> RepoService:
        if repo_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Repo service not available",
            )
        return repo_service

    @router.get("/repos", response_model=dict[str, list[RepoResponse]])
    async def list_repos(
        request: Request,
        principal: Principal = Depends(extract_principal),
    ) -> dict[str, list[RepoResponse]]:
        """List repositories from all providers visible to the current user.

        The caller is whoever the platform authenticated, the same way every
        other route sees them; without sign-in that is the development user,
        whose own Git accounts must still be consulted.
        """
        del request
        if repo_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Repo service not available",
            )
        repos_by_provider = await repo_service.list_repos(user_id=principal.user_id)
        return {
            provider_name: [
                RepoResponse(
                    provider=repo.provider,
                    org=repo.org,
                    name=repo.name,
                    url=repo.url,
                    clone_url=repo.clone_url,
                    default_branch=repo.default_branch,
                    branches=list(repo.branches),
                )
                for repo in repos
            ]
            for provider_name, repos in repos_by_provider.items()
        }

    @router.get("/repos/branches", response_model=list[str])
    async def list_branches(
        request: Request,
        repo_url: str,
        principal: Principal = Depends(extract_principal),
    ) -> list[str]:
        """List branches for a repository visible to the current user."""
        del request
        if repo_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Repo service not available",
            )
        try:
            return await repo_service.list_branches(repo_url, user_id=principal.user_id)
        except (ValueError, GitRepoNotFoundError) as exc:
            # The launcher asks while the address is still being typed.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No connected Git host serves {repo_url!r}: {exc}",
            ) from exc
        except GitAuthError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not fetch branches from the Git provider; try again shortly.",
            ) from exc

    @router.get("/repos/commit", response_model=CommitResponse)
    async def get_commit(
        repo_url: str = Query(..., description="Repository URL"),
        ref: str = Query(..., description="Commit SHA, branch or tag"),
        paths: list[str] = Query(
            default=[],
            alias="path",
            description="Only these files or directories (repeatable); default all files",
        ),
        include_patch: bool = Query(
            default=False,
            description="Include each file's patch; without it only the file list and stats",
        ),
        principal: Principal = Depends(extract_principal),
    ) -> CommitResponse:
        """Read one commit with its changed files, using the caller's Git credentials."""
        with _git_read_errors(repo_url):
            commit = await _repo_service().get_commit(
                repo_url, ref, paths=paths, include_patch=include_patch, user_id=principal.user_id
            )
        return CommitResponse.from_commit(commit)

    @router.get("/repos/compare", response_model=ComparisonResponse)
    async def compare_refs(
        repo_url: str = Query(..., description="Repository URL"),
        base: str = Query(..., description="Base commit SHA, branch or tag"),
        head: str = Query(..., description="Head commit SHA, branch or tag"),
        paths: list[str] = Query(
            default=[],
            alias="path",
            description="Only these files or directories (repeatable); default all files",
        ),
        include_patch: bool = Query(
            default=False,
            description="Include each file's patch; without it only the file list and stats",
        ),
        principal: Principal = Depends(extract_principal),
    ) -> ComparisonResponse:
        """Compare ``base...head``: ahead/behind, commits and changed files."""
        with _git_read_errors(repo_url):
            comparison = await _repo_service().compare_refs(
                repo_url,
                base,
                head,
                paths=paths,
                include_patch=include_patch,
                user_id=principal.user_id,
            )
        return ComparisonResponse.from_comparison(comparison)

    @router.get("/repos/checks", response_model=RefChecksResponse)
    async def get_ref_checks(
        repo_url: str = Query(..., description="Repository URL"),
        ref: str = Query(..., description="Commit SHA, branch or tag"),
        principal: Principal = Depends(extract_principal),
    ) -> RefChecksResponse:
        """CI / check results for a branch, tag or commit; no pull request needed."""
        with _git_read_errors(repo_url):
            result = await _repo_service().get_ref_checks(repo_url, ref, user_id=principal.user_id)
        return RefChecksResponse.from_checks(result)

    @router.get("/repos/provenance", response_model=ImageProvenanceResponse)
    async def verify_image_provenance(
        repo_url: str = Query(..., description="Repository URL the image was built from"),
        digest: str = Query(..., description="Image manifest digest, sha256:<64 hex>"),
        principal: Principal = Depends(extract_principal),
    ) -> ImageProvenanceResponse:
        """Verify the build provenance of an image digest, server-side.

        The platform fetches the repository's GitHub attestations with its own
        credentials and verifies them cryptographically; 200 means at least one
        attestation verified. 404: no attestation exists. 422: attestations
        exist but none verified (or the digest is malformed). 501: the host has
        no attestation API (GitLab).
        """
        with _provenance_errors(repo_url):
            result = await _repo_service().verify_image_provenance(
                repo_url, digest, user_id=principal.user_id
            )
        return ImageProvenanceResponse.from_provenance(result)

    @router.get("/repos/prs", response_model=list[PullRequestResponse])
    async def list_pull_requests(
        repo_url: str = Query(..., description="Repository URL"),
        status_filter: Literal["open", "closed", "merged", "all"] = Query(
            default="open",
            alias="status",
            description="Filter by status (open, closed, merged, all)",
        ),
        principal: Principal = Depends(extract_principal),
    ) -> list[PullRequestResponse]:
        """List pull requests for a repository visible to the current user."""
        with _git_host_errors(repo_url):
            prs = await _repo_service().list_pull_requests(
                repo_url, status_filter, user_id=principal.user_id
            )
        return [PullRequestResponse.from_pull_request(pr) for pr in prs]

    @router.get("/repos/prs/{pr_number}", response_model=PullRequestResponse)
    async def get_pull_request(
        pr_number: int = Path(description="Pull request number"),
        repo_url: str = Query(..., description="Repository URL"),
        principal: Principal = Depends(extract_principal),
    ) -> PullRequestResponse:
        """Get a pull request by number."""
        with _git_host_errors(repo_url):
            pr = await _repo_service().get_pull_request(
                repo_url, pr_number, user_id=principal.user_id
            )
        if pr is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"PR #{pr_number} not found in {repo_url}",
            )
        return PullRequestResponse.from_pull_request(pr)

    @router.post("/repos/prs/{pr_number}/merge", response_model=MergeResultResponse)
    async def merge_pull_request(
        pr_number: int = Path(description="Pull request number"),
        repo_url: str = Query(..., description="Repository URL"),
        request: PRMergeRequest = Body(default_factory=PRMergeRequest),
        principal: Principal = Depends(extract_principal),
    ) -> MergeResultResponse:
        """Merge a pull request."""
        with _git_host_errors(repo_url):
            merged = await _repo_service().merge_pull_request(
                repo_url, pr_number, request.merge_method, user_id=principal.user_id
            )
        if not merged:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Failed to merge PR #{pr_number} — check CI status and reviews",
            )
        return MergeResultResponse(merged=True)

    @router.get("/repos/prs/{pr_number}/ci", response_model=CIStatusResponse)
    async def get_ci_status(
        pr_number: int = Path(description="Pull request number"),
        repo_url: str = Query(..., description="Repository URL"),
        branch: str = Query(..., description="Branch name"),
        principal: Principal = Depends(extract_principal),
    ) -> CIStatusResponse:
        """Get CI status for a pull request's branch."""
        del pr_number
        with _git_host_errors(repo_url):
            ci_status = await _repo_service().get_ci_status(
                repo_url, branch, user_id=principal.user_id
            )
        return CIStatusResponse(status=ci_status.value)

    return router

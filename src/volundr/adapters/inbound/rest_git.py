"""FastAPI REST adapter for git workflow operations."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from volundr.domain.models import PullRequest
from volundr.domain.services.git_workflow import (
    GitWorkflowService,
    SessionNotFoundError,
)

logger = logging.getLogger(__name__)


# --- Request models ---


class PRCreateRequest(BaseModel):
    """Request to create a PR from a session."""

    session_id: UUID = Field(
        description="Session ID to create a pull request from",
    )
    title: str | None = Field(
        default=None,
        description="PR title (auto-generated from session if omitted)",
    )
    target_branch: str = Field(
        default="main",
        description="Target branch for the pull request",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "session_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "title": "fix: resolve JWT validation bypass",
                "target_branch": "main",
            },
        },
    }


class PRMergeRequest(BaseModel):
    """Request to merge a PR."""

    merge_method: str = Field(
        default="squash",
        description="Merge method: merge, squash, or rebase",
    )


class ConfidenceRequest(BaseModel):
    """Request to calculate merge confidence."""

    tests_pass: bool = Field(description="Whether all tests pass")
    coverage_delta: float = Field(
        default=0.0,
        description="Change in code coverage percentage",
    )
    lines_changed: int = Field(description="Total lines changed")
    files_changed: int = Field(description="Total files changed")
    has_dependency_changes: bool = Field(
        default=False,
        description="Whether dependency files were modified",
    )
    change_categories: list[str] = Field(
        default_factory=list,
        description="Categories of changes (e.g. bugfix, feature)",
    )


# --- Response models ---


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


class MergeConfidenceResponse(BaseModel):
    """Response model for merge confidence scoring."""

    score: float = Field(description="Confidence score from 0.0 to 1.0")
    factors: dict[str, float] = Field(
        description="Individual factor scores contributing to confidence",
    )
    action: str = Field(
        description="Recommended action (auto_merge, notify_then_merge, require_approval)",
    )
    reason: str = Field(description="Human-readable rationale")


class CIStatusResponse(BaseModel):
    """Response model for CI status."""

    status: str = Field(
        description="CI status (passing, failing, pending, unknown)",
    )


class MergeResultResponse(BaseModel):
    """Response model for merge result."""

    merged: bool = Field(description="Whether the PR was successfully merged")


class ErrorResponse(BaseModel):
    """Response model for errors."""

    detail: str = Field(description="Human-readable error message")


# --- Router factory ---


def create_git_router(
    git_workflow_service: GitWorkflowService,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    """Create FastAPI router for git workflow endpoints."""
    router = APIRouter(prefix=prefix)

    @router.post(
        "/repos/prs",
        response_model=PullRequestResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            404: {"model": ErrorResponse},
            400: {"model": ErrorResponse},
        },
        tags=["Repositories"],
    )
    async def create_pr(request: PRCreateRequest) -> PullRequestResponse:
        """Create a pull request from a session."""
        try:
            pr = await git_workflow_service.create_pr_from_session(
                session_id=request.session_id,
                title=request.title,
                target_branch=request.target_branch,
            )
        except SessionNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {request.session_id}",
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
        except RuntimeError as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(e),
            )
        return PullRequestResponse.from_pull_request(pr)

    @router.get(
        "/repos/prs",
        response_model=list[PullRequestResponse],
        tags=["Repositories"],
    )
    async def list_prs(
        repo_url: str = Query(..., description="Repository URL"),
        status_filter: str = Query(
            default="open",
            alias="status",
            description="Filter by status (open, closed, merged, all)",
        ),
    ) -> list[PullRequestResponse]:
        """List pull requests for a repository."""
        try:
            prs = await git_workflow_service.list_prs(repo_url, status_filter)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
        return [PullRequestResponse.from_pull_request(pr) for pr in prs]

    @router.get(
        "/repos/prs/{pr_number}",
        response_model=PullRequestResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Repositories"],
    )
    async def get_pr(
        pr_number: int = Path(description="Pull request number"),
        repo_url: str = Query(..., description="Repository URL"),
    ) -> PullRequestResponse:
        """Get a pull request by number."""
        try:
            pr = await git_workflow_service.get_pr(repo_url, pr_number)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
        if pr is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"PR #{pr_number} not found in {repo_url}",
            )
        return PullRequestResponse.from_pull_request(pr)

    @router.post(
        "/repos/prs/{pr_number}/merge",
        response_model=MergeResultResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
        tags=["Repositories"],
    )
    async def merge_pr(
        pr_number: int = Path(description="Pull request number"),
        request: PRMergeRequest = ...,
        repo_url: str = Query(..., description="Repository URL"),
    ) -> MergeResultResponse:
        """Merge a pull request."""
        try:
            merged = await git_workflow_service.merge_pr(repo_url, pr_number, request.merge_method)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
        if not merged:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Failed to merge PR #{pr_number} — check CI status and reviews",
            )
        return MergeResultResponse(merged=True)

    @router.get(
        "/repos/prs/{pr_number}/ci",
        response_model=CIStatusResponse,
        tags=["Repositories"],
    )
    async def get_ci_status(
        pr_number: int = Path(description="Pull request number"),
        repo_url: str = Query(..., description="Repository URL"),
        branch: str = Query(..., description="Branch name"),
    ) -> CIStatusResponse:
        """Get CI status for a branch."""
        try:
            ci_status = await git_workflow_service.get_ci_status(repo_url, branch)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
        return CIStatusResponse(status=ci_status.value)

    @router.post(
        "/repos/confidence",
        response_model=MergeConfidenceResponse,
        tags=["Repositories"],
    )
    async def calculate_confidence(
        request: ConfidenceRequest,
    ) -> MergeConfidenceResponse:
        """Calculate merge confidence for a set of changes."""
        result = git_workflow_service.calculate_confidence(
            tests_pass=request.tests_pass,
            coverage_delta=request.coverage_delta,
            lines_changed=request.lines_changed,
            files_changed=request.files_changed,
            has_dependency_changes=request.has_dependency_changes,
            change_categories=request.change_categories,
        )
        return MergeConfidenceResponse(
            score=result.score,
            factors=result.factors,
            action=result.action,
            reason=result.reason,
        )

    return router

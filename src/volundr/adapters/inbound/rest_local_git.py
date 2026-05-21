"""FastAPI REST adapter for local git workspace operations.

Provides endpoints that execute git commands directly on session workspace
directories. Only available when the workspace exists on the local filesystem
(mini/local mode). Returns 404 when workspace is not found (K8s mode or
session not provisioned).
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from volundr.domain.ports import GitWorkspacePort, SessionRepository

logger = logging.getLogger(__name__)

# Base path where session workspaces are mounted.
DEFAULT_SESSIONS_BASE = "/volundr/sessions"


# --- Response models ---


class DiffFileEntry(BaseModel):
    """A single file changed in the diff."""

    path: str = Field(description="File path relative to the workspace root")
    additions: int = Field(description="Lines added")
    deletions: int = Field(description="Lines deleted")


class DiffFilesResponse(BaseModel):
    """Response for the diff files endpoint."""

    files: list[DiffFileEntry] = Field(description="Changed files with stats")


class FileDiffResponse(BaseModel):
    """Response for a single file unified diff."""

    path: str = Field(description="File path")
    diff: str | None = Field(description="Unified diff text, null if no changes")


class CommitEntry(BaseModel):
    """A single commit in the log."""

    hash: str = Field(description="Full commit hash")
    short_hash: str = Field(description="Abbreviated commit hash")
    message: str = Field(description="Commit message subject line")


class CommitLogResponse(BaseModel):
    """Response for the commit log endpoint."""

    commits: list[CommitEntry] = Field(description="Recent commits")


class CheckEntry(BaseModel):
    """A single CI check."""

    name: str = Field(description="Check name")
    status: str = Field(description="Check status/conclusion")


class PRStatusResponse(BaseModel):
    """Response for the PR status endpoint."""

    number: int | None = Field(description="PR number")
    url: str = Field(description="PR web URL")
    state: str = Field(description="PR state (OPEN, CLOSED, MERGED)")
    mergeable: str = Field(description="Mergeable status")
    checks: list[CheckEntry] = Field(description="CI status checks")


class ErrorResponse(BaseModel):
    """Error response."""

    detail: str


def _validate_path(value: str) -> str:
    """Reject path traversal sequences for defense in depth."""
    if ".." in value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path must not contain '..' sequences.",
        )
    return value


def _validate_no_flag(value: str, name: str) -> str:
    """Reject values starting with '-' to prevent option injection."""
    if value.startswith("-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{name} must not start with '-'.",
        )
    return value


def _resolve_workspace(session_id: UUID, sessions_base: str) -> Path:
    """Resolve the workspace directory for a session.

    Raises HTTPException 404 if the workspace does not exist on the
    local filesystem.
    """
    base = Path(sessions_base).resolve()
    try:
        canonical_session_id = UUID(str(session_id))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session identifier.",
        ) from None
    workspace = (base / str(canonical_session_id) / "workspace").resolve()

    try:
        workspace.relative_to(base)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid workspace path.",
        ) from None

    if not workspace.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Workspace not found for session {session_id}. "
                "Local git endpoints are only available when the session "
                "workspace exists on the local filesystem."
            ),
        )
    return workspace


def create_local_git_router(
    git_workspace: GitWorkspacePort,
    session_repository: SessionRepository,
    sessions_base: str = DEFAULT_SESSIONS_BASE,
    *,
    prefix: str = "/api/v1/forge",
) -> APIRouter:
    """Create FastAPI router for local git workspace endpoints."""
    router = APIRouter(prefix=prefix)

    @router.get(
        "/sessions/{session_id}/pr",
        response_model=PRStatusResponse | None,
        responses={404: {"model": ErrorResponse}},
        tags=["Git Workspace"],
        summary="Get PR status for a session workspace",
    )
    async def get_pr_status(session_id: UUID) -> PRStatusResponse | None:
        """Detect the current PR for a session's workspace branch.

        Runs ``gh pr view`` in the workspace directory. Returns null
        if no PR exists or ``gh`` is not installed.
        """
        session = await session_repository.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        workspace = _resolve_workspace(session_id, sessions_base)
        result = await git_workspace.pr_status(str(workspace))
        if result is None:
            return None
        return PRStatusResponse(
            number=result.get("number"),
            url=result.get("url", ""),
            state=result.get("state", ""),
            mergeable=result.get("mergeable", "UNKNOWN"),
            checks=[
                CheckEntry(name=c["name"], status=c["status"]) for c in result.get("checks", [])
            ],
        )

    @router.get(
        "/sessions/{session_id}/diff/files",
        response_model=DiffFilesResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Git Workspace"],
        summary="List changed files in the session workspace",
    )
    async def get_diff_files(session_id: UUID) -> DiffFilesResponse:
        """Return files changed relative to HEAD with addition/deletion counts."""
        session = await session_repository.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        workspace = _resolve_workspace(session_id, sessions_base)
        files = await git_workspace.diff_files(str(workspace))
        return DiffFilesResponse(
            files=[
                DiffFileEntry(
                    path=f["path"],
                    additions=f["additions"],
                    deletions=f["deletions"],
                )
                for f in files
            ],
        )

    @router.get(
        "/sessions/{session_id}/diff",
        response_model=FileDiffResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Git Workspace"],
        summary="Get unified diff for a specific file",
    )
    async def get_file_diff(
        session_id: UUID,
        path: str = Query(..., description="File path relative to workspace root"),
        base_branch: str = Query(
            default="main",
            description="Base branch for the diff comparison",
        ),
    ) -> FileDiffResponse:
        """Return the unified diff for a single file in the session workspace."""
        _validate_path(path)
        _validate_no_flag(base_branch, "base_branch")
        session = await session_repository.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        workspace = _resolve_workspace(session_id, sessions_base)
        diff = await git_workspace.file_diff(str(workspace), path, base_branch)
        return FileDiffResponse(path=path, diff=diff)

    @router.get(
        "/sessions/{session_id}/commits",
        response_model=CommitLogResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Git Workspace"],
        summary="Get recent commits for a session workspace",
    )
    async def get_commits(
        session_id: UUID,
        since: str | None = Query(
            default=None,
            description="Only show commits after this date (ISO 8601 or git date format)",
        ),
    ) -> CommitLogResponse:
        """Return the recent commit log for the session workspace."""
        if since is not None:
            _validate_no_flag(since, "since")
        session = await session_repository.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session not found: {session_id}",
            )
        workspace = _resolve_workspace(session_id, sessions_base)
        commits = await git_workspace.commit_log(str(workspace), since=since)
        return CommitLogResponse(
            commits=[
                CommitEntry(
                    hash=c["hash"],
                    short_hash=c["short_hash"],
                    message=c["message"],
                )
                for c in commits
            ],
        )

    return router

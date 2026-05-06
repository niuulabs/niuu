"""Shared repos REST endpoint — /api/v1/niuu/repos.

Serves repository listings for all services (Volundr, Tyr, etc.).
The RepoService is injected by the hosting application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

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


def create_repos_router(repo_service: RepoService) -> APIRouter:
    """Create the shared repos router at /api/v1/niuu."""
    router = APIRouter(prefix="/api/v1/niuu", tags=["Shared"])

    @router.get("/repos", response_model=dict[str, list[RepoResponse]])
    async def list_repos(request: Request) -> dict[str, list[RepoResponse]]:
        """List repositories from all providers visible to the current user."""
        if repo_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Repo service not available",
            )
        user_id = request.headers.get("x-auth-user-id")
        repos_by_provider = await repo_service.list_repos(user_id=user_id)
        return {
            provider_name: [
                RepoResponse(
                    provider=repo.provider,
                    org=repo.org,
                    name=repo.name,
                    url=repo.url,
                    clone_url=repo.url,
                    default_branch=repo.default_branch,
                    branches=list(repo.branches),
                )
                for repo in repos
            ]
            for provider_name, repos in repos_by_provider.items()
        }

    @router.get("/repos/branches", response_model=list[str])
    async def list_branches(request: Request, repo_url: str) -> list[str]:
        """List branches for a repository visible to the current user."""
        if repo_service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Repo service not available",
            )
        user_id = request.headers.get("x-auth-user-id")
        return await repo_service.list_branches(repo_url, user_id=user_id)

    return router

"""Shared repos REST endpoint — /api/v1/niuu/repos.

Serves repository listings for all services (Volundr, Ting, etc.).
The RepoService is injected by the hosting application.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from niuu.adapters.inbound.auth import extract_principal
from niuu.domain.models import Principal
from niuu.ports.git import GitAuthError, GitRepoNotFoundError

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

    return router

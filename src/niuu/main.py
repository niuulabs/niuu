"""Application factory for the Niuu shared API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from niuu.adapters.inbound.rest_repos import create_repos_router
from niuu.adapters.outbound.git_registry import create_git_registry
from niuu.config import GitConfig, NiuuSettings
from niuu.cors import apply_cors_middleware
from niuu.domain.services.repo import RepoService


def _load_settings() -> NiuuSettings:
    """Load shared-service settings from YAML and environment."""
    return NiuuSettings()


def create_app(
    git_config: GitConfig | None = None,
    settings: NiuuSettings | None = None,
) -> FastAPI:
    """Create the Niuu shared FastAPI application.

    Args:
        git_config: Git provider configuration.  When ``None``, loaded
            from the shared YAML / env vars automatically.
        settings: Shared-service settings. When ``None``, loaded from the
            shared YAML / env vars automatically.
    """
    app = FastAPI(
        title="Niuu Shared Services",
        description="Shared API endpoints — repos, PATs, integrations.",
        version="0.1.0",
    )

    loaded_settings = settings or _load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        cfg = git_config or loaded_settings.git
        git_registry = create_git_registry(cfg)

        repo_service = RepoService(git_registry)
        app.state.git_registry = git_registry
        app.state.repo_service = repo_service

        repos_router = create_repos_router(repo_service)
        app.include_router(repos_router)

        try:
            yield
        finally:
            await git_registry.close()

    app.router.lifespan_context = lifespan

    apply_cors_middleware(app, loaded_settings.cors)

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_app()

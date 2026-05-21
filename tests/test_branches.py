"""Tests for git branch listing functionality (NIU-54)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from volundr.adapters.inbound.rest import create_router
from volundr.domain.models import GitProviderType
from volundr.domain.ports import (
    GitAuthError,
    GitProvider,
    GitRepoNotFoundError,
)
from volundr.domain.services.repo import RepoService

# ---------------------------------------------------------------------------
# Lightweight test doubles (defined here to avoid conftest import issues)
# ---------------------------------------------------------------------------


class _StubProvider(GitProvider):
    """Minimal stub provider for branch listing tests."""

    def __init__(
        self,
        *,
        supported_hosts: list[str] | None = None,
        branches: list[str] | None = None,
        error: Exception | None = None,
    ):
        self._hosts = supported_hosts or ["github.com"]
        self._branches = branches or ["main", "develop", "feature/test"]
        self._error = error

    @property
    def provider_type(self) -> GitProviderType:
        return GitProviderType.GITHUB

    @property
    def name(self) -> str:
        return "stub"

    @property
    def base_url(self) -> str:
        return f"https://{self._hosts[0]}"

    @property
    def orgs(self) -> tuple[str, ...]:
        return ()

    def supports(self, repo_url: str) -> bool:
        return any(h in repo_url for h in self._hosts)

    async def validate_repo(self, repo_url: str) -> bool:
        return True

    def parse_repo(self, repo_url: str):
        return None

    def get_clone_url(self, repo_url: str):
        return None

    async def list_repos(self, org: str):
        return []

    async def list_branches(self, repo_url: str) -> list[str]:
        if self._error is not None:
            raise self._error
        return list(self._branches)


class _StubRegistry:
    """Minimal registry that delegates to a single provider."""

    def __init__(self, provider: _StubProvider | None = None):
        self._provider = provider

    @property
    def providers(self):
        return [self._provider] if self._provider else []

    async def list_configured_repos(self):
        return {}

    async def list_branches(self, repo_url: str) -> list[str]:
        if self._provider is None or not self._provider.supports(repo_url):
            raise ValueError(f"No git provider found for: {repo_url}")
        return await self._provider.list_branches(repo_url)


# ---------------------------------------------------------------------------
# RepoService tests
# ---------------------------------------------------------------------------


class TestRepoServiceListBranches:
    """Tests for RepoService.list_branches."""

    @pytest.mark.asyncio
    async def test_list_branches_success(self):
        registry = _StubRegistry(_StubProvider())
        service = RepoService(git_registry=registry)

        branches = await service.list_branches("https://github.com/org/repo")
        assert branches == ["main", "develop", "feature/test"]

    @pytest.mark.asyncio
    async def test_list_branches_no_provider(self):
        registry = _StubRegistry()
        service = RepoService(git_registry=registry)

        with pytest.raises(ValueError, match="No git provider found"):
            await service.list_branches("https://unknown.com/org/repo")

    @pytest.mark.asyncio
    async def test_list_branches_auth_error(self):
        provider = _StubProvider(
            error=GitAuthError("Authentication failed: HTTP 401"),
        )
        registry = _StubRegistry(provider)
        service = RepoService(git_registry=registry)

        with pytest.raises(GitAuthError, match="Authentication failed"):
            await service.list_branches("https://github.com/org/private-repo")

    @pytest.mark.asyncio
    async def test_list_branches_not_found(self):
        provider = _StubProvider(
            error=GitRepoNotFoundError("Repository not found"),
        )
        registry = _StubRegistry(provider)
        service = RepoService(git_registry=registry)

        with pytest.raises(GitRepoNotFoundError, match="Repository not found"):
            await service.list_branches("https://github.com/org/missing-repo")

    @pytest.mark.asyncio
    async def test_list_branches_unsupported_url(self):
        provider = _StubProvider(supported_hosts=["github.com"])
        registry = _StubRegistry(provider)
        service = RepoService(git_registry=registry)

        with pytest.raises(ValueError, match="No git provider found"):
            await service.list_branches("https://gitlab.com/org/repo")


# ---------------------------------------------------------------------------
# REST endpoint tests
# ---------------------------------------------------------------------------


def _make_app(provider: _StubProvider | None = None, *, repo_service=...):
    """Build a minimal FastAPI app with the repos router."""
    from fastapi import FastAPI

    if repo_service is ...:
        if provider is None:
            provider = _StubProvider()
        registry = _StubRegistry(provider)
        repo_service = RepoService(git_registry=registry)

    router = create_router(
        session_service=None,
        chronicle_service=None,
        broadcaster=None,
        repo_service=repo_service,
    )
    app = FastAPI()
    app.include_router(router)
    return app


class TestBranchesEndpoint:
    """Tests for GET /repos/branches."""

    @pytest.mark.asyncio
    async def test_list_branches_ok(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/forge/repos/branches",
                params={"repo_url": "https://github.com/org/repo"},
            )

        assert resp.status_code == 200
        assert resp.json() == ["main", "develop", "feature/test"]

    @pytest.mark.asyncio
    async def test_list_branches_auth_error_returns_401(self):
        provider = _StubProvider(
            error=GitAuthError("Authentication failed: HTTP 401"),
        )
        app = _make_app(provider=provider)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/forge/repos/branches",
                params={"repo_url": "https://github.com/org/private"},
            )

        assert resp.status_code == 401
        assert "Authentication failed" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_list_branches_not_found_returns_404(self):
        provider = _StubProvider(
            error=GitRepoNotFoundError("Repository not found"),
        )
        app = _make_app(provider=provider)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/forge/repos/branches",
                params={"repo_url": "https://github.com/org/gone"},
            )

        assert resp.status_code == 404
        assert "Repository not found" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_list_branches_no_provider_returns_400(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/forge/repos/branches",
                params={"repo_url": "https://unknown.host/org/repo"},
            )

        assert resp.status_code == 400
        assert "No git provider found" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_list_branches_missing_param_returns_422(self):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/forge/repos/branches")

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_branches_no_service_returns_503(self):
        app = _make_app(repo_service=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/forge/repos/branches",
                params={"repo_url": "https://github.com/org/repo"},
            )

        assert resp.status_code == 503

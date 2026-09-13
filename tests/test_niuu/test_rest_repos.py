"""The repos routes see the caller the way every other route does."""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.rest_repos import create_repos_router
from niuu.domain.models import Principal
from niuu.domain.services.repo import RepoService
from tests.conftest import MockGitProvider, MockGitRegistry
from volundr.domain.models import GitProviderType, RepoInfo


def _client(user_id: str = "dev-user") -> tuple[TestClient, AsyncMock, MockGitProvider]:
    personal = MockGitProvider(
        name="github-signin",
        orgs=(),
        repos=[
            RepoInfo(
                provider=GitProviderType.GITHUB,
                org="jve",
                name="dotfiles",
                clone_url="https://github.com/jve/dotfiles.git",
                url="https://github.com/jve/dotfiles",
            )
        ],
    )
    user_integration = AsyncMock()
    user_integration.get_git_providers = AsyncMock(return_value=[personal])
    user_integration.find_git_provider_for = AsyncMock(return_value=None)
    app = FastAPI()
    app.include_router(
        create_repos_router(RepoService(MockGitRegistry([]), user_integration=user_integration))
    )
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id=user_id, email="dev@example.test", tenant_id="default", roles=[]
    )
    return TestClient(app), user_integration, personal


def test_repos_come_from_the_authenticated_persons_accounts() -> None:
    client, user_integration, personal = _client()

    response = client.get("/api/v1/niuu/repos")  # no x-auth-user-id header

    assert response.status_code == 200
    assert list(response.json()) == ["github-signin"]
    assert response.json()["github-signin"][0]["name"] == "dotfiles"
    user_integration.get_git_providers.assert_awaited_once_with("dev-user")
    assert personal.list_repos_calls == [""]


def test_branches_for_an_unknown_address_is_not_found_not_a_crash() -> None:
    client, _, _ = _client()

    response = client.get("/api/v1/niuu/repos/branches", params={"repo_url": "jve"})

    assert response.status_code == 404
    assert "No connected Git host serves 'jve'" in response.json()["detail"]

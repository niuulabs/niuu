"""Device-flow token refresh: the provider token endpoint is an explicitly mocked boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.config import _default_integration_definitions
from volundr.domain.models import IntegrationConnection, IntegrationType, SecretType
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)
from volundr.domain.services.oauth_token_refresh import (
    REFRESH_FAILED_ERROR_CODE,
    OAuthTokenRefreshService,
    refresh_oauth_tokens_loop,
)

GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITLAB_TOKEN_URL = "https://gitlab.com/oauth/token"


class _CredentialStore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str], dict] = {}

    async def get(self, owner_type, owner_id, name):
        item = self.items.get((owner_type, owner_id, name))
        if item is None:
            return None
        return SimpleNamespace(secret_type=item["secret_type"], metadata=item["metadata"])

    async def get_value(self, owner_type, owner_id, name):
        item = self.items.get((owner_type, owner_id, name))
        return dict(item["data"]) if item is not None else None

    async def store(self, owner_type, owner_id, name, secret_type, data, metadata=None):
        self.items[(owner_type, owner_id, name)] = {
            "secret_type": secret_type,
            "data": dict(data),
            "metadata": dict(metadata or {}),
        }
        return await self.get(owner_type, owner_id, name)


def _registry() -> IntegrationRegistry:
    return IntegrationRegistry(
        definitions_from_config([d.model_dump() for d in _default_integration_definitions()])
    )


def _connection(slug: str, name: str, *, owner: str = "user-1", enabled: bool = True):
    now = datetime.now(UTC)
    return IntegrationConnection(
        id=f"{slug}-{name}",
        owner_id=owner,
        integration_type=IntegrationType.SOURCE_CONTROL
        if slug in {"github", "gitlab"}
        else IntegrationType.AI_PROVIDER,
        adapter="x",
        credential_name=name,
        config={},
        enabled=enabled,
        created_at=now,
        updated_at=now,
        slug=slug,
    )


@pytest.fixture
def store() -> _CredentialStore:
    return _CredentialStore()


@pytest.fixture
def repo() -> InMemoryIntegrationRepository:
    return InMemoryIntegrationRepository()


def _service(repo, store, **kwargs) -> OAuthTokenRefreshService:
    return OAuthTokenRefreshService(
        integration_repository=repo,
        integration_registry=_registry(),
        credential_store=store,
        client_ids=kwargs.pop("client_ids", {"github": "Iv1.app", "gitlab": "glpub"}),
        **kwargs,
    )


async def _seed(repo, store, slug, name, *, expires_in: timedelta, **extra):
    await repo.save_connection(_connection(slug, name, **extra))
    await store.store(
        "user",
        "user-1",
        name,
        SecretType.OAUTH_TOKEN,
        {
            "token": "old-token",
            "refresh_token": "old-refresh",
            "expires_at": (datetime.now(UTC) + expires_in).isoformat(),
        },
        {"source": "credential_enrollment", "auth_state": "active"},
    )


@pytest.mark.asyncio
@respx.mock
async def test_refreshes_a_token_that_is_about_to_expire(repo, store) -> None:
    await _seed(repo, store, "gitlab", "gitlab-signin", expires_in=timedelta(minutes=3))
    route = respx.post(GITLAB_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "new-token",
                "refresh_token": "new-refresh",
                "expires_in": 7200,
            },
        )
    )

    report = await _service(repo, store).refresh_due()

    assert report.refreshed == ["gitlab/user-1/gitlab-signin"]
    assert report.failed == []
    sent = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert sent == {
        "grant_type": "refresh_token",
        "refresh_token": "old-refresh",
        "client_id": "glpub",
    }
    item = store.items[("user", "user-1", "gitlab-signin")]
    assert item["data"]["token"] == "new-token"
    assert item["data"]["refresh_token"] == "new-refresh"
    expires = datetime.fromisoformat(item["data"]["expires_at"])
    assert timedelta(hours=1, minutes=55) < expires - datetime.now(UTC) <= timedelta(hours=2)
    assert item["metadata"]["auth_state"] == "active"
    assert item["metadata"]["auth_expires_at"] == item["data"]["expires_at"]
    assert item["metadata"]["source"] == "credential_enrollment"


@pytest.mark.asyncio
@respx.mock
async def test_github_refresh_carries_the_app_secret_when_configured(repo, store) -> None:
    await _seed(repo, store, "github", "github-signin", expires_in=timedelta(minutes=1))
    route = respx.post(GITHUB_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "gh-new", "expires_in": 28800})
    )

    report = await _service(repo, store, client_secrets={"github": "shh"}).refresh_due()

    assert report.refreshed == ["github/user-1/github-signin"]
    sent = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert sent["client_secret"] == "shh"
    assert route.calls.last.request.headers["accept"] == "application/json"
    item = store.items[("user", "user-1", "github-signin")]
    # GitHub answers without a new refresh token; the old one stays usable.
    assert item["data"]["refresh_token"] == "old-refresh"


@pytest.mark.asyncio
@respx.mock
async def test_skips_tokens_that_are_not_due_or_cannot_refresh(repo, store) -> None:
    await _seed(repo, store, "gitlab", "fresh", expires_in=timedelta(hours=1))
    await _seed(repo, store, "gitlab", "disabled", expires_in=timedelta(minutes=1), enabled=False)
    await _seed(repo, store, "anthropic", "claude-signin", expires_in=timedelta(minutes=1))
    await repo.save_connection(_connection("github", "github-pat"))
    await store.store("user", "user-1", "github-pat", SecretType.API_KEY, {"token": "ghp"}, {})
    route = respx.post(GITLAB_TOKEN_URL)

    report = await _service(repo, store).refresh_due()

    assert report.refreshed == []
    assert report.failed == []
    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_a_rejected_refresh_flips_the_connection_to_sign_in_needed(repo, store) -> None:
    await _seed(repo, store, "gitlab", "gitlab-signin", expires_in=timedelta(minutes=1))
    respx.post(GITLAB_TOKEN_URL).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "revoked"}
        )
    )

    report = await _service(repo, store).refresh_due()

    assert report.failed == ["gitlab/user-1/gitlab-signin"]
    item = store.items[("user", "user-1", "gitlab-signin")]
    assert item["data"]["token"] == "old-token"
    assert item["metadata"]["auth_state"] == "auth_required"
    assert item["metadata"]["auth_error_code"] == REFRESH_FAILED_ERROR_CODE


@pytest.mark.asyncio
@respx.mock
async def test_a_missing_client_id_is_a_failure_not_a_silent_skip(repo, store) -> None:
    await _seed(repo, store, "gitlab", "gitlab-signin", expires_in=timedelta(minutes=1))
    route = respx.post(GITLAB_TOKEN_URL)

    report = await _service(repo, store, client_ids={"github": "Iv1.app"}).refresh_due()

    assert report.failed == ["gitlab/user-1/gitlab-signin"]
    assert not route.called
    item = store.items[("user", "user-1", "gitlab-signin")]
    assert item["metadata"]["auth_state"] == "auth_required"


@pytest.mark.asyncio
async def test_loop_runs_until_cancelled_and_survives_an_iteration_error() -> None:
    service = AsyncMock()
    service.refresh_due.side_effect = [
        RuntimeError("boom"),
        SimpleNamespace(refreshed=["a"], failed=[]),
        SimpleNamespace(refreshed=[], failed=[]),
    ]
    real_sleep = asyncio.sleep
    naps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        naps.append(seconds)
        await real_sleep(0)

    with patch("volundr.domain.services.oauth_token_refresh.asyncio.sleep", new=fake_sleep):
        task = asyncio.create_task(refresh_oauth_tokens_loop(service, interval_seconds=7))
        for _ in range(50):
            await real_sleep(0)
            if service.refresh_due.call_count >= 3:
                break
        task.cancel()
        await task  # the loop swallows its own cancellation and returns

    assert service.refresh_due.call_count >= 3
    assert naps[:2] == [7, 7]

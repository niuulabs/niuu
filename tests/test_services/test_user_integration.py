"""Tests for UserIntegrationService — ephemeral provider factory."""

from __future__ import annotations

import datetime
from unittest.mock import AsyncMock

import pytest

from volundr.domain.models import IntegrationConnection, IntegrationType
from volundr.domain.services.integration_registry import IntegrationRegistry
from volundr.domain.services.user_integration import UserIntegrationService

# Default config for GitHub API connections used in tests
_GH_CONFIG = {"name": "My GitHub", "base_url": "https://api.github.com", "orgs": ["personal"]}


# --- Fixtures ---


class FakeGitProvider:
    """Minimal fake that satisfies the GitProvider interface shape."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str = "",
        token: str | None = None,
        orgs: tuple[str, ...] = (),
    ):
        self._name = name
        self._token = token
        self._orgs = orgs

    @property
    def name(self) -> str:
        return self._name

    @property
    def orgs(self) -> tuple[str, ...]:
        return self._orgs

    def supports(self, repo_url: str) -> bool:
        return True


def _make_connection(
    *,
    conn_id: str = "conn-1",
    user_id: str = "user-1",
    slug: str = "github",
    adapter: str = "volundr.adapters.outbound.github.GitHubProvider",
    credential_name: str = "my-pat",
    integration_type: str = "source_control",
    enabled: bool = True,
    config: dict | None = None,
) -> IntegrationConnection:
    now = datetime.datetime.now(datetime.UTC)
    return IntegrationConnection(
        id=conn_id,
        owner_id=user_id,
        slug=slug,
        adapter=adapter,
        credential_name=credential_name,
        integration_type=integration_type,
        enabled=enabled,
        config=config or dict(_GH_CONFIG),
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def integration_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.list_connections = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def credential_store() -> AsyncMock:
    store = AsyncMock()
    store.get_value = AsyncMock(return_value={"token": "ghp_test123"})
    return store


@pytest.fixture
def registry() -> IntegrationRegistry:
    # Use default built-in definitions (includes github, gitlab, etc.)
    return IntegrationRegistry()


@pytest.fixture
def service(
    integration_repo: AsyncMock,
    credential_store: AsyncMock,
    registry: IntegrationRegistry,
) -> UserIntegrationService:
    shared_git = FakeGitProvider(name="Org GitHub", orgs=("niuulabs",))
    return UserIntegrationService(
        shared_git_providers=[shared_git],
        integration_repo=integration_repo,
        integration_registry=registry,
        credential_store=credential_store,
    )


# --- Tests ---


async def test_shared_providers_always_included(
    service: UserIntegrationService,
) -> None:
    providers = await service.get_git_providers("user-1")
    assert len(providers) == 1
    assert providers[0].name == "Org GitHub"


async def test_user_providers_added_from_connections(
    service: UserIntegrationService,
    integration_repo: AsyncMock,
) -> None:
    conn = _make_connection()
    integration_repo.list_connections.return_value = [conn]

    providers = await service.get_git_providers("user-1")

    # 1 shared + 1 user
    assert len(providers) == 2
    assert providers[0].name == "My GitHub"
    assert list(providers[0].orgs) == ["personal"]


async def test_disabled_connections_skipped(
    service: UserIntegrationService,
    integration_repo: AsyncMock,
) -> None:
    conn = _make_connection(enabled=False)
    integration_repo.list_connections.return_value = [conn]

    providers = await service.get_git_providers("user-1")
    assert len(providers) == 1  # only shared


async def test_missing_credentials_skipped(
    service: UserIntegrationService,
    integration_repo: AsyncMock,
    credential_store: AsyncMock,
) -> None:
    conn = _make_connection()
    integration_repo.list_connections.return_value = [conn]
    credential_store.get_value.return_value = None

    providers = await service.get_git_providers("user-1")
    # Provider still instantiated (token will be None), but constructor accepts it
    assert len(providers) == 2


async def test_credential_fetched_per_request(
    service: UserIntegrationService,
    integration_repo: AsyncMock,
    credential_store: AsyncMock,
) -> None:
    conn = _make_connection()
    integration_repo.list_connections.return_value = [conn]

    await service.get_git_providers("user-1")
    await service.get_git_providers("user-1")

    # Credential store called fresh each time, not cached
    assert credential_store.get_value.call_count == 2


async def test_bad_adapter_class_skipped(
    service: UserIntegrationService,
    integration_repo: AsyncMock,
) -> None:
    conn = _make_connection(adapter="nonexistent.module.Class")
    integration_repo.list_connections.return_value = [conn]

    providers = await service.get_git_providers("user-1")
    assert len(providers) == 1  # only shared, bad adapter skipped


async def test_multiple_user_connections(
    service: UserIntegrationService,
    integration_repo: AsyncMock,
) -> None:
    conn1 = _make_connection(
        conn_id="c1",
        config={"name": "Personal", "base_url": "https://api.github.com", "orgs": ["me"]},
    )
    conn2 = _make_connection(
        conn_id="c2",
        config={"name": "Work", "base_url": "https://api.github.com", "orgs": ["acme"]},
    )
    integration_repo.list_connections.return_value = [conn1, conn2]

    providers = await service.get_git_providers("user-1")
    assert len(providers) == 3  # 1 shared + 2 user
    names = [p.name for p in providers]
    assert "Personal" in names
    assert "Work" in names


async def test_get_providers_filters_by_type(
    service: UserIntegrationService,
    integration_repo: AsyncMock,
) -> None:
    await service.get_providers("user-1", IntegrationType.SOURCE_CONTROL)
    integration_repo.list_connections.assert_called_once_with(
        "user-1",
        integration_type=IntegrationType.SOURCE_CONTROL,
    )


async def test_get_credential_for_connection(
    service: UserIntegrationService,
    credential_store: AsyncMock,
) -> None:
    conn = _make_connection()
    creds = await service.get_credential_for_connection("user-1", conn)
    assert creds == {"token": "ghp_test123"}
    credential_store.get_value.assert_called_once_with("user", "user-1", "my-pat")


async def test_empty_credential_name_returns_empty(
    service: UserIntegrationService,
) -> None:
    conn = _make_connection(credential_name="")
    creds = await service.get_credential_for_connection("user-1", conn)
    assert creds == {}


class FakeIssueProvider:
    """Minimal fake that satisfies the IssueTrackerProvider interface shape."""

    def __init__(self, *, name: str = "FakeIssue"):
        self._name = name

    @property
    def name(self) -> str:
        return self._name


async def test_get_issue_providers_includes_shared(
    integration_repo: AsyncMock,
    credential_store: AsyncMock,
    registry: IntegrationRegistry,
) -> None:
    shared_issue = FakeIssueProvider(name="Org Linear")
    svc = UserIntegrationService(
        shared_issue_providers=[shared_issue],
        integration_repo=integration_repo,
        integration_registry=registry,
        credential_store=credential_store,
    )
    providers = await svc.get_issue_providers("user-1")
    assert len(providers) == 1
    assert providers[0].name == "Org Linear"


async def test_get_providers_unknown_type_returns_empty_shared(
    service: UserIntegrationService,
) -> None:
    providers = await service.get_providers("user-1", IntegrationType.MESSAGING)
    # No shared providers for messaging type, no user connections
    assert providers == []


async def test_connection_without_adapter_skipped(
    service: UserIntegrationService,
    integration_repo: AsyncMock,
) -> None:
    conn = _make_connection(adapter="")
    integration_repo.list_connections.return_value = [conn]

    providers = await service.get_git_providers("user-1")
    assert len(providers) == 1  # only shared


async def test_build_provider_constructor_failure_skipped(
    service: UserIntegrationService,
    integration_repo: AsyncMock,
    credential_store: AsyncMock,
) -> None:
    # Use a valid importable class that will fail with wrong kwargs
    conn = _make_connection(
        adapter="volundr.domain.services.user_integration.UserIntegrationService",
        config={"name": "Bad"},
    )
    integration_repo.list_connections.return_value = [conn]

    providers = await service.get_git_providers("user-1")
    assert len(providers) == 1  # only shared, failed instantiation skipped


async def test_find_git_provider_for_returns_matching(
    service: UserIntegrationService,
) -> None:
    """find_git_provider_for returns the first provider that supports the URL."""
    provider = await service.find_git_provider_for("https://github.com/org/repo", "user-1")
    # The shared FakeGitProvider.supports() returns True for all URLs
    assert provider is not None
    assert provider.name == "Org GitHub"


async def test_find_git_provider_for_returns_none_when_no_match(
    integration_repo: AsyncMock,
    credential_store: AsyncMock,
    registry: IntegrationRegistry,
) -> None:
    """find_git_provider_for returns None when no provider supports the URL."""

    class NeverMatchProvider(FakeGitProvider):
        def supports(self, repo_url: str) -> bool:
            return False

    svc = UserIntegrationService(
        shared_git_providers=[NeverMatchProvider(name="NoMatch")],
        integration_repo=integration_repo,
        integration_registry=registry,
        credential_store=credential_store,
    )
    provider = await svc.find_git_provider_for("https://unknown.com/repo", "user-1")
    assert provider is None


async def test_resolve_credentials(
    service: UserIntegrationService,
    credential_store: AsyncMock,
) -> None:
    """resolve_credentials fetches credential values by name."""
    result = await service.resolve_credentials("user-1", "my-pat")
    assert result == {"token": "ghp_test123"}
    credential_store.get_value.assert_called_once_with("user", "user-1", "my-pat")


async def test_resolve_credentials_empty_name(
    service: UserIntegrationService,
) -> None:
    """resolve_credentials returns empty dict for empty credential name."""
    result = await service.resolve_credentials("user-1", "")
    assert result == {}


async def test_add_shared_issue_provider(
    service: UserIntegrationService,
) -> None:
    """add_shared_issue_provider adds a provider that appears in get_issue_providers."""
    fake = FakeIssueProvider(name="NewIssue")
    service.add_shared_issue_provider(fake)

    providers = await service.get_issue_providers("user-1")
    assert any(p.name == "NewIssue" for p in providers)


async def test_user_git_provider_takes_precedence(service, integration_repo):
    integration_repo.list_connections.return_value = [_make_connection()]
    provider = await service.find_git_provider_for("https://github.com/org/repo", "user-1")
    assert provider.name == "My GitHub"


async def test_session_git_uses_only_selected_connections(
    service, integration_repo, credential_store
):
    selected = _make_connection(conn_id="selected", credential_name="selected-token")
    integration_repo.list_connections.return_value = [
        _make_connection(credential_name="unselected")
    ]
    connection, provider = await service.find_session_git_provider(
        "https://github.com/org/repo",
        "user-1",
        (selected,),
    )
    assert connection.id == "selected"
    assert provider.name == "My GitHub"
    credential_store.get_value.assert_awaited_once_with("user", "user-1", "selected-token")
    integration_repo.list_connections.assert_not_called()


@pytest.mark.parametrize("failure", ["missing_token", "other_owner", "disabled", "bad_adapter"])
async def test_session_git_fails_closed(service, credential_store, failure):
    kwargs = {}
    if failure == "missing_token":
        credential_store.get_value.return_value = {}
    elif failure == "other_owner":
        kwargs["user_id"] = "someone-else"
    elif failure == "disabled":
        kwargs["enabled"] = False
    else:
        kwargs["adapter"] = "missing.Adapter"
    with pytest.raises(ValueError):
        await service.find_session_git_provider(
            "https://github.com/org/repo",
            "user-1",
            (_make_connection(**kwargs),),
        )
    if failure in {"other_owner", "disabled"}:
        credential_store.get_value.assert_not_called()


async def test_session_git_nonmatching_host(service):
    result = await service.find_session_git_provider(
        "https://gitlab.com/org/repo",
        "user-1",
        (_make_connection(),),
    )
    assert result is None


async def test_session_git_supports_gitlab(service):
    conn = _make_connection(
        adapter="volundr.adapters.outbound.gitlab.GitLabProvider",
        slug="gitlab",
        config={"name": "GitLab", "base_url": "https://gitlab.com"},
    )
    connection, provider = await service.find_session_git_provider(
        "https://gitlab.com/org/repo",
        "user-1",
        (conn,),
    )
    assert connection is conn
    assert provider.get_clone_url("https://gitlab.com/org/repo").startswith("https://oauth2:")


async def test_unnamed_accounts_are_known_by_their_credential_name(
    service: UserIntegrationService,
    integration_repo: AsyncMock,
) -> None:
    """Two GitHub accounts stay apart in the repository list."""
    unnamed = {"base_url": "https://api.github.com"}
    personal = _make_connection(conn_id="c-1", credential_name="github-signin", config=unnamed)
    work = _make_connection(conn_id="c-2", credential_name="github-work", config=unnamed)
    integration_repo.list_connections.return_value = [personal, work]

    providers = await service.get_git_providers("user-1")

    assert [p.name for p in providers[:2]] == ["github-signin", "github-work"]

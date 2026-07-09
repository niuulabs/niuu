"""Tests for GitContributor."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from volundr.adapters.outbound.contributors.git import GitContributor
from volundr.domain.models import GitSource, Principal, Session
from volundr.domain.ports import SessionContext


@pytest.fixture
def session():
    return Session(
        name="test",
        model="claude",
        source=GitSource(repo="https://github.com/org/repo", branch="feat/test"),
    )


class TestGitContributor:
    async def test_name(self):
        c = GitContributor()
        assert c.name == "git"

    async def test_no_registry_returns_empty(self, session):
        c = GitContributor()
        result = await c.contribute(session, SessionContext())
        assert result.values == {}

    async def test_no_repo_returns_empty(self):
        session = Session(name="test", model="claude", source=GitSource(repo="", branch="main"))
        registry = MagicMock()
        c = GitContributor(git_registry=registry)
        result = await c.contribute(session, SessionContext())
        assert result.values == {}
        registry.get_clone_url.assert_not_called()

    async def test_clone_url_available(self, session):
        registry = MagicMock()
        registry.get_clone_url.return_value = "https://token@github.com/org/repo.git"
        c = GitContributor(git_registry=registry)
        result = await c.contribute(session, SessionContext())
        assert result.values["git"]["repoUrl"] == "https://github.com/org/repo"
        assert result.values["git"]["cloneUrl"] == "https://token@github.com/org/repo.git"
        assert result.values["git"]["branch"] == "feat/test"

    async def test_openshell_uses_clean_repo_url_and_dynamic_provider_auth(self, session):
        registry = MagicMock()
        registry.get_clone_url.return_value = "https://token@github.com/org/repo.git"
        c = GitContributor(git_registry=registry)

        result = await c.contribute(session, SessionContext(runtime_backend="openshell"))

        assert result.values["git"]["repoUrl"] == "https://github.com/org/repo"
        assert result.values["git"]["cloneUrl"] == "https://github.com/org/repo"
        registry.get_clone_url.assert_not_called()

    async def test_clone_url_none(self, session):
        registry = MagicMock()
        registry.get_clone_url.return_value = None
        c = GitContributor(git_registry=registry)
        result = await c.contribute(session, SessionContext())
        assert result.values == {}

    async def test_user_integration_provides_clone_url(self, session):
        provider = MagicMock()
        provider.get_clone_url.return_value = "https://user-token@github.com/org/repo.git"
        user_integration = AsyncMock()
        user_integration.find_git_provider_for.return_value = provider

        principal = Principal(user_id="u1", email="u@test.com", tenant_id="t1", roles=[])
        c = GitContributor(user_integration=user_integration)
        result = await c.contribute(session, SessionContext(principal=principal))
        assert result.values["git"]["cloneUrl"] == "https://user-token@github.com/org/repo.git"
        user_integration.find_git_provider_for.assert_called_once_with(session.repo, "u1")

    async def test_user_integration_no_provider_falls_back(self, session):
        user_integration = AsyncMock()
        user_integration.find_git_provider_for.return_value = None
        registry = MagicMock()
        registry.get_clone_url.return_value = "https://shared@github.com/org/repo.git"

        principal = Principal(user_id="u1", email="u@test.com", tenant_id="t1", roles=[])
        c = GitContributor(git_registry=registry, user_integration=user_integration)
        result = await c.contribute(session, SessionContext(principal=principal))
        assert result.values["git"]["cloneUrl"] == "https://shared@github.com/org/repo.git"

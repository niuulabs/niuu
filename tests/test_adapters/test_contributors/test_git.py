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

    @pytest.mark.parametrize("backend", ["kubernetes", "openshell"])
    async def test_user_integration_provides_mounted_token(self, session, backend):
        provider = MagicMock()
        provider.get_clone_url.return_value = (
            "https://x-access-token:user-token@github.com/org/repo.git"
        )
        connection = MagicMock(id="selected-github")
        user_integration = AsyncMock()
        user_integration.find_session_git_provider.return_value = (connection, provider)
        registry = MagicMock()
        principal = Principal(user_id="u1", email="u@test.com", tenant_id="t1", roles=[])
        context = SessionContext(
            principal=principal, integration_connections=(connection,), runtime_backend=backend
        )
        c = GitContributor(user_integration=user_integration, git_registry=registry)

        result = await c.contribute(session, context)

        git = result.values["git"]
        assert git["cloneUrl"] == "https://github.com/org/repo.git"
        assert git["credentials"]["secretName"] == ""
        if backend == "kubernetes":
            assert git["credentials"]["tokenFile"].startswith("/run/secrets/git/")
        else:
            assert git["credentials"]["tokenFile"] == ""
        assert git["credentials"]["username"] == "x-access-token"
        assert git["userEmail"] == principal.email
        assert "user-token" not in str(result)
        registry.get_clone_url.assert_not_called()
        user_integration.find_session_git_provider.assert_awaited_once_with(
            session.repo,
            "u1",
            (connection,),
        )

    async def test_missing_integration_does_not_use_cluster_token(self, session):
        user_integration = AsyncMock()
        user_integration.find_session_git_provider.return_value = None
        registry = MagicMock()
        principal = Principal(user_id="u1", email="u@test.com", tenant_id="t1", roles=[])
        c = GitContributor(git_registry=registry, user_integration=user_integration)

        with pytest.raises(ValueError, match="Attach a source-control integration"):
            await c.contribute(session, SessionContext(principal=principal))
        registry.get_clone_url.assert_not_called()

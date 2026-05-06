"""Tests for the NiuuPlugin and niuu shared services."""

from __future__ import annotations

from unittest.mock import AsyncMock, PropertyMock

import pytest

from niuu.config import (
    GitConfig,
    GitHubConfig,
    GitHubInstance,
    GitLabConfig,
    GitLabInstance,
)
from niuu.domain.models import (
    CIStatus,
    GitProviderType,
    PullRequest,
    PullRequestStatus,
    RepoInfo,
    ReviewStatus,
)
from niuu.domain.services.repo import ProviderInfo, RepoService
from niuu.plugin import NiuuPlugin, _NiuuStub
from niuu.ports.git import (
    GitAuthError,
    GitProvider,
    GitRepoNotFoundError,
    GitWorkflowProvider,
)

# ---------------------------------------------------------------------------
# NiuuPlugin tests
# ---------------------------------------------------------------------------


class TestNiuuPlugin:
    """Tests for NiuuPlugin registration and metadata."""

    @pytest.fixture
    def plugin(self) -> NiuuPlugin:
        return NiuuPlugin()

    def test_name(self, plugin: NiuuPlugin) -> None:
        assert plugin.name == "niuu"

    def test_description(self, plugin: NiuuPlugin) -> None:
        assert "Shared" in plugin.description

    def test_register_service(self, plugin: NiuuPlugin) -> None:
        defn = plugin.register_service()
        assert defn.name == "niuu"
        assert defn.default_enabled is True
        assert "postgres" in defn.depends_on

    def test_create_service(self, plugin: NiuuPlugin) -> None:
        svc = plugin.create_service()
        assert isinstance(svc, _NiuuStub)

    def test_create_api_app(self, plugin: NiuuPlugin) -> None:
        app = plugin.create_api_app()
        assert app is not None
        assert app.title == "Niuu Shared Services"

    def test_api_route_domains(self, plugin: NiuuPlugin) -> None:
        route_domains = plugin.api_route_domains()
        assert route_domains
        assert route_domains[0].name == "niuu-api"
        assert route_domains[0].prefixes == ("/api/v1/niuu",)


class TestNiuuStub:
    """Tests for the _NiuuStub service lifecycle."""

    @pytest.fixture
    def stub(self) -> _NiuuStub:
        return _NiuuStub()

    @pytest.mark.asyncio
    async def test_start(self, stub: _NiuuStub) -> None:
        await stub.start()

    @pytest.mark.asyncio
    async def test_stop(self, stub: _NiuuStub) -> None:
        await stub.stop()

    @pytest.mark.asyncio
    async def test_health_check(self, stub: _NiuuStub) -> None:
        assert await stub.health_check() is True


# ---------------------------------------------------------------------------
# Shared model tests (moved from volundr to niuu)
# ---------------------------------------------------------------------------


class TestSharedModels:
    """Tests for models moved to niuu.domain.models."""

    def test_pull_request_status_values(self) -> None:
        assert PullRequestStatus.OPEN == "open"
        assert PullRequestStatus.MERGED == "merged"
        assert PullRequestStatus.CLOSED == "closed"

    def test_ci_status_values(self) -> None:
        assert CIStatus.PASSING == "passing"
        assert CIStatus.FAILING == "failing"
        assert CIStatus.PENDING == "pending"
        assert CIStatus.UNKNOWN == "unknown"

    def test_review_status_values(self) -> None:
        assert ReviewStatus.APPROVED == "approved"
        assert ReviewStatus.CHANGES_REQUESTED == "changes_requested"

    def test_pull_request_creation(self) -> None:
        pr = PullRequest(
            number=42,
            title="test PR",
            url="https://github.com/org/repo/pull/42",
            repo_url="https://github.com/org/repo",
            provider=GitProviderType.GITHUB,
            source_branch="feature",
            target_branch="main",
            status=PullRequestStatus.OPEN,
        )
        assert pr.number == 42
        assert pr.status == PullRequestStatus.OPEN
        assert pr.ci_status is None

    def test_pull_request_with_ci_status(self) -> None:
        pr = PullRequest(
            number=1,
            title="test",
            url="u",
            repo_url="r",
            provider=GitProviderType.GITLAB,
            source_branch="a",
            target_branch="b",
            status=PullRequestStatus.MERGED,
            ci_status=CIStatus.PASSING,
            review_status=ReviewStatus.APPROVED,
        )
        assert pr.ci_status == CIStatus.PASSING
        assert pr.review_status == ReviewStatus.APPROVED


# ---------------------------------------------------------------------------
# Git port tests
# ---------------------------------------------------------------------------


class TestGitPorts:
    """Tests for git port interfaces moved to niuu."""

    def test_git_auth_error(self) -> None:
        err = GitAuthError("auth failed")
        assert str(err) == "auth failed"
        assert isinstance(err, Exception)

    def test_git_repo_not_found_error(self) -> None:
        err = GitRepoNotFoundError("not found")
        assert str(err) == "not found"
        assert isinstance(err, Exception)

    def test_git_provider_is_abstract(self) -> None:
        assert hasattr(GitProvider, "list_repos")
        assert hasattr(GitProvider, "list_branches")
        assert hasattr(GitProvider, "supports")
        assert hasattr(GitProvider, "orgs")

    def test_git_workflow_provider_is_abstract(self) -> None:
        assert hasattr(GitWorkflowProvider, "create_branch")
        assert hasattr(GitWorkflowProvider, "create_pull_request")
        assert hasattr(GitWorkflowProvider, "merge_pull_request")
        assert hasattr(GitWorkflowProvider, "get_ci_status")


# ---------------------------------------------------------------------------
# Git config tests
# ---------------------------------------------------------------------------


class TestGitConfig:
    """Tests for git config classes moved to niuu."""

    def test_github_instance(self) -> None:
        inst = GitHubInstance(
            name="gh", base_url="https://api.github.com", token="tok", orgs=("org1",)
        )
        assert inst.name == "gh"
        assert inst.orgs == ("org1",)

    def test_gitlab_instance(self) -> None:
        inst = GitLabInstance(name="gl", base_url="https://gitlab.com", token="tok")
        assert inst.orgs == ()

    def test_github_config_get_instances_empty(self) -> None:
        config = GitHubConfig()
        assert config.get_instances() == []

    def test_github_config_get_instances_enabled(self) -> None:
        config = GitHubConfig(enabled=True, token="t")
        instances = config.get_instances()
        assert len(instances) == 1
        assert instances[0].name == "GitHub"

    def test_github_config_get_instances_from_list(self) -> None:
        config = GitHubConfig(
            instances=[
                {
                    "name": "gh1",
                    "base_url": "https://api.github.com",
                    "token": "t1",
                    "orgs": ["o1"],
                },
            ]
        )
        instances = config.get_instances()
        assert len(instances) == 1
        assert instances[0].name == "gh1"
        assert instances[0].orgs == ("o1",)

    def test_gitlab_config_get_instances_empty(self) -> None:
        config = GitLabConfig()
        assert config.get_instances() == []

    def test_gitlab_config_get_instances_enabled(self) -> None:
        config = GitLabConfig(enabled=True, token="t")
        instances = config.get_instances()
        assert len(instances) == 1
        assert instances[0].name == "GitLab"

    def test_git_config_defaults(self) -> None:
        config = GitConfig()
        assert isinstance(config.github, GitHubConfig)
        assert isinstance(config.gitlab, GitLabConfig)

    def test_github_config_skips_invalid_instances(self) -> None:
        config = GitHubConfig(
            instances=[
                {"name": "", "base_url": "u"},
                {"name": "n", "base_url": ""},
            ]
        )
        assert len(config.get_instances()) == 0

    def test_github_config_token_fallback(self) -> None:
        config = GitHubConfig(
            token="fallback",
            instances=[{"name": "gh", "base_url": "https://api.github.com"}],
        )
        instances = config.get_instances()
        assert instances[0].token == "fallback"

    def test_gitlab_config_skips_invalid_instances(self) -> None:
        config = GitLabConfig(
            instances=[
                {"name": "", "base_url": "u"},
                {"name": "n", "base_url": ""},
            ]
        )
        assert len(config.get_instances()) == 0


# ---------------------------------------------------------------------------
# RepoService tests
# ---------------------------------------------------------------------------


class TestRepoService:
    """Tests for RepoService moved to niuu."""

    @pytest.fixture
    def mock_registry(self):
        registry = AsyncMock()
        provider = AsyncMock()
        type(provider).name = PropertyMock(return_value="GitHub")
        type(provider).provider_type = PropertyMock(return_value=GitProviderType.GITHUB)
        type(provider).orgs = PropertyMock(return_value=("niuulabs",))
        registry.providers = [provider]
        return registry

    @pytest.fixture
    def service(self, mock_registry) -> RepoService:
        return RepoService(mock_registry)

    def test_list_providers(self, service: RepoService) -> None:
        providers = service.list_providers()
        assert len(providers) == 1
        assert providers[0].name == "GitHub"
        assert providers[0].type == GitProviderType.GITHUB

    @pytest.mark.asyncio
    async def test_list_repos_no_user(self, service: RepoService, mock_registry) -> None:
        expected = {
            "GitHub": [
                RepoInfo(
                    provider=GitProviderType.GITHUB,
                    org="niuulabs",
                    name="volundr",
                    clone_url="https://github.com/niuulabs/volundr.git",
                    url="https://github.com/niuulabs/volundr",
                )
            ]
        }
        mock_registry.list_configured_repos.return_value = expected
        result = await service.list_repos()
        assert result == expected
        mock_registry.list_configured_repos.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_repos_with_user_no_integration(
        self, service: RepoService, mock_registry
    ) -> None:
        """Without user_integration, user_id is ignored."""
        mock_registry.list_configured_repos.return_value = {}
        result = await service.list_repos(user_id="user-1")
        assert result == {}
        mock_registry.list_configured_repos.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_branches_no_user(self, service: RepoService, mock_registry) -> None:
        mock_registry.list_branches.return_value = ["main", "dev"]
        branches = await service.list_branches("https://github.com/org/repo")
        assert branches == ["main", "dev"]

    def test_provider_info_frozen(self) -> None:
        info = ProviderInfo(name="test", type=GitProviderType.GITHUB, orgs=("o",))
        assert info.name == "test"


# ---------------------------------------------------------------------------
# Re-export tests
# ---------------------------------------------------------------------------


class TestReExports:
    """Verify that volundr still re-exports moved types."""

    def test_volundr_models_re_exports(self) -> None:
        import importlib

        volundr_models = importlib.import_module("volundr.domain.models")
        assert volundr_models.CIStatus is CIStatus
        assert volundr_models.PullRequest is PullRequest
        assert volundr_models.PullRequestStatus is PullRequestStatus

    def test_volundr_ports_re_exports(self) -> None:
        import importlib

        volundr_ports = importlib.import_module("volundr.domain.ports")
        assert volundr_ports.GitAuthError is GitAuthError
        assert volundr_ports.GitProvider is GitProvider
        assert volundr_ports.GitRepoNotFoundError is GitRepoNotFoundError
        assert volundr_ports.GitWorkflowProvider is GitWorkflowProvider

    def test_volundr_config_re_exports(self) -> None:
        import importlib

        volundr_config = importlib.import_module("volundr.config")
        assert volundr_config.GitHubConfig is GitHubConfig
        assert volundr_config.GitHubInstance is GitHubInstance
        assert volundr_config.GitLabConfig is GitLabConfig
        assert volundr_config.GitLabInstance is GitLabInstance

    def test_volundr_adapters_re_exports(self) -> None:
        import importlib

        niuu_gh = importlib.import_module("niuu.adapters.outbound.github")
        niuu_gl = importlib.import_module("niuu.adapters.outbound.gitlab")
        niuu_reg = importlib.import_module("niuu.adapters.outbound.git_registry")
        volundr_gh = importlib.import_module("volundr.adapters.outbound.github")
        volundr_gl = importlib.import_module("volundr.adapters.outbound.gitlab")
        volundr_reg = importlib.import_module("volundr.adapters.outbound.git_registry")

        assert volundr_gh.GitHubProvider is niuu_gh.GitHubProvider
        assert volundr_gl.GitLabProvider is niuu_gl.GitLabProvider
        assert volundr_reg.GitProviderRegistry is niuu_reg.GitProviderRegistry

    def test_volundr_services_re_exports(self) -> None:
        import importlib

        niuu_repo = importlib.import_module("niuu.domain.services.repo")
        volundr_repo = importlib.import_module("volundr.domain.services.repo")
        assert volundr_repo.RepoService is niuu_repo.RepoService

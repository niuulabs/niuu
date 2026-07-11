"""Git provider registry for managing multiple git providers."""

import logging

from niuu.config import GitConfig
from niuu.domain.models import CIStatus, GitProviderType, PullRequest, RepoInfo
from niuu.ports.git import GitProvider, GitWorkflowProvider

from .github import GitHubProvider
from .gitlab import GitLabProvider

logger = logging.getLogger(__name__)


class GitProviderRegistry:
    """Registry for managing multiple git provider instances.

    Supports multiple providers (GitHub, GitLab) and multiple instances
    of the same provider type (e.g., multiple GitLab servers).
    """

    def __init__(self):
        self._providers: list[GitProvider] = []
        self._url_to_provider: dict[str, GitProvider] = {}

    def register(self, provider: GitProvider) -> None:
        """Register a git provider."""
        logger.info(
            "Registering git provider: name=%s, type=%s",
            provider.name,
            provider.provider_type.value,
        )
        self._providers.append(provider)
        logger.debug(
            "Provider registered successfully, total providers: %d",
            len(self._providers),
        )

    def unregister(self, provider: GitProvider) -> None:
        """Unregister a git provider."""
        self._providers.remove(provider)

    @property
    def providers(self) -> list[GitProvider]:
        """Return all registered providers."""
        return list(self._providers)

    def get_provider(self, repo_url: str) -> GitProvider | None:
        """Find a provider that supports the given repository URL.

        Checks the reverse lookup cache first (populated by list_configured_repos),
        then falls back to iterating providers by URL pattern matching.

        Args:
            repo_url: Repository URL or shorthand.

        Returns:
            The first provider that supports this URL, or None.
        """
        # Check reverse lookup first — exact match from prior listing
        cached = self._url_to_provider.get(repo_url)
        if cached is not None:
            logger.debug(
                "Reverse lookup hit for URL %s: %s (%s)",
                repr(repo_url),
                cached.name,
                cached.provider_type.value,
            )
            return cached

        logger.debug(
            "Looking for provider that supports URL: %s (checking %d providers)",
            repr(repo_url),
            len(self._providers),
        )
        for provider in self._providers:
            logger.debug(
                "Checking provider %s (%s) for URL support",
                provider.name,
                provider.provider_type.value,
            )
            if provider.supports(repo_url):
                logger.info(
                    "Found provider for URL %s: %s (%s)",
                    repr(repo_url),
                    provider.name,
                    provider.provider_type.value,
                )
                return provider
            logger.debug(
                "Provider %s does not support URL: %s",
                provider.name,
                repr(repo_url),
            )
        logger.warning(
            "No provider found for URL: %s (checked %d providers: %s)",
            repr(repo_url),
            len(self._providers),
            ", ".join(p.name for p in self._providers) if self._providers else "none registered",
        )
        return None

    def parse_repo(self, repo_url: str) -> RepoInfo | None:
        """Parse a repository URL using the appropriate provider.

        Args:
            repo_url: Repository URL or shorthand.

        Returns:
            RepoInfo if any provider can parse it, None otherwise.
        """
        provider = self.get_provider(repo_url)
        if provider is None:
            return None
        return provider.parse_repo(repo_url)

    def get_clone_url(self, repo_url: str) -> str | None:
        """Get authenticated clone URL using the appropriate provider.

        Args:
            repo_url: Repository URL or shorthand.

        Returns:
            Authenticated clone URL, or None if no provider supports it.
        """
        provider = self.get_provider(repo_url)
        if provider is None:
            return None
        return provider.get_clone_url(repo_url)

    async def validate_repo(self, repo_url: str) -> bool:
        """Validate a repository using the appropriate provider.

        Args:
            repo_url: Repository URL or shorthand.

        Returns:
            True if the repository exists and is accessible.
        """
        logger.info("Validating repository: %s", repr(repo_url))
        provider = self.get_provider(repo_url)
        if provider is None:
            logger.error(
                "Cannot validate repository %s: no provider supports this URL",
                repr(repo_url),
            )
            return False
        logger.debug(
            "Using provider %s to validate repository %s",
            provider.name,
            repr(repo_url),
        )
        is_valid = await provider.validate_repo(repo_url)
        if is_valid:
            logger.info(
                "Repository validation successful: %s (provider: %s)",
                repr(repo_url),
                provider.name,
            )
        else:
            logger.warning(
                "Repository validation failed: %s (provider: %s)",
                repr(repo_url),
                provider.name,
            )
        return is_valid

    def _register_repos(self, provider: GitProvider, repos: list[RepoInfo]) -> None:
        """Populate reverse lookup for a batch of repos from a provider."""
        for repo in repos:
            self._url_to_provider[repo.url] = provider

    async def list_repos(
        self,
        org: str,
        provider_type: GitProviderType | None = None,
    ) -> list[RepoInfo]:
        """List repositories from all providers or a specific provider type.

        Args:
            org: Organization or group name.
            provider_type: Optional filter by provider type.

        Returns:
            Aggregated list of repositories from all matching providers.
        """
        repos: list[RepoInfo] = []

        for provider in self._providers:
            if provider_type is not None and provider.provider_type != provider_type:
                continue

            provider_repos = await provider.list_repos(org)
            self._register_repos(provider, provider_repos)
            repos.extend(provider_repos)

        return repos

    async def list_configured_repos(self) -> dict[str, list[RepoInfo]]:
        """List repositories from all providers using their configured orgs.

        Iterates each provider's configured orgs and fetches repos. Populates
        the reverse URL lookup so that subsequent get_provider calls for any
        listed repo resolve to the correct provider instance.

        Returns:
            Dict mapping provider name to list of repositories.
        """
        result: dict[str, list[RepoInfo]] = {}

        for provider in self._providers:
            if not provider.orgs:
                continue

            provider_repos: list[RepoInfo] = []
            for org in provider.orgs:
                repos = await provider.list_repos(org)
                provider_repos.extend(repos)

            self._register_repos(provider, provider_repos)

            if provider_repos:
                result[provider.name] = provider_repos

        return result

    def _get_workflow_provider(self, repo_url: str) -> GitWorkflowProvider:
        """Get a workflow-capable provider for the given repo URL.

        Raises:
            ValueError: If no provider supports the URL or the provider
                doesn't implement GitWorkflowProvider.
        """
        provider = self.get_provider(repo_url)
        if provider is None:
            raise ValueError(f"No git provider found for: {repo_url}")
        if not isinstance(provider, GitWorkflowProvider):
            raise ValueError(f"Provider {provider.name} does not support workflow operations")
        return provider

    async def create_branch(
        self,
        repo_url: str,
        branch_name: str,
        from_branch: str = "main",
    ) -> bool:
        """Create a branch via the appropriate provider."""
        provider = self._get_workflow_provider(repo_url)
        return await provider.create_branch(repo_url, branch_name, from_branch)

    async def create_pull_request(
        self,
        repo_url: str,
        title: str,
        description: str,
        source_branch: str,
        target_branch: str,
        labels: list[str] | None = None,
    ) -> PullRequest:
        """Create a PR via the appropriate provider."""
        provider = self._get_workflow_provider(repo_url)
        return await provider.create_pull_request(
            repo_url, title, description, source_branch, target_branch, labels
        )

    async def get_pull_request(self, repo_url: str, pr_number: int) -> PullRequest | None:
        """Get a PR via the appropriate provider."""
        provider = self._get_workflow_provider(repo_url)
        return await provider.get_pull_request(repo_url, pr_number)

    async def list_pull_requests(self, repo_url: str, status: str = "open") -> list[PullRequest]:
        """List PRs via the appropriate provider."""
        provider = self._get_workflow_provider(repo_url)
        return await provider.list_pull_requests(repo_url, status)

    async def merge_pull_request(
        self,
        repo_url: str,
        pr_number: int,
        merge_method: str = "squash",
    ) -> bool:
        """Merge a PR via the appropriate provider."""
        provider = self._get_workflow_provider(repo_url)
        return await provider.merge_pull_request(repo_url, pr_number, merge_method)

    async def get_ci_status(self, repo_url: str, branch: str) -> CIStatus:
        """Get CI status via the appropriate provider."""
        provider = self._get_workflow_provider(repo_url)
        return await provider.get_ci_status(repo_url, branch)

    async def list_branches(self, repo_url: str) -> list[str]:
        """List branches for a specific repository via the appropriate provider.

        Args:
            repo_url: Repository URL.

        Returns:
            List of branch names.

        Raises:
            ValueError: If no provider supports the URL.
            GitAuthError: If authentication fails.
            GitRepoNotFoundError: If the repository is not found.
        """
        provider = self.get_provider(repo_url)
        if provider is None:
            raise ValueError(f"No git provider found for: {repo_url}")
        return await provider.list_branches(repo_url)

    async def close(self) -> None:
        """Close all provider HTTP clients."""
        for provider in self._providers:
            if hasattr(provider, "close"):
                await provider.close()


def create_git_registry(config: GitConfig) -> GitProviderRegistry:
    """Create a GitProviderRegistry from configuration.

    Args:
        config: Git configuration with provider settings.

    Returns:
        Configured GitProviderRegistry with all enabled providers.
    """
    logger.info("Creating git provider registry from configuration")
    registry = GitProviderRegistry()

    # Register GitHub instances
    github_instances = config.github.get_instances()
    logger.info("Found %d GitHub instance(s) to register", len(github_instances))
    for instance in github_instances:
        logger.debug(
            "Configuring GitHub provider: name=%s, base_url=%s, token=%s",
            instance.name,
            instance.base_url,
            "***" if instance.token else "not set",
        )
        registry.register(
            GitHubProvider(
                name=instance.name,
                base_url=instance.base_url,
                token=instance.token,
                orgs=instance.orgs,
            )
        )

    # Register GitLab instances
    gitlab_instances = config.gitlab.get_instances()
    logger.info("Found %d GitLab instance(s) to register", len(gitlab_instances))
    for instance in gitlab_instances:
        logger.debug(
            "Configuring GitLab provider: name=%s, base_url=%s, token=%s",
            instance.name,
            instance.base_url,
            "***" if instance.token else "not set",
        )
        registry.register(
            GitLabProvider(
                name=instance.name,
                base_url=instance.base_url,
                token=instance.token,
                orgs=instance.orgs,
            )
        )

    logger.info(
        "Git provider registry created with %d provider(s): %s",
        len(registry.providers),
        ", ".join(f"{p.name} ({p.provider_type.value})" for p in registry.providers)
        if registry.providers
        else "none",
    )
    return registry

"""Re-export from niuu — GitHub provider now lives in the shared module."""

from niuu.adapters.outbound.github import GitHubProvider  # noqa: F401

__all__ = ["GitHubProvider"]

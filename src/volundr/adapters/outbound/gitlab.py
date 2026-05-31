"""Re-export from niuu — GitLab provider now lives in the shared module."""

from niuu.adapters.outbound.gitlab import GitLabProvider  # noqa: F401

__all__ = ["GitLabProvider"]

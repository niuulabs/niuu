"""Re-export from niuu — RepoService now lives in the shared module."""

from niuu.domain.services.repo import ProviderInfo, RepoService  # noqa: F401

__all__ = ["ProviderInfo", "RepoService"]

"""Re-export from niuu — git registry now lives in the shared module."""

from niuu.adapters.outbound.git_registry import (  # noqa: F401
    GitProviderRegistry,
    create_git_registry,
)

__all__ = ["GitProviderRegistry", "create_git_registry"]

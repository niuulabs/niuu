"""Shared model-catalog port for Bifrost-owned LLM metadata."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.model_catalog import ManagedModel, ManagedModelAlias, ManagedProvider


class ModelCatalogPort(ABC):
    """Read access to the canonical Bifrost model catalog."""

    @abstractmethod
    async def list_models(self) -> list[ManagedModel]:
        """Return all managed model entries."""

    @abstractmethod
    async def get_model(self, model_id: str) -> ManagedModel | None:
        """Return one managed model entry by canonical ID or alias."""

    @abstractmethod
    async def list_aliases(self) -> list[ManagedModelAlias]:
        """Return all configured alias mappings."""

    @abstractmethod
    async def list_providers(self) -> list[ManagedProvider]:
        """Return all configured providers plus derived health state."""

"""Volundr-owned port for reading the Bifrost model catalog."""

from __future__ import annotations

from typing import Protocol

from niuu.domain.model_catalog import ManagedModel


class BifrostCatalogPort(Protocol):
    """Read model metadata from a configured Bifrost service."""

    async def list_models(self) -> list[ManagedModel]:
        """Return the current Bifrost model catalog."""

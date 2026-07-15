"""Composition helpers for standalone-resident discovery adapters."""

from __future__ import annotations

import logging
from typing import Any

from ravn.dynamic_adapters import build_dynamic_adapter
from ravn.ports.resident_discovery import ResidentDiscoveryPort, StandaloneResident

logger = logging.getLogger(__name__)


class CompositeResidentDiscoveryAdapter:
    """Combine multiple standalone-resident discovery sources into one read model."""

    def __init__(self, adapters: list[ResidentDiscoveryPort]) -> None:
        self._adapters = adapters

    async def list_residents(self) -> list[StandaloneResident]:
        """Return residents from all adapters, de-duplicated by id."""
        by_id: dict[str, StandaloneResident] = {}
        for adapter in self._adapters:
            try:
                residents = await adapter.list_residents()
            except Exception as exc:
                logger.warning(
                    "Resident discovery adapter %s failed: %s",
                    adapter.__class__.__name__,
                    exc,
                )
                continue
            for resident in residents:
                by_id[resident.id] = resident
        return sorted(by_id.values(), key=lambda item: (item.resident_name.lower(), item.id))


def build_resident_discovery(config: Any | None = None) -> ResidentDiscoveryPort:
    """Build the configured standalone-resident discovery adapter chain."""
    if config is None:
        return CompositeResidentDiscoveryAdapter([])

    if not getattr(config, "enabled", True):
        return CompositeResidentDiscoveryAdapter([])

    adapters_config = list(getattr(config, "adapters", []) or [])
    adapters = [build_dynamic_adapter(adapter_config) for adapter_config in adapters_config]
    return CompositeResidentDiscoveryAdapter(
        [adapter for adapter in adapters if adapter is not None]
    )

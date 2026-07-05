"""Composition helpers for Warden discovery adapters."""

from __future__ import annotations

import logging
from typing import Any

from ravn.adapters.warden_discovery.spec import WardenSpecDiscoveryAdapter
from ravn.dynamic_adapters import build_dynamic_adapter
from ravn.ports.warden_discovery import WardenDiscoveryPort
from ravn.warden.models import WardenSpec
from ravn.warden.store import WardenStore

logger = logging.getLogger(__name__)


class CompositeWardenDiscoveryAdapter:
    """Combine multiple Warden discovery sources into one read model."""

    def __init__(self, adapters: list[WardenDiscoveryPort]) -> None:
        self._adapters = adapters

    async def list_wardens(self) -> list[WardenSpec]:
        """Return wardens from all adapters, de-duplicated by id."""
        by_id: dict[str, WardenSpec] = {}
        for adapter in self._adapters:
            try:
                wardens = await adapter.list_wardens()
            except Exception as exc:
                logger.warning(
                    "Warden discovery adapter %s failed: %s",
                    adapter.__class__.__name__,
                    exc,
                )
                continue
            for warden in wardens:
                by_id[warden.id] = warden
        return sorted(by_id.values(), key=lambda spec: (spec.name.lower(), spec.id))


def build_warden_discovery(
    config: Any | None = None,
    *,
    store: WardenStore | None = None,
) -> WardenDiscoveryPort:
    """Build the configured Warden discovery adapter chain."""
    if config is None:
        return WardenSpecDiscoveryAdapter(store=store)

    if not getattr(config, "enabled", True):
        return CompositeWardenDiscoveryAdapter([])

    adapters_config = list(getattr(config, "adapters", []) or [])
    if not adapters_config:
        return WardenSpecDiscoveryAdapter(store=store)

    extra_kwargs = {"store": store} if store is not None else {}
    adapters = [
        build_dynamic_adapter(adapter_config, extra_kwargs=extra_kwargs)
        for adapter_config in adapters_config
    ]
    return CompositeWardenDiscoveryAdapter([adapter for adapter in adapters if adapter is not None])

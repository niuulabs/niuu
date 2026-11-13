"""Adapters for consuming the canonical Bifrost model catalog."""

from __future__ import annotations

from typing import Any

import httpx

from bifrost import catalog as bifrost_catalog
from bifrost.config import BifrostConfig
from niuu.domain.model_catalog import (
    ManagedModel,
    ManagedModelAlias,
    ManagedModelProvider,
    ManagedModelTier,
    ManagedProvider,
    ProviderHealthState,
)
from niuu.ports.model_catalog import ModelCatalogPort


def _coerce_model(payload: dict[str, Any]) -> ManagedModel:
    return ManagedModel(
        id=str(payload.get("id") or "").strip(),
        name=str(payload.get("name") or "").strip(),
        vendor=str(payload.get("vendor") or "").strip(),
        provider=ManagedModelProvider(str(payload.get("provider") or ManagedModelProvider.CLOUD)),
        tier=ManagedModelTier(str(payload.get("tier") or ManagedModelTier.BALANCED)),
        color=str(payload.get("color") or "#2563EB"),
        description=str(payload.get("description") or ""),
        cost_per_million_tokens=payload.get("cost_per_million_tokens"),
        vram_required=payload.get("vram_required"),
        session_definition=payload.get("session_definition"),
        supports_tools=bool(payload.get("supports_tools", True)),
        supports_thinking=bool(payload.get("supports_thinking", True)),
        enabled=bool(payload.get("enabled", True)),
        aliases=tuple(str(item) for item in payload.get("aliases") or []),
        provider_keys=tuple(str(item) for item in payload.get("provider_keys") or []),
    )


def _coerce_alias(payload: dict[str, Any]) -> ManagedModelAlias:
    return ManagedModelAlias(
        alias=str(payload.get("alias") or "").strip(),
        target=str(payload.get("target") or "").strip(),
    )


def _coerce_provider(payload: dict[str, Any]) -> ManagedProvider:
    return ManagedProvider(
        key=str(payload.get("key") or "").strip(),
        vendor=str(payload.get("vendor") or "").strip(),
        base_url=str(payload.get("base_url") or "").strip(),
        model_ids=tuple(str(item) for item in payload.get("model_ids") or []),
        timeout_seconds=float(payload.get("timeout_seconds") or 120.0),
        cost_per_token=float(payload.get("cost_per_token") or 0.0),
        state=ProviderHealthState(str(payload.get("state") or ProviderHealthState.UNKNOWN)),
        detail=str(payload.get("detail") or ""),
    )


class StaticBifrostModelCatalog(ModelCatalogPort):
    """In-process model catalog backed by a BifrostConfig instance."""

    def __init__(self, config: BifrostConfig | None = None) -> None:
        self._config = config or BifrostConfig()

    async def list_models(self) -> list[ManagedModel]:
        return bifrost_catalog.list_models(self._config)

    async def get_model(self, model_id: str) -> ManagedModel | None:
        return bifrost_catalog.get_model(self._config, model_id)

    async def list_aliases(self) -> list[ManagedModelAlias]:
        return bifrost_catalog.list_aliases(self._config)

    async def list_providers(self) -> list[ManagedProvider]:
        return bifrost_catalog.list_providers(self._config)


class HttpBifrostModelCatalog(ModelCatalogPort):
    """HTTP adapter for a live Bifrost service."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def list_models(self) -> list[ManagedModel]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base_url}/models")
            response.raise_for_status()
        return [_coerce_model(item) for item in response.json()]

    async def get_model(self, model_id: str) -> ManagedModel | None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base_url}/models/{model_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _coerce_model(response.json())

    async def list_aliases(self) -> list[ManagedModelAlias]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base_url}/aliases")
            response.raise_for_status()
        return [_coerce_alias(item) for item in response.json()]

    async def list_providers(self) -> list[ManagedProvider]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{self._base_url}/providers/health")
            response.raise_for_status()
        return [_coerce_provider(item) for item in response.json()]

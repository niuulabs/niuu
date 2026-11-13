"""HTTP adapter for Volundr's Bifrost model catalog dependency."""

from __future__ import annotations

from typing import Any

import httpx

from niuu.domain.model_catalog import (
    ManagedModel,
    ManagedModelProvider,
    ManagedModelTier,
)
from niuu.ports.http_auth import HttpAuthPort
from volundr.ports.bifrost_catalog import BifrostCatalogPort


def _coerce_model(payload: dict[str, Any]) -> ManagedModel:
    return ManagedModel(
        id=str(payload.get("id") or "").strip(),
        name=str(payload.get("name") or "").strip(),
        vendor=str(payload.get("vendor") or "").strip(),
        provider=ManagedModelProvider(
            str(payload.get("provider") or ManagedModelProvider.CLOUD.value)
        ),
        tier=ManagedModelTier(str(payload.get("tier") or ManagedModelTier.BALANCED.value)),
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


class HttpBifrostCatalogAdapter(BifrostCatalogPort):
    """Load Volundr's model catalog from a configured Bifrost API."""

    def __init__(
        self,
        *,
        base_url: str,
        auth: HttpAuthPort,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._timeout = timeout_seconds
        self._transport = transport

    async def list_models(self) -> list[ManagedModel]:
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            response = await client.get(
                f"{self._base_url}/api/v1/bifrost/models",
                headers=self._auth.headers(),
            )
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        return [_coerce_model(item) for item in payload if isinstance(item, dict)]

"""Guild-backed runtime instance discovery for Ting."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx

from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.ports.http_auth import HttpAuthPort
from ting.adapters.inbound.auth import current_bearer_token

logger = logging.getLogger(__name__)


class GuildInstanceRegistryClient:
    """Discover registered runtime instances from Guild's shared registry API."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        auth: HttpAuthPort | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)
        self._auth = auth

    async def close(self) -> None:
        await self._client.aclose()

    async def list_volundr_targets(self, principal: Principal) -> list[RegisteredInstance]:
        response = await self._client.get(
            "/api/v1/niuu/instances",
            params={"kind": InstanceKind.VOLUNDR.value, "enabledOnly": "true"},
            headers=_principal_headers(
                principal,
                auth_headers=self._auth.headers() if self._auth else None,
            ),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            logger.warning("Guild instance registry returned non-list payload")
            return []
        return [_to_instance(item) for item in payload if isinstance(item, dict)]

    async def get_volundr_target(
        self,
        principal: Principal,
        instance_id: str,
    ) -> RegisteredInstance | None:
        for instance in await self.list_volundr_targets(principal):
            if instance.id == instance_id:
                return instance
        return None


def _principal_headers(
    principal: Principal,
    *,
    auth_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    headers = dict(auth_headers or {})
    principal_headers = {
        "x-auth-user-id": principal.user_id,
        "x-auth-email": principal.email,
        "x-auth-tenant": principal.tenant_id,
        "x-auth-roles": ",".join(principal.roles),
    }
    token = current_bearer_token()
    if token:
        headers = {key: value for key, value in headers.items() if key.lower() != "authorization"}
        headers["authorization"] = f"Bearer {token}"
    headers.update(principal_headers)
    return headers


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    return datetime.fromisoformat(raw)


def _to_instance(data: dict[str, Any]) -> RegisteredInstance:
    return RegisteredInstance(
        id=str(data["id"]),
        kind=InstanceKind(str(data["kind"])),
        slug=str(data["slug"]),
        name=str(data["name"]),
        base_url=str(data.get("baseUrl") or data.get("base_url") or "").rstrip("/"),
        visibility=InstanceVisibility(str(data["visibility"])),
        owner_id=data.get("ownerId") or data.get("owner_id"),
        tenant_id=data.get("tenantId") or data.get("tenant_id"),
        enabled=bool(data["enabled"]),
        is_default=bool(data.get("isDefault") or data.get("is_default")),
        config=dict(data.get("config") or {}),
        created_at=_parse_datetime(data.get("createdAt") or data.get("created_at")),
        updated_at=_parse_datetime(data.get("updatedAt") or data.get("updated_at")),
        tags=list(data.get("tags") or []),
    )

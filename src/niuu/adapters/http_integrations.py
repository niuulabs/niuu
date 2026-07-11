"""HTTP adapter for shared integration connections."""

from __future__ import annotations

from datetime import datetime

import httpx

from niuu.domain.models import IntegrationConnection, IntegrationType
from niuu.ports.integrations import IntegrationRepository


class UnsupportedIntegrationQueryError(NotImplementedError):
    """Raised when an HTTP repository cannot perform a global integration query."""


class HTTPIntegrationRepository(IntegrationRepository):
    """Resolve integration connections from the shared niuu service over HTTP."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        default_email: str = "",
        default_tenant_id: str = "default",
        default_roles: tuple[str, ...] = ("volundr:admin",),
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_email = default_email
        self._default_tenant_id = default_tenant_id
        self._default_roles = default_roles
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def list_connections(
        self,
        owner_id: str,
        integration_type: IntegrationType | None = None,
    ) -> list[IntegrationConnection]:
        response = await self._client.get(
            "/internal/api/v1/integrations",
            headers=self._headers(owner_id),
        )
        response.raise_for_status()
        connections = [self._to_connection(item, owner_id=owner_id) for item in response.json()]
        if integration_type is None:
            return connections
        return [item for item in connections if item.integration_type == integration_type]

    async def list_connections_global(
        self,
        integration_type: IntegrationType | None = None,
        *,
        slug: str | None = None,
        enabled_only: bool = False,
    ) -> list[IntegrationConnection]:
        raise UnsupportedIntegrationQueryError(
            "Global integration queries require a repository with shared-store access; "
            "the HTTP integration API is owner-scoped."
        )

    async def get_connection(self, connection_id: str) -> IntegrationConnection | None:
        response = await self._client.get(
            f"/internal/api/v1/integrations/{connection_id}",
            headers=self._headers(""),
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        return self._to_connection(data, owner_id=str(data.get("owner_id", "")))

    async def save_connection(
        self,
        connection: IntegrationConnection,
    ) -> IntegrationConnection:
        existing = await self.get_connection(connection.id)
        payload = {
            "integration_type": str(connection.integration_type),
            "adapter": connection.adapter,
            "credential_name": connection.credential_name,
            "config": connection.config,
            "enabled": connection.enabled,
            "slug": connection.slug,
        }
        if existing is None:
            response = await self._client.post(
                "/internal/api/v1/integrations",
                headers=self._headers(connection.owner_id),
                json=payload,
            )
        else:
            response = await self._client.put(
                f"/internal/api/v1/integrations/{connection.id}",
                headers=self._headers(connection.owner_id),
                json={
                    "credential_name": connection.credential_name,
                    "config": connection.config,
                    "enabled": connection.enabled,
                },
            )
        response.raise_for_status()
        return self._to_connection(response.json(), owner_id=connection.owner_id)

    async def delete_connection(self, connection_id: str) -> None:
        existing = await self.get_connection(connection_id)
        if existing is None:
            return
        response = await self._client.delete(
            f"/internal/api/v1/integrations/{connection_id}",
            headers=self._headers(existing.owner_id),
        )
        if response.status_code not in (204, 404):
            response.raise_for_status()

    def _headers(self, owner_id: str) -> dict[str, str]:
        return {
            "x-auth-user-id": owner_id or "service-integrations",
            "x-auth-email": self._default_email,
            "x-auth-tenant": self._default_tenant_id,
            "x-auth-roles": ",".join(self._default_roles),
        }

    @staticmethod
    def _to_connection(data: dict, *, owner_id: str) -> IntegrationConnection:
        resolved_owner_id = owner_id or str(data.get("owner_id") or "")
        return IntegrationConnection(
            id=str(data["id"]),
            owner_id=resolved_owner_id,
            integration_type=IntegrationType(str(data["integration_type"])),
            adapter=str(data["adapter"]),
            credential_name=str(data["credential_name"]),
            config=dict(data.get("config") or {}),
            enabled=bool(data["enabled"]),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
            slug=str(data.get("slug") or ""),
        )

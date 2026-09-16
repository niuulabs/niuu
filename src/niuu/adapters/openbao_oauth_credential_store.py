"""OAuth lifecycle delegated to OpenBao oauthapp; ordinary secrets remain in KV.

Configure oauth_servers as {"integration-slug/oauth-app": "engine-server-name"}.
Those server definitions must be provisioned in oauthapp with the same OAuth
client used by enrollment. No application process runs a refresh loop here.
"""

from __future__ import annotations

import re
from dataclasses import replace

import httpx

from niuu.adapters.openbao_credential_store import OpenBaoCredentialStore
from niuu.domain.models import SecretType, StoredCredential
from niuu.domain.oauth_credentials import (
    OAUTH_ENGINE,
    OAuthCredentialUnavailableError,
    oauth_application_name,
    oauth_credential_name,
)
from niuu.ports.credentials import OAuthApplicationStorePort


class OpenBaoOAuthCredentialStore(OpenBaoCredentialStore, OAuthApplicationStorePort):
    def __init__(
        self,
        *,
        oauth_mount_path: str,
        oauth_servers: dict[str, str] | None = None,
        manage_oauth_applications: bool = False,
        minimum_seconds: int = 120,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", oauth_mount_path):
            raise ValueError("oauth_mount_path must be a literal OpenBao mount path")
        if minimum_seconds <= 0:
            raise ValueError("minimum_seconds must be positive")
        self._oauth_mount = oauth_mount_path
        self._oauth_servers = dict(oauth_servers or {})
        self._manage_apps = manage_oauth_applications
        if self._manage_apps and self._oauth_servers:
            raise ValueError("Choose managed OAuth applications or manual oauth_servers mappings")
        self._minimum_seconds = minimum_seconds

    @property
    def manages_oauth_applications(self) -> bool:
        return self._manage_apps

    async def configure_oauth_application(
        self,
        *,
        slug: str,
        app: str,
        client_id: str,
        client_secret: str,
        authorize_url: str,
        token_url: str,
    ) -> None:
        if not self._manage_apps:
            raise ValueError("Managed OAuth application provisioning is disabled")
        if not all(url.startswith("https://") for url in (authorize_url, token_url)):
            raise ValueError("Managed OAuth applications require HTTPS provider endpoints")
        server = oauth_application_name(slug, app)
        response = await self._request(
            "post",
            f"/v1/{self._oauth_mount}/servers/{server}",
            json={
                "provider": "custom",
                "client_id": client_id,
                "client_secret": client_secret,
                "provider_options": {
                    "auth_code_url": authorize_url,
                    "token_url": token_url,
                    "auth_style": "in_params",
                },
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenBao OAuth application provisioning failed (HTTP {response.status_code})"
            )

    def _oauth_path(self, owner_id: str, name: str, metadata: dict) -> str:
        key = oauth_credential_name(metadata.get("tenant_id", ""), owner_id, name)
        return f"{self._oauth_mount}/creds/{key}"

    async def store(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
        secret_type: SecretType,
        data: dict[str, str],
        metadata: dict | None = None,
    ) -> StoredCredential:
        existing = await super().get(owner_type, owner_id, name)
        meta = dict(metadata or {})
        managed = existing is not None and existing.metadata.get("renewal_owner") == OAUTH_ENGINE
        if managed:
            if data and not data.get("refresh_token"):
                raise ValueError("Replacing managed OAuth authorization requires a refresh token")
            for key in ("tenant_id", "oauth_app", "integration", "oauth_token_field"):
                previous = existing.metadata.get(key)
                if key in meta and meta[key] != previous:
                    raise ValueError("Managed OAuth credential identity cannot be changed")
                if previous is not None:
                    meta[key] = previous
            meta["renewal_owner"] = OAUTH_ENGINE
        if not data.get("refresh_token") and not managed:
            if meta.get("renewal_owner") == OAUTH_ENGINE:
                raise ValueError("OAuth engine references must be created by enrollment")
            return await super().store(owner_type, owner_id, name, secret_type, data, meta)
        if owner_type != "user":
            raise ValueError("OAuth enrollment requires a user-owned integration")
        path = self._oauth_path(owner_id, name, meta)
        if data.get("refresh_token"):
            server_key = f"{meta.get('integration', '')}/{meta.get('oauth_app', 'default')}"
            server = (
                oauth_application_name(
                    meta.get("integration", ""), meta.get("oauth_app", "default")
                )
                if self._manage_apps
                else self._oauth_servers.get(server_key)
            )
            if not server:
                raise ValueError(f"Configure oauth_servers[{server_key!r}] before enrollment")
            field = str(meta.get("oauth_token_field") or "token")
            if field in {"refresh_token", "expires_at"}:
                raise ValueError("Invalid OAuth access token field")
            response = await self._request(
                "post",
                f"/v1/{path}",
                json={
                    "server": server,
                    "grant_type": "refresh_token",
                    "refresh_token": data["refresh_token"],
                },
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"OpenBao OAuth enrollment failed (HTTP {response.status_code}); reconnect"
                )
            meta.update(renewal_owner=OAUTH_ENGINE, oauth_token_field=field)
        # KV holds only connection metadata. In particular, neither the old
        # access token nor the provider's rotated refresh token is copied here.
        stored = await super().store(owner_type, owner_id, name, secret_type, {}, meta)
        return replace(stored, keys=(meta["oauth_token_field"], "expires_at"))

    async def get(self, owner_type: str, owner_id: str, name: str) -> StoredCredential | None:
        stored = await super().get(owner_type, owner_id, name)
        if stored is None or stored.metadata.get("renewal_owner") != OAUTH_ENGINE:
            return stored
        return replace(stored, keys=(stored.metadata["oauth_token_field"], "expires_at"))

    async def get_value(self, owner_type: str, owner_id: str, name: str) -> dict[str, str] | None:
        stored = await self.get(owner_type, owner_id, name)
        if stored is None or stored.metadata.get("renewal_owner") != OAUTH_ENGINE:
            return await super().get_value(owner_type, owner_id, name)
        path = self._oauth_path(owner_id, name, stored.metadata)
        try:
            response = await self._request(
                "get", f"/v1/{path}", params={"minimum_seconds": self._minimum_seconds}
            )
        except httpx.RequestError:
            raise OAuthCredentialUnavailableError() from None
        if response.status_code >= 400:
            # Do not reflect provider error bodies (which may contain secrets).
            errors = []
            if response.status_code == 400:
                try:
                    errors = response.json().get("errors", [])
                except ValueError:
                    pass
            reconnect = response.status_code == 404 or any(
                message == "token expired" or "invalid_grant" in str(message) for message in errors
            )
            raise OAuthCredentialUnavailableError(reconnect=reconnect)
        data = response.json().get("data", {})
        if not data.get("access_token"):
            raise OAuthCredentialUnavailableError(reconnect=True)
        result = {stored.metadata["oauth_token_field"]: str(data["access_token"])}
        if data.get("expire_time"):
            result["expires_at"] = str(data["expire_time"])
        return result

    async def delete(self, owner_type: str, owner_id: str, name: str) -> None:
        stored = await self.get(owner_type, owner_id, name)
        if stored is not None and stored.metadata.get("renewal_owner") == OAUTH_ENGINE:
            path = self._oauth_path(owner_id, name, stored.metadata)
            response = await self._request("delete", f"/v1/{path}")
            if response.status_code >= 400 and response.status_code != 404:
                raise RuntimeError(f"OpenBao OAuth deletion failed (HTTP {response.status_code})")
        await super().delete(owner_type, owner_id, name)

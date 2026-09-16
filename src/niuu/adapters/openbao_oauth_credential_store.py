"""OAuth lifecycle delegated to OpenBao oauthapp; ordinary secrets remain in KV.

Configure oauth_servers as {"integration-slug/oauth-app": "engine-server-name"}.
Those server definitions must be provisioned in oauthapp with the same OAuth
client used by enrollment. No application process runs a refresh loop here.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace

import httpx

from niuu.adapters.openbao_credential_store import OpenBaoCredentialStore
from niuu.domain.codex_credentials import (
    CODEX_AUTH_FORMAT,
    codex_plan_type,
    codex_token_claims,
    parse_codex_auth_document,
)
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
        codex_oauth_server: str = "",
        codex_maximum_expiry_seconds: int = 3600,
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
        if codex_oauth_server and not re.fullmatch(r"[A-Za-z0-9_-]+", codex_oauth_server):
            raise ValueError("codex_oauth_server must be a literal server name")
        self._codex_server = codex_oauth_server
        if codex_maximum_expiry_seconds <= minimum_seconds:
            raise ValueError("codex_maximum_expiry_seconds must exceed minimum_seconds")
        self._codex_maximum_expiry_seconds = codex_maximum_expiry_seconds

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
        if meta.get("enrollment_method") == "codex_device" or (
            existing and existing.metadata.get("oauth_format") == CODEX_AUTH_FORMAT
        ):
            return await self._store_codex(
                owner_type, owner_id, name, secret_type, data, meta, existing
            )

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
        return replace(
            stored,
            keys=tuple(
                dict.fromkeys((*stored.keys, stored.metadata["oauth_token_field"], "expires_at"))
            ),
        )

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
        if stored.metadata.get("oauth_format") == CODEX_AUTH_FORMAT:
            token = str(data["access_token"])
            self._check_codex_account(token, stored.metadata["codex_account_id"])
            auth = {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": token,
                    "account_id": stored.metadata["codex_account_id"],
                },
                "chatgpt_plan_type": stored.metadata.get("codex_plan_type", ""),
            }
            result = dict(await super().get_value(owner_type, owner_id, name) or {})
            result[stored.metadata["oauth_token_field"]] = json.dumps(auth, separators=(",", ":"))
        else:
            result = {stored.metadata["oauth_token_field"]: str(data["access_token"])}
        if data.get("expire_time"):
            result["expires_at"] = str(data["expire_time"])
        return result

    @staticmethod
    def _check_codex_account(token: str, account_id: str) -> None:
        claims = codex_token_claims(token).get("https://api.openai.com/auth", {})
        if not isinstance(claims, dict) or claims.get("chatgpt_account_id") != account_id:
            raise ValueError("Codex OAuth token does not match the stored account")

    async def _store_codex(
        self, owner_type, owner_id, name, secret_type, data, meta, existing, *, resume=False
    ) -> StoredCredential:
        if owner_type != "user" or not self._codex_server:
            raise ValueError("Codex enrollment requires a user-owned grant and codex_oauth_server")
        if existing and existing.metadata.get("renewal_owner") == OAUTH_ENGINE:
            for key in ("tenant_id", "oauth_token_field", "oauth_format"):
                previous = existing.metadata[key]
                if key in meta and meta[key] != previous:
                    raise ValueError("Managed Codex credential identity cannot be changed")
                meta[key] = previous
        field = str(meta.get("oauth_token_field") or "auth.json")
        path = self._oauth_path(owner_id, name, meta)
        if not data:
            if not existing or existing.metadata.get("oauth_format") != CODEX_AUTH_FORMAT:
                raise ValueError("Codex enrollment requires an auth document")
            preserved = dict(await super().get_value(owner_type, owner_id, name) or {})
            return await super().store(
                owner_type, owner_id, name, secret_type, preserved, {**existing.metadata, **meta}
            )
        auth = parse_codex_auth_document(data.get(field), require_refresh=True)
        account_id = auth["tokens"]["account_id"]
        response = None
        if resume:
            response = await self._request("get", f"/v1/{path}")
            if response.status_code not in {200, 404}:
                raise OAuthCredentialUnavailableError()
        if response is None or response.status_code == 404:
            response = await self._request(
                "post",
                f"/v1/{path}",
                json={
                    "server": self._codex_server,
                    "grant_type": "refresh_token",
                    "refresh_token": auth["tokens"]["refresh_token"],
                    "maximum_expiry_seconds": self._codex_maximum_expiry_seconds,
                },
            )
            if response.status_code >= 400:
                raise OAuthCredentialUnavailableError(reconnect=response.status_code == 400)
            response = await self._request("get", f"/v1/{path}")
        if response.status_code >= 400:
            raise OAuthCredentialUnavailableError()
        grant = response.json().get("data", {})
        if grant.get("server") != self._codex_server:
            raise ValueError("Codex credential belongs to a different OAuth server")
        self._check_codex_account(str(grant.get("access_token") or ""), account_id)
        meta.update(
            renewal_owner=OAUTH_ENGINE,
            oauth_format=CODEX_AUTH_FORMAT,
            oauth_token_field=field,
            codex_account_id=account_id,
            codex_plan_type=codex_plan_type(auth, str(grant["access_token"])),
        )
        # Preserve unrelated configuration, but never retain the login document.
        stored = await super().store(
            owner_type,
            owner_id,
            name,
            secret_type,
            {k: v for k, v in data.items() if k != field},
            meta,
        )
        return replace(stored, keys=(*stored.keys, field, "expires_at"))

    async def migrate_codex_credential(self, *, owner_id: str, tenant_id: str, name: str) -> bool:
        """Operator-only, resumable import; run after all legacy brokers are stopped."""
        stored = await super().get("user", owner_id, name)
        if stored is None:
            raise ValueError("Codex credential does not exist")
        if stored.metadata.get("tenant_id") not in {None, "", tenant_id}:
            raise ValueError("Codex credential does not belong to the tenant")
        if stored.metadata.get("oauth_format") == CODEX_AUTH_FORMAT:
            await self.get_value("user", owner_id, name)
            return False
        if stored.metadata.get("renewal_owner"):
            raise ValueError("Credential already has a different renewal owner")
        values = await super().get_value("user", owner_id, name)
        meta = {**stored.metadata, "tenant_id": tenant_id, "enrollment_method": "codex_device"}
        await self._store_codex(
            "user", owner_id, name, stored.secret_type, values, meta, stored, resume=True
        )
        return True

    async def delete(self, owner_type: str, owner_id: str, name: str) -> None:
        stored = await self.get(owner_type, owner_id, name)
        if stored is not None and stored.metadata.get("renewal_owner") == OAUTH_ENGINE:
            path = self._oauth_path(owner_id, name, stored.metadata)
            response = await self._request("delete", f"/v1/{path}")
            if response.status_code >= 400 and response.status_code != 404:
                raise RuntimeError(f"OpenBao OAuth deletion failed (HTTP {response.status_code})")
        await super().delete(owner_type, owner_id, name)

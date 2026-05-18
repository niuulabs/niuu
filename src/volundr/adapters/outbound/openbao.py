"""OpenBao admin helpers for bootstrap and runtime access reconciliation.

This module is intentionally concrete:
- enable KV v2 mounts per app (for example ``volundr`` and ``ting``)
- enable/configure JWT auth backends
- reconcile ACL policies
- reconcile service-account-bound JWT roles

The goal is to support lightweight idempotent bootstrap jobs and future
runtime provisioning without needing a separate IaC stack.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30.0


@dataclass(frozen=True)
class OpenBaoAdminConfig:
    """Configuration for the OpenBao admin client."""

    url: str = "http://openbao.volundr-system:8200"
    token: str = ""
    namespace: str = ""
    auth_method: str = "token"
    approle_mount_path: str = "auth/approle"
    role_id: str = ""
    secret_id: str = ""


@dataclass(frozen=True)
class OpenBaoJWTAuthConfig:
    """Desired configuration for a JWT auth backend."""

    path: str = "jwt"
    oidc_discovery_url: str = ""
    bound_issuer: str = ""
    default_role: str = ""
    oidc_discovery_ca_pem: str = ""
    jwt_supported_algs: tuple[str, ...] = ("RS256",)

    def payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "oidc_discovery_url": self.oidc_discovery_url,
            "jwt_supported_algs": list(self.jwt_supported_algs),
        }
        if self.bound_issuer:
            payload["bound_issuer"] = self.bound_issuer
        if self.default_role:
            payload["default_role"] = self.default_role
        if self.oidc_discovery_ca_pem:
            payload["oidc_discovery_ca_pem"] = self.oidc_discovery_ca_pem
        return payload


@dataclass(frozen=True)
class OpenBaoJWTAuthRole:
    """Desired configuration for a JWT role."""

    name: str
    auth_path: str = "jwt"
    user_claim: str = "sub"
    role_type: str = "jwt"
    bound_audiences: tuple[str, ...] = ("openbao",)
    bound_subject: str = ""
    policies: tuple[str, ...] = ()
    ttl: str = "1h"
    bound_claims: dict[str, object] = field(default_factory=dict)

    def payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "role_type": self.role_type,
            "user_claim": self.user_claim,
            "bound_audiences": list(self.bound_audiences),
            "policies": list(self.policies),
            "ttl": self.ttl,
        }
        if self.bound_subject:
            payload["bound_subject"] = self.bound_subject
        if self.bound_claims:
            payload["bound_claims"] = self.bound_claims
        return payload


class OpenBaoApiError(Exception):
    """Raised when OpenBao returns an API error."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"OpenBao API error ({status_code}): {message}")


class OpenBaoAdminClient:
    """Idempotent admin client for OpenBao bootstrap and access provisioning."""

    def __init__(
        self,
        config: OpenBaoAdminConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None
        self._client_token: str | None = config.token or None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client

        headers: dict[str, str] = {}
        if self._config.namespace:
            headers["X-Vault-Namespace"] = self._config.namespace

        self._client = httpx.AsyncClient(
            base_url=self._config.url.rstrip("/"),
            headers=headers,
            timeout=_HTTP_TIMEOUT,
        )
        return self._client

    async def _ensure_authenticated(self) -> str:
        if self._client_token:
            return self._client_token

        if self._config.auth_method != "approle":
            raise RuntimeError("OpenBao admin client requires a token or approle credentials")
        if not self._config.role_id or not self._config.secret_id:
            raise RuntimeError("AppRole auth requires role_id and secret_id")

        client = await self._get_client()
        response = await client.post(
            f"/v1/{self._config.approle_mount_path.strip('/')}/login",
            json={
                "role_id": self._config.role_id,
                "secret_id": self._config.secret_id,
            },
        )
        if response.status_code >= 400:
            raise OpenBaoApiError(response.status_code, response.text)

        self._client_token = response.json()["auth"]["client_token"]
        return self._client_token

    async def _headers(self) -> dict[str, str]:
        token = await self._ensure_authenticated()
        headers = {"X-Vault-Token": token}
        if self._config.namespace:
            headers["X-Vault-Namespace"] = self._config.namespace
        return headers

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            response = await client.get("/v1/sys/health")
            return response.status_code < 500
        except Exception:
            logger.exception("OpenBao health check failed")
            return False

    async def ensure_kv_v2_mount(self, mount_path: str, description: str = "") -> None:
        """Ensure a KV v2 mount exists at *mount_path*."""
        mount_path = mount_path.strip("/")
        mounts = await self._list_mounts()
        if f"{mount_path}/" in mounts:
            return

        client = await self._get_client()
        response = await client.post(
            f"/v1/sys/mounts/{mount_path}",
            headers=await self._headers(),
            json={
                "type": "kv",
                "description": description,
                "options": {"version": "2"},
            },
        )
        if response.status_code >= 400:
            raise OpenBaoApiError(response.status_code, response.text)

    async def ensure_jwt_auth_backend(self, path: str = "jwt", description: str = "") -> None:
        """Ensure a JWT auth backend exists at *path*."""
        path = path.strip("/")
        auth_backends = await self._list_auth_backends()
        if f"{path}/" in auth_backends:
            return

        client = await self._get_client()
        response = await client.post(
            f"/v1/sys/auth/{path}",
            headers=await self._headers(),
            json={
                "type": "jwt",
                "description": description,
            },
        )
        if response.status_code >= 400:
            raise OpenBaoApiError(response.status_code, response.text)

    async def configure_jwt_auth(self, config: OpenBaoJWTAuthConfig) -> None:
        """Apply JWT auth configuration idempotently."""
        client = await self._get_client()
        response = await client.post(
            f"/v1/auth/{config.path.strip('/')}/config",
            headers=await self._headers(),
            json=config.payload(),
        )
        if response.status_code >= 400:
            raise OpenBaoApiError(response.status_code, response.text)

    async def ensure_policy(self, name: str, policy_hcl: str) -> None:
        """Write or replace an ACL policy."""
        client = await self._get_client()
        response = await client.put(
            f"/v1/sys/policy/{name}",
            headers=await self._headers(),
            json={"policy": policy_hcl},
        )
        if response.status_code >= 400:
            raise OpenBaoApiError(response.status_code, response.text)

    async def ensure_jwt_role(self, role: OpenBaoJWTAuthRole) -> None:
        """Write or replace a JWT role."""
        client = await self._get_client()
        response = await client.post(
            f"/v1/auth/{role.auth_path.strip('/')}/role/{role.name}",
            headers=await self._headers(),
            json=role.payload(),
        )
        if response.status_code >= 400:
            raise OpenBaoApiError(response.status_code, response.text)

    async def delete_jwt_role(self, name: str, auth_path: str = "jwt") -> None:
        """Delete a JWT role if it exists."""
        client = await self._get_client()
        response = await client.delete(
            f"/v1/auth/{auth_path.strip('/')}/role/{name}",
            headers=await self._headers(),
        )
        if response.status_code >= 400 and response.status_code != 404:
            raise OpenBaoApiError(response.status_code, response.text)

    async def ensure_service_account_access(
        self,
        *,
        mount_path: str,
        user_id: str,
        tenant_id: str = "",
        auth_path: str = "jwt",
        audience: str = "openbao",
        service_account_namespace: str,
        service_account_name: str,
        policy_name: str | None = None,
        role_name: str | None = None,
        ttl: str = "1h",
    ) -> tuple[str, str]:
        """Ensure a user-scoped policy and a service-account JWT role exist."""
        resolved_policy_name = policy_name or self.user_policy_name(mount_path, user_id)
        resolved_role_name = role_name or self.service_account_role_name(
            mount_path,
            user_id,
            service_account_namespace,
            service_account_name,
        )

        await self.ensure_policy(
            resolved_policy_name,
            self.build_user_policy(
                mount_path=mount_path,
                user_id=user_id,
                tenant_id=tenant_id,
            ),
        )
        await self.ensure_jwt_role(
            OpenBaoJWTAuthRole(
                name=resolved_role_name,
                auth_path=auth_path,
                bound_audiences=(audience,),
                bound_subject=self.service_account_subject(
                    service_account_namespace,
                    service_account_name,
                ),
                policies=(resolved_policy_name,),
                ttl=ttl,
            )
        )
        return resolved_policy_name, resolved_role_name

    async def _list_mounts(self) -> dict[str, object]:
        client = await self._get_client()
        response = await client.get("/v1/sys/mounts", headers=await self._headers())
        if response.status_code >= 400:
            raise OpenBaoApiError(response.status_code, response.text)
        return response.json().get("data", {})

    async def _list_auth_backends(self) -> dict[str, object]:
        client = await self._get_client()
        response = await client.get("/v1/sys/auth", headers=await self._headers())
        if response.status_code >= 400:
            raise OpenBaoApiError(response.status_code, response.text)
        return response.json().get("data", {})

    @staticmethod
    def build_user_policy(
        *,
        mount_path: str,
        user_id: str,
        tenant_id: str = "",
        owner_type: str = "user",
    ) -> str:
        """Render an ACL policy for one user's credentials under one mount."""
        mount = mount_path.strip("/")
        owners = f"{owner_type}s"
        lines = [
            f'path "{mount}/data/{owners}/{user_id}/*" {{',
            '  capabilities = ["create", "read", "update", "delete"]',
            "}",
            "",
            f'path "{mount}/metadata/{owners}/{user_id}" {{',
            '  capabilities = ["read", "list"]',
            "}",
            "",
            f'path "{mount}/metadata/{owners}/{user_id}/*" {{',
            '  capabilities = ["read", "list"]',
            "}",
        ]
        if tenant_id:
            lines.extend(
                [
                    "",
                    f'path "{mount}/data/tenants/{tenant_id}/shared/*" {{',
                    '  capabilities = ["read"]',
                    "}",
                    "",
                    f'path "{mount}/metadata/tenants/{tenant_id}/shared" {{',
                    '  capabilities = ["read", "list"]',
                    "}",
                    "",
                    f'path "{mount}/metadata/tenants/{tenant_id}/shared/*" {{',
                    '  capabilities = ["read", "list"]',
                    "}",
                ]
            )
        return "\n".join(lines) + "\n"

    @staticmethod
    def service_account_subject(namespace: str, service_account_name: str) -> str:
        """Return the Kubernetes service account JWT subject."""
        return f"system:serviceaccount:{namespace}:{service_account_name}"

    @staticmethod
    def user_policy_name(mount_path: str, user_id: str) -> str:
        mount = mount_path.strip("/").replace("/", "-")
        return f"{mount}-user-{user_id}"

    @staticmethod
    def service_account_role_name(
        mount_path: str,
        user_id: str,
        namespace: str,
        service_account_name: str,
    ) -> str:
        mount = mount_path.strip("/").replace("/", "-")
        ns = namespace.replace("/", "-")
        sa = service_account_name.replace("/", "-")
        return f"{mount}-{user_id}-{ns}-{sa}"

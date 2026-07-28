"""OpenBao/OpenBao-compatible KV v2 credential store adapter.

Stores credentials as KV v2 documents under structured paths:
``<mount>/data/{owner_type}s/{owner_id}/{credential_name}``.

Each document stores all secret fields plus a ``__meta__`` JSON field used
to reconstruct ``StoredCredential`` metadata without returning secret values.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

import httpx

from niuu.domain.models import SecretType, StoredCredential
from niuu.ports.credentials import CredentialStorePort

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30.0
_META_KEY = "__meta__"


class OpenBaoCredentialStore(CredentialStorePort):
    """OpenBao-compatible KV v2 implementation of ``CredentialStorePort``.

    Supported auth modes:
    - ``token``: use a pre-provisioned token
    - ``approle``: authenticate via ``auth/approle/login``
    - ``jwt``: authenticate a workload JWT via ``auth/<jwt_mount_path>/login``

    Constructor kwargs:
        url: Base OpenBao URL.
        namespace: Optional namespace sent via ``X-Vault-Namespace``.
        mount_path: KV v2 mount path (for example ``volundr`` or ``ting``).
        auth_method: ``token`` or ``approle``.
        token: Static token for ``token`` auth.
        approle_mount_path: AppRole mount path.
        role_id: AppRole role ID for ``approle`` auth.
        secret_id: AppRole secret ID for ``approle`` auth.
        jwt_mount_path: JWT auth backend path.
        jwt_role: JWT auth role.
        jwt_token_file: File containing the workload JWT.
    """

    def __init__(
        self,
        url: str = "http://openbao.volundr-system:8200",
        namespace: str = "",
        mount_path: str = "volundr",
        auth_method: str = "token",
        token: str = "",
        approle_mount_path: str = "auth/approle",
        role_id: str = "",
        secret_id: str = "",
        jwt_mount_path: str = "auth/jwt",
        jwt_role: str = "",
        jwt_token_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token",
    ) -> None:
        self._url = url.rstrip("/")
        self._namespace = namespace.strip("/")
        self._mount_path = mount_path.strip("/")
        self._auth_method = auth_method
        self._token = token
        self._approle_mount_path = approle_mount_path.strip("/")
        self._role_id = role_id
        self._secret_id = secret_id
        self._jwt_mount_path = jwt_mount_path.strip("/")
        self._jwt_role = jwt_role
        self._jwt_token_file = jwt_token_file
        self._client: httpx.AsyncClient | None = None
        self._client_token: str | None = token or None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client

        headers: dict[str, str] = {}
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace

        self._client = httpx.AsyncClient(
            base_url=self._url,
            headers=headers,
            timeout=_HTTP_TIMEOUT,
        )
        return self._client

    async def _ensure_authenticated(self) -> str:
        if self._client_token:
            return self._client_token

        if self._auth_method == "token":
            return ""

        client = await self._get_client()
        if self._auth_method == "approle":
            if not self._role_id or not self._secret_id:
                raise RuntimeError("AppRole auth requires role_id and secret_id")
            path = f"/v1/{self._approle_mount_path}/login"
            payload = {"role_id": self._role_id, "secret_id": self._secret_id}
            label = "AppRole"
        elif self._auth_method == "jwt":
            if not self._jwt_role:
                raise RuntimeError("JWT auth requires jwt_role")
            try:
                jwt = Path(self._jwt_token_file).read_text().strip()
            except OSError as exc:
                raise RuntimeError(f"JWT auth could not read token file: {exc}") from exc
            if not jwt:
                raise RuntimeError("JWT auth token file is empty")
            path = f"/v1/{self._jwt_mount_path}/login"
            payload = {"role": self._jwt_role, "jwt": jwt}
            label = "JWT"
        else:
            raise RuntimeError(
                "OpenBao credential store requires token, approle, or jwt authentication"
            )

        response = await client.post(path, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenBao {label} auth failed ({response.status_code}): {response.text}"
            )

        self._client_token = response.json()["auth"]["client_token"]
        return self._client_token

    async def _headers(self) -> dict[str, str]:
        token = await self._ensure_authenticated()
        headers: dict[str, str] = {}
        if token:
            headers["X-Vault-Token"] = token
        if self._namespace:
            headers["X-Vault-Namespace"] = self._namespace
        return headers

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue one authenticated request, re-authenticating once when a lease expires."""
        client = await self._get_client()
        request = getattr(client, method)
        response = await request(path, headers=await self._headers(), **kwargs)
        if response.status_code not in {401, 403} or self._auth_method == "token":
            return response

        self._client_token = None
        return await request(path, headers=await self._headers(), **kwargs)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _data_path(self, owner_type: str, owner_id: str, name: str) -> str:
        return str(PurePosixPath(self._mount_path, "data", f"{owner_type}s", owner_id, name))

    def _metadata_path(self, owner_type: str, owner_id: str, name: str) -> str:
        return str(PurePosixPath(self._mount_path, "metadata", f"{owner_type}s", owner_id, name))

    def _list_path(self, owner_type: str, owner_id: str) -> str:
        return str(PurePosixPath(self._mount_path, "metadata", f"{owner_type}s", owner_id))

    async def _read_raw(self, owner_type: str, owner_id: str, name: str) -> dict[str, str] | None:
        response = await self._request(
            "get",
            f"/v1/{self._data_path(owner_type, owner_id, name)}",
        )
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            logger.error("OpenBao read failed: %s %s", response.status_code, response.text)
            return None

        body = response.json()
        return body.get("data", {}).get("data")

    async def store(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
        secret_type: SecretType,
        data: dict[str, str],
        metadata: dict | None = None,
    ) -> StoredCredential:
        now = datetime.now(UTC)

        existing = await self.get(owner_type, owner_id, name)
        cred_id = existing.id if existing else str(uuid4())
        created_at = existing.created_at if existing else now

        payload = dict(data)
        payload[_META_KEY] = json.dumps(
            {
                "id": cred_id,
                "name": name,
                "secret_type": secret_type.value,
                "keys": list(data.keys()),
                "metadata": metadata or {},
                "owner_id": owner_id,
                "owner_type": owner_type,
                "created_at": created_at.isoformat(),
                "updated_at": now.isoformat(),
            }
        )

        response = await self._request(
            "post",
            f"/v1/{self._data_path(owner_type, owner_id, name)}",
            json={"data": payload},
        )
        if response.status_code >= 400:
            raise RuntimeError(f"OpenBao store error ({response.status_code}): {response.text}")

        return StoredCredential(
            id=cred_id,
            name=name,
            secret_type=secret_type,
            keys=tuple(data.keys()),
            metadata=metadata or {},
            owner_id=owner_id,
            owner_type=owner_type,
            created_at=created_at,
            updated_at=now,
        )

    async def get(self, owner_type: str, owner_id: str, name: str) -> StoredCredential | None:
        raw = await self._read_raw(owner_type, owner_id, name)
        if raw is None:
            return None

        meta_str = raw.get(_META_KEY)
        if not meta_str:
            return None

        try:
            meta = json.loads(meta_str)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

        return StoredCredential(
            id=meta["id"],
            name=meta.get("name", name),
            secret_type=SecretType(meta["secret_type"]),
            keys=tuple(meta["keys"]),
            metadata=meta.get("metadata", {}),
            owner_id=meta["owner_id"],
            owner_type=meta["owner_type"],
            created_at=datetime.fromisoformat(meta["created_at"]),
            updated_at=datetime.fromisoformat(meta["updated_at"]),
        )

    async def get_value(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> dict[str, str] | None:
        raw = await self._read_raw(owner_type, owner_id, name)
        if raw is None:
            return None
        return {k: v for k, v in raw.items() if k != _META_KEY}

    async def delete(self, owner_type: str, owner_id: str, name: str) -> None:
        response = await self._request(
            "delete",
            f"/v1/{self._data_path(owner_type, owner_id, name)}",
        )
        if response.status_code >= 400 and response.status_code != 404:
            logger.error("OpenBao delete failed: %s %s", response.status_code, response.text)

    async def list(
        self,
        owner_type: str,
        owner_id: str,
        secret_type: SecretType | None = None,
    ) -> list[StoredCredential]:
        response = await self._request(
            "get",
            f"/v1/{self._list_path(owner_type, owner_id)}",
            params={"list": "true"},
        )
        if response.status_code == 404:
            return []
        if response.status_code >= 400:
            logger.error("OpenBao list failed: %s %s", response.status_code, response.text)
            return []

        body = response.json()
        keys = body.get("data", {}).get("keys", [])

        results: list[StoredCredential] = []
        for key in keys:
            cred_name = key.rstrip("/")
            cred = await self.get(owner_type, owner_id, cred_name)
            if cred is None:
                continue
            if secret_type is not None and cred.secret_type != secret_type:
                continue
            results.append(cred)

        return sorted(results, key=lambda c: c.name)

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            response = await client.get("/v1/sys/health")
            return response.status_code < 400
        except Exception:
            logger.exception("OpenBao health check failed")
            return False

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
from pathlib import PurePosixPath
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

    Constructor kwargs:
        url: Base OpenBao URL.
        namespace: Optional namespace sent via ``X-Vault-Namespace``.
        mount_path: KV v2 mount path (for example ``volundr`` or ``ting``).
        auth_method: ``token`` or ``approle``.
        token: Static token for ``token`` auth.
        approle_mount_path: AppRole mount path.
        role_id: AppRole role ID for ``approle`` auth.
        secret_id: AppRole secret ID for ``approle`` auth.
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
    ) -> None:
        self._url = url.rstrip("/")
        self._namespace = namespace.strip("/")
        self._mount_path = mount_path.strip("/")
        self._auth_method = auth_method
        self._token = token
        self._approle_mount_path = approle_mount_path.strip("/")
        self._role_id = role_id
        self._secret_id = secret_id
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

        if self._auth_method != "approle":
            raise RuntimeError(
                "OpenBao credential store requires a token or approle credentials"
            )
        if not self._role_id or not self._secret_id:
            raise RuntimeError("AppRole auth requires role_id and secret_id")

        client = await self._get_client()
        response = await client.post(
            f"/v1/{self._approle_mount_path}/login",
            json={
                "role_id": self._role_id,
                "secret_id": self._secret_id,
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenBao AppRole auth failed ({response.status_code}): {response.text}"
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

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _data_path(self, owner_type: str, owner_id: str, name: str) -> str:
        return str(PurePosixPath(self._mount_path, "data", f"{owner_type}s", owner_id, name))

    def _metadata_path(self, owner_type: str, owner_id: str, name: str) -> str:
        return str(
            PurePosixPath(self._mount_path, "metadata", f"{owner_type}s", owner_id, name)
        )

    def _list_path(self, owner_type: str, owner_id: str) -> str:
        return str(PurePosixPath(self._mount_path, "metadata", f"{owner_type}s", owner_id))

    async def _read_raw(self, owner_type: str, owner_id: str, name: str) -> dict[str, str] | None:
        client = await self._get_client()
        response = await client.get(
            f"/v1/{self._data_path(owner_type, owner_id, name)}",
            headers=await self._headers(),
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
        client = await self._get_client()
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

        response = await client.post(
            f"/v1/{self._data_path(owner_type, owner_id, name)}",
            headers=await self._headers(),
            json={"data": payload},
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenBao store error ({response.status_code}): {response.text}"
            )

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
        client = await self._get_client()
        response = await client.delete(
            f"/v1/{self._data_path(owner_type, owner_id, name)}",
            headers=await self._headers(),
        )
        if response.status_code >= 400 and response.status_code != 404:
            logger.error("OpenBao delete failed: %s %s", response.status_code, response.text)

    async def list(
        self,
        owner_type: str,
        owner_id: str,
        secret_type: SecretType | None = None,
    ) -> list[StoredCredential]:
        client = await self._get_client()
        response = await client.get(
            f"/v1/{self._list_path(owner_type, owner_id)}",
            headers=await self._headers(),
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

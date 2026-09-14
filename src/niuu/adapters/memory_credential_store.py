"""In-memory CredentialStore adapter.

Used as a default for development and testing when no external
secret manager (Vault, Infisical) is available.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from niuu.domain.models import SecretType, StoredCredential
from niuu.ports.credentials import CredentialStorePort

logger = logging.getLogger(__name__)


class MemoryCredentialStore(CredentialStorePort):
    """In-memory implementation of CredentialStorePort.

    Stores both metadata and secret values in plain dicts.
    Not suitable for production use.
    """

    def __init__(self) -> None:
        self._metadata: dict[str, StoredCredential] = {}
        self._values: dict[str, dict[str, str]] = {}

    def _key(self, owner_type: str, owner_id: str, name: str) -> str:
        return f"{owner_type}/{owner_id}/{name}"

    async def store(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
        secret_type: SecretType,
        data: dict[str, str],
        metadata: dict | None = None,
    ) -> StoredCredential:
        key = self._key(owner_type, owner_id, name)
        now = datetime.now(UTC)

        existing = self._metadata.get(key)
        cred_id = existing.id if existing else str(uuid4())

        credential = StoredCredential(
            id=cred_id,
            name=name,
            secret_type=secret_type,
            keys=tuple(data.keys()),
            metadata=metadata or {},
            owner_id=owner_id,
            owner_type=owner_type,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

        self._metadata[key] = credential
        self._values[key] = dict(data)
        logger.debug("Stored a credential for %r/%r", owner_type, owner_id)
        return credential

    async def get(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> StoredCredential | None:
        return self._metadata.get(self._key(owner_type, owner_id, name))

    async def get_value(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> dict[str, str] | None:
        return self._values.get(self._key(owner_type, owner_id, name))

    async def delete(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> None:
        key = self._key(owner_type, owner_id, name)
        self._metadata.pop(key, None)
        self._values.pop(key, None)
        logger.debug("Deleted a credential for %r/%r", owner_type, owner_id)

    async def list(
        self,
        owner_type: str,
        owner_id: str,
        secret_type: SecretType | None = None,
    ) -> list[StoredCredential]:
        prefix = f"{owner_type}/{owner_id}/"
        results = [cred for key, cred in self._metadata.items() if key.startswith(prefix)]
        if secret_type is not None:
            results = [c for c in results if c.secret_type == secret_type]
        return sorted(results, key=lambda c: c.name)

    async def health_check(self) -> bool:
        return True

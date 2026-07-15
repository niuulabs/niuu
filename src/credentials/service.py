"""Domain service for credential management.

Composes CredentialStorePort with SecretMountStrategyRegistry
to validate, store, and retrieve credentials.
"""

from __future__ import annotations

import logging

from credentials.models import SecretType, StoredCredential
from credentials.mount_strategies import SecretMountStrategyRegistry
from credentials.ports import CredentialStorePort

logger = logging.getLogger(__name__)


class CredentialValidationError(Exception):
    """Raised when credential data fails validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Validation errors: {', '.join(errors)}")


class CredentialService:
    """Service for CRUD operations on credentials with validation."""

    def __init__(
        self,
        store: CredentialStorePort,
        strategies: SecretMountStrategyRegistry,
    ) -> None:
        self._store = store
        self._strategies = strategies

    async def create(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
        secret_type: SecretType,
        data: dict[str, str],
        metadata: dict | None = None,
    ) -> StoredCredential:
        """Create or update a credential after validation."""
        strategy = self._strategies.get(secret_type)
        errors = strategy.validate(data)
        if errors:
            raise CredentialValidationError(errors)

        return await self._store.store(
            owner_type=owner_type,
            owner_id=owner_id,
            name=name,
            secret_type=secret_type,
            data=data,
            metadata=metadata,
        )

    async def list(
        self,
        owner_type: str,
        owner_id: str,
        secret_type: SecretType | None = None,
    ) -> list[StoredCredential]:
        """List credentials (metadata only, never values)."""
        return await self._store.list(
            owner_type=owner_type,
            owner_id=owner_id,
            secret_type=secret_type,
        )

    async def get(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> StoredCredential | None:
        """Get credential metadata."""
        return await self._store.get(
            owner_type=owner_type,
            owner_id=owner_id,
            name=name,
        )

    async def delete(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> None:
        """Delete a credential."""
        await self._store.delete(
            owner_type=owner_type,
            owner_id=owner_id,
            name=name,
        )

    def get_types(self) -> list[dict]:
        """Return info about available secret types."""
        return self._strategies.list_types()

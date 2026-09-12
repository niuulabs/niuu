"""Domain service for credential management.

Composes CredentialStorePort with SecretMountStrategyRegistry
to validate, store, and retrieve credentials.
"""

from __future__ import annotations

import logging

from credentials.models import SecretType, StoredCredential
from credentials.mount_strategies import SecretMountStrategyRegistry
from credentials.ports import CredentialStorePort
from identity.models import Principal, Resource
from identity.ports import AuthorizationDeniedError, AuthorizationPort

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
        *,
        authorization: AuthorizationPort,
    ) -> None:
        self._store = store
        self._strategies = strategies
        self._authorization = authorization

    def _resource(
        self, principal: Principal, owner_type: str, owner_id: str, name: str
    ) -> Resource:
        if not principal.user_id or not principal.tenant_id or not owner_id:
            raise AuthorizationDeniedError(
                "An authenticated user, tenant and resource owner are required"
            )
        if owner_type not in ("user", "tenant"):
            raise AuthorizationDeniedError("Unknown credential owner type")
        return Resource(
            "secret",
            f"{owner_type}/{owner_id}/{name}",
            {
                "owner_type": owner_type,
                "owner_id": owner_id,
                "tenant_id": owner_id if owner_type == "tenant" else principal.tenant_id,
            },
        )

    async def _check(self, principal: Principal, action: str, resource: Resource) -> None:
        if not await self._authorization.is_allowed(principal, action, resource):
            raise AuthorizationDeniedError("Credential operation denied")

    async def create(
        self,
        principal: Principal,
        owner_type: str,
        owner_id: str,
        name: str,
        secret_type: SecretType,
        data: dict[str, str],
        metadata: dict | None = None,
    ) -> StoredCredential:
        """Create or update a credential after validation."""
        resource = self._resource(principal, owner_type, owner_id, name)
        # The store operation is an upsert. Require replacement authority too,
        # rather than racing a metadata lookup against another writer.
        await self._check(principal, "create", resource)
        await self._check(principal, "update", resource)
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
        principal: Principal,
        owner_type: str,
        owner_id: str,
        secret_type: SecretType | None = None,
    ) -> list[StoredCredential]:
        """List credentials (metadata only, never values)."""
        self._resource(principal, owner_type, owner_id, "")
        credentials = await self._store.list(
            owner_type=owner_type, owner_id=owner_id, secret_type=secret_type
        )
        resources = [
            self._resource(principal, c.owner_type, c.owner_id, c.name) for c in credentials
        ]
        allowed = await self._authorization.filter_allowed(principal, "list", resources)
        allowed_ids = {r.id for r in allowed}
        return [c for c, r in zip(credentials, resources, strict=True) if r.id in allowed_ids]

    async def get(
        self,
        principal: Principal,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> StoredCredential | None:
        """Get credential metadata."""
        await self._check(principal, "read", self._resource(principal, owner_type, owner_id, name))
        return await self._store.get(
            owner_type=owner_type,
            owner_id=owner_id,
            name=name,
        )

    async def delete(
        self,
        principal: Principal,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> None:
        """Delete a credential."""
        await self._check(
            principal, "delete", self._resource(principal, owner_type, owner_id, name)
        )
        await self._store.delete(
            owner_type=owner_type,
            owner_id=owner_id,
            name=name,
        )

    def get_types(self) -> list[dict]:
        """Return info about available secret types."""
        return self._strategies.list_types()

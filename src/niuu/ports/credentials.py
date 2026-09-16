"""Port interface for pluggable credential storage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager

from niuu.domain.models import SecretType, StoredCredential


class CredentialStorePort(ABC):
    """Port for pluggable credential storage (Vault, Infisical, memory)."""

    @abstractmethod
    async def store(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
        secret_type: SecretType,
        data: dict[str, str],
        metadata: dict | None = None,
    ) -> StoredCredential:
        """Store a credential. Overwrites if name already exists."""

    @abstractmethod
    async def get(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> StoredCredential | None:
        """Get credential metadata by name. Returns None if not found."""

    @abstractmethod
    async def get_value(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> dict[str, str] | None:
        """Get credential secret data by name. Returns None if not found."""

    @abstractmethod
    async def delete(
        self,
        owner_type: str,
        owner_id: str,
        name: str,
    ) -> None:
        """Delete a credential. No-op if not found."""

    @abstractmethod
    async def list(
        self,
        owner_type: str,
        owner_id: str,
        secret_type: SecretType | None = None,
    ) -> list[StoredCredential]:
        """List credentials for an owner, optionally filtered by type."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the credential store backend is reachable."""


class CredentialRefreshLockPort(ABC):
    """Serialize mini-mode credential rotation across processes sharing a database."""

    @abstractmethod
    def hold(self, owner_type: str, owner_id: str, name: str) -> AbstractAsyncContextManager[None]:
        """Hold a credential's lock for one refresh transaction."""


class OAuthApplicationStorePort(ABC):
    """Optional provider-client provisioning capability of a managed OAuth store."""

    @property
    def supports_resource_indicators(self) -> bool:
        """Whether the deployed refresh engine preserves RFC 8707 resource binding."""
        return False

    @property
    @abstractmethod
    def manages_oauth_applications(self) -> bool:
        """Whether this store owns provider-client provisioning."""

    @abstractmethod
    async def configure_oauth_application(
        self,
        *,
        slug: str,
        app: str,
        client_id: str,
        client_secret: str,
        authorize_url: str,
        token_url: str,
        token_endpoint_auth_method: str = "client_secret_post",
        public_endpoints_only: bool = False,
    ) -> None:
        """Provision the client used by the same enrollment flow and refresh engine."""

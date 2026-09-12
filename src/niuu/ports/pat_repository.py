"""Port for personal access token persistence operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from niuu.domain.models import PersonalAccessToken


class PATRepository(ABC):
    """Port for personal access token persistence operations."""

    @abstractmethod
    async def create(
        self,
        owner_id: str,
        name: str,
        token_hash: str,
        *,
        tenant_id: str = "",
        scopes: tuple[str, ...] | None = None,
        expires_at: datetime | None = None,
    ) -> PersonalAccessToken:
        """Persist a new PAT record."""

    @abstractmethod
    async def list(self, owner_id: str) -> list[PersonalAccessToken]:
        """List all PATs for an owner."""

    @abstractmethod
    async def get(self, pat_id: UUID, owner_id: str) -> PersonalAccessToken | None:
        """Retrieve a PAT by ID scoped to an owner."""

    @abstractmethod
    async def delete(self, pat_id: UUID, owner_id: str) -> str | None:
        """Delete a PAT. Returns the token_hash if deleted, None otherwise."""

    @abstractmethod
    async def exists_by_hash(self, token_hash: str) -> bool:
        """Check if a PAT with the given hash exists (i.e. not revoked)."""

    @abstractmethod
    async def touch_last_used(self, token_hash: str) -> None:
        """Update last_used_at for the PAT identified by token_hash."""

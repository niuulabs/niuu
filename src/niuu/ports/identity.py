"""Shared identity and authentication port contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from niuu.domain.models import Principal


class IdentityPort(ABC):
    """Port for identity and authentication operations."""

    @abstractmethod
    async def validate_token(self, raw_token: str) -> Principal:
        """Validate a token and return its authenticated principal."""

    @abstractmethod
    async def get_or_provision_user(self, principal: Principal) -> Any:
        """Return the provisioned user represented by a principal."""


class InvalidTokenError(Exception):
    """Raised when a token is invalid, expired, or malformed."""


class UserProvisioningError(Exception):
    """Raised when just-in-time user provisioning fails."""

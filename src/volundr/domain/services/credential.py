"""Compatibility exports for the shared credential service."""

from credentials.service import CredentialService, CredentialValidationError

__all__ = ["CredentialService", "CredentialValidationError"]

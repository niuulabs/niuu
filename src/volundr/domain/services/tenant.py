"""Compatibility exports for the shared identity service."""

from identity.service import (
    TenantAlreadyExistsError,
    TenantNotFoundError,
    TenantService,
)

__all__ = ["TenantAlreadyExistsError", "TenantNotFoundError", "TenantService"]

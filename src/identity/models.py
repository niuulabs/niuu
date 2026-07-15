"""Shared identity and tenancy domain contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from niuu.domain.models import Principal


@dataclass(frozen=True)
class Resource:
    """A resource used for authorization decisions."""

    kind: str
    id: str
    attr: dict[str, object]


class UserStatus(StrEnum):
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    FAILED = "failed"


class TenantTier(StrEnum):
    DEVELOPER = "developer"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class TenantRole(StrEnum):
    ADMIN = "volundr:admin"
    DEVELOPER = "volundr:developer"
    VIEWER = "volundr:viewer"


@dataclass(frozen=True)
class Tenant:
    id: str
    path: str
    name: str
    parent_id: str | None = None
    tier: TenantTier = TenantTier.DEVELOPER
    max_sessions: int = 5
    max_storage_gb: int = 50
    created_at: datetime | None = None


@dataclass(frozen=True)
class User:
    id: str
    email: str
    display_name: str = ""
    status: UserStatus = UserStatus.ACTIVE
    home_pvc: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class TenantMembership:
    user_id: str
    tenant_id: str
    role: TenantRole = TenantRole.DEVELOPER
    granted_at: datetime | None = None


@dataclass(frozen=True)
class QuotaCheck:
    allowed: bool
    tenant_id: str
    max_sessions: int
    current_sessions: int
    reason: str = ""


@dataclass(frozen=True)
class StorageQuota:
    home_gb: int = 1
    workspace_gb: int = 1


@dataclass(frozen=True)
class ProvisioningResult:
    success: bool
    user_id: str
    home_pvc: str | None = None
    errors: list[str] = field(default_factory=list)


__all__ = [
    "Principal",
    "ProvisioningResult",
    "QuotaCheck",
    "Resource",
    "StorageQuota",
    "Tenant",
    "TenantMembership",
    "TenantRole",
    "TenantTier",
    "User",
    "UserStatus",
]

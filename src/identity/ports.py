"""Shared identity and tenancy ports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from identity.models import (
    Principal,
    Resource,
    StorageQuota,
    Tenant,
    TenantMembership,
    User,
)


class TenantRepository(ABC):
    @abstractmethod
    async def create(self, tenant: Tenant) -> Tenant: ...

    @abstractmethod
    async def get(self, tenant_id: str) -> Tenant | None: ...

    @abstractmethod
    async def get_by_path(self, path: str) -> Tenant | None: ...

    @abstractmethod
    async def list(self, parent_id: str | None = None) -> list[Tenant]: ...

    @abstractmethod
    async def get_ancestors(self, path: str) -> list[Tenant]: ...

    @abstractmethod
    async def update(self, tenant: Tenant) -> Tenant: ...

    @abstractmethod
    async def delete(self, tenant_id: str) -> bool: ...


class UserRepository(ABC):
    @abstractmethod
    async def create(self, user: User) -> User: ...

    @abstractmethod
    async def get(self, user_id: str) -> User | None: ...

    @abstractmethod
    async def get_by_email(self, email: str) -> User | None: ...

    @abstractmethod
    async def list(self) -> list[User]: ...

    @abstractmethod
    async def update(self, user: User) -> User: ...

    @abstractmethod
    async def delete(self, user_id: str) -> bool: ...

    @abstractmethod
    async def add_membership(self, membership: TenantMembership) -> TenantMembership: ...

    @abstractmethod
    async def get_memberships(self, user_id: str) -> list[TenantMembership]: ...

    @abstractmethod
    async def get_members(self, tenant_id: str) -> list[TenantMembership]: ...

    @abstractmethod
    async def remove_membership(self, user_id: str, tenant_id: str) -> bool: ...


class AuthorizationPort(ABC):
    """Port for authorization decisions."""

    @abstractmethod
    async def is_allowed(
        self,
        principal: Principal,
        action: str,
        resource: Resource,
    ) -> bool:
        """Return whether a principal may perform an action on a resource."""

    @abstractmethod
    async def filter_allowed(
        self,
        principal: Principal,
        action: str,
        resources: list[Resource],
    ) -> list[Resource]:
        """Return only resources the principal may access."""


class StorageReference(Protocol):
    name: str


class StoragePort(Protocol):
    async def provision_user_storage(
        self, user_id: str, quota: StorageQuota
    ) -> StorageReference: ...

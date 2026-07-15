"""Shared credential, MCP metadata, and secret-management ports."""

from __future__ import annotations

from abc import ABC, abstractmethod

from credentials.models import MCPServerConfig, SecretInfo, SecretMountSpec, SecretType
from niuu.ports.credentials import CredentialStorePort


class MCPServerProvider(ABC):
    @abstractmethod
    def list(self) -> list[MCPServerConfig]: ...

    @abstractmethod
    def get(self, name: str) -> MCPServerConfig | None: ...


class SecretManager(ABC):
    @abstractmethod
    async def list(self) -> list[SecretInfo]: ...

    @abstractmethod
    async def get(self, name: str) -> SecretInfo | None: ...

    @abstractmethod
    async def create(self, name: str, data: dict[str, str]) -> SecretInfo: ...


class SecretAlreadyExistsError(Exception):
    pass


class SecretValidationError(Exception):
    pass


class SecretMountStrategy(ABC):
    @abstractmethod
    def secret_type(self) -> SecretType: ...

    @abstractmethod
    def default_mount_spec(self, secret_path: str, secret_data: dict) -> SecretMountSpec: ...

    @abstractmethod
    def validate(self, secret_data: dict) -> list[str]: ...


__all__ = [
    "CredentialStorePort",
    "MCPServerProvider",
    "SecretAlreadyExistsError",
    "SecretManager",
    "SecretMountStrategy",
    "SecretValidationError",
]

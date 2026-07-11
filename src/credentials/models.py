"""Shared credential and secret domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from niuu.domain.models import SecretType, StoredCredential


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    type: str = "stdio"
    command: str | None = None
    url: str | None = None
    args: list[str] = ()  # type: ignore[assignment]
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.args, tuple):
            object.__setattr__(self, "args", tuple(self.args))


class MountType(StrEnum):
    ENV_FILE = "env_file"
    FILE = "file"
    TEMPLATE = "template"


@dataclass(frozen=True)
class SecretMountSpec:
    secret_path: str
    mount_type: MountType
    destination: str
    template: str | None = None
    renewal: bool = False


@dataclass(frozen=True)
class SecretInfo:
    name: str
    keys: list[str] = ()  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not isinstance(self.keys, tuple):
            object.__setattr__(self, "keys", tuple(self.keys))


__all__ = [
    "MCPServerConfig",
    "MountType",
    "SecretInfo",
    "SecretMountSpec",
    "SecretType",
    "StoredCredential",
]

"""Shared credential and secret domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from niuu.domain.models import SecretType, StoredCredential


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    type: str = "stdio"
    command: str | None = None
    url: str | None = None
    args: list[str] = ()  # type: ignore[assignment]
    description: str = ""
    #: A pinned program Skuld installs and runs (``niuu.domain.session_program``).
    program: dict[str, Any] | None = None
    env: dict[str, str] | None = None
    #: The session owner's stored credential whose ``credential_key`` is sent to ``url``.
    credential: str = ""
    credential_key: str = "token"
    default_tools_approval_mode: str | None = None
    startup_timeout_sec: float | None = None
    tool_timeout_sec: float | None = None
    #: Tags the machine of a session with this server must carry.
    host_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.args, tuple):
            object.__setattr__(self, "args", tuple(self.args))
        if not isinstance(self.host_tags, tuple):
            object.__setattr__(self, "host_tags", tuple(self.host_tags))


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

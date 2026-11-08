"""Strict parsing of the guest agent's JSON config.

Every field of the contract is required and every unknown field is rejected,
each with a message that names the field, because the file is written by Forge
and a drifting schema must stop the agent rather than be guessed around.
"""

from __future__ import annotations

import ipaddress
import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from volundr.guest_agent.errors import ConfigError

_TOP_LEVEL = (
    "metadata_url",
    "allocation_id",
    "slot",
    "auth",
    "token_volumes",
    "forge_session",
    "listen",
    "refresh_fraction",
    "retry_seconds",
    "request_timeout_seconds",
    "ca_file",
)
_MAX_PORT = 65535


@dataclass(frozen=True)
class HostKeyAuth:
    """Sign requests with the guest's pinned Ed25519 SSH host key."""

    key_path: str


@dataclass(frozen=True)
class BearerFileAuth:
    """Present the token the machine provider keeps in ``token_path`` (read per request)."""

    token_path: str


@dataclass(frozen=True)
class SpiffeAuth:
    """Present a JWT-SVID fetched from the local SPIRE agent."""

    audience: str
    socket_path: str
    spire_agent_binary: str


@dataclass(frozen=True)
class TokenFile:
    path: str


@dataclass(frozen=True)
class TokenVolume:
    volume: str
    directory: str
    tokens: tuple[TokenFile, ...]


@dataclass(frozen=True)
class ForgeSessionTarget:
    directory: str
    file: str


@dataclass(frozen=True)
class Listen:
    address: str
    port: int


@dataclass(frozen=True)
class AgentConfig:
    metadata_url: str
    allocation_id: str
    #: The VM slot whose session this agent serves; Forge answers for that slot only.
    slot: int
    auth: HostKeyAuth | BearerFileAuth | SpiffeAuth
    token_volumes: tuple[TokenVolume, ...]
    forge_session: ForgeSessionTarget | None
    listen: Listen
    refresh_fraction: float
    retry_seconds: float
    request_timeout_seconds: float
    #: PEM bundle that verifies Forge's certificate; empty means the system roots.
    ca_file: str


def load_config(path: str | os.PathLike[str]) -> AgentConfig:
    """Read and validate the config file at ``path``."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read the config file {path}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"the config file {path} is not valid JSON: {exc}") from exc
    return parse_config(raw)


def parse_config(raw: object) -> AgentConfig:
    """Validate a decoded config document, raising ``ConfigError`` naming the field."""
    top = _fields(raw, "", _TOP_LEVEL)
    metadata_url = _metadata_url(top["metadata_url"])
    allocation_id = _allocation_id(top["allocation_id"])
    auth = _auth(top["auth"])
    token_volumes = _token_volumes(top["token_volumes"])
    forge_session = _forge_session(top["forge_session"])
    if not token_volumes and forge_session is None:
        raise ConfigError(
            "token_volumes and forge_session: nothing to deliver; the agent is only "
            "started for a session that needs credentials"
        )
    return AgentConfig(
        metadata_url=metadata_url,
        allocation_id=allocation_id,
        slot=_slot(top["slot"]),
        auth=auth,
        token_volumes=token_volumes,
        forge_session=forge_session,
        listen=_listen(top["listen"]),
        refresh_fraction=_refresh_fraction(top["refresh_fraction"]),
        retry_seconds=_positive_number(top["retry_seconds"], "retry_seconds"),
        request_timeout_seconds=_positive_number(
            top["request_timeout_seconds"], "request_timeout_seconds"
        ),
        ca_file=_string(top["ca_file"], "ca_file", allow_empty=True),
    )


def _qualify(parent: str, name: str) -> str:
    return f"{parent}.{name}" if parent else name


def _fields(raw: object, parent: str, names: tuple[str, ...]) -> Mapping[str, object]:
    label = parent or "the config"
    if not isinstance(raw, dict):
        raise ConfigError(f"{label}: expected an object, got {type(raw).__name__}")
    for name in raw:
        if name not in names:
            raise ConfigError(f"{_qualify(parent, str(name))}: unknown field")
    for name in names:
        if name not in raw:
            raise ConfigError(f"{_qualify(parent, name)}: missing field")
    return raw


def _string(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{field}: expected a string, got {type(value).__name__}")
    if not value and not allow_empty:
        raise ConfigError(f"{field}: must not be empty")
    return value


def _absolute_path(value: object, field: str) -> str:
    text = _string(value, field)
    if not os.path.isabs(text):
        raise ConfigError(f"{field}: must be an absolute path, got {text!r}")
    return text


def _relative_path(value: object, field: str) -> str:
    text = _string(value, field)
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts or pure.name == "":
        raise ConfigError(f"{field}: must be a relative path inside its directory, got {text!r}")
    return text


def _positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{field}: expected a number, got {type(value).__name__}")
    if value <= 0:
        raise ConfigError(f"{field}: must be greater than 0, got {value}")
    return float(value)


def _refresh_fraction(value: object) -> float:
    fraction = _positive_number(value, "refresh_fraction")
    if fraction >= 1:
        raise ConfigError(
            f"refresh_fraction: must be between 0 and 1 (exclusive), got {value}; a token "
            "must be renewed before it expires"
        )
    return fraction


def _metadata_url(value: object) -> str:
    url = _string(value, "metadata_url")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise ConfigError(
            f"metadata_url: must be an https URL (credentials are never sent unencrypted), "
            f"got {url!r}"
        )
    return url


def _allocation_id(value: object) -> str:
    text = _string(value, "allocation_id")
    try:
        return str(uuid.UUID(text))
    except ValueError as exc:
        raise ConfigError(f"allocation_id: not a UUID: {text!r}") from exc


def _slot(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"slot: expected an integer, got {type(value).__name__}")
    if value < 0:
        raise ConfigError(f"slot: must be 0 or greater, got {value}")
    return value


def _auth(raw: object) -> HostKeyAuth | BearerFileAuth | SpiffeAuth:
    if not isinstance(raw, dict):
        raise ConfigError(f"auth: expected an object, got {type(raw).__name__}")
    kind = raw.get("kind")
    if kind is None:
        raise ConfigError("auth.kind: missing field")
    if kind == "host_key":
        fields = _fields(raw, "auth", ("kind", "key_path"))
        return HostKeyAuth(key_path=_absolute_path(fields["key_path"], "auth.key_path"))
    if kind == "bearer_file":
        fields = _fields(raw, "auth", ("kind", "token_path"))
        return BearerFileAuth(token_path=_absolute_path(fields["token_path"], "auth.token_path"))
    if kind == "spiffe":
        fields = _fields(raw, "auth", ("kind", "audience", "socket_path", "spire_agent_binary"))
        return SpiffeAuth(
            audience=_string(fields["audience"], "auth.audience"),
            socket_path=_absolute_path(fields["socket_path"], "auth.socket_path"),
            spire_agent_binary=_absolute_path(
                fields["spire_agent_binary"], "auth.spire_agent_binary"
            ),
        )
    raise ConfigError(
        f"auth.kind: unknown kind {kind!r}; expected 'host_key', 'bearer_file' or 'spiffe'"
    )


def _token_volumes(raw: object) -> tuple[TokenVolume, ...]:
    if not isinstance(raw, list):
        raise ConfigError(f"token_volumes: expected a list, got {type(raw).__name__}")
    volumes: list[TokenVolume] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw):
        where = f"token_volumes[{index}]"
        fields = _fields(item, where, ("volume", "directory", "tokens"))
        volume = _string(fields["volume"], f"{where}.volume")
        raw_tokens = fields["tokens"]
        if not isinstance(raw_tokens, list):
            raise ConfigError(f"{where}.tokens: expected a list, got {type(raw_tokens).__name__}")
        tokens: list[TokenFile] = []
        for token_index, token in enumerate(raw_tokens):
            token_where = f"{where}.tokens[{token_index}]"
            token_fields = _fields(token, token_where, ("path",))
            path = _relative_path(token_fields["path"], f"{token_where}.path")
            if (volume, path) in seen:
                raise ConfigError(f"{token_where}.path: duplicate token {volume}/{path}")
            seen.add((volume, path))
            tokens.append(TokenFile(path=path))
        volumes.append(
            TokenVolume(
                volume=volume,
                directory=_absolute_path(fields["directory"], f"{where}.directory"),
                tokens=tuple(tokens),
            )
        )
    return tuple(volumes)


def _forge_session(raw: object) -> ForgeSessionTarget | None:
    if raw is None:
        return None
    fields = _fields(raw, "forge_session", ("directory", "file"))
    file = _relative_path(fields["file"], "forge_session.file")
    return ForgeSessionTarget(
        directory=_absolute_path(fields["directory"], "forge_session.directory"), file=file
    )


def _listen(raw: object) -> Listen:
    fields = _fields(raw, "listen", ("address", "port"))
    address = _string(fields["address"], "listen.address")
    try:
        ipaddress.ip_address(address)
    except ValueError as exc:
        raise ConfigError(f"listen.address: not an IP address: {address!r}") from exc
    port = fields["port"]
    if isinstance(port, bool) or not isinstance(port, int):
        raise ConfigError(f"listen.port: expected an integer, got {type(port).__name__}")
    if not 0 <= port <= _MAX_PORT:
        raise ConfigError(f"listen.port: must be between 0 and {_MAX_PORT}, got {port}")
    return Listen(address=address, port=port)

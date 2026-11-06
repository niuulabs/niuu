"""Shared fixtures for the guest agent tests."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

ALLOCATION_ID = "8d3f2a52-0c47-4a49-9b52-3f7a1f1c2d10"
METADATA_URL = "https://forge.test:8443/"
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def host_key(tmp_path: Path) -> tuple[str, Ed25519PublicKey]:
    """An OpenSSH-format Ed25519 key on disk, and its public half."""
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "ssh_host_ed25519_key"
    path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()))
    return str(path), key.public_key()


@pytest.fixture
def config_dict(tmp_path: Path, host_key: tuple[str, Ed25519PublicKey]) -> dict:
    """A valid config document: one token volume, a Forge session, host-key auth."""
    return {
        "metadata_url": METADATA_URL,
        "allocation_id": ALLOCATION_ID,
        "auth": {"kind": "host_key", "key_path": host_key[0]},
        "token_volumes": [
            {
                "volume": "identity",
                "directory": str(tmp_path / "tokens" / "identity"),
                "tokens": [{"path": "token"}],
            }
        ],
        "forge_session": {"directory": str(tmp_path / "forge-session"), "file": "token"},
        "listen": {"address": "127.0.0.1", "port": 0},
        "refresh_fraction": 0.8,
        "retry_seconds": 10,
        "request_timeout_seconds": 30,
        "ca_file": "",
    }


@pytest.fixture
def make_config(config_dict: dict) -> Callable[..., dict]:
    """A copy of the valid config with dotted-path overrides, e.g. ``{"listen.port": 80}``."""

    def build(**overrides: object) -> dict:
        document = copy.deepcopy(config_dict)
        for dotted, value in overrides.items():
            node = document
            *parents, leaf = dotted.split("__")
            for parent in parents:
                node = node[parent]
            node[leaf] = value
        return document

    return build


@pytest.fixture
def config_file(tmp_path: Path, config_dict: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config_dict))
    return path


def credentials_body(
    *,
    token: str = "sa-token-1",
    lifetime: timedelta = timedelta(seconds=100),
    now: datetime = NOW,
    forge_token: str | None = "forge-token-1",
) -> dict:
    """What Forge answers for ``config_dict``'s plan."""
    return {
        "service_account_tokens": [
            {
                "volume": "identity",
                "path": "token",
                "token": token,
                "expires_at": (now + lifetime).isoformat().replace("+00:00", "Z"),
            }
        ],
        "forge_session": (
            None if forge_token is None else {"token": forge_token, "grants": ["message"]}
        ),
    }

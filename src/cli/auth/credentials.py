"""Encrypted credential storage in ~/.niuu/credentials."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CREDENTIALS_PATH = Path.home() / ".niuu" / "credentials"
MACHINE_KEY_ENV = "NIUU_CREDENTIAL_KEY"


@dataclass
class StoredTokens:
    """Token set persisted to disk."""

    access_token: str
    refresh_token: str = ""
    id_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0.0
    issuer: str = ""


def _derive_key() -> bytes:
    """Derive a Fernet key from a stable machine-specific secret.

    Priority:
    1. ``NIUU_CREDENTIAL_KEY`` env-var (explicit override, CI-friendly).
    2. Fallback: SHA-256 of hostname + uid, base64-encoded to 32 bytes.
    """
    explicit = os.environ.get(MACHINE_KEY_ENV)
    if explicit:
        raw = hashlib.sha256(explicit.encode()).digest()
        return base64.urlsafe_b64encode(raw)

    seed = f"{os.uname().nodename}-{os.getuid()}".encode()
    raw = hashlib.sha256(seed).digest()
    return base64.urlsafe_b64encode(raw)


class CredentialStore:
    """Read/write encrypted tokens to disk using Fernet symmetric encryption."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CREDENTIALS_PATH

    def _fernet(self):  # noqa: ANN202
        from cryptography.fernet import Fernet

        return Fernet(_derive_key())

    def store(self, tokens: StoredTokens) -> None:
        """Encrypt and persist tokens."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "id_token": tokens.id_token,
                "token_type": tokens.token_type,
                "expires_at": tokens.expires_at,
                "issuer": tokens.issuer,
            }
        ).encode()
        encrypted = self._fernet().encrypt(payload)
        self._path.write_bytes(encrypted)
        self._path.chmod(0o600)
        logger.debug("credentials stored at %s", self._path)

    def load(self) -> StoredTokens | None:
        """Load and decrypt stored tokens, or None if absent/corrupt."""
        if not self._path.exists():
            return None
        try:
            encrypted = self._path.read_bytes()
            payload = self._fernet().decrypt(encrypted)
            data = json.loads(payload)
            return StoredTokens(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", ""),
                id_token=data.get("id_token", ""),
                token_type=data.get("token_type", "Bearer"),
                expires_at=data.get("expires_at", 0.0),
                issuer=data.get("issuer", ""),
            )
        except Exception:
            logger.debug("failed to load credentials", exc_info=True)
            return None

    def clear(self) -> None:
        """Remove stored credentials."""
        if self._path.exists():
            if self._path.is_dir():
                shutil.rmtree(self._path)
            else:
                self._path.unlink()
            logger.debug("credentials cleared at %s", self._path)

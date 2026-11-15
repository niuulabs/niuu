"""The ``niuu login`` token set, encrypted at ``<niuu home>/login``.

The key is generated once and kept beside it (``login.key``, 0600), so the login stays
readable whatever the machine's hostname becomes. ``NIUU_CREDENTIAL_KEY`` replaces the
key file (CI). The login lives in its own file because ``~/.niuu/credentials`` is the
local platform's credential directory; a login an earlier CLI wrote there (as a file,
encrypted with a key derived from the hostname) is moved on first read.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MACHINE_KEY_ENV = "NIUU_CREDENTIAL_KEY"
#: Selects the CLI's directory, as ``niuu --home`` does.
HOME_ENV = "NIUU_HOME"
LOGIN_FILE = "login"
KEY_FILE = "login.key"
#: Where CLIs before the login file stored it, as a file; now the platform's directory.
LEGACY_FILE = "credentials"


def niuu_home() -> Path:
    configured = os.environ.get(HOME_ENV)
    return Path(configured).expanduser() if configured else Path.home() / ".niuu"


class CredentialReadError(RuntimeError):
    """A stored login exists but cannot be read."""


@dataclass
class StoredTokens:
    """Token set persisted to disk."""

    access_token: str
    refresh_token: str = ""
    id_token: str = ""
    token_type: str = "Bearer"
    expires_at: float = 0.0
    issuer: str = ""
    #: The OIDC client the tokens were issued to, so a refresh asks the same client.
    client_id: str = ""


def _explicit_key() -> bytes | None:
    explicit = os.environ.get(MACHINE_KEY_ENV)
    if not explicit:
        return None
    return base64.urlsafe_b64encode(hashlib.sha256(explicit.encode()).digest())


def _legacy_key() -> bytes:
    """The key earlier CLIs derived from the hostname and uid, to move their login."""
    seed = f"{os.uname().nodename}-{os.getuid()}".encode()
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest())


class CredentialStore:
    """Read/write the encrypted login. ``path`` defaults to ``<niuu home>/login``."""

    def __init__(self, path: Path | None = None) -> None:
        home = niuu_home()
        self._path = path or home / LOGIN_FILE
        self._key_path = self._path.with_name(self._path.name + ".key")
        # Only the default store looks for a login an earlier CLI left behind.
        self._legacy_path = home / LEGACY_FILE if path is None else None

    def _key(self, *, create: bool) -> bytes | None:
        explicit = _explicit_key()
        if explicit is not None:
            return explicit
        if self._key_path.is_file():
            return self._key_path.read_bytes().strip()
        if not create:
            return None
        from cryptography.fernet import Fernet

        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        descriptor = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as out:
            out.write(key)
        return key

    def store(self, tokens: StoredTokens) -> None:
        """Encrypt and persist tokens."""
        from cryptography.fernet import Fernet

        self._path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = Fernet(self._key(create=True)).encrypt(json.dumps(asdict(tokens)).encode())
        self._path.write_bytes(encrypted)
        self._path.chmod(0o600)
        logger.debug("credentials stored at %s", self._path)

    def load(self) -> StoredTokens | None:
        """The stored login; ``None`` when there is none, and an error when it is unreadable."""
        if not self._path.exists():
            return self._move_legacy()
        key = self._key(create=False)
        if key is None:
            raise CredentialReadError(
                f"The login at {self._path} has no key ({self._key_path} is missing); "
                "run `niuu login` again"
            )
        return self._decrypt(self._path.read_bytes(), key, self._path)

    def _decrypt(self, encrypted: bytes, key: bytes, where: Path) -> StoredTokens:
        from cryptography.fernet import Fernet, InvalidToken

        try:
            data = json.loads(Fernet(key).decrypt(encrypted))
        except (InvalidToken, ValueError) as exc:
            raise CredentialReadError(
                f"The login at {where} cannot be read with its key; run `niuu login` again"
            ) from exc
        return StoredTokens(**{k: v for k, v in data.items() if k in StoredTokens.__annotations__})

    def _move_legacy(self) -> StoredTokens | None:
        legacy = self._legacy_path
        if legacy is None or not legacy.is_file():
            return None
        key = _explicit_key() or _legacy_key()
        try:
            tokens = self._decrypt(legacy.read_bytes(), key, legacy)
        except CredentialReadError as exc:
            raise CredentialReadError(
                f"The login an earlier niuu stored at {legacy} was encrypted for another "
                f"hostname and cannot be read; delete it and run `niuu login` again"
            ) from exc
        self.store(tokens)
        legacy.unlink()
        logger.info("moved the stored login from %s to %s", legacy, self._path)
        return tokens

    def clear(self) -> None:
        """Remove the stored login (and one an earlier CLI left behind)."""
        self._path.unlink(missing_ok=True)
        if self._legacy_path is not None and self._legacy_path.is_file():
            self._legacy_path.unlink()
        logger.debug("credentials cleared at %s", self._path)

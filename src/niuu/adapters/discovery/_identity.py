"""Peer identity persistence helpers (NIU-538).

``peer_id`` is a stable UUID generated on first run, persisted to
``~/.ravn/peer_id``.  ``realm.key`` is a 32-byte random secret
used for HMAC handshakes — only the SHA-256 hash is ever transmitted.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path


def _ravn_dir() -> Path:
    return Path.home() / ".ravn"


def load_or_create_peer_id() -> str:
    """Return the stable peer UUID, creating it on first run."""
    ravn_dir = _ravn_dir()
    ravn_dir.mkdir(parents=True, exist_ok=True)
    path = ravn_dir / "peer_id"
    if path.exists():
        return path.read_text().strip()
    peer_id = str(uuid.uuid4())
    path.write_text(peer_id)
    path.chmod(0o600)
    return peer_id


def load_or_create_realm_key() -> bytes:
    """Return the realm key bytes, creating a 32-byte random key on first run."""
    ravn_dir = _ravn_dir()
    ravn_dir.mkdir(parents=True, exist_ok=True)
    path = ravn_dir / "realm.key"
    if path.exists():
        return path.read_bytes()
    key = os.urandom(32)
    path.write_bytes(key)
    path.chmod(0o600)
    return key


def realm_id_from_key(realm_key: bytes) -> str:
    """Derive a stable realm_id string from the raw key bytes (hex of the key)."""
    return realm_key.hex()


def realm_id_hash(realm_key: bytes) -> str:
    """Return SHA-256(realm_key)[:16] — the value transmitted in announcements.

    This lets peers confirm they share a realm without revealing the secret.
    """
    return hashlib.sha256(realm_key).hexdigest()[:16]

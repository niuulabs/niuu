"""Tests for cli.auth.node_key — the persistent Ed25519 node identity."""

from __future__ import annotations

import base64
import stat
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from cli.auth.node_key import NodeIdentity


def test_generates_and_persists_a_new_key_with_0600_permissions(tmp_path: Path) -> None:
    key_path = tmp_path / "node_key"

    identity = NodeIdentity.load_or_create(key_path)

    assert key_path.exists()
    mode = stat.S_IMODE(key_path.stat().st_mode)
    assert mode == 0o600
    # The public key round-trips through the cryptography library.
    raw = base64.b64decode(identity.public_key_b64)
    assert len(raw) == 32
    Ed25519PublicKey.from_public_bytes(raw)


def test_load_or_create_reuses_an_existing_key(tmp_path: Path) -> None:
    key_path = tmp_path / "node_key"

    first = NodeIdentity.load_or_create(key_path)
    second = NodeIdentity.load_or_create(key_path)

    assert first.public_key_b64 == second.public_key_b64


def test_sign_produces_a_signature_the_matching_public_key_verifies(tmp_path: Path) -> None:
    identity = NodeIdentity.load_or_create(tmp_path / "node_key")
    message = b"POST\n/api/v1/niuu/guild/nodes/node-1/heartbeat\n1700000000\nabc"

    signature = identity.sign(message)

    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(identity.public_key_b64))
    public_key.verify(base64.b64decode(signature), message)

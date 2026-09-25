"""This host's persistent Ed25519 node identity for `niuu join`.

Generated on first join and stored under the CLI's data dir with 0600
permissions (the "Node keys" owner decision — see
``docs/operator/joining-machines.md``). Guild only ever sees the public key;
the private key never leaves this host.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

#: Default node key filename under the CLI's config directory (~/.niuu/).
DEFAULT_NODE_KEY_FILENAME = "node_key"


class NodeIdentity:
    """A loaded or freshly generated Ed25519 keypair for signing node requests."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key

    @property
    def public_key_b64(self) -> str:
        """Base64-encoded raw 32-byte public key, as presented at join time."""
        raw = self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")

    def sign(self, message: bytes) -> str:
        """Sign *message* (see ``niuu.adapters.node_signature.signing_message``)."""
        return base64.b64encode(self._private_key.sign(message)).decode("ascii")

    @classmethod
    def load_or_create(cls, key_path: Path) -> NodeIdentity:
        """Load the node key at *key_path*, generating and persisting one if absent."""
        if key_path.exists():
            raw = base64.b64decode(key_path.read_text(encoding="utf-8").strip())
            return cls(Ed25519PrivateKey.from_private_bytes(raw))

        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(base64.b64encode(raw).decode("ascii"), encoding="utf-8")
        os.chmod(key_path, 0o600)
        return cls(private_key)

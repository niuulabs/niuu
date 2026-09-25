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


class NodeKeyMissingError(FileNotFoundError):
    """Raised by ``NodeIdentity.load`` when no node key file exists.

    `niuu leave` must fail with this rather than silently generating a new
    keypair — a freshly generated key's public half would not match what
    Guild has on file, so every subsequent signed request would fail
    signature verification.
    """


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
    def load(cls, key_path: Path) -> NodeIdentity:
        """Load the node key at *key_path*. Raises :class:`NodeKeyMissingError` if absent.

        Used by `niuu leave`: this host must sign with the exact key Guild
        already trusts, never a newly generated one.
        """
        if not key_path.exists():
            raise NodeKeyMissingError(
                f"No node key at {key_path}. This host has no `niuu join` identity to leave with."
            )
        raw = base64.b64decode(key_path.read_text(encoding="utf-8").strip())
        return cls(Ed25519PrivateKey.from_private_bytes(raw))

    @classmethod
    def load_or_create(cls, key_path: Path) -> NodeIdentity:
        """Load the node key at *key_path*, generating and persisting one if absent.

        Creation is atomic (``O_CREAT | O_EXCL``, mode ``0600``): if another
        process wins the race to create the file first, this falls back to
        loading whatever it wrote, never overwriting an existing key.
        """
        if key_path.exists():
            return cls.load(key_path)

        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        encoded = base64.b64encode(raw).decode("ascii")
        key_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(key_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return cls.load(key_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
        except BaseException:
            key_path.unlink(missing_ok=True)
            raise
        return cls(private_key)

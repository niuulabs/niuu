"""A signing key kept in a private file, generated once on first use.

Local (mini-mode) Forge has no configured workload-identity key, and a key
generated per process would invalidate every issued token on each API restart,
while the session brokers that hold them keep running. This keeps one RSA key in
a 0600 file under the Forge state directory instead.

The file is created atomically (write a temporary file, then hard-link it into
place, which fails if another process won the race), is never overwritten, and
is refused when its permissions let anyone but the owner read it.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)

RSA_PUBLIC_EXPONENT = 65537
_PRIVATE_FILE_MODE = 0o600
_PRIVATE_DIR_MODE = 0o700
_GROUP_OR_OTHER_BITS = 0o077


class SigningKeyFileError(RuntimeError):
    """The key file cannot be created, read or trusted. The message says how to fix it."""


def load_or_create_rsa_key_pem(path: str | Path, *, key_size: int) -> str:
    """Return the PEM private key at ``path``, creating it (0600) when absent."""
    key_path = Path(path).expanduser()
    if not key_path.exists():
        _create(key_path, key_size=key_size)
    return _read(key_path)


def _create(key_path: Path, *, key_size: int) -> None:
    try:
        key_path.parent.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    except OSError as exc:
        raise SigningKeyFileError(
            f"cannot create the directory for the signing key {key_path}: {exc}; "
            "create it or point the signing key setting at a writable location"
        ) from exc
    key = rsa.generate_private_key(public_exponent=RSA_PUBLIC_EXPONENT, key_size=key_size)
    pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".signing-key-", dir=key_path.parent)
    except OSError as exc:
        raise SigningKeyFileError(f"cannot write the signing key {key_path}: {exc}") from exc
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, _PRIVATE_FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(pem)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, key_path)
        except FileExistsError:
            pass  # another process created it first; use theirs
    except OSError as exc:
        raise SigningKeyFileError(f"cannot write the signing key {key_path}: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)


def _read(key_path: Path) -> str:
    try:
        info = key_path.stat()
    except OSError as exc:
        raise SigningKeyFileError(f"cannot read the signing key {key_path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise SigningKeyFileError(f"the signing key {key_path} is not a regular file")
    if info.st_uid != os.getuid():
        raise SigningKeyFileError(
            f"the signing key {key_path} is owned by another user; it must belong to the "
            "user running Forge"
        )
    if info.st_mode & _GROUP_OR_OTHER_BITS:
        raise SigningKeyFileError(
            f"the signing key {key_path} is readable by other users; run "
            f"`chmod 600 {key_path}` (or delete it to have a new key generated)"
        )
    try:
        pem = key_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SigningKeyFileError(f"cannot read the signing key {key_path}: {exc}") from exc
    try:
        key = load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError) as exc:
        raise SigningKeyFileError(
            f"the signing key {key_path} is not a valid PEM private key; delete it to have a "
            "new key generated (tokens it signed stop working)"
        ) from exc
    if not isinstance(key, RSAPrivateKey):
        raise SigningKeyFileError(f"the signing key {key_path} is not an RSA private key")
    return pem

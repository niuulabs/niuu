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

import base64
import fcntl
import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_ssh_private_key,
)

RSA_PUBLIC_EXPONENT = 65537
_PRIVATE_FILE_MODE = 0o600
_PRIVATE_DIR_MODE = 0o700
_PUBLIC_FILE_MODE = 0o644
_GROUP_OR_OTHER_BITS = 0o077
_LOCK_FILE_NAME = ".lock"
_DEFAULT_REMEDY = "delete it to have a new one generated"


class SigningKeyFileError(RuntimeError):
    """The key file cannot be created, read or trusted. The message says how to fix it."""


def load_or_create_rsa_key_pem(path: str | Path, *, key_size: int) -> str:
    """Return the PEM private key at ``path``, creating it (0600) when absent."""
    key_path = Path(path).expanduser()
    if not key_path.exists():
        _create(key_path, key_size=key_size)
    return _read(key_path)


def _create(key_path: Path, *, key_size: int) -> None:
    key = rsa.generate_private_key(public_exponent=RSA_PUBLIC_EXPONENT, key_size=key_size)
    write_private_file_once(
        key_path,
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
        what="signing key",
    )


def write_private_file_once(path: Path, data: bytes, *, what: str) -> None:
    """Create ``path`` (0600, directory 0700) atomically; an existing file is kept as is."""
    try:
        path.parent.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    except OSError as exc:
        raise SigningKeyFileError(
            f"cannot create the directory for the {what} {path}: {exc}; "
            f"create it or point the {what} setting at a writable location"
        ) from exc
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".niuu-private-", dir=path.parent)
    except OSError as exc:
        raise SigningKeyFileError(f"cannot write the {what} {path}: {exc}") from exc
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, _PRIVATE_FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            pass  # another process created it first; use theirs
    except OSError as exc:
        raise SigningKeyFileError(f"cannot write the {what} {path}: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)


def read_private_file(path: Path, *, what: str, remedy: str = _DEFAULT_REMEDY) -> bytes:
    """Read a file only its owner (the user running this process) can read.

    ``remedy`` finishes the "run chmod 600" advice when the file is readable by others.
    """
    try:
        info = path.stat()
    except OSError as exc:
        raise SigningKeyFileError(f"cannot read the {what} {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise SigningKeyFileError(f"the {what} {path} is not a regular file")
    if info.st_uid != os.getuid():
        raise SigningKeyFileError(
            f"the {what} {path} is owned by another user; it must belong to the user running Forge"
        )
    if info.st_mode & _GROUP_OR_OTHER_BITS:
        raise SigningKeyFileError(
            f"the {what} {path} is readable by other users; run `chmod 600 {path}` (or {remedy})"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SigningKeyFileError(f"cannot read the {what} {path}: {exc}") from exc


def _read(key_path: Path) -> str:
    data = read_private_file(key_path, what="signing key")
    try:
        pem = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SigningKeyFileError(f"cannot read the signing key {key_path}: {exc}") from exc
    try:
        key = load_pem_private_key(data, password=None)
    except (ValueError, TypeError) as exc:
        raise SigningKeyFileError(
            f"the signing key {key_path} is not a valid PEM private key; delete it to have a "
            "new key generated (tokens it signed stop working)"
        ) from exc
    if not isinstance(key, RSAPrivateKey):
        raise SigningKeyFileError(f"the signing key {key_path} is not an RSA private key")
    return pem


class SshKeyFileError(RuntimeError):
    """The SSH key pair cannot be created, read or trusted. The message says how to fix it."""


@dataclass(frozen=True)
class SshKeyPairFiles:
    """An Ed25519 key pair kept in two files, and what to tell the operator about it."""

    private_path: Path
    public_path: Path
    #: The public key's line as stored, ``ssh-ed25519 AAAA… <comment>``.
    public_key: str
    #: ``SHA256:…``, as ``ssh-keygen -l`` prints it.
    fingerprint: str
    #: True when this call generated the pair, False when it found one.
    created: bool


def load_or_create_ssh_keypair(
    directory: str | Path, *, name: str, comment: str
) -> SshKeyPairFiles:
    """Return the Ed25519 pair ``<directory>/<name>`` and ``<name>.pub``, making it once.

    The pair is generated (OpenSSH format, no passphrase) when neither file exists and is
    never overwritten. One file without the other, a private key others can read, a
    public file that is not the private key's, or a key that is not Ed25519 is refused,
    naming the fix. Processes starting together end up with the same pair: the lock
    serialises them, and the private file is only ever created, never replaced.
    """
    pair_dir = Path(directory).expanduser()
    private_path = pair_dir / name
    public_path = pair_dir / f"{name}.pub"
    try:
        created = _ensure_ssh_keypair(pair_dir, private_path, public_path, comment=comment)
        key = _read_ssh_private_key(private_path)
        derived = _public_line(key)
        stored = _read_stored_public_line(public_path, derived, private_path)
    except SigningKeyFileError as exc:
        raise SshKeyFileError(str(exc)) from exc
    blob = base64.b64decode(derived.split()[1])
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return SshKeyPairFiles(
        private_path=private_path,
        public_path=public_path,
        public_key=stored,
        fingerprint=f"SHA256:{digest}",
        created=created,
    )


def _ensure_ssh_keypair(
    pair_dir: Path, private_path: Path, public_path: Path, *, comment: str
) -> bool:
    new_directory = not pair_dir.exists()
    try:
        pair_dir.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
        if new_directory:
            pair_dir.chmod(_PRIVATE_DIR_MODE)
        lock = os.open(
            pair_dir / _LOCK_FILE_NAME,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            _PRIVATE_FILE_MODE,
        )
    except OSError as exc:
        raise SigningKeyFileError(f"cannot use the SSH key directory {pair_dir}: {exc}") from exc
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        private_present = os.path.lexists(private_path)
        public_present = os.path.lexists(public_path)
        if private_present != public_present:
            present, missing = (
                (private_path, public_path) if private_present else (public_path, private_path)
            )
            raise SigningKeyFileError(
                f"the SSH key pair is incomplete: {present} exists but {missing} does not. "
                f"Restore {missing}, or delete {present} to have a new pair generated "
                "(every host must then join again)"
            )
        if private_present:
            return False
        key = Ed25519PrivateKey.generate()
        write_private_file_once(
            private_path,
            key.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption()),
            what="SSH private key",
        )
        # Whoever won the creation of the private file owns the pair; the public half is
        # derived from what is on disk, so a loser publishes the winner's key.
        winner = _read_ssh_private_key(private_path)
        _write_public_file(public_path, f"{_public_line(winner)} {comment}\n")
        return True
    finally:
        os.close(lock)


def _read_ssh_private_key(private_path: Path) -> Ed25519PrivateKey:
    data = read_private_file(
        private_path,
        what="SSH private key",
        remedy=f"delete it and {private_path}.pub to have a new pair generated; "
        "every host must then join again",
    )
    try:
        key = load_ssh_private_key(data, password=None)
    except (ValueError, TypeError) as exc:
        raise SigningKeyFileError(
            f"the SSH private key {private_path} is not a valid unencrypted OpenSSH private "
            f"key; delete it and {private_path}.pub to have a new pair generated "
            "(every host must then join again)"
        ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise SigningKeyFileError(
            f"the SSH private key {private_path} is not an Ed25519 key; delete it and "
            f"{private_path}.pub to have a new pair generated (every host must then join again)"
        )
    return key


def _public_line(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH).decode()


def _read_stored_public_line(public_path: Path, derived: str, private_path: Path) -> str:
    try:
        stored = public_path.read_text().strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise SigningKeyFileError(f"cannot read the SSH public key {public_path}: {exc}") from exc
    if stored.split()[:2] != derived.split():
        raise SigningKeyFileError(
            f"the SSH public key {public_path} does not belong to the private key "
            f"{private_path}; delete {public_path} and restore the matching file, or delete "
            "both to have a new pair generated (every host must then join again)"
        )
    return stored


def _write_public_file(path: Path, text: str) -> None:
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".niuu-public-", dir=path.parent)
    except OSError as exc:
        raise SigningKeyFileError(f"cannot write the SSH public key {path}: {exc}") from exc
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, _PUBLIC_FILE_MODE)
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        raise SigningKeyFileError(f"cannot write the SSH public key {path}: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)

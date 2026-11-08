"""The SSH key Forge reaches its VMs and registered hosts with, kept in its credential store.

The key is a credential of the compute pool (owner ``compute``, the pool id, name
``host-access-ssh-key``) in the store every Forge already configures (``credential_store``:
OpenBao on a cluster, the file store in mini mode), so where it lives is that store's
choice and nothing SSH-specific is configured. The first Forge of a pool generates an
Ed25519 pair and stores it; every later start reads the same pair. Generation happens under
the credential lock (an advisory lock on Forge's database), so Forges starting together
store one key. "Not stored yet" is the only absence there is: a store that fails, or a
stored value that is not a matching Ed25519 pair, stops Forge with the reason.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.domain.models import SecretType
from niuu.ports.credentials import CredentialRefreshLockPort, CredentialStorePort

OWNER_TYPE = "compute"
CREDENTIAL_NAME = "host-access-ssh-key"
KEY_COMMENT = "forge-host-access"


class HostAccessKeyError(RuntimeError):
    """The pool's stored host-access key cannot be used."""


@dataclass(frozen=True)
class HostAccessKey:
    """An Ed25519 pair: the OpenSSH private key and its ``ssh-ed25519 … comment`` line."""

    private_key: str
    public_key: str

    @property
    def fingerprint(self) -> str:
        blob = base64.b64decode(self.public_key.split()[1])
        return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _public_line(key: Ed25519PrivateKey) -> str:
    public = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    )
    return f"{public.decode()} {KEY_COMMENT}"


def generate_host_access_key() -> HostAccessKey:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    return HostAccessKey(private_key=private.decode(), public_key=_public_line(key))


def _checked(pool_id: str, data: dict[str, str]) -> HostAccessKey:
    where = f"credential {OWNER_TYPE}/{pool_id}/{CREDENTIAL_NAME}"
    try:
        key = serialization.load_ssh_private_key(data["private_key"].encode(), password=None)
    except (KeyError, ValueError, TypeError) as exc:
        raise HostAccessKeyError(
            f"The {where} holds no OpenSSH private key; delete it to have a new one made "
            "(every host must then join again)"
        ) from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise HostAccessKeyError(f"The {where} is not an Ed25519 key")
    derived = _public_line(key)
    stored = data.get("public_key", "")
    if stored.split()[:2] != derived.split()[:2]:
        raise HostAccessKeyError(f"The {where} has a public key that is not its private key's")
    return HostAccessKey(private_key=data["private_key"], public_key=derived)


class HostAccessKeyService:
    """The pool's host-access key: read from the credential store, made once if absent."""

    def __init__(self, store: CredentialStorePort, lock: CredentialRefreshLockPort) -> None:
        self._store = store
        self._lock = lock

    async def ensure(self, pool_id: str) -> HostAccessKey:
        async with self._lock.hold(OWNER_TYPE, pool_id, CREDENTIAL_NAME):
            data = await self._store.get_value(OWNER_TYPE, pool_id, CREDENTIAL_NAME)
            if data is not None:
                return _checked(pool_id, data)
            made = generate_host_access_key()
            await self._store.store(
                OWNER_TYPE,
                pool_id,
                CREDENTIAL_NAME,
                SecretType.SSH_KEY,
                {"private_key": made.private_key, "public_key": made.public_key},
                metadata={"purpose": "Forge's SSH key for the pool's VMs and hosts"},
            )
            stored = await self._store.get_value(OWNER_TYPE, pool_id, CREDENTIAL_NAME)
            if stored is None:
                raise HostAccessKeyError(
                    f"The credential store accepted {OWNER_TYPE}/{pool_id}/{CREDENTIAL_NAME} "
                    "but returns nothing for it; check credential_store"
                )
            return _checked(pool_id, stored)

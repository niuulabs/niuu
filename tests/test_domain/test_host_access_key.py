"""The pool's host-access SSH key: read from the credential store, made there once if absent."""

import asyncio
import contextlib

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.domain.models import SecretType
from niuu.ports.credentials import CredentialRefreshLockPort
from volundr.adapters.outbound.platform_host_runtime import PlatformHostRuntime
from volundr.adapters.outbound.ssh_guest_access import SshGuestAccess
from volundr.adapters.outbound.ssh_host_vm_runtime import SshHostVmRuntime
from volundr.compute import main as compute_main
from volundr.domain.services.host_access_key import (
    CREDENTIAL_NAME,
    KEY_COMMENT,
    OWNER_TYPE,
    HostAccessKey,
    HostAccessKeyError,
    HostAccessKeyService,
    generate_host_access_key,
)


class Lock(CredentialRefreshLockPort):
    """One asyncio lock per credential, the way the advisory lock serialises Forges."""

    def __init__(self) -> None:
        self.locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self.held: list[tuple[str, str, str]] = []

    @contextlib.asynccontextmanager
    async def hold(self, owner_type, owner_id, name):
        key = (owner_type, owner_id, name)
        async with self.locks.setdefault(key, asyncio.Lock()):
            self.held.append(key)
            yield


class DurableStore(MemoryCredentialStore):
    durable = True


def _private(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode()


def _public(key) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )


async def _put(store, data: dict[str, str]) -> None:
    await store.store(OWNER_TYPE, "pool", CREDENTIAL_NAME, SecretType.SSH_KEY, data)


def test_a_generated_key_is_an_ed25519_openssh_pair_with_its_fingerprint():
    key = generate_host_access_key()

    loaded = serialization.load_ssh_private_key(key.private_key.encode(), password=None)
    assert isinstance(loaded, Ed25519PrivateKey)
    assert key.public_key == f"{_public(loaded)} {KEY_COMMENT}"
    assert key.fingerprint.startswith("SHA256:") and "=" not in key.fingerprint


async def test_the_first_start_stores_a_key_and_every_later_start_reads_it():
    store, lock = DurableStore(), Lock()

    first = await HostAccessKeyService(store, lock).ensure("pool")
    second = await HostAccessKeyService(store, lock).ensure("pool")

    assert second == first
    stored = await store.get(OWNER_TYPE, "pool", CREDENTIAL_NAME)
    assert stored.secret_type is SecretType.SSH_KEY
    assert await store.get_value(OWNER_TYPE, "pool", CREDENTIAL_NAME) == {
        "private_key": first.private_key,
        "public_key": first.public_key,
    }
    assert lock.held == [(OWNER_TYPE, "pool", CREDENTIAL_NAME)] * 2


async def test_each_pool_has_its_own_key():
    store, lock = DurableStore(), Lock()

    one = await HostAccessKeyService(store, lock).ensure("one")
    two = await HostAccessKeyService(store, lock).ensure("two")

    assert one.public_key != two.public_key


async def test_forges_starting_together_store_one_key():
    store, lock = DurableStore(), Lock()
    service = HostAccessKeyService(store, lock)

    keys = await asyncio.gather(*(service.ensure("pool") for _ in range(8)))

    assert len({key.public_key for key in keys}) == 1


async def test_a_stored_key_keeps_its_private_half_and_derives_the_public_line():
    key = Ed25519PrivateKey.generate()
    private = _private(key)
    store = DurableStore()
    await _put(store, {"private_key": private, "public_key": _public(key) + " elsewhere"})

    found = await HostAccessKeyService(store, Lock()).ensure("pool")

    assert found == HostAccessKey(private, f"{_public(key)} {KEY_COMMENT}")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"public_key": "ssh-ed25519 AAAA"}, "holds no OpenSSH private key"),
        ({"private_key": "not a key", "public_key": "x"}, "holds no OpenSSH private key"),
        (
            {
                "private_key": _private(
                    rsa.generate_private_key(public_exponent=65537, key_size=2048)
                ),
                "public_key": "ssh-rsa AAAA",
            },
            "is not an Ed25519 key",
        ),
        (
            {
                "private_key": _private(Ed25519PrivateKey.generate()),
                "public_key": _public(Ed25519PrivateKey.generate()),
            },
            "public key that is not its private key's",
        ),
    ],
    ids=["no-private", "not-openssh", "rsa", "mismatched"],
)
async def test_a_stored_value_that_is_not_a_matching_ed25519_pair_stops_forge(data, message):
    store = DurableStore()
    await _put(store, data)

    with pytest.raises(HostAccessKeyError, match=message) as raised:
        await HostAccessKeyService(store, Lock()).ensure("pool")

    assert f"{OWNER_TYPE}/pool/{CREDENTIAL_NAME}" in str(raised.value)


async def test_a_store_that_fails_stops_forge_and_nothing_is_made_elsewhere():
    class Broken(DurableStore):
        async def get_value(self, *args):
            raise ConnectionError("openbao unreachable")

    with pytest.raises(ConnectionError, match="openbao unreachable"):
        await HostAccessKeyService(Broken(), Lock()).ensure("pool")


async def test_a_store_that_accepts_the_key_but_returns_nothing_stops_forge():
    class Forgetful(DurableStore):
        async def store(self, *args, **kwargs):
            return None

    with pytest.raises(HostAccessKeyError, match="returns nothing for it; check credential_store"):
        await HostAccessKeyService(Forgetful(), Lock()).ensure("pool")


# -- startup: the key goes to the adapters that take it ------------------------------------------


async def test_startup_refuses_a_store_that_forgets_on_restart():
    source = compute_main.HostKeySource("pool", MemoryCredentialStore(), Lock())

    with pytest.raises(ValueError, match="configure a durable credential_store"):
        await source.key()


async def test_startup_makes_the_key_once_and_hands_it_to_each_adapter_that_takes_it():
    store = DurableStore()
    source = compute_main.HostKeySource("pool", store, Lock())

    access = await source.for_factory(SshGuestAccess)
    # A subclass passing **kwargs to its base still takes what the base names.
    runtime = await source.for_factory(SshHostVmRuntime)

    assert access["host_key"] is runtime["host_key"]
    assert await store.get_value(OWNER_TYPE, "pool", CREDENTIAL_NAME) is not None


async def test_an_adapter_that_takes_no_key_gets_none_and_none_is_stored():
    store = MemoryCredentialStore()  # refused if a key were asked of it
    source = compute_main.HostKeySource("pool", store, Lock())

    assert await source.for_factory(PlatformHostRuntime) == {}
    assert await source.for_factory(lambda **kwargs: None) == {}
    assert await store.list(OWNER_TYPE, "pool") == []

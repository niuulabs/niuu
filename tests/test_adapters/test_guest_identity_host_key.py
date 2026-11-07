"""HostKeyGuestIdentity: signature, skew and replay, every rejection the same error."""

from __future__ import annotations

import base64
import time
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

from niuu.adapters.node_signature import signing_message
from volundr.adapters.outbound.guest_identity_host_key import (
    ALLOCATION_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    HostKeyGuestIdentity,
)
from volundr.adapters.outbound.ssh_guest_access import HOST_KEY_PATH
from volundr.domain.compute import BootstrapFile, Machine, MachineBootstrap, MachineState
from volundr.domain.guest_credentials import GuestAuthenticationError, GuestRequest

PATH = "/api/v1/internal/compute/metadata/credentials"
SKEW_SECONDS = 30.0


def openssh_public(key) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )


def machine_with(public: str) -> MachineBootstrap:
    return MachineBootstrap(
        files=(
            BootstrapFile(path=HOST_KEY_PATH + ".pub", content=public + "\n", permissions="0644"),
        )
    )


class InMemoryWatermarks:
    def __init__(self) -> None:
        self.marks: dict[UUID, int] = {}
        self.calls = 0

    async def try_advance(self, allocation_id: UUID, timestamp_ms: int) -> bool:
        self.calls += 1
        if self.marks.get(allocation_id, -1) >= timestamp_ms:
            return False
        self.marks[allocation_id] = timestamp_ms
        return True


class Guest:
    """One allocation: its host key, and a signer that behaves like the guest agent."""

    def __init__(self) -> None:
        self.allocation_id = uuid4()
        self.key = Ed25519PrivateKey.generate()
        self.watermarks = InMemoryWatermarks()
        self.bootstraps: dict[UUID, MachineBootstrap] = {
            self.allocation_id: machine_with(openssh_public(self.key))
        }
        self.identity = HostKeyGuestIdentity(
            machine_bootstrap_for=self._bootstrap,
            watermarks=self.watermarks,
            clock_skew_seconds=SKEW_SECONDS,
        )

    async def _bootstrap(self, allocation_id: UUID) -> MachineBootstrap | None:
        return self.bootstraps.get(allocation_id)

    def request(
        self,
        *,
        key=None,
        timestamp_ms: int | None = None,
        body: bytes = b'{"allocation_id": "x"}',
        method: str = "POST",
        path: str = PATH,
        signed_body: bytes | None = None,
        allocation_id: UUID | None = None,
    ) -> GuestRequest:
        timestamp_ms = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
        signature = (key or self.key).sign(
            signing_message(
                method, path, timestamp_ms, body if signed_body is None else signed_body
            )
        )
        return GuestRequest(
            method=method,
            path=path,
            headers={
                ALLOCATION_HEADER.lower(): str(allocation_id or self.allocation_id),
                TIMESTAMP_HEADER.lower(): str(timestamp_ms),
                SIGNATURE_HEADER.lower(): base64.b64encode(signature).decode(),
            },
            body=body,
        )


def now_ms() -> int:
    return int(time.time() * 1000)


async def test_a_valid_signature_proves_the_allocation():
    guest = Guest()

    assert await guest.identity.authenticate(guest.request()) == guest.allocation_id


async def test_header_names_are_case_insensitive():
    guest = Guest()
    request = guest.request()
    shouting = GuestRequest(
        method=request.method,
        path=request.path,
        headers={name.upper(): value for name, value in request.headers.items()},
        body=request.body,
    )

    assert await guest.identity.authenticate(shouting) == guest.allocation_id


async def test_the_signed_bytes_are_the_documented_scheme():
    guest = Guest()
    body = b'{"allocation_id": "abc"}'
    timestamp = now_ms()
    import hashlib

    message = f"POST\n{PATH}\n{timestamp}\n{hashlib.sha256(body).hexdigest()}".encode()
    signature = guest.key.sign(message)
    request = GuestRequest(
        method="POST",
        path=PATH,
        headers={
            "niuu-guest-allocation": str(guest.allocation_id),
            "niuu-guest-timestamp": str(timestamp),
            "niuu-guest-signature": base64.b64encode(signature).decode(),
        },
        body=body,
    )

    assert await guest.identity.authenticate(request) == guest.allocation_id


async def test_a_replayed_request_is_rejected():
    guest = Guest()
    request = guest.request()
    await guest.identity.authenticate(request)

    with pytest.raises(GuestAuthenticationError):
        await guest.identity.authenticate(request)


async def test_an_older_timestamp_than_the_watermark_is_rejected():
    guest = Guest()
    latest = now_ms()
    await guest.identity.authenticate(guest.request(timestamp_ms=latest))

    with pytest.raises(GuestAuthenticationError):
        await guest.identity.authenticate(guest.request(timestamp_ms=latest - 1))
    assert await guest.identity.authenticate(guest.request(timestamp_ms=latest + 1))


@pytest.mark.parametrize(
    "offset_ms", [-(int(SKEW_SECONDS * 1000) + 5000), int(SKEW_SECONDS * 1000) + 5000]
)
async def test_a_timestamp_outside_the_skew_window_is_rejected_without_advancing(offset_ms):
    guest = Guest()

    with pytest.raises(GuestAuthenticationError):
        await guest.identity.authenticate(guest.request(timestamp_ms=now_ms() + offset_ms))

    assert guest.watermarks.calls == 0


async def test_a_signature_by_another_key_is_rejected():
    guest = Guest()

    with pytest.raises(GuestAuthenticationError):
        await guest.identity.authenticate(guest.request(key=Ed25519PrivateKey.generate()))

    assert guest.watermarks.calls == 0


async def test_a_tampered_body_method_or_path_is_rejected():
    guest = Guest()
    for request in (
        guest.request(body=b"{}", signed_body=b'{"allocation_id": "x"}'),
        guest.request(method="PUT"),
    ):
        tampered = GuestRequest(
            method="POST" if request.method == "PUT" else request.method,
            path=request.path,
            headers=request.headers,
            body=request.body,
        )
        with pytest.raises(GuestAuthenticationError):
            await guest.identity.authenticate(tampered)
    other_path = guest.request()
    with pytest.raises(GuestAuthenticationError):
        await guest.identity.authenticate(
            GuestRequest(
                method=other_path.method,
                path="/api/v1/internal/compute/other",
                headers=other_path.headers,
                body=other_path.body,
            )
        )


async def test_an_unknown_allocation_is_rejected_like_a_bad_signature():
    guest = Guest()
    stranger = uuid4()

    with pytest.raises(GuestAuthenticationError) as unknown:
        await guest.identity.authenticate(guest.request(allocation_id=stranger))
    with pytest.raises(GuestAuthenticationError) as bad_signature:
        await guest.identity.authenticate(guest.request(key=Ed25519PrivateKey.generate()))

    assert type(unknown.value) is type(bad_signature.value)
    assert unknown.value.args == bad_signature.value.args == ()


async def test_an_allocation_without_a_stored_host_key_is_rejected():
    guest = Guest()
    guest.bootstraps[guest.allocation_id] = MachineBootstrap()

    with pytest.raises(GuestAuthenticationError):
        await guest.identity.authenticate(guest.request())


@pytest.mark.parametrize(
    "public",
    [
        "not an ssh key",
        "ssh-ed25519 AAAA",
        openssh_public(generate_private_key(public_exponent=65537, key_size=2048)),
    ],
    ids=["not-ssh", "truncated-ed25519", "rsa"],
)
async def test_a_stored_key_that_is_not_ed25519_is_rejected(public):
    guest = Guest()
    guest.bootstraps[guest.allocation_id] = machine_with(public)

    with pytest.raises(GuestAuthenticationError):
        await guest.identity.authenticate(guest.request())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda h: h.pop(ALLOCATION_HEADER.lower()),
        lambda h: h.pop(TIMESTAMP_HEADER.lower()),
        lambda h: h.pop(SIGNATURE_HEADER.lower()),
        lambda h: h.update({ALLOCATION_HEADER.lower(): "not-a-uuid"}),
        lambda h: h.update({TIMESTAMP_HEADER.lower(): "soon"}),
        lambda h: h.update({TIMESTAMP_HEADER.lower(): "-5"}),
        lambda h: h.update({TIMESTAMP_HEADER.lower(): "1e12"}),
        lambda h: h.update({SIGNATURE_HEADER.lower(): "!!!not-base64"}),
        lambda h: h.update({SIGNATURE_HEADER.lower(): base64.b64encode(b"short").decode()}),
    ],
)
async def test_missing_or_malformed_headers_are_rejected_before_any_lookup(mutate):
    guest = Guest()
    looked_up = []

    async def lookup(allocation_id):
        looked_up.append(allocation_id)

    guest.identity = HostKeyGuestIdentity(
        machine_bootstrap_for=lookup, watermarks=guest.watermarks, clock_skew_seconds=SKEW_SECONDS
    )
    request = guest.request()
    headers = dict(request.headers)
    mutate(headers)

    with pytest.raises(GuestAuthenticationError):
        await guest.identity.authenticate(
            GuestRequest(method="POST", path=PATH, headers=headers, body=request.body)
        )

    assert looked_up == []


async def test_a_failed_signature_does_not_consume_the_timestamp():
    guest = Guest()
    timestamp = now_ms()

    with pytest.raises(GuestAuthenticationError):
        await guest.identity.authenticate(
            guest.request(key=Ed25519PrivateKey.generate(), timestamp_ms=timestamp)
        )

    assert await guest.identity.authenticate(guest.request(timestamp_ms=timestamp))


async def test_a_lookup_failure_propagates_instead_of_reading_as_a_bad_signature():
    guest = Guest()

    async def broken(allocation_id):
        raise RuntimeError("credential store is down")

    guest.identity = HostKeyGuestIdentity(
        machine_bootstrap_for=broken, watermarks=guest.watermarks, clock_skew_seconds=SKEW_SECONDS
    )

    with pytest.raises(RuntimeError, match="credential store"):
        await guest.identity.authenticate(guest.request())


def test_the_agent_signs_with_the_pinned_host_key_and_mounts_it_read_only():
    guest = Guest()

    assert guest.identity.agent_auth(guest.allocation_id) == {
        "kind": "host_key",
        "key_path": HOST_KEY_PATH,
    }
    assert guest.identity.agent_mounts() == (
        {"source": HOST_KEY_PATH, "target": HOST_KEY_PATH, "readonly": True},
    )


@pytest.mark.parametrize("skew", [0, -1, float("nan"), float("inf")])
def test_the_clock_skew_must_be_positive_and_finite(skew):
    with pytest.raises(ValueError, match="clock_skew_seconds"):
        HostKeyGuestIdentity(
            machine_bootstrap_for=None, watermarks=InMemoryWatermarks(), clock_skew_seconds=skew
        )


# -- registered hosts: the key is the one the host registered at pairing ------------------------


class RegisteredGuest(Guest):
    """A guest on a registered host: Forge generated no key; the machine record carries it."""

    def __init__(self) -> None:
        super().__init__()
        # The bootstrap must never be consulted in this mode.
        self.bootstraps.clear()
        self.machines: dict[UUID, Machine] = {
            self.allocation_id: self.machine(openssh_public(self.key))
        }
        self.identity = HostKeyGuestIdentity(
            machine_bootstrap_for=self._no_bootstrap,
            watermarks=self.watermarks,
            clock_skew_seconds=SKEW_SECONDS,
            key_source="registered",
            machine_for=self._machine,
        )

    def machine(self, public: str | None) -> Machine:
        return Machine(
            allocation_id=self.allocation_id,
            resource_id="host-1",
            state=MachineState.RUNNING,
            host_public_key=public,
        )

    async def _no_bootstrap(self, allocation_id: UUID) -> MachineBootstrap | None:
        raise AssertionError("a registered host's key is not read from a bootstrap")

    async def _machine(self, allocation_id: UUID) -> Machine | None:
        return self.machines.get(allocation_id)


async def test_a_registered_hosts_signature_is_checked_against_its_registered_key():
    guest = RegisteredGuest()

    assert await guest.identity.authenticate(guest.request()) == guest.allocation_id


async def test_a_signature_by_another_key_is_rejected_for_a_registered_host():
    guest = RegisteredGuest()

    with pytest.raises(GuestAuthenticationError):
        await guest.identity.authenticate(guest.request(key=Ed25519PrivateKey.generate()))


async def test_a_registered_host_key_with_surrounding_whitespace_still_verifies():
    guest = RegisteredGuest()
    guest.machines[guest.allocation_id] = guest.machine(f"  {openssh_public(guest.key)}\n")

    assert await guest.identity.authenticate(guest.request()) == guest.allocation_id


async def test_a_registered_replay_is_rejected():
    guest = RegisteredGuest()
    timestamp = now_ms()
    request = guest.request(timestamp_ms=timestamp)

    assert await guest.identity.authenticate(request)
    with pytest.raises(GuestAuthenticationError):
        await guest.identity.authenticate(request)


@pytest.mark.parametrize(
    "stored",
    [
        "absent",
        None,
        "",
        "not an ssh key",
        "ssh-ed25519 AAAA",
        openssh_public(generate_private_key(public_exponent=65537, key_size=2048)),
    ],
    ids=["no-machine", "no-key", "empty-key", "not-ssh", "truncated-ed25519", "rsa"],
)
async def test_every_unusable_registered_key_is_the_identical_authentication_error(stored):
    guest = RegisteredGuest()
    if stored == "absent":
        guest.machines.clear()
    else:
        guest.machines[guest.allocation_id] = guest.machine(stored)
    with pytest.raises(GuestAuthenticationError) as bad_signature:
        other = RegisteredGuest()
        await other.identity.authenticate(other.request(key=Ed25519PrivateKey.generate()))

    with pytest.raises(GuestAuthenticationError) as refused:
        await guest.identity.authenticate(guest.request())

    assert type(refused.value) is type(bad_signature.value)
    assert refused.value.args == bad_signature.value.args == ()


async def test_a_registered_host_without_a_key_is_logged_with_the_remedy(caplog):
    import logging

    guest = RegisteredGuest()
    guest.machines[guest.allocation_id] = guest.machine(None)

    with caplog.at_level(logging.ERROR, logger="volundr.adapters.outbound.guest_identity_host_key"):
        with pytest.raises(GuestAuthenticationError):
            await guest.identity.authenticate(guest.request())

    assert "no registered host key" in caplog.text
    assert "registered-host provider" in caplog.text


async def test_a_registered_machine_lookup_failure_propagates():
    guest = RegisteredGuest()

    async def broken(allocation_id):
        raise RuntimeError("host repository is down")

    guest.identity = HostKeyGuestIdentity(
        machine_bootstrap_for=guest._no_bootstrap,
        watermarks=guest.watermarks,
        clock_skew_seconds=SKEW_SECONDS,
        key_source="registered",
        machine_for=broken,
    )

    with pytest.raises(RuntimeError, match="host repository"):
        await guest.identity.authenticate(guest.request())


async def test_the_bootstrap_key_source_never_reads_the_machine_record():
    guest = Guest()
    guest.identity = HostKeyGuestIdentity(
        machine_bootstrap_for=guest._bootstrap,
        watermarks=guest.watermarks,
        clock_skew_seconds=SKEW_SECONDS,
        key_source="bootstrap",
        machine_for=AsyncMock(side_effect=AssertionError("not a registered host")),
    )

    assert await guest.identity.authenticate(guest.request()) == guest.allocation_id


def test_an_unknown_key_source_is_refused():
    with pytest.raises(ValueError, match="key_source must be 'bootstrap' or 'registered'"):
        HostKeyGuestIdentity(
            machine_bootstrap_for=None, watermarks=InMemoryWatermarks(), key_source="tofu"
        )


def test_the_registered_key_source_needs_the_machine_port():
    with pytest.raises(ValueError, match="key_source 'registered' needs the machine_for port"):
        HostKeyGuestIdentity(
            machine_bootstrap_for=None, watermarks=InMemoryWatermarks(), key_source="registered"
        )


def test_the_default_key_source_is_the_bootstrap_and_needs_no_machine_port():
    HostKeyGuestIdentity(machine_bootstrap_for=None, watermarks=InMemoryWatermarks())

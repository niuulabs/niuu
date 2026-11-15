"""The VM guest metadata endpoint, alone and end to end with host-key authentication."""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from niuu.adapters.node_signature import signing_message
from niuu.domain.services.forge_session_token import IssuedForgeSessionToken
from niuu.forge_mcp.models import ForgeMcpGrant
from volundr.adapters.inbound.rest_compute_metadata import (
    MAX_METADATA_REQUEST_BYTES,
    create_compute_metadata_router,
)
from volundr.adapters.outbound.guest_identity_host_key import HostKeyGuestIdentity
from volundr.adapters.outbound.ssh_guest_access import HOST_KEY_PATH
from volundr.domain.compute import (
    BootstrapFile,
    ComputeLease,
    ComputeSlot,
    LeaseState,
    MachineBootstrap,
    SlotState,
)
from volundr.domain.guest_credentials import (
    METADATA_CREDENTIALS_PATH,
    GuestAuthenticationError,
    GuestCredentials,
    GuestCredentialUnavailableError,
    GuestUnavailableReason,
    IssuedGuestForgeSession,
    IssuedGuestToken,
)
from volundr.domain.service_account_tokens import ServiceAccountToken
from volundr.domain.services.guest_metadata import (
    BAD_BODY,
    LAUNCH_PAYLOAD_PATH,
    GuestMetadataService,
    GuestRequestError,
)


def client_for(service) -> TestClient:
    app = FastAPI()
    app.include_router(create_compute_metadata_router(service))
    return TestClient(app)


def service_returning(result=None, error: Exception | None = None):
    service = AsyncMock()
    service.credentials.side_effect = error
    service.credentials.return_value = result
    return service


def test_issued_credentials_are_returned_in_the_wire_format_and_never_cached():
    expires = datetime(2030, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    service = service_returning(
        GuestCredentials(
            service_account_tokens=(
                IssuedGuestToken(
                    volume="vault", path="token", token="sa-secret", expires_at=expires
                ),
            ),
            forge_session=IssuedGuestForgeSession(token="forge-secret", grants=("message",)),
        )
    )

    response = client_for(service).post(
        METADATA_CREDENTIALS_PATH, json={"allocation_id": str(uuid4())}, headers={"X-Probe": "1"}
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "service_account_tokens": [
            {
                "volume": "vault",
                "path": "token",
                "token": "sa-secret",
                "expires_at": "2030-01-01T10:00:00+00:00",
            }
        ],
        "forge_session": {"token": "forge-secret", "grants": ["message"]},
    }
    request = service.credentials.await_args.args[0]
    assert (request.method, request.path) == ("POST", METADATA_CREDENTIALS_PATH)
    assert request.headers["x-probe"] == "1"
    assert json.loads(request.body)["allocation_id"]


def test_a_naive_expiry_is_reported_as_utc_and_no_forge_session_is_null():
    service = service_returning(
        GuestCredentials(
            service_account_tokens=(
                IssuedGuestToken(
                    volume="v", path="p", token="t", expires_at=datetime(2030, 1, 1, 0, 0)
                ),
            )
        )
    )

    body = client_for(service).post(METADATA_CREDENTIALS_PATH, content=b"{}").json()

    assert body["service_account_tokens"][0]["expires_at"] == "2030-01-01T00:00:00+00:00"
    assert body["forge_session"] is None


def test_every_authentication_failure_is_the_same_401():
    response = client_for(service_returning(error=GuestAuthenticationError())).post(
        METADATA_CREDENTIALS_PATH, content=b"{}"
    )

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert response.headers["cache-control"] == "no-store"


def test_an_allocation_without_a_running_session_is_a_409_with_the_reason_code(caplog):
    error = GuestCredentialUnavailableError(
        GuestUnavailableReason.NOT_RUNNING, "The allocation is draining"
    )
    response = client_for(service_returning(error=error)).post(
        METADATA_CREDENTIALS_PATH, content=b"{}"
    )

    assert response.status_code == 409
    assert response.json() == {"error": "allocation_not_running"}
    assert "The allocation is draining" in caplog.text


def test_an_unreadable_authenticated_body_is_a_400_without_the_exception_text():
    response = client_for(service_returning(error=GuestRequestError("internal detail"))).post(
        METADATA_CREDENTIALS_PATH, content=b"{}"
    )

    assert response.status_code == 400
    assert response.json() == {"error": BAD_BODY}


def test_a_body_over_64_kib_is_refused_before_authentication():
    service = service_returning()

    response = client_for(service).post(
        METADATA_CREDENTIALS_PATH, content=b"x" * (MAX_METADATA_REQUEST_BYTES + 1)
    )

    assert response.status_code == 413
    service.credentials.assert_not_awaited()


def test_a_body_of_exactly_64_kib_is_read():
    service = service_returning(GuestCredentials())

    response = client_for(service).post(
        METADATA_CREDENTIALS_PATH, content=b"x" * MAX_METADATA_REQUEST_BYTES
    )

    assert response.status_code == 200
    service.credentials.assert_awaited_once()


def test_only_post_is_served():
    assert client_for(service_returning()).get(METADATA_CREDENTIALS_PATH).status_code == 405


def test_an_unexpected_failure_is_a_server_error_not_an_authentication_result():
    app = FastAPI()
    app.include_router(
        create_compute_metadata_router(service_returning(error=RuntimeError("boom")))
    )

    response = TestClient(app, raise_server_exceptions=False).post(
        METADATA_CREDENTIALS_PATH, content=b"{}"
    )

    assert response.status_code == 500


class SignedGuest:
    """A real HostKeyGuestIdentity and GuestMetadataService behind the real router."""

    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.lease = ComputeLease(
            id=uuid4(),
            pool_id="pool",
            profile="p",
            request_fingerprint="f",
            state=LeaseState.IDLE,
        )
        self.binding = ComputeSlot(
            id=uuid4(),
            allocation_id=self.lease.id,
            slot=0,
            pool_id="pool",
            session_id=uuid4(),
            tenant_id="t",
            owner_id="o",
            state=SlotState.BUSY,
        )
        public = (
            self.key.public_key()
            .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
            .decode()
        )
        machine = MachineBootstrap(
            files=(BootstrapFile(path=HOST_KEY_PATH + ".pub", content=public),)
        )
        launch = {"service_account": "sa", "token_volumes": [], "forge_session": True}
        session = MachineBootstrap(
            files=(BootstrapFile(path=LAUNCH_PAYLOAD_PATH, content=json.dumps(launch)),)
        )
        self.marks: dict[UUID, int] = {}

        marks = self.marks

        class Watermarks:
            async def try_advance(self, allocation_id, timestamp_ms):
                if marks.get(allocation_id, -1) >= timestamp_ms:
                    return False
                marks[allocation_id] = timestamp_ms
                return True

        async def machine_bootstrap_for(allocation_id):
            return machine if allocation_id == self.lease.id else None

        compute = AsyncMock()
        compute.bootstrap_for.return_value = session
        repository = AsyncMock()
        repository.get.side_effect = lambda lease_id: (
            self.lease if lease_id == self.lease.id else None
        )
        repository.slot.side_effect = lambda allocation_id, slot: (
            self.binding if (allocation_id, slot) == (self.lease.id, 0) else None
        )
        self.forge_session = AsyncMock(
            return_value=IssuedForgeSessionToken(
                token="forge-secret",
                expires_at=1,
                session_id=str(self.binding.session_id),
                launch_id="l",
                scopes=(),
                grants=(ForgeMcpGrant.MESSAGE,),
            )
        )
        tokens = AsyncMock()
        tokens.issue.return_value = ServiceAccountToken(
            token="sa", expires_at=datetime.now(UTC) + timedelta(hours=1)
        )
        service = GuestMetadataService(
            repository,
            compute,
            "pool",
            HostKeyGuestIdentity(
                machine_bootstrap_for=machine_bootstrap_for, watermarks=Watermarks()
            ),
            tokens,
            self.forge_session,
        )
        self.client = client_for(service)

    def post(
        self,
        *,
        key=None,
        timestamp_ms: int | None = None,
        allocation: UUID | None = None,
        **body_fields,
    ):
        body = json.dumps({"allocation_id": str(self.lease.id), **body_fields}).encode()
        timestamp_ms = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
        signature = (key or self.key).sign(
            signing_message("POST", METADATA_CREDENTIALS_PATH, timestamp_ms, body)
        )
        return self.client.post(
            METADATA_CREDENTIALS_PATH,
            content=body,
            headers={
                "Niuu-Guest-Allocation": str(allocation or self.lease.id),
                "Niuu-Guest-Timestamp": str(timestamp_ms),
                "Niuu-Guest-Signature": base64.b64encode(signature).decode(),
            },
        )


def test_a_signed_request_is_answered_for_the_bound_session_only():
    guest = SignedGuest()

    response = guest.post()

    assert response.status_code == 200
    assert response.json()["forge_session"] == {"token": "forge-secret", "grants": ["message"]}
    guest.forge_session.assert_awaited_once_with(guest.binding.session_id)


def test_replay_wrong_key_and_unknown_allocation_are_indistinguishable_401s():
    guest = SignedGuest()
    stamp = int(time.time() * 1000)
    assert guest.post(timestamp_ms=stamp).status_code == 200

    replay = guest.post(timestamp_ms=stamp)
    wrong_key = guest.post(key=Ed25519PrivateKey.generate(), timestamp_ms=stamp + 1)
    unknown = guest.post(allocation=uuid4(), timestamp_ms=stamp + 2)
    stale = guest.post(timestamp_ms=stamp - 10 * 60 * 1000)

    for rejected in (replay, wrong_key, unknown, stale):
        assert rejected.status_code == 401
        assert rejected.json() == {"error": "unauthorized"}


def test_a_stopped_session_is_a_409_even_with_a_valid_signature():
    guest = SignedGuest()
    guest.binding = guest.binding.model_copy(update={"stop_requested": True})

    response = guest.post()

    assert response.status_code == 409
    assert response.json() == {"error": "session_stopping"}


def test_a_missing_forge_session_is_a_409_not_a_partial_answer():
    guest = SignedGuest()
    guest.forge_session.return_value = None

    response = guest.post()

    assert response.status_code == 409
    assert response.json() == {"error": "forge_session_unavailable"}


def test_the_slot_is_named_in_the_signed_body_and_must_exist():
    guest = SignedGuest()

    assert guest.post(slot=0).status_code == 200

    # A slot the single-slot allocation does not have is a 400 after authentication.
    rejected = guest.post(slot=1)
    assert rejected.status_code == 400
    assert "slot" in rejected.json()["error"]


def test_a_free_slot_is_a_409_with_the_unbound_reason():
    guest = SignedGuest()
    guest.binding = None

    response = guest.post()

    assert response.status_code == 409
    assert response.json() == {"error": "no_bound_session"}


@pytest.mark.parametrize("headers", [{}, {"Niuu-Guest-Allocation": "x"}])
def test_an_unsigned_request_is_401(headers):
    guest = SignedGuest()

    response = guest.client.post(METADATA_CREDENTIALS_PATH, content=b"{}", headers=headers)

    assert response.status_code == 401

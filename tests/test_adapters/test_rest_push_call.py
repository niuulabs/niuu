"""``POST /push/call``: a resident rings its owner's phone, and ``/push/test kind=voip``."""

from __future__ import annotations

import asyncio
import copy
import time
from datetime import timedelta

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from niuu.domain.services.token_scope import (
    FORGE_PUSH_CALL_SCOPE,
    VALKYRIE_BUILD_TOKEN_USE,
    credential_allows_route,
)
from tests.support.push_fakes import (
    BUNDLE,
    THOR,
    Clock,
    FakeApns,
    FakeDeviceRepository,
    FakeGuildFeed,
    FakePushDeliveryRepository,
    device,
    gateway_settings,
)
from volundr.adapters.inbound.rest_push import create_push_router
from volundr.domain.push import PushTokenKind
from volundr.domain.services.push_gateway import CallSettings, PushGateway

URL = "/api/v1/forge/push"
OWNER = "dev-user"
RAVN = "0b7f3f0e-sidekick"


def _token(**claims) -> str:
    now = int(time.time())
    payload = {"sub": OWNER, "iat": now, "exp": now + 600}
    payload.update(claims)
    return jwt.encode(payload, "test-only-signing-key-32-bytes-long!", algorithm="HS256")


def resident_token(*, scopes=(FORGE_PUSH_CALL_SCOPE,), resident_id: str | None = RAVN) -> str:
    """A delegated resident's exchanged credential, as ymir's exchange mints it."""
    claims = {"token_use": VALKYRIE_BUILD_TOKEN_USE, "scopes": list(scopes)}
    if resident_id is not None:
        claims["workload_resident_id"] = resident_id
    return _token(**claims)


def headers(token: str | None = None, owner: str = OWNER) -> dict[str, str]:
    # Envoy verifies the bearer and forwards the owner as x-auth-* headers.
    values = {"x-auth-user-id": owner, "x-auth-tenant": "default"}
    values["authorization"] = f"Bearer {token if token is not None else resident_token()}"
    return values


class _HeaderIdentity(EnvoyHeaderAuthenticationAdapter):
    async def get_or_provision_user(self, principal):
        return None


class Rig:
    def __init__(self, calls: CallSettings | None = None) -> None:
        clock = Clock()
        self.clock = clock
        self.devices = FakeDeviceRepository(clock)
        self.apns = FakeApns()
        self.feed = FakeGuildFeed()
        self.gateway = PushGateway(
            self.feed,
            self.devices,
            FakePushDeliveryRepository(clock),
            self.apns,
            gateway_settings(calls=calls or CallSettings()),
            clock=clock,
        )
        self.feed.add_node(THOR)
        asyncio.run(self.gateway.run_once())
        app = FastAPI()
        app.state.identity = _HeaderIdentity()
        app.include_router(create_push_router(self.gateway))
        self.client = TestClient(app)

    def body(self, request_id: str = "req-1", **payload_overrides) -> dict:
        payload = {
            "id": request_id,
            "source": "resident",
            "session": {"host": "valhalla", "id": RAVN, "name": "Sidekick"},
            "text": "The deploy finished.",
            "notificationId": "8a1c5c1e-7d0b-5d3f-9b0e-3e2f1d0c9a10",
            "priority": "now",
            "expiresAt": (self.clock() + timedelta(minutes=5)).isoformat(),
        }
        payload.update(payload_overrides)
        return {
            "request_id": request_id,
            "frame": {"type": "call.request", "id": request_id, "payload": payload},
        }

    def ring(self, body: dict | None = None, **kwargs):
        return self.client.post(f"{URL}/call", json=body or self.body(), **kwargs)


@pytest.fixture
def rig() -> Rig:
    rig = Rig()
    rig.devices.add(
        device("voip-mine", PushTokenKind.VOIP),
        device("voip-theirs", PushTokenKind.VOIP, owner_id="someone-else"),
    )
    return rig


def test_a_resident_rings_its_owner(rig):
    response = rig.ring(headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["sent"] is True and body["tokens"] == 1
    (result,) = body["results"]
    assert set(result) == {"fingerprint", "status", "reason"}
    assert result["status"] == 200
    (push,) = rig.apns.sent
    assert push.token == "voip-mine" and push.topic == BUNDLE + ".voip"
    assert push.payload == rig.body()["frame"]


def test_the_owner_comes_from_the_credential(rig):
    # The same resident credential, Envoy naming another owner: only theirs ring.
    response = rig.ring(headers=headers(owner="someone-else"))

    assert response.status_code == 200
    assert [push.token for push in rig.apns.sent] == ["voip-theirs"]


def test_no_voip_token_answers_not_sent(rig):
    response = rig.ring(headers=headers(owner="nobody"))

    assert response.status_code == 200
    assert response.json() == {"sent": False, "tokens": 0, "results": []}


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(_token(), id="unscoped (a human)"),
        pytest.param(resident_token(scopes=["forge:session:create"]), id="without the scope"),
        pytest.param(resident_token(scopes=[]), id="empty scopes"),
        pytest.param(resident_token(resident_id=None), id="not a delegated resident"),
        pytest.param(
            _token(token_use="forge_session", scopes=["forge:notify"]), id="a Forge session"
        ),
    ],
)
def test_ringing_needs_a_residents_scoped_credential(rig, token):
    response = rig.ring(headers=headers(token))

    assert response.status_code == 403
    assert rig.apns.sent == []


def test_no_credential_at_all_is_refused(rig):
    response = rig.client.post(f"{URL}/call", json=rig.body())

    assert response.status_code in (401, 403)
    assert rig.apns.sent == []


def test_a_resident_cannot_ring_as_another_session(rig):
    body = rig.body(session={"host": "valhalla", "id": "another-resident", "name": "Other"})

    response = rig.ring(body, headers=headers())

    assert response.status_code == 403
    assert rig.apns.sent == []


def test_the_resident_id_matches_case_insensitively(rig):
    response = rig.ring(headers=headers(resident_token(resident_id=RAVN.upper())))

    assert response.status_code == 200 and response.json()["sent"]


def _mutated(rig: Rig, mutate) -> dict:
    body = copy.deepcopy(rig.body())
    mutate(body)
    return body


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda b: b["frame"].update(type="notification"), id="wrong type"),
        pytest.param(lambda b: b["frame"]["payload"].update(text="x" * 601), id="text over 600"),
        pytest.param(lambda b: b["frame"]["payload"]["session"].pop("id"), id="no session.id"),
        pytest.param(lambda b: b["frame"].pop("id"), id="no id"),
        pytest.param(lambda b: b.update(request_id="other"), id="another request id"),
        pytest.param(lambda b: b.pop("request_id"), id="no request_id"),
        pytest.param(lambda b: b.update(extra=True), id="unknown key"),
        pytest.param(
            lambda b: b["frame"]["payload"].update(expiresAt="2020-01-01T00:00:00Z"), id="expired"
        ),
        pytest.param(
            lambda b: b["frame"]["payload"].update(padding="x" * 4096), id="body over 4096"
        ),
    ],
)
def test_anything_but_a_call_request_is_refused(rig, mutate):
    response = rig.ring(_mutated(rig, mutate), headers=headers())

    assert response.status_code == 422
    assert rig.apns.sent == []


def test_rings_past_the_cap_answer_429():
    rig = Rig(CallSettings(max_per_hour=1))
    rig.devices.add(device("voip-mine", PushTokenKind.VOIP))

    assert rig.ring(rig.body("req-1"), headers=headers()).status_code == 200
    refused = rig.ring(rig.body("req-2"), headers=headers())

    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) > 3500
    assert len(rig.apns.sent) == 1


def test_rings_switched_off_answer_404():
    rig = Rig(CallSettings(enabled=False))
    rig.devices.add(device("voip-mine", PushTokenKind.VOIP))

    assert rig.ring(headers=headers()).status_code == 404
    assert rig.apns.sent == []


def test_the_edge_admits_the_scoped_credential_on_this_route_only():
    token = resident_token()

    assert credential_allows_route(token, "POST", "/api/v1/forge/push/call")
    assert not credential_allows_route(token, "POST", "/api/v1/forge/push/test")
    assert not credential_allows_route(token, "GET", "/api/v1/forge/notifications")
    assert not credential_allows_route(
        resident_token(scopes=["forge:session:create"]), "POST", "/api/v1/forge/push/call"
    )


def test_the_operator_test_ring(rig):
    response = rig.client.post(
        f"{URL}/test",
        json={"kind": "voip", "session_id": RAVN},
        headers={"x-auth-user-id": OWNER, "x-auth-tenant": "default"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "voip" and body["sent"] == 1 and body["accepted"] == 1
    (push,) = rig.apns.sent
    assert push.token == "voip-mine" and push.push_type.value == "voip"
    assert push.payload["payload"]["text"] == "Test call"
    assert push.payload["payload"]["session"]["id"] == RAVN


def test_the_operator_test_ring_without_a_voip_token():
    rig = Rig()
    rig.devices.add(device("alert-1"))

    response = rig.client.post(
        f"{URL}/test",
        json={"kind": "voip"},
        headers={"x-auth-user-id": OWNER, "x-auth-tenant": "default"},
    )

    assert response.status_code == 404
    assert "voip" in response.json()["detail"]

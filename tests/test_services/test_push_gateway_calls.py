"""The push gateway's rings: a resident's call request as a VoIP push."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.support.push_fakes import (
    BUNDLE,
    START,
    Clock,
    FakeApns,
    FakeDeviceRepository,
    FakeGuildFeed,
    FakePushDeliveryRepository,
    device,
    gateway_settings,
)
from volundr.domain.push import (
    ApnsResult,
    PushEnvironment,
    PushTestKind,
    PushTokenKind,
    token_fingerprint,
    validate_call_request,
)
from volundr.domain.services.push_gateway import (
    CallRateLimitedError,
    CallSettings,
    PushGateway,
)

START_UNIX = int(START.timestamp())
RAVN = "0b7f3f0e-sidekick"


def call_frame(request_id: str = "req-1", *, expires_in: float | None = 300) -> dict:
    payload = {
        "id": request_id,
        "source": "resident",
        "session": {"host": "valhalla", "id": RAVN, "name": "Sidekick"},
        "text": "The deploy finished.",
        "notificationId": "8a1c5c1e-7d0b-5d3f-9b0e-3e2f1d0c9a10",
        "priority": "now",
    }
    if expires_in is not None:
        payload["expiresAt"] = (START + timedelta(seconds=expires_in)).isoformat()
    return {"type": "call.request", "id": request_id, "payload": payload}


class Rig:
    def __init__(self, calls: CallSettings | None = None) -> None:
        self.clock = Clock()
        self.devices = FakeDeviceRepository(self.clock)
        self.apns = FakeApns()
        self.gateway = PushGateway(
            FakeGuildFeed(),
            self.devices,
            FakePushDeliveryRepository(self.clock),
            self.apns,
            gateway_settings(calls=calls or CallSettings()),
            clock=self.clock,
        )

    async def ring(self, request_id: str = "req-1", owner: str = "dev-user", **frame_args):
        call = validate_call_request(
            request_id, call_frame(request_id, **frame_args), now=self.clock()
        )
        return await self.gateway.ring(owner, call)


@pytest.fixture
def rig() -> Rig:
    return Rig()


async def test_a_ring_is_a_voip_push_of_the_frame(rig):
    rig.devices.add(device("voip-1", PushTokenKind.VOIP), device("alert-1"))

    result = await rig.ring()

    (push,) = rig.apns.sent
    assert push.token == "voip-1"
    assert push.push_type.value == "voip"
    assert push.topic == BUNDLE + ".voip"
    assert push.priority == 10
    # min(expiresAt = +300 s, now + 60 s): a late ring is worse than none.
    assert push.expiration == START_UNIX + 60
    assert push.payload == call_frame()
    assert "aps" not in push.payload
    assert result.sent and result.tokens == 1
    (answer,) = result.results
    assert answer.fingerprint == token_fingerprint("voip-1")
    assert (answer.status, answer.reason) == (200, "")
    assert rig.gateway.stats.calls_rung == 1


async def test_an_expiry_inside_the_ring_window_is_kept(rig):
    rig.devices.add(device("voip-1", PushTokenKind.VOIP))

    await rig.ring(expires_in=20)

    assert rig.apns.sent[0].expiration == START_UNIX + 20


async def test_only_the_owners_voip_tokens_for_the_bundle(rig):
    rig.devices.add(
        device("mine-sandbox", PushTokenKind.VOIP, environment=PushEnvironment.SANDBOX),
        device("mine-production", PushTokenKind.VOIP, install_id="install-2"),
        device("theirs", PushTokenKind.VOIP, owner_id="someone-else"),
        device("other-app", PushTokenKind.VOIP, app_bundle_id="com.other.app"),
        device("mine-widget", PushTokenKind.WIDGET),
    )

    result = await rig.ring()

    # Each token goes to its own environment's APNs host.
    assert {(p.token, p.environment) for p in rig.apns.sent} == {
        ("mine-sandbox", PushEnvironment.SANDBOX),
        ("mine-production", PushEnvironment.PRODUCTION),
    }
    assert result.tokens == 2


async def test_no_voip_token_sends_nothing_and_costs_no_ring():
    calls = Rig(CallSettings(max_per_hour=1))
    calls.devices.add(device("alert-1"))

    result = await calls.ring("req-1")

    assert (result.sent, result.tokens, result.results) == (False, 0, ())
    assert calls.apns.sent == []
    calls.devices.add(device("voip-1", PushTokenKind.VOIP))
    assert (await calls.ring("req-2")).sent


async def test_a_dead_token_is_deleted(rig):
    rig.devices.add(device("voip-dead", PushTokenKind.VOIP), device("voip-ok", PushTokenKind.VOIP))
    rig.apns.answer("voip-dead", ApnsResult(410, "Unregistered"))

    result = await rig.ring()

    assert result.sent  # the other token took it
    assert rig.devices.tokens(PushTokenKind.VOIP) == ["voip-ok"]
    assert {(r.status, r.reason) for r in result.results} == {(410, "Unregistered"), (200, "")}
    assert rig.gateway.stats.tokens_pruned == 1


async def test_bad_device_token_is_deleted_and_the_ring_not_sent(rig):
    rig.devices.add(device("voip-bad", PushTokenKind.VOIP))
    rig.apns.answer("voip-bad", ApnsResult(400, "BadDeviceToken"))

    result = await rig.ring()

    assert not result.sent and result.tokens == 1
    assert rig.devices.tokens() == []
    assert rig.gateway.stats.calls_undelivered == 1


async def test_rings_are_capped_per_owner_per_hour():
    rig = Rig(CallSettings(max_per_hour=2))
    rig.devices.add(
        device("voip-1", PushTokenKind.VOIP), device("voip-2", PushTokenKind.VOIP, owner_id="b")
    )

    await rig.ring("req-1")
    await rig.ring("req-2")
    with pytest.raises(CallRateLimitedError) as refused:
        await rig.ring("req-3")

    assert 3590 < refused.value.retry_after_seconds <= 3600
    assert rig.gateway.stats.calls_rate_limited == 1
    # Another owner has a cap of their own.
    assert (await rig.ring("req-1", owner="b")).sent
    # An hour after the first ring, one more is allowed.
    rig.clock.advance(3600)
    assert (await rig.ring("req-3", expires_in=None)).sent


async def test_the_same_request_id_rung_again_counts_once():
    rig = Rig(CallSettings(max_per_hour=1))
    rig.devices.add(device("voip-1", PushTokenKind.VOIP))

    await rig.ring("req-1")
    # The socket escalation re-rings the same id: pushed again, not counted again.
    await rig.ring("req-1")

    assert len(rig.apns.sent) == 2
    with pytest.raises(CallRateLimitedError):
        await rig.ring("req-2")


async def test_the_test_push_rings_with_a_test_call(rig):
    rig.devices.add(device("voip-1", PushTokenKind.VOIP, environment=PushEnvironment.SANDBOX))

    (result,) = await rig.gateway.send_test("dev-user", PushTestKind.VOIP, title="t", body="b")

    (push,) = rig.apns.sent
    assert push.push_type.value == "voip" and push.topic == BUNDLE + ".voip"
    assert push.expiration == START_UNIX + 60 and push.priority == 10
    payload = push.payload
    assert payload["type"] == "call.request" and payload["id"] == payload["payload"]["id"]
    assert payload["payload"]["text"] == "Test call"
    assert payload["payload"]["session"]["id"] == "forge-push-test"
    # The frame is one the ring route itself would take.
    validate_call_request(payload["id"], payload, now=rig.clock())
    assert (result.kind, result.environment, result.accepted) == ("voip", "sandbox", True)
    # A test ring is not counted against the owner's cap.
    assert rig.gateway.stats.calls_rung == 0


async def test_the_test_push_can_name_the_session(rig):
    rig.devices.add(device("voip-1", PushTokenKind.VOIP))

    await rig.gateway.send_test(
        "dev-user", PushTestKind.VOIP, title="t", body="b", call_session_id=RAVN
    )

    assert rig.apns.sent[0].payload["payload"]["session"]["id"] == RAVN


async def test_the_test_push_needs_a_voip_token(rig):
    rig.devices.add(device("alert-1"))

    assert await rig.gateway.send_test("dev-user", PushTestKind.VOIP, title="t", body="b") == []
    assert rig.apns.sent == []


def test_status_says_whether_rings_are_on():
    assert Rig().gateway.status()["calls_enabled"] is True
    assert Rig(CallSettings(enabled=False)).gateway.status()["calls_enabled"] is False

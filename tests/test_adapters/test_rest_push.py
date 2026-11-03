"""The push gateway's status and test-push routes."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from tests.support.push_fakes import (
    THOR,
    Clock,
    FakeApns,
    FakeDeviceRepository,
    FakeGuildFeed,
    FakePushDeliveryRepository,
    device,
    gateway_settings,
    guild_notification,
)
from volundr.adapters.inbound.rest_push import create_push_router
from volundr.domain.push import ApnsResult, PushEnvironment, PushTokenKind
from volundr.domain.push_ports import GuildFeedError
from volundr.domain.services.push_gateway import PushGateway

AUTH = {"x-auth-user-id": "dev-user", "x-auth-tenant": "default"}
URL = "/api/v1/forge/push"


class _HeaderIdentity(EnvoyHeaderAuthenticationAdapter):
    async def get_or_provision_user(self, principal):
        return None


class Rig:
    def __init__(self) -> None:
        clock = Clock()
        self.feed = FakeGuildFeed()
        self.devices = FakeDeviceRepository(clock)
        self.apns = FakeApns()
        self.gateway = PushGateway(
            self.feed,
            self.devices,
            FakePushDeliveryRepository(clock),
            self.apns,
            gateway_settings(),
            clock=clock,
        )
        # One tick, as after a start: the gateway knows the Guild's nodes.
        self.feed.add_node(THOR)
        asyncio.run(self.gateway.run_once())
        app = FastAPI()
        # Forwarded x-auth-* headers identify the caller (the Envoy header identity).
        app.state.identity = _HeaderIdentity()
        app.include_router(create_push_router(self.gateway))
        self.client = TestClient(app)


@pytest.fixture
def rig() -> Rig:
    return Rig()


def test_status_reports_the_gateway(rig):
    response = rig.client.get(f"{URL}/status", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["running"] is False and body["nodes"] == {THOR: 0}
    assert body["unreachable_nodes"] == {}
    assert body["alerts_sent"] == 0 and body["ticks"] == 1
    assert body["alerts_enabled"] is True
    # Counts only, per APNs environment and kind; never a token.
    assert body["tokens_by_environment"] == {}
    assert body["apns_by_environment"] == {}
    assert body["registrations_environment_missing"] == 0


def test_status_needs_a_caller(rig):
    assert rig.client.get(f"{URL}/status").status_code == 401


def test_test_alert_goes_to_the_callers_alert_tokens_only(rig):
    rig.devices.add(
        device("aaaaaaaa11111111", environment=PushEnvironment.SANDBOX),
        device("bbbbbbbb22222222", owner_id="someone-else"),
        device("cccccccc33333333", PushTokenKind.WIDGET),
    )

    response = rig.client.post(f"{URL}/test", json={}, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "kind": "alert",
        "sent": 1,
        "accepted": 1,
        "devices": [
            {
                "kind": "alert",
                "environment": "sandbox",
                "token_suffix": "11111111",
                "accepted": True,
                "detail": "accepted",
                "apns_id": "apns-1",
            }
        ],
    }
    (push,) = rig.apns.sent
    assert push.token == "aaaaaaaa11111111"
    assert push.payload["aps"]["alert"] == {
        "title": "Forge test push",
        "body": "Push from Forge reaches this device.",
    }


def test_test_alert_with_its_own_text_and_a_refusal(rig):
    rig.devices.add(device("aaaaaaaa11111111"))
    rig.apns.answer("aaaaaaaa11111111", ApnsResult(403, "InvalidProviderToken"))

    response = rig.client.post(
        f"{URL}/test", json={"kind": "alert", "title": "Hello", "body": "World"}, headers=AUTH
    )

    body = response.json()
    assert (body["sent"], body["accepted"]) == (1, 0)
    assert body["devices"][0]["detail"] == "HTTP 403 InvalidProviderToken"
    assert rig.apns.sent[0].payload["aps"]["alert"] == {"title": "Hello", "body": "World"}


def test_test_live_activity_and_widget(rig):
    rig.devices.add(
        device("la-token-00000001", PushTokenKind.LIVE_ACTIVITY),
        device("widget-token-0002", PushTokenKind.WIDGET),
    )
    rig.feed.add(guild_notification("n-1", instance_id=THOR))

    live = rig.client.post(f"{URL}/test", json={"kind": "live_activity"}, headers=AUTH)
    widget = rig.client.post(f"{URL}/test", json={"kind": "widget"}, headers=AUTH)

    assert live.json()["accepted"] == 1 and widget.json()["accepted"] == 1
    assert [push.push_type.value for push in rig.apns.sent] == ["liveactivity", "widgets"]


def test_no_token_of_that_kind_is_a_404(rig):
    response = rig.client.post(f"{URL}/test", json={"kind": "live_activity_start"}, headers=AUTH)

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "You have no registered live_activity_start token on this Forge"
    )


def test_background_push_goes_to_the_alert_token(rig):
    rig.devices.add(device("aaaaaaaa11111111"), device("cccccccc33333333", PushTokenKind.WIDGET))

    response = rig.client.post(f"{URL}/test", json={"kind": "background"}, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert (body["kind"], body["sent"], body["accepted"]) == ("background", 1, 1)
    assert body["devices"][0]["kind"] == "background"
    (push,) = rig.apns.sent
    assert push.token == "aaaaaaaa11111111"
    assert push.push_type.value == "background"
    assert push.payload == {"aps": {"content-available": 1}, "forge": {"reason": "notification"}}


def test_background_without_an_alert_token_is_a_404(rig):
    response = rig.client.post(f"{URL}/test", json={"kind": "background"}, headers=AUTH)
    assert response.status_code == 404
    assert response.json()["detail"] == "You have no registered alert token on this Forge"


def test_one_registered_token(rig):
    rig.devices.add(device("aaaaaaaa11111111"), device("bbbbbbbb22222222"))

    response = rig.client.post(
        f"{URL}/test", json={"kind": "alert", "token": "bbbbbbbb22222222"}, headers=AUTH
    )

    assert response.json()["sent"] == 1
    assert [push.token for push in rig.apns.sent] == ["bbbbbbbb22222222"]


def test_a_token_that_is_not_registered_is_a_404(rig):
    rig.devices.add(device("aaaaaaaa11111111"))

    response = rig.client.post(
        f"{URL}/test", json={"kind": "alert", "token": "ffffffff99999999"}, headers=AUTH
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        "That token is not one of your registered alert tokens on this Forge"
    )
    assert rig.apns.sent == []


def test_when_no_guild_node_answers_the_test_push_is_a_502(rig):
    rig.devices.add(device("la-token-00000001", PushTokenKind.LIVE_ACTIVITY))
    rig.feed.down[THOR] = GuildFeedError("Guild node inst-thor's feed answered HTTP 502")

    response = rig.client.post(f"{URL}/test", json={"kind": "live_activity"}, headers=AUTH)

    assert response.status_code == 502
    assert response.json()["detail"] == "No Guild node answers right now; try again in a moment"


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "voip"},
        {"title": ""},
        {"title": "t" * 121},
        {"body": "b" * 281},
        {"token": ""},
        {"token": "x" * 513},
        {"device": "x"},
    ],
)
def test_bad_requests_are_rejected(rig, body):
    assert rig.client.post(f"{URL}/test", json=body, headers=AUTH).status_code == 422


def test_test_push_needs_a_caller(rig):
    assert rig.client.post(f"{URL}/test", json={}).status_code == 401

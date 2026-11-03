"""A resident calls its owner: notify's speak, the guardrails, the phone's socket, call.result."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from niuu.domain.notifications import NotificationSpeak, notification_id
from ravn.adapters.channels.voice.calls import (
    CallPresenceRegistry,
    CallRequester,
    ResidentCallRequests,
    call_request_frame,
    resident_notification_id,
)
from ravn.adapters.channels.voice.store import CallRequest, VoiceCallStore
from ravn.adapters.tools.notify import ResidentNotifyTool
from ravn.config import VoiceCallsConfig
from ravn.domain.notification_channel import current_notification_channel
from ravn.ports.call_push import CallPushPort, CallPushResult, NullCallPush
from tests.test_ravn.test_voice_gateway import build

GOLDEN = Path(__file__).resolve().parents[1] / "ravn" / "fixtures" / "voice" / "call_request.json"
RESIDENT = "0b6f8c1e-5a2d-4f39-8e7a-3c9d1f2a6b54"
T0 = 1_762_000_000_000  # epoch ms
MINUTE = 60_000


class Clock:
    def __init__(self, ms: int = T0) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms


class Mono:
    def __init__(self) -> None:
        self.at = 1000.0

    def __call__(self) -> float:
        return self.at


class Phone:
    """One socket's send: what the phone would have received."""

    def __init__(self, *, alive: bool = True) -> None:
        self.frames: list[dict[str, Any]] = []
        self.alive = alive

    async def send(self, frame: dict[str, Any]) -> bool:
        if self.alive:
            self.frames.append(frame)
        return self.alive


class FakePush(CallPushPort):
    def __init__(self, *, sent: bool = True, reason: str = "") -> None:
        self.rings: list[tuple[str, dict[str, Any]]] = []
        self.sent = sent
        self.reason = reason

    async def ring(self, *, request_id: str, frame: dict[str, Any]) -> CallPushResult:
        self.rings.append((request_id, frame))
        return CallPushResult(sent=self.sent, tokens=1 if self.sent else 0, reason=self.reason)


@pytest.fixture
def store(tmp_path: Path):
    opened = VoiceCallStore(str(tmp_path / "calls.db"))
    yield opened
    opened.close()


def config(**overrides: Any) -> VoiceCallsConfig:
    values = {"enabled": True, "resident_id": RESIDENT, "host": "valhalla", **overrides}
    return VoiceCallsConfig(**values)


def queue(store: VoiceCallStore, clock: Clock, key: str = "k1", **speak: Any) -> CallRequest:
    calls = ResidentCallRequests(
        store, resident_id=RESIDENT, expires_after_seconds=300, clock=clock
    )
    request = calls.request(
        speak=NotificationSpeak(
            text=speak.get("text", "The deploy is done."),
            **{k: v for k, v in speak.items() if k != "text"},
        ),
        idempotency_key=key,
        channel=None,
    )
    assert request is not None
    return request


def requester(
    store: VoiceCallStore,
    registry: CallPresenceRegistry,
    clock: Clock,
    push: CallPushPort | None = None,
    **overrides: Any,
) -> CallRequester:
    return CallRequester(
        store,
        registry,
        config(**overrides),
        name="Sidekick",
        push=push or NullCallPush(),
        clock=clock,
    )


def standby(
    registry: CallPresenceRegistry, phone: Phone, device: str = "dev-1", **fields: Any
) -> bool:
    payload = {
        "device": "iphone",
        "deviceId": device,
        "standby": True,
        "activeCall": False,
        **fields,
    }
    return registry.update(payload, connection=phone, send=phone.send)


# -- the frame -----------------------------------------------------------------


def test_the_frame_is_the_golden_one_the_app_decodes() -> None:
    golden = json.loads(GOLDEN.read_text())
    request = CallRequest(
        request_id="7f3c2a9e-1b4d-4c8e-9a51-2d6f0e8b4c13",
        notification_id=resident_notification_id(RESIDENT, None, "deploy-done-1"),
        idempotency_key="deploy-done-1",
        channel=None,
        text="The deploy finished and every check is green.",
        priority="now",
        created_at_ms=1_793_718_245_000 - 300_000,
        expires_at_ms=1_793_718_245_000,  # 2026-11-03T15:04:05Z
        state="queued",
        delivery=None,
        device_id=None,
        sent_at_ms=None,
        push_tried=False,
        push_reason=None,
        outcome=None,
        reason=None,
        answered_at_ms=None,
        call_id=None,
    )
    assert (
        call_request_frame(request, host="valhalla", resident_id=RESIDENT, name="Sidekick")
        == golden
    )
    payload = golden["payload"]
    assert golden["id"] == payload["id"] and "aps" not in golden
    assert 0 < len(payload["text"]) <= 600


def test_the_notification_id_is_the_one_forge_derives() -> None:
    from volundr.domain.services.notifications import resident_notify_dedupe_key

    session = uuid.uuid4()
    for channel, anchor in ((None, None), (f"agent:main:niuu-{session}", session), ("main", None)):
        expected = notification_id(resident_notify_dedupe_key(uuid.UUID(RESIDENT), anchor, "k1"))
        assert resident_notification_id(RESIDENT, channel, "k1") == str(expected)


class _AdapterPush:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    async def ring(self, *, request_id: str, frame: dict[str, Any]) -> CallPushResult:
        return CallPushResult(sent=True, tokens=1)


def test_the_push_is_built_from_its_adapter_or_is_null() -> None:
    from ravn.cli.voice_wiring import build_call_push

    assert isinstance(build_call_push(config()), NullCallPush)
    adapter = f"{__name__}._AdapterPush"
    push = build_call_push(config(push={"adapter": adapter, "kwargs": {"base_url": "https://x"}}))
    assert isinstance(push, _AdapterPush) and push.base_url == "https://x"
    with pytest.raises(TypeError, match="CallPushPort"):
        build_call_push(config(push={"adapter": "builtins.object"}))


def test_calls_need_the_residents_id() -> None:
    with pytest.raises(ValidationError, match="resident_id"):
        VoiceCallsConfig(enabled=True)
    with pytest.raises(ValidationError, match="needs host"):
        VoiceCallsConfig(enabled=True, resident_id=RESIDENT)
    assert VoiceCallsConfig().enabled is False


# -- the presence registry -----------------------------------------------------


def test_a_newer_socket_replaces_the_older_one() -> None:
    mono = Mono()
    registry = CallPresenceRegistry(clock=mono)
    old, new = Phone(), Phone()
    assert standby(registry, old)
    mono.at += 5
    assert standby(registry, new)
    assert len(registry) == 1
    assert registry.fresh().connection is new
    registry.drop(old)  # the old socket closing later drops nothing
    assert registry.fresh().connection is new
    registry.drop(new)
    assert registry.fresh() is None


def test_a_stale_heartbeat_is_no_presence_until_the_socket_speaks_again() -> None:
    mono = Mono()
    registry = CallPresenceRegistry(clock=mono)
    phone = Phone()
    standby(registry, phone, liveness={"heartbeatSeconds": 60})
    mono.at += 119
    assert registry.fresh() is not None
    mono.at += 2
    assert registry.fresh() is None, "two heartbeats without a frame"
    registry.touch(phone)  # a ping on the same socket
    assert registry.fresh() is not None


def test_only_a_phone_in_standby_or_a_call_can_take_one() -> None:
    registry = CallPresenceRegistry(clock=Mono())
    mac = Phone()
    assert not registry.update(
        {"device": "mac", "deviceId": "mac-1", "standby": True}, connection=mac, send=mac.send
    )
    assert registry.fresh() is None and len(registry) == 0
    phone = Phone()
    standby(registry, phone, standby=False)
    assert registry.fresh() is None
    standby(registry, phone, standby=False, activeCall=True)
    assert registry.fresh() is not None, "an aside"
    with pytest.raises(ValueError, match="deviceId"):
        registry.update({"device": "iphone"}, connection=phone, send=phone.send)


# -- notify's speak ------------------------------------------------------------


async def test_notify_offers_speak_only_when_the_resident_places_calls(
    tmp_path, open_outbox, store
) -> None:
    outbox = open_outbox(tmp_path / "state.db")
    plain = ResidentNotifyTool(outbox)
    assert "speak" not in plain.input_schema["properties"]
    assert "speak:" not in plain.description
    refused = await plain.execute({"kind": "info", "title": "x", "speak": {"text": "hi"}})
    assert refused.is_error and "places no calls" in refused.content
    assert await outbox.pending(limit=10) == []

    calls = ResidentCallRequests(store, resident_id=RESIDENT, expires_after_seconds=300)
    tool = ResidentNotifyTool(outbox, calls=calls)
    schema = tool.input_schema["properties"]["speak"]
    assert schema["properties"]["text"]["maxLength"] == 600
    assert schema["properties"]["priority"]["enum"] == ["now", "next_window"]
    assert "never for an FYI" in tool.description


async def test_notify_with_speak_queues_one_call_request_and_the_notification(
    tmp_path, open_outbox, store
) -> None:
    outbox = open_outbox(tmp_path / "state.db")
    clock = Clock()
    calls = ResidentCallRequests(
        store, resident_id=RESIDENT, expires_after_seconds=300, clock=clock
    )
    tool = ResidentNotifyTool(outbox, calls=calls)
    session = uuid.uuid4()
    token = current_notification_channel.set(f"agent:main:niuu-{session}")
    try:
        args = {
            "kind": "milestone",
            "title": "Deploy done",
            "idempotency_key": "deploy-1",
            "speak": {"text": "  The deploy is done.  ", "priority": "next_window"},
        }
        first = json.loads((await tool.execute(args)).content)
        again = json.loads((await tool.execute(args)).content)
        plain = json.loads((await tool.execute({"kind": "info", "title": "FYI"})).content)
    finally:
        current_notification_channel.reset(token)

    assert first["call"] == "requested" and again["call"] == "already_requested"
    assert "call" not in plain, "a notification without speak asks for no call"
    [request] = store.call_requests()
    assert request.text == "The deploy is done." and request.priority == "next_window"
    assert request.expires_at_ms == T0 + 300_000 and request.state == "queued"
    assert request.notification_id == resident_notification_id(
        RESIDENT, f"agent:main:niuu-{session}", "deploy-1"
    )
    entries = await outbox.pending(limit=10)
    assert len(entries) == 3 and entries[0].draft.speak.text == "The deploy is done."


# -- the requester -------------------------------------------------------------


async def test_a_queued_request_goes_out_once_on_a_fresh_standby_socket(store) -> None:
    clock, registry, phone = Clock(), CallPresenceRegistry(clock=Mono()), Phone()
    standby(registry, phone)
    request = queue(store, clock)
    await requester(store, registry, clock).tick()
    [frame] = phone.frames
    assert frame["type"] == "call.request" and frame["id"] == request.request_id
    assert frame["payload"]["session"] == {"host": "valhalla", "id": RESIDENT, "name": "Sidekick"}
    assert frame["payload"]["notificationId"] == request.notification_id
    sent = store.call_request(request.request_id)
    assert (sent.state, sent.delivery, sent.device_id, sent.sent_at_ms) == (
        "sent",
        "socket",
        "dev-1",
        T0,
    )
    # A restart (a new requester on the same store) sends nothing again.
    await requester(store, registry, clock).tick()
    assert len(phone.frames) == 1


async def test_an_expired_request_is_dropped_unsent(store) -> None:
    clock, registry, phone = Clock(), CallPresenceRegistry(clock=Mono()), Phone()
    request = queue(store, clock)
    clock.ms += 300_000
    standby(registry, phone)
    await requester(store, registry, clock).tick()
    assert phone.frames == []
    expired = store.call_request(request.request_id)
    assert (expired.state, expired.delivery, expired.reason) == ("expired", "none", "expired")


async def test_the_rate_cap_and_the_gap_hold_requests_until_the_window_opens(store) -> None:
    clock, registry, phone = Clock(), CallPresenceRegistry(clock=Mono()), Phone()
    standby(registry, phone)
    calls = requester(
        store, registry, clock, max_per_hour=2, min_gap_minutes=10, expires_after_seconds=7200
    )
    calls_store = ResidentCallRequests(
        store, resident_id=RESIDENT, expires_after_seconds=7200, clock=clock
    )

    def ask(key: str, priority: str = "now") -> str:
        speak = NotificationSpeak(text=f"About {key}.", priority=priority)
        return calls_store.request(speak=speak, idempotency_key=key, channel=None).request_id

    first = ask("a")
    await calls.tick()
    held = ask("b", "next_window")
    clock.ms += 5 * MINUTE
    await calls.tick()
    assert [f["id"] for f in phone.frames] == [first], "inside the gap"
    clock.ms += 5 * MINUTE
    await calls.tick()
    assert [f["id"] for f in phone.frames] == [first, held], "the gap opened"
    third = ask("c")
    clock.ms += 30 * MINUTE
    await calls.tick()
    assert len(phone.frames) == 2, "two calls in the last hour already"
    clock.ms += 31 * MINUTE  # the first call is over an hour ago
    await calls.tick()
    assert phone.frames[-1]["id"] == third


async def test_a_call_the_phone_turned_down_leaves_the_window_open(store) -> None:
    clock, registry, phone = Clock(), CallPresenceRegistry(clock=Mono()), Phone()
    standby(registry, phone)
    calls = requester(store, registry, clock)
    first = queue(store, clock, "a")
    await calls.tick()
    store.record_call_result(first.request_id, outcome="deferred", reason="quiet_hours", at_ms=T0)
    second = queue(store, clock, "b")
    await calls.tick()
    assert [f["id"] for f in phone.frames] == [first.request_id, second.request_id]


async def test_no_answer_on_the_socket_rings_by_push_once_with_the_same_id(store) -> None:
    clock, registry, phone = Clock(), CallPresenceRegistry(clock=Mono()), Phone()
    standby(registry, phone)
    push = FakePush()
    calls = requester(store, registry, clock, push)
    request = queue(store, clock)
    await calls.tick()
    clock.ms += 19_000
    await calls.tick()
    assert push.rings == []
    clock.ms += 1_000
    await calls.tick()
    await calls.tick()
    [(request_id, frame)] = push.rings
    assert request_id == request.request_id and frame == phone.frames[0]
    assert store.call_request(request.request_id).delivery == "voip_push"


async def test_an_answer_in_time_stops_the_push(store) -> None:
    clock, registry, phone = Clock(), CallPresenceRegistry(clock=Mono()), Phone()
    standby(registry, phone)
    push = FakePush()
    calls = requester(store, registry, clock, push)
    request = queue(store, clock)
    await calls.tick()
    store.record_call_result(request.request_id, outcome="spoke", reason=None, at_ms=T0 + 5_000)
    clock.ms += 60_000
    await calls.tick()
    assert push.rings == []


async def test_without_a_presence_it_pushes_once_then_waits_for_a_socket(store) -> None:
    clock, registry = Clock(), CallPresenceRegistry(clock=Mono())
    push = FakePush(sent=False, reason="push_not_configured")
    calls = requester(store, registry, clock, push)
    request = queue(store, clock)
    await calls.tick()
    await calls.tick()
    assert len(push.rings) == 1, "one push per request"
    waiting = store.call_request(request.request_id)
    assert (waiting.state, waiting.push_tried, waiting.push_reason) == (
        "queued",
        True,
        "push_not_configured",
    )
    phone = Phone()
    standby(registry, phone)  # the phone's standby socket came back
    await calls.tick()
    assert [f["id"] for f in phone.frames] == [request.request_id]


async def test_a_dead_socket_falls_back_to_the_push(store) -> None:
    clock, registry = Clock(), CallPresenceRegistry(clock=Mono())
    standby(registry, Phone(alive=False))
    push = FakePush()
    request = queue(store, clock)
    await requester(store, registry, clock, push).tick()
    assert [ring[0] for ring in push.rings] == [request.request_id]
    sent = store.call_request(request.request_id)
    assert (sent.state, sent.delivery) == ("sent", "voip_push")


# -- the gateway ---------------------------------------------------------------


def ask(ws, message_type: str, payload: dict | None = None, message_id: str = "m1") -> dict:
    ws.send_json({"type": message_type, "id": message_id, "payload": payload or {}})
    return ws.receive_json()


def test_presence_is_registered_and_push_tokens_are_dropped_unlogged(tmp_path, caplog) -> None:
    gateway, client = build(tmp_path)
    caplog.set_level(logging.DEBUG)
    with client.websocket_connect("/") as ws:
        presence = {"device": "iphone", "deviceId": "dev-1", "standby": True, "activeCall": False}
        assert ask(ws, "call.presence", presence, "p1") == {
            "type": "call.presence.result",
            "id": "p1",
            "payload": {"registered": True},
        }
        assert client.get("/health").json()["presences"] == 1
        tokens = {
            "deviceId": "dev-1",
            "device": "iphone",
            "bundleId": "com.example.app",
            "environment": "sandbox",
            "voipToken": "deadbeefvoip0001",
            "alertToken": "deadbeefalert0002",
        }
        answer = ask(ws, "push.register", tokens, "r1")
        assert answer["type"] == "push.register.result" and answer["id"] == "r1"
        assert answer["payload"]["registered"] is False and answer["payload"]["reason"]
        assert ask(ws, "call.presence", {"device": "iphone"}, "p2")["payload"]["code"] == (
            "INVALID_PARAMS"
        )
    assert "deadbeef" not in caplog.text
    assert client.get("/health").json()["presences"] == 0, "the socket closed"


def test_call_result_is_recorded_by_request_id_on_any_socket_with_no_reply(tmp_path) -> None:
    gateway, client = build(tmp_path)
    store = gateway._store
    request = queue(store, Clock())
    store.mark_call_sent(request.request_id, delivery="socket", device_id="dev-1", at_ms=T0)
    with client.websocket_connect("/"):
        pass  # the socket the request went out on is gone
    with client.websocket_connect("/") as ws:
        result = {"id": request.request_id, "outcome": "refused", "reason": "duplicate"}
        ws.send_json({"type": "call.result", "id": request.request_id, "payload": result})
        result = {"id": request.request_id, "outcome": "spoke"}
        ws.send_json({"type": "call.result", "id": request.request_id, "payload": result})
        ws.send_json({"type": "call.result", "id": "unknown", "payload": {"outcome": "spoke"}})
        # Events get no reply: the next frame the phone reads answers its ping.
        assert ask(ws, "ping", message_id="after") == {"type": "pong", "id": "after", "payload": {}}
        started = ask(
            ws,
            "session.start",
            {"sessionId": "call-1", "metadata": {"ambientRequestId": request.request_id}},
        )
        assert started["type"] == "session.started"
    answered = store.call_request(request.request_id)
    assert (answered.state, answered.outcome, answered.reason) == ("answered", "spoke", None)
    assert answered.call_id == "call-1"


def test_the_gateway_sends_the_request_on_the_phones_socket(tmp_path) -> None:
    gateway, client = build(tmp_path)
    store = gateway._store
    clock = Clock()
    calls = requester(store, gateway._presences, clock)
    request = queue(store, clock)
    with client, client.websocket_connect("/") as ws:
        presence = {"device": "iphone", "deviceId": "dev-1", "standby": True, "activeCall": False}
        assert ask(ws, "call.presence", presence)["payload"]["registered"] is True
        client.portal.call(calls.tick)  # on the app's own loop, as the gateway runs it
        frame = ws.receive_json()
        assert frame["type"] == "call.request" and frame["id"] == request.request_id
        ws.send_json(
            {
                "type": "call.result",
                "id": request.request_id,
                "payload": {"id": request.request_id, "outcome": "spoke"},
            }
        )
        assert ask(ws, "ping")["type"] == "pong"
    assert store.call_request(request.request_id).outcome == "spoke"

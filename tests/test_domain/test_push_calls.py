"""The VoIP push of a resident's call request: topic, frame contract, expiration."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from volundr.domain.push import (
    CALL_TEXT_MAX_CHARS,
    TEST_CALL_SESSION_ID,
    ApnsPushType,
    CallRequestError,
    PushTestKind,
    PushTokenKind,
    apns_topic,
    call_expiration,
    operator_test_call_frame,
    validate_call_request,
    voip_payload,
)

NOW = datetime(2026, 11, 3, 15, 0, 0, tzinfo=UTC)
RAVN = "0b7f3f0e-sidekick"


def frame(**payload_overrides) -> dict:
    """The contract's frame (resident-calls-contract.md), as phase 1 sends it."""
    payload = {
        "id": "req-1",
        "source": "resident",
        "session": {"host": "valhalla", "id": RAVN, "name": "Sidekick"},
        "text": "The deploy finished; two tests are flaky.",
        "notificationId": "8a1c5c1e-7d0b-5d3f-9b0e-3e2f1d0c9a10",
        "priority": "now",
        "expiresAt": "2026-11-03T15:05:00Z",
    }
    payload.update(payload_overrides)
    return {"type": "call.request", "id": "req-1", "payload": payload}


def test_voip_topic_and_kinds():
    assert apns_topic("com.niuu.lexi.chat", ApnsPushType.VOIP) == "com.niuu.lexi.chat.voip"
    assert ApnsPushType.VOIP.value == "voip"
    assert PushTokenKind.VOIP.value == "voip"
    assert PushTestKind.VOIP.token_kind is PushTokenKind.VOIP


def test_a_contract_frame_is_accepted():
    call = validate_call_request("req-1", frame(), now=NOW)

    assert call.request_id == "req-1" and call.session_id == RAVN
    assert call.expires_at == datetime(2026, 11, 3, 15, 5, tzinfo=UTC)
    assert call.frame == frame()


def test_epoch_milliseconds_and_no_expiry_are_accepted():
    millis = int((NOW + timedelta(minutes=2)).timestamp() * 1000)
    assert validate_call_request("req-1", frame(expiresAt=millis), now=NOW).expires_at == (
        NOW + timedelta(minutes=2)
    )
    bare = frame()
    del bare["payload"]["expiresAt"], bare["payload"]["priority"]
    assert validate_call_request("req-1", bare, now=NOW).expires_at is None


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda f: f.update(type="call.result"), "frame.type"),
        (lambda f: f.update(type="notification"), "frame.type"),
        (lambda f: f.pop("id"), "frame.id is missing"),
        (lambda f: f.update(id="other"), "not the request_id"),
        (lambda f: f.update(payload="text"), "payload is not an object"),
        (lambda f: f["payload"].update(id="other"), "payload.id"),
        (lambda f: f["payload"].update(text="   "), "payload.text is missing"),
        (lambda f: f["payload"].pop("text"), "payload.text is missing"),
        (lambda f: f["payload"].update(text="x" * (CALL_TEXT_MAX_CHARS + 1)), "longer than 600"),
        (lambda f: f["payload"]["session"].pop("id"), "payload.session.id is missing"),
        (lambda f: f["payload"]["session"].update(host=""), "payload.session.host is missing"),
        (lambda f: f["payload"].pop("session"), "payload.session is not an object"),
        (lambda f: f["payload"].update(priority="later"), "payload.priority"),
        (lambda f: f["payload"].update(expiresAt="soon"), "expiresAt"),
        (lambda f: f["payload"].update(expiresAt="2026-11-03T14:59:59Z"), "expired"),
    ],
)
def test_anything_else_is_refused(mutate, message):
    bad = copy.deepcopy(frame())
    mutate(bad)

    with pytest.raises(CallRequestError, match=message):
        validate_call_request("req-1", bad, now=NOW)


def test_text_of_exactly_600_characters_is_accepted():
    validate_call_request("req-1", frame(text="x" * CALL_TEXT_MAX_CHARS), now=NOW)


def test_expiration_is_the_earlier_of_the_request_and_the_ring_window():
    soon = NOW + timedelta(seconds=20)
    late = NOW + timedelta(minutes=5)
    now_unix = int(NOW.timestamp())

    assert call_expiration(soon, now=NOW, ring_seconds=60) == now_unix + 20
    assert call_expiration(late, now=NOW, ring_seconds=60) == now_unix + 60
    assert call_expiration(None, now=NOW, ring_seconds=60) == now_unix + 60


def test_the_payload_is_the_frame_without_aps():
    with_aps = {**frame(), "aps": {"alert": "x"}}
    assert voip_payload(with_aps) == frame()


def test_the_operator_test_frame_is_a_valid_call_request():
    test = operator_test_call_frame("t-1", host="ymir", now=NOW, ring_seconds=60)

    call = validate_call_request("t-1", test, now=NOW)
    assert call.session_id == TEST_CALL_SESSION_ID
    assert test["payload"]["text"] == "Test call"
    assert test["payload"]["session"]["host"] == "ymir"
    assert test["payload"]["expiresAt"] == "2026-11-03T15:01:00Z"


def test_the_operator_test_frame_can_name_a_session():
    test = operator_test_call_frame(
        "t-1", host="ymir", now=NOW, ring_seconds=60, session_id=RAVN, session_name="Sidekick"
    )
    assert test["payload"]["session"] == {"host": "ymir", "id": RAVN, "name": "Sidekick"}


#: Phase 1's golden frame (tests/ravn/fixtures/voice/call_request.json), the one the
#: resident's voice gateway sends and the app's wire test decodes.
GOLDEN = {
    "type": "call.request",
    "id": "7f3c2a9e-1b4d-4c8e-9a51-2d6f0e8b4c13",
    "payload": {
        "id": "7f3c2a9e-1b4d-4c8e-9a51-2d6f0e8b4c13",
        "source": "resident",
        "session": {
            "host": "valhalla",
            "id": "0b6f8c1e-5a2d-4f39-8e7a-3c9d1f2a6b54",
            "name": "Sidekick",
        },
        "text": "The deploy finished and every check is green.",
        "notificationId": "01177db3-f589-59a9-bf46-c3e6c106e6e6",
        "priority": "now",
        "expiresAt": "2026-11-03T15:04:05Z",
    },
}


def test_the_golden_frame_is_rung_as_it_is():
    call = validate_call_request(GOLDEN["id"], GOLDEN, now=NOW)

    assert call.session_id == "0b6f8c1e-5a2d-4f39-8e7a-3c9d1f2a6b54"
    assert voip_payload(call.frame) == GOLDEN

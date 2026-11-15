"""The voice channel's wire frames (``skuld.voice.protocol``)."""

from __future__ import annotations

import pytest

from skuld.voice import protocol as wire


def test_frame_has_type_id_and_payload_object():
    assert wire.frame(wire.PONG, "p1") == {"type": "pong", "id": "p1", "payload": {}}
    assert wire.frame(wire.AUTH_OK, "a", {"x": 1}) == {
        "type": "auth.ok",
        "id": "a",
        "payload": {"x": 1},
    }


def test_error_frame_carries_message_and_code():
    assert wire.error_frame("m", "nope", wire.ErrorCodes.UNKNOWN_TYPE) == {
        "type": "error",
        "id": "m",
        "payload": {"error": "nope", "code": "UNKNOWN_TYPE"},
    }


def test_parse_frame_returns_type_id_payload():
    assert wire.parse_frame({"type": "ping", "id": "1", "payload": {"a": 1}}) == (
        "ping",
        "1",
        {"a": 1},
    )


def test_parse_frame_treats_a_missing_payload_as_empty():
    assert wire.parse_frame({"type": "ping", "id": "1"}) == ("ping", "1", {})
    assert wire.parse_frame({"type": "ping", "id": "1", "payload": None}) == ("ping", "1", {})


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "must be a JSON object"),
        ("ping", "must be a JSON object"),
        ({"id": "1"}, "message.type is required"),
        ({"type": "", "id": "1"}, "message.type is required"),
        ({"type": 3, "id": "1"}, "message.type is required"),
        ({"type": "ping"}, "message.id is required"),
        ({"type": "ping", "id": ""}, "message.id is required"),
        ({"type": "ping", "id": 7}, "message.id is required"),
        ({"type": "ping", "id": "1", "payload": [1]}, "payload must be an object"),
        ({"type": "ping", "id": "1", "payload": "x"}, "payload must be an object"),
    ],
)
def test_parse_frame_refuses_a_malformed_frame_loudly(raw, message):
    with pytest.raises(wire.ProtocolError, match=message) as refused:
        wire.parse_frame(raw)

    assert refused.value.code == wire.ErrorCodes.INVALID_MESSAGE

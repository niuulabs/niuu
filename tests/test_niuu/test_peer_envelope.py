"""Building the envelope a platform host stamps: ids, session messages, relayed room messages."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from niuu.domain.peer_envelope import (
    STAMPED_BODY_KEYS,
    RelayedSpeech,
    build_relayed_message,
    build_session_message,
    derive_message_id,
    session_reply_route,
)
from niuu.domain.peer_message import (
    PeerAddress,
    PeerKind,
    PeerMessageError,
    ReplyKind,
    ReplyRoute,
)

SENDER = PeerAddress(kind=PeerKind.SESSION, id="s-1", name="alpha", node="thor")
RECIPIENT = PeerAddress(kind=PeerKind.SESSION, id="s-2", name="beta")
NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)


def test_the_id_is_stable_for_a_retry_and_differs_otherwise() -> None:
    assert derive_message_id(SENDER, RECIPIENT, "r1") == derive_message_id(SENDER, RECIPIENT, "r1")
    assert derive_message_id(SENDER, RECIPIENT, "r1") != derive_message_id(SENDER, RECIPIENT, "r2")
    other = PeerAddress(kind=PeerKind.SESSION, id="s-3")
    assert derive_message_id(SENDER, other, "r1") != derive_message_id(SENDER, RECIPIENT, "r1")
    assert derive_message_id(other, RECIPIENT, "r1") != derive_message_id(SENDER, RECIPIENT, "r1")


def test_a_session_message_can_be_answered_by_a_session_reply() -> None:
    message = build_session_message(
        sender=SENDER,
        recipient=RECIPIENT,
        content="hello",
        request_id="r1",
        in_reply_to="m-0",
        urgent=True,
        now=NOW,
    )

    assert message.reply == session_reply_route(SENDER)
    assert (message.reply.kind, message.reply.target, message.reply.node) == (
        ReplyKind.SESSION,
        "s-1",
        "thor",
    )
    assert (message.sent_at, message.in_reply_to, message.urgent) == (NOW, "m-0", True)
    assert message.message_id == derive_message_id(SENDER, RECIPIENT, "r1")
    assert message.relayed_by is None and message.room_id == ""


def test_a_session_message_stamps_the_time_when_none_is_given() -> None:
    message = build_session_message(
        sender=SENDER, recipient=RECIPIENT, content="hello", request_id="r1"
    )

    assert message.sent_at.tzinfo is not None


SPEAKER = PeerAddress(kind=PeerKind.PERSON, id="human:jozef", name="Jozef")


def _speech(**overrides) -> RelayedSpeech:
    fields = {
        "speaker": SPEAKER,
        "room_id": "huddle-1",
        "reply": ReplyRoute(kind=ReplyKind.ROOM, target="host", node="thor"),
    }
    return RelayedSpeech(**{**fields, **overrides})


def test_a_relayed_message_names_the_speaker_the_room_and_the_relaying_host() -> None:
    message = build_relayed_message(
        relayed_by=SENDER,
        recipient=RECIPIENT,
        speech=_speech(),
        content="ship it",
        request_id="room-1",
        now=NOW,
    )

    assert message.sender == SPEAKER and message.relayed_by == SENDER
    assert message.room_id == "huddle-1" and message.reply.kind is ReplyKind.ROOM


def test_only_a_session_can_relay() -> None:
    with pytest.raises(PeerMessageError, match="relayed by a session"):
        build_relayed_message(
            relayed_by=SPEAKER,
            recipient=RECIPIENT,
            speech=_speech(),
            content="x",
            request_id="r",
        )


def test_a_relayed_message_needs_a_room() -> None:
    with pytest.raises(PeerMessageError, match="room_id"):
        _speech(room_id=" ")


def test_the_body_block_round_trips() -> None:
    speech = _speech()

    parsed = RelayedSpeech.from_body(speech.to_body())

    assert (parsed.speaker, parsed.room_id, parsed.reply) == (
        speech.speaker,
        speech.room_id,
        speech.reply,
    )
    assert RelayedSpeech.from_body({**speech.to_body(), "reply": None}).reply is None


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("x", "peer must be an object"),
        ({"speaker": {"kind": "person", "id": "p"}, "room_id": 5}, "room_id must be a string"),
        ({"speaker": 1, "room_id": "r"}, "speaker must be an object"),
        (
            {"speaker": {"kind": "person", "id": "p"}, "room_id": "r", "relayed_by": {}},
            "unknown keys: relayed_by",
        ),
        ({"speaker": {"kind": "person", "id": "p"}, "room_id": "r", "reply": 3}, "reply"),
    ],
)
def test_a_malformed_body_block_raises(raw, message) -> None:
    with pytest.raises(PeerMessageError, match=message):
        RelayedSpeech.from_body(raw)


def test_the_stamped_keys_are_every_party_and_time_field() -> None:
    assert STAMPED_BODY_KEYS == {"sender", "recipient", "relayed_by", "sent_at", "message_id"}

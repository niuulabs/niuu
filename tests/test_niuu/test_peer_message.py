"""The peer message envelope: strict wire format and the header every harness reads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from niuu.domain.peer_message import (
    PEER_MESSAGE_VERSION,
    REPLY_TOOL,
    PeerAddress,
    PeerKind,
    PeerMessage,
    PeerMessageError,
    ReplyKind,
    ReplyRoute,
    render_for_harness,
)
from niuu.forge_mcp.models import ForgeApiError
from niuu.forge_mcp.ports import ForgeMcpHost

SENT = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def coder() -> PeerAddress:
    return PeerAddress(PeerKind.SESSION, "s-coder", name="coder", node="valhalla")


def reviewer() -> PeerAddress:
    return PeerAddress(PeerKind.SESSION, "s-review", name="reviewer")


def message(**overrides) -> PeerMessage:
    fields = {
        "message_id": "m-1",
        "sender": coder(),
        "recipient": reviewer(),
        "sent_at": SENT,
        "content": "Can you look at the retry path?",
        "reply": ReplyRoute(ReplyKind.SESSION, "s-coder", node="valhalla"),
    }
    fields.update(overrides)
    return PeerMessage(**fields)


def test_a_message_round_trips_through_the_wire() -> None:
    original = message(
        in_reply_to="m-0",
        room_id="room-7",
        relayed_by=PeerAddress(PeerKind.SESSION, "s-host", name="host"),
        urgent=True,
    )

    wire = original.to_wire()

    assert wire["version"] == PEER_MESSAGE_VERSION
    assert PeerMessage.from_wire(wire) == original


def test_a_message_without_a_reply_route_round_trips() -> None:
    original = message(reply=None)

    assert PeerMessage.from_wire(original.to_wire()) == original


def test_sent_at_keeps_its_instant_across_timezones() -> None:
    later = SENT.astimezone(timezone(timedelta(hours=2)))

    parsed = PeerMessage.from_wire(message(sent_at=later).to_wire())

    assert parsed.sent_at == SENT


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda w: w.update(version=2), "unsupported peer message version 2"),
        (lambda w: w.pop("message_id"), "message_id is required"),
        (lambda w: w.update(content="  "), "content is required"),
        (lambda w: w.update(sender="coder"), "sender must be an object"),
        (lambda w: w["sender"].update(kind="robot"), "sender.kind must be one of"),
        (lambda w: w["recipient"].update(id=""), "recipient.id is required"),
        (lambda w: w.update(sent_at="yesterday"), "sent_at is not ISO 8601"),
        (lambda w: w.update(sent_at="2026-11-08T12:00:00"), "sent_at must carry a timezone"),
        (lambda w: w.update(sent_at=0), "sent_at must be an ISO 8601 string"),
        (lambda w: w.update(urgent="yes"), "urgent must be true or false"),
        (lambda w: w.update(reply="s-coder"), "reply must be an object or null"),
        (lambda w: w["reply"].update(kind="email"), "reply.kind must be one of"),
        (lambda w: w["reply"].update(target=""), "reply.target is required"),
        (lambda w: w.update(room_id=7), "room_id must be a string"),
    ],
)
def test_a_malformed_envelope_is_refused_with_the_reason(mutate, error: str) -> None:
    wire = message().to_wire()
    mutate(wire)

    with pytest.raises(PeerMessageError, match=error):
        PeerMessage.from_wire(wire)


def test_a_non_object_envelope_is_refused() -> None:
    with pytest.raises(PeerMessageError, match="must be an object"):
        PeerMessage.from_wire(["m-1"])


def test_the_model_refuses_what_the_wire_would() -> None:
    with pytest.raises(PeerMessageError, match="needs a message_id"):
        message(message_id=" ")
    with pytest.raises(PeerMessageError, match="needs content"):
        message(content="")
    with pytest.raises(PeerMessageError, match="timezone-aware"):
        message(sent_at=datetime(2026, 11, 8, 12, 0))
    with pytest.raises(PeerMessageError, match="session address needs an id"):
        PeerAddress(PeerKind.SESSION, "")
    with pytest.raises(PeerMessageError, match="room reply route needs a target"):
        ReplyRoute(ReplyKind.ROOM, "")


def test_the_harness_reads_who_sent_it_when_and_how_to_answer() -> None:
    text = render_for_harness(message())

    assert text.splitlines()[0] == (
        "[Message m-1 from coder (session s-coder on valhalla), sent 2026-11-08T12:00:00+00:00]"
    )
    assert "Can you look at the retry path?" in text
    assert f'`{REPLY_TOOL}` tool, message_id "m-1"' in text
    assert "does not reach the sender" in text


def test_the_header_names_the_room_the_relay_and_the_message_answered() -> None:
    text = render_for_harness(
        message(
            room_id="room-7",
            relayed_by=PeerAddress(PeerKind.SESSION, "s-host"),
            in_reply_to="m-0",
            sender=PeerAddress(PeerKind.PERSON, "u-1"),
        )
    )

    assert "from person u-1," in text
    assert "[Said in room room-7]" in text
    assert "[Relayed by session s-host]" in text
    assert "[In reply to your message m-0]" in text


def test_a_notice_says_no_reply_is_taken() -> None:
    text = render_for_harness(message(reply=None))

    assert "takes no reply" in text
    assert f"`{REPLY_TOOL}` tool" not in text


async def test_a_host_without_an_inbox_refuses_to_look_up_a_message() -> None:
    class Bare(ForgeMcpHost):
        session_id = None

        async def environment(self):
            return {}

        async def notify(self, draft):
            return {}

    with pytest.raises(ForgeApiError, match="only that session's broker"):
        await Bare().received_peer_message("m-1")

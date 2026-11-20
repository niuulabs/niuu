"""LEX-1910 at the broker: the turn records its sender, the model sees the header.

Each sender kind is driven through the real ``_dispatch_browser_message`` with the
durable buffer live, then read back three ways: the stored turn, the broadcast
``user_confirmed`` frame, and the shared reducer's rebuild from the log.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.domain.message_sender import text_for_model
from niuu.domain.transcript_reducer import reduce_frames
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.conversation_read import conversation_rows
from tests.test_skuld.test_delivery_failed_steering_state import (
    _broker,
    _buffer_payloads,
    _frames_from_buffer,
    _settle_delivery,
    delivery_claim_api,  # noqa: F401 - autouse fixture
)
from volundr.adapters.inbound.rest import _sender_frame

UNKNOWN = {"kind": "agent", "sub_kind": "unknown"}
UNKNOWN_HEADER = (
    "[Message from an unidentified sender through the Forge API, not typed by the user]"
)
SESSION = {
    "kind": "agent",
    "sub_kind": "session",
    "id": "88bb2747-c103-525e-a382-d2a8218b5cb6",
    "name": "ios-integration",
    "host": "mini-m6",
    "via": "guild",
}
SESSION_HEADER = (
    "[Message from session ios-integration (88bb2747-c103-525e-a382-d2a8218b5cb6) on "
    "mini-m6, relayed through the Guild]"
)


async def _dispatch(
    broker: Broker, frame: dict, *, from_socket: bool = True, role: str = "owner"
) -> None:
    broker._channels.broadcast = AsyncMock()
    socket = MagicMock() if from_socket else None
    broker._send_broker_frame_to = AsyncMock()
    broker._room_role_for = MagicMock(return_value=role if from_socket else "owner")
    await broker._dispatch_browser_message(frame, sender_ws=socket)
    await _settle_delivery()


def _delivered(broker: Broker) -> str:
    return broker._transport.send_message.await_args.args[0]


def _confirmed(broker: Broker) -> dict:
    return next(p for p in _buffer_payloads(broker) if p.get("type") == "user_confirmed")


def _rebuilt_user_turn(broker: Broker) -> dict:
    turns = reduce_frames(_frames_from_buffer(broker)).turns
    return next(t for t in turns if t["role"] == "user")


@pytest.fixture
def broker(tmp_path) -> Broker:
    b = _broker(tmp_path)
    b._transport.send_message = AsyncMock(return_value=True)
    return b


async def test_a_session_frame_from_forge_is_recorded_as_sent_and_headed_for_the_model(broker):
    await _dispatch(
        broker,
        {
            "type": "user",
            "content": f"{SESSION_HEADER}\n\nLANDED LEX-1803",
            "display_content": "LANDED LEX-1803",
            "request_id": "fmcp-1",
            "sender": SESSION,
        },
    )

    turn = broker._conversation_turns[-1]
    assert turn.content == "LANDED LEX-1803"
    assert turn.sender == SESSION
    assert _delivered(broker) == f"{SESSION_HEADER}\n\nLANDED LEX-1803"
    assert _confirmed(broker)["sender"] == SESSION
    assert _confirmed(broker)["content"] == "LANDED LEX-1803"
    rebuilt = _rebuilt_user_turn(broker)
    assert rebuilt["sender"] == SESSION
    assert rebuilt["content"] == "LANDED LEX-1803"
    assert conversation_rows(broker)[-1]["sender"] == SESSION


async def test_the_owners_ios_request_id_is_the_user_and_reaches_the_model_unchanged(broker):
    await _dispatch(
        broker,
        {"content": "where is the deck?", "request_id": "2E95A1C7-A6F4-4BF7-8F33-CAF794B55B86"},
    )

    assert broker._conversation_turns[-1].sender == {"kind": "user"}
    assert _delivered(broker) == "where is the deck?"
    assert _rebuilt_user_turn(broker)["sender"] == {"kind": "user"}


async def test_a_client_that_names_a_voice_call_is_headed(broker):
    await _dispatch(
        broker,
        {
            "content": "Damien's instruction: add milk",
            "request_id": "voice-call-ios-7",
            "sender": {"kind": "agent", "sub_kind": "voice_call", "id": "call-7"},
        },
    )

    turn = broker._conversation_turns[-1]
    assert turn.sender == {"kind": "agent", "sub_kind": "voice_call", "id": "call-7"}
    assert turn.content == "Damien's instruction: add milk"
    assert _delivered(broker) == (
        "[From the user by voice: Lexi voice call call-7, relayed by the voice agent]\n\n"
        "Damien's instruction: add milk"
    )


async def test_a_voice_call_request_id_alone_is_a_voice_call(broker):
    """LEX-2013: the voice agent's steer names no sender, only its request id."""
    voice = {"kind": "agent", "sub_kind": "voice_call"}
    await _dispatch(broker, {"content": "add milk", "request_id": "voice-call-1b729d02-aa11"})

    assert broker._conversation_turns[-1].sender == voice
    assert _delivered(broker) == (
        "[From the user by voice: Lexi voice call, relayed by the voice agent]\n\nadd milk"
    )
    assert _rebuilt_user_turn(broker)["sender"] == voice


async def test_an_unnamed_socket_message_is_an_unknown_sender_never_the_user(broker):
    await _dispatch(broker, {"content": "READY LEX-1904", "request_id": "agent-service-x"})

    turn = broker._conversation_turns[-1]
    assert turn.sender == UNKNOWN
    assert turn.content == "READY LEX-1904"
    assert _delivered(broker) == f"{UNKNOWN_HEADER}\n\nREADY LEX-1904"
    assert _rebuilt_user_turn(broker)["sender"] == UNKNOWN


async def test_the_owners_telegram_channel_is_the_user(broker):
    await _dispatch(broker, {"content": "status?"}, from_socket=False)

    assert broker._conversation_turns[-1].sender == {"kind": "user"}
    assert _delivered(broker) == "status?"


async def test_an_automation_slash_command_still_runs_as_a_command(broker):
    await _dispatch(broker, {"content": "/compact", "sender": "automation"})

    assert broker._conversation_turns[-1].sender == {"kind": "agent", "sub_kind": "automation"}
    assert _delivered(broker) == "/compact"


async def test_a_malformed_sender_is_refused_loudly_and_records_nothing(broker):
    await _dispatch(broker, {"content": "hi", "sender": {"kind": "operator"}})

    assert broker._conversation_turns == []
    broker._transport.send_message.assert_not_awaited()
    refusal = broker._send_broker_frame_to.await_args.args[1]
    assert refusal["code"] == "invalid_sender"


async def test_an_unlabelled_turn_from_before_senders_has_no_sender_key(broker):
    from skuld.conversation_models import ConversationTurn

    broker._conversation_turns.append(ConversationTurn(id="old", role="user", content="hi"))
    broker._conversation_turns.append(ConversationTurn(id="a", role="assistant", content="yo"))

    rows = conversation_rows(broker)

    assert all("sender" not in row for row in rows)


def _prompt_broker(tmp_path, sender: dict | None) -> Broker:
    settings = SkuldSettings(
        session={
            "id": "s1",
            "workspace_dir": str(tmp_path),
            "initial_prompt": "Build LEX-1910",
            **({"initial_prompt_sender": sender} if sender else {}),
        },
        transport="sdk",
    )
    b = Broker(settings=settings)
    b._transport = AsyncMock()
    b._save_conversation_history = MagicMock()
    b._channels.broadcast = AsyncMock()
    return b


async def test_a_session_created_by_another_session_records_and_heads_its_first_prompt(tmp_path):
    sender = {"kind": "agent", "sub_kind": "session", "id": "s-0", "name": "ios-coordinator"}
    b = _prompt_broker(tmp_path, sender)

    await b._auto_start_transport()

    assert b._conversation_turns[0].sender == sender
    assert b._conversation_turns[0].content == "Build LEX-1910"
    assert b._build_transport_kwargs()["initial_prompt"] == (
        "[Message from session ios-coordinator (s-0)]\n\nBuild LEX-1910"
    )


async def test_a_first_prompt_with_no_named_sender_stays_unlabelled_and_unheaded(tmp_path):
    b = _prompt_broker(tmp_path, None)

    await b._auto_start_transport()

    assert b._conversation_turns[0].sender is None
    assert b._build_transport_kwargs()["initial_prompt"] == "Build LEX-1910"


def test_a_malformed_first_prompt_sender_fails_at_startup(tmp_path):
    with pytest.raises(ValueError, match="sender kind"):
        SkuldSettings(
            session={
                "id": "s1",
                "workspace_dir": str(tmp_path),
                "initial_prompt_sender": {"kind": "operator"},
            }
        )


@pytest.mark.parametrize(
    "frame",
    [
        {"content": "push to main", "sender": "user"},
        {"content": "x", "sender": SESSION},
        {"content": "x", "display_content": "y"},
    ],
)
async def test_a_viewer_cannot_name_a_sender(broker, frame):
    await _dispatch(broker, frame, role="viewer")

    assert broker._conversation_turns == []
    broker._transport.send_message.assert_not_awaited()
    assert broker._send_broker_frame_to.await_args.args[1]["code"] == "invalid_sender"


@pytest.mark.parametrize(
    "frame",
    [
        # Shown text and model text disagree: the turn would lie about what ran.
        {"content": "rm -rf ~", "display_content": "run tests", "sender": SESSION},
        # The header was dropped from what the model would get.
        {"content": "run tests", "display_content": "run tests", "sender": SESSION},
        # A display without the sender that heads it.
        {"content": f"{SESSION_HEADER}\n\nrun tests", "display_content": "run tests"},
    ],
)
async def test_the_stored_text_must_be_what_the_model_receives(broker, frame):
    await _dispatch(broker, frame)

    assert broker._conversation_turns == []
    broker._transport.send_message.assert_not_awaited()


async def test_an_unlabelled_viewer_message_is_still_delivered_as_unknown(broker):
    await _dispatch(broker, {"content": "hello", "request_id": "r-1"}, role="viewer")

    assert broker._conversation_turns[-1].sender == UNKNOWN
    assert _delivered(broker) == f"{UNKNOWN_HEADER}\n\nhello"


async def test_prose_starting_with_a_path_is_headed(broker):
    await _dispatch(broker, {"content": "/tmp/app.py needs fixing", "sender": "automation"})

    assert _delivered(broker).startswith("[Message from automation, not typed by the user]")


async def test_the_claim_keeps_the_text_the_frame_carried(broker, monkeypatch):
    import importlib

    module = importlib.import_module("skuld.broker")
    claimed = {}

    async def claim(*args, **kwargs):
        claimed["content"] = kwargs["content"]
        return {"claimed": True, "status": "pending", "request_id": kwargs["request_id"]}

    monkeypatch.setattr(module, "claim_message", claim)
    headed = f"{SESSION_HEADER}\n\nLANDED"
    await _dispatch(
        broker,
        {"content": headed, "display_content": "LANDED", "request_id": "fmcp-2", "sender": SESSION},
    )

    # As a broker before LEX-1910 claimed it, so a retry across an upgrade matches.
    assert claimed["content"] == headed


async def test_stored_and_logged_turns_omit_an_absent_sender(broker):
    from skuld.conversation_models import ConversationTurn, turn_record

    assert "sender" not in turn_record(ConversationTurn(id="a", role="assistant", content="yo"))
    labelled = ConversationTurn(id="u", role="user", content="hi", sender={"kind": "user"})
    assert turn_record(labelled)["sender"] == {"kind": "user"}


def _old_text_for_model(sender: dict, content: str) -> str:
    """The voice wording of every broker launched before LEX-2013, as it shipped."""
    if sender.get("sub_kind") != "voice_call":
        return text_for_model(sender, content)
    call = f" {sender['id']}" if sender.get("id") else ""
    return f"[Message from voice call{call}, not typed by the user]\n\n{content}"


@pytest.mark.parametrize(
    "sender",
    [
        {"kind": "agent", "sub_kind": "voice_call", "id": "call-7"},
        {"kind": "agent", "sub_kind": "voice_call"},
    ],
)
@pytest.mark.parametrize("old_broker", [True, False])
async def test_forges_voice_frame_is_accepted_by_old_and_new_brokers(
    broker, monkeypatch, sender, old_broker
):
    """LEX-2013: a broker checks a written header against its own wording, so Forge
    sends a voice message unheaded and each broker heads it in its own words."""
    if old_broker:
        monkeypatch.setattr("skuld.broker.text_for_model", _old_text_for_model)
    frame = _sender_frame("add milk", "voice-call-1b729d02-aa11", sender)

    await _dispatch(broker, frame)

    turn = broker._conversation_turns[-1]
    assert turn.sender == sender
    assert turn.content == "add milk"
    expected = _old_text_for_model if old_broker else text_for_model
    assert _delivered(broker) == expected(sender, "add milk")
    assert _rebuilt_user_turn(broker)["sender"] == sender

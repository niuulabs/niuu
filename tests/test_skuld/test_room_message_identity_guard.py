"""A viewer/approver posting through /api/room/message or /api/room/direct
must not be able to choose which room participant a message is attributed
to, or spoof its "source" — see skuld.broker_api._room_identity_fields.

Confused-deputy scenario this guards against: a viewer names another
participant's id (e.g. a Ravn peer's) as body.participant_id, and the
broker's own credentials record the message as if that peer said it, into
the transcript and any huddle log. The owner keeps full control of both
fields, since nothing here narrows what the owner's OWN client can already
do with the broker's credentials.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from skuld.broker_api import (
    _DirectedRoomMessageRequest,
    _RoomMessageRequest,
    send_directed_room_message,
    send_room_message,
)


def _request(room_role: str | None):
    headers = {}
    if room_role is not None:
        headers["x-niuu-room-role"] = room_role
    return SimpleNamespace(headers=headers)


def _fake_broker(monkeypatch, **overrides):
    attrs = {
        "handle_human_room_message": AsyncMock(return_value="msg-1"),
        "handle_directed_room_message": AsyncMock(return_value="msg-2"),
        "_reply_context_consumption_allowed": lambda *_a, **_kw: True,
        # "proxy": these tests use a header-less fake request with no
        # .client at all to mean "no verified role" -> viewer, matching the
        # process-backend/session-proxy topology these routes are reached
        # through in practice. K8s ("deployment") is covered separately in
        # test_enforce_room_role_middleware.py.
        "_settings": SimpleNamespace(ws_auth=SimpleNamespace(room_role_source="proxy")),
    }
    attrs.update(overrides)
    fake = SimpleNamespace(**attrs)
    monkeypatch.setattr("skuld.broker_api.broker", fake)
    return fake


@pytest.mark.parametrize("role", ["viewer", "approver", None, "", "not-a-real-role"])
async def test_sub_owner_room_message_ignores_client_participant_id_and_source(role, monkeypatch):
    fake = _fake_broker(monkeypatch)
    body = _RoomMessageRequest(
        content="hello",
        source="telegram",
        participant_id="some-ravn-peer",
    )
    await send_room_message(_request(role), body)
    kwargs = fake.handle_human_room_message.await_args.kwargs
    assert kwargs["participant_id"] is None
    assert kwargs["source"] != "telegram"
    assert kwargs["source"].startswith("room_")


async def test_owner_room_message_keeps_client_participant_id_and_source(monkeypatch):
    fake = _fake_broker(monkeypatch)
    body = _RoomMessageRequest(
        content="hello",
        source="telegram",
        participant_id="some-ravn-peer",
    )
    await send_room_message(_request("owner"), body)
    kwargs = fake.handle_human_room_message.await_args.kwargs
    assert kwargs["participant_id"] == "some-ravn-peer"
    assert kwargs["source"] == "telegram"


@pytest.mark.parametrize("role", ["viewer", "approver", None])
async def test_sub_owner_room_direct_ignores_client_participant_id_and_source(role, monkeypatch):
    fake = _fake_broker(monkeypatch)
    body = _DirectedRoomMessageRequest(
        target_peer_id="peer-1",
        content="hello",
        source="telegram",
        participant_id="some-ravn-peer",
    )
    await send_directed_room_message(_request(role), body)
    kwargs = fake.handle_directed_room_message.await_args.kwargs
    assert kwargs["source"] != "telegram"
    assert "participant_id" not in kwargs["metadata"]


async def test_owner_room_direct_keeps_client_participant_id_and_source(monkeypatch):
    fake = _fake_broker(monkeypatch)
    body = _DirectedRoomMessageRequest(
        target_peer_id="peer-1",
        content="hello",
        source="telegram",
        participant_id="some-ravn-peer",
    )
    await send_directed_room_message(_request("owner"), body)
    kwargs = fake.handle_directed_room_message.await_args.kwargs
    assert kwargs["source"] == "telegram"
    assert kwargs["metadata"]["participant_id"] == "some-ravn-peer"


async def test_room_direct_denies_when_target_has_pending_reply_and_role_is_below_approver(
    monkeypatch,
):
    fake = _fake_broker(monkeypatch, _reply_context_consumption_allowed=lambda *_a, **_kw: False)
    body = _DirectedRoomMessageRequest(target_peer_id="peer-1", content="hello")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await send_directed_room_message(_request("viewer"), body)
    assert exc_info.value.status_code == 403
    fake.handle_directed_room_message.assert_not_awaited()

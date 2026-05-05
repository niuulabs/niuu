from __future__ import annotations

from uuid import uuid4

import httpx
import respx

from volundr.adapters.outbound.skuld_room import SkuldRoomAdapter
from volundr.domain.models import GitSource, Session


class _FakeSessionRepository:
    def __init__(self, session: Session | None):
        self._session = session

    async def get(self, session_id):
        if self._session and self._session.id == session_id:
            return self._session
        return None


def _session() -> Session:
    sid = uuid4()
    return Session(
        id=sid,
        name="session",
        model="model",
        source=GitSource(),
        chat_endpoint=f"ws://127.0.0.1:8080/s/{sid}/session",
    )


@respx.mock
async def test_send_room_message_posts_to_internal_skuld_endpoint():
    session = _session()
    route = respx.post(f"http://127.0.0.1:8080/s/{session.id}/api/room/message").mock(
        return_value=httpx.Response(200, json={"status": "sent"})
    )
    adapter = SkuldRoomAdapter(_FakeSessionRepository(session))

    await adapter.send_room_message(
        session.id,
        "hello from telegram",
        source="telegram",
        metadata={"thread_id": "5"},
    )

    assert route.called
    assert route.calls[0].request.content


@respx.mock
async def test_send_directed_room_message_posts_to_internal_skuld_endpoint():
    session = _session()
    route = respx.post(f"http://127.0.0.1:8080/s/{session.id}/api/room/direct").mock(
        return_value=httpx.Response(200, json={"status": "sent"})
    )
    adapter = SkuldRoomAdapter(_FakeSessionRepository(session))

    await adapter.send_directed_room_message(
        session.id,
        "peer-coder",
        "check this",
        source="telegram",
    )

    assert route.called


@respx.mock
async def test_list_room_participants_uses_internal_skuld_endpoint():
    session = _session()
    respx.get(f"http://127.0.0.1:8080/s/{session.id}/api/room/participants").mock(
        return_value=httpx.Response(
            200,
            json={
                "participants": [
                    {
                        "peer_id": "peer-coder",
                        "persona": "coder",
                        "display_name": "Coder",
                        "participant_type": "ravn",
                        "status": "idle",
                    }
                ]
            },
        )
    )
    adapter = SkuldRoomAdapter(_FakeSessionRepository(session))

    participants = await adapter.list_room_participants(session.id)

    assert participants[0].peer_id == "peer-coder"
    assert participants[0].persona == "coder"


@respx.mock
async def test_list_communication_targets_uses_internal_skuld_endpoint():
    session = _session()
    respx.get(f"http://127.0.0.1:8080/s/{session.id}/api/communication/routes").mock(
        return_value=httpx.Response(
            200,
            json={
                "routes": [
                    {
                        "platform": "telegram",
                        "conversation_id": "-100123",
                        "thread_id": "7",
                        "mode": "room",
                        "metadata": {"topic_mode": "topic_per_session"},
                    }
                ]
            },
        )
    )
    adapter = SkuldRoomAdapter(_FakeSessionRepository(session))

    targets = await adapter.list_communication_targets(session.id)

    assert len(targets) == 1
    assert targets[0].platform.value == "telegram"
    assert targets[0].conversation_id == "-100123"
    assert targets[0].thread_id == "7"

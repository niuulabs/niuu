from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from volundr.domain.models import (
    CommunicationPlatform,
    CommunicationRoute,
    InboundCommunicationMessage,
    RoomParticipantInfo,
)
from volundr.domain.services.communication_ingress import CommunicationIngressService


@dataclass
class _FakeRouteRepository:
    route: CommunicationRoute | None = None

    async def upsert_route(self, route: CommunicationRoute) -> CommunicationRoute:
        self.route = route
        return route

    async def get_active_route(self, platform: str, conversation_id: str, thread_id: str | None):
        if self.route is None:
            return None
        if (
            self.route.platform.value == platform
            and self.route.conversation_id == conversation_id
            and self.route.thread_id == thread_id
            and self.route.active
        ):
            return self.route
        return None

    async def deactivate_routes_for_session(self, session_id):
        return 0

    async def list_routes_for_session(self, session_id):
        return []


class _FakeRoomPort:
    def __init__(self) -> None:
        self.sent_room_messages: list[tuple] = []
        self.sent_directed_messages: list[tuple] = []
        self.participants: list[RoomParticipantInfo] = []

    async def send_room_message(self, session_id, text, *, source: str, metadata=None) -> None:
        self.sent_room_messages.append((session_id, text, source, metadata))

    async def send_directed_room_message(
        self, session_id, target_peer_id, text, *, source: str, metadata=None
    ) -> None:
        self.sent_directed_messages.append((session_id, target_peer_id, text, source, metadata))

    async def list_room_participants(self, session_id):
        return list(self.participants)


def _route() -> CommunicationRoute:
    return CommunicationRoute(
        id=uuid4(),
        platform=CommunicationPlatform.TELEGRAM,
        conversation_id="chat-1",
        thread_id="5",
        session_id=uuid4(),
        owner_id="dev-user",
    )


async def test_handle_inbound_message_routes_plain_message():
    route = _route()
    repo = _FakeRouteRepository(route=route)
    room_port = _FakeRoomPort()
    svc = CommunicationIngressService(repo, room_port)

    result = await svc.handle_inbound_message(
        InboundCommunicationMessage(
            platform=CommunicationPlatform.TELEGRAM,
            conversation_id="chat-1",
            thread_id="5",
            sender_external_id="u1",
            sender_display_name="Jozef",
            text="hello team",
        )
    )

    assert result is not None
    assert result.directed is False
    assert room_port.sent_room_messages == [
        (
            route.session_id,
            "hello team",
            "telegram",
            {
                "source_platform": "telegram",
                "conversation_id": "chat-1",
                "thread_id": "5",
                "sender_external_id": "u1",
                "sender_display_name": "Jozef",
            },
        )
    ]


async def test_handle_inbound_message_routes_dynamic_target():
    route = _route()
    repo = _FakeRouteRepository(route=route)
    room_port = _FakeRoomPort()
    room_port.participants = [
        RoomParticipantInfo(peer_id="peer-coder", persona="coder", display_name="Coder"),
        RoomParticipantInfo(peer_id="peer-reviewer", persona="reviewer", display_name="Reviewer"),
    ]
    svc = CommunicationIngressService(repo, room_port)

    result = await svc.handle_inbound_message(
        InboundCommunicationMessage(
            platform=CommunicationPlatform.TELEGRAM,
            conversation_id="chat-1",
            thread_id="5",
            sender_external_id="u1",
            sender_display_name="Jozef",
            text="@reviewer please re-check the outcome",
        )
    )

    assert result is not None
    assert result.directed is True
    assert result.target_peer_id == "peer-reviewer"
    assert room_port.sent_directed_messages == [
        (
            route.session_id,
            "peer-reviewer",
            "please re-check the outcome",
            "telegram",
            {
                "source_platform": "telegram",
                "conversation_id": "chat-1",
                "thread_id": "5",
                "sender_external_id": "u1",
                "sender_display_name": "Jozef",
            },
        )
    ]


async def test_handle_inbound_message_routes_skuld_target_as_plain_room_message():
    route = _route()
    repo = _FakeRouteRepository(route=route)
    room_port = _FakeRoomPort()
    room_port.participants = [
        RoomParticipantInfo(
            peer_id="skuld-session",
            persona="Skuld",
            display_name="Skuld",
            participant_type="skuld",
        )
    ]
    svc = CommunicationIngressService(repo, room_port)

    result = await svc.handle_inbound_message(
        InboundCommunicationMessage(
            platform=CommunicationPlatform.TELEGRAM,
            conversation_id="chat-1",
            thread_id="5",
            sender_external_id="u1",
            sender_display_name="Jozef",
            text="@Skuld can you summarize the participants?",
        )
    )

    assert result is not None
    assert result.directed is False
    assert room_port.sent_room_messages == [
        (
            route.session_id,
            "can you summarize the participants?",
            "telegram",
            {
                "source_platform": "telegram",
                "conversation_id": "chat-1",
                "thread_id": "5",
                "sender_external_id": "u1",
                "sender_display_name": "Jozef",
                "target_peer_id": "skuld-session",
                "target_persona": "Skuld",
            },
        )
    ]


async def test_handle_inbound_message_resolves_skuld_case_insensitively():
    route = _route()
    repo = _FakeRouteRepository(route=route)
    room_port = _FakeRoomPort()
    room_port.participants = [
        RoomParticipantInfo(
            peer_id="skuld-session",
            persona="Skuld",
            display_name="Skuld",
            participant_type="skuld",
        )
    ]
    svc = CommunicationIngressService(repo, room_port)

    result = await svc.handle_inbound_message(
        InboundCommunicationMessage(
            platform=CommunicationPlatform.TELEGRAM,
            conversation_id="chat-1",
            thread_id="5",
            sender_external_id="u1",
            sender_display_name="Jozef",
            text="@skuld can you summarize the participants?",
        )
    )

    assert result is not None
    assert result.directed is False
    assert room_port.sent_room_messages[0][1] == "can you summarize the participants?"


async def test_handle_inbound_message_resolves_ravn_case_insensitively():
    route = _route()
    repo = _FakeRouteRepository(route=route)
    room_port = _FakeRoomPort()
    room_port.participants = [
        RoomParticipantInfo(peer_id="peer-reviewer", persona="reviewer", display_name="Reviewer")
    ]
    svc = CommunicationIngressService(repo, room_port)

    result = await svc.handle_inbound_message(
        InboundCommunicationMessage(
            platform=CommunicationPlatform.TELEGRAM,
            conversation_id="chat-1",
            thread_id="5",
            sender_external_id="u1",
            sender_display_name="Jozef",
            text="@Reviewer please re-check the outcome",
        )
    )

    assert result is not None
    assert result.directed is True
    assert result.target_peer_id == "peer-reviewer"
    assert room_port.sent_directed_messages[0][2] == "please re-check the outcome"


async def test_handle_inbound_message_returns_none_when_route_missing():
    svc = CommunicationIngressService(_FakeRouteRepository(route=None), _FakeRoomPort())

    result = await svc.handle_inbound_message(
        InboundCommunicationMessage(
            platform=CommunicationPlatform.TELEGRAM,
            conversation_id="chat-x",
            thread_id="9",
            sender_external_id="u1",
            sender_display_name="Jozef",
            text="hello?",
        )
    )

    assert result is None

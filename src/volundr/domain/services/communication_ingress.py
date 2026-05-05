"""Route inbound communication messages into active flock sessions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

from volundr.domain.models import InboundCommunicationMessage, RoomParticipantInfo
from volundr.domain.ports import CommunicationRouteRepository, SessionRoomPort

_TARGET_RE = re.compile(r"^\s*@([^\s@]+)\s*(.*)$", re.DOTALL)


class MentionMatch(NamedTuple):
    participant: RoomParticipantInfo
    content: str


@dataclass(frozen=True)
class CommunicationDispatchResult:
    """Outcome of handling an inbound communication message."""

    session_id: str
    route_found: bool
    directed: bool = False
    target_peer_id: str | None = None


class CommunicationIngressService:
    """Resolve external thread routes and forward messages into live session rooms."""

    def __init__(
        self,
        route_repository: CommunicationRouteRepository,
        room_port: SessionRoomPort,
    ) -> None:
        self._route_repository = route_repository
        self._room_port = room_port

    async def handle_inbound_message(
        self,
        message: InboundCommunicationMessage,
    ) -> CommunicationDispatchResult | None:
        route = await self._route_repository.get_active_route(
            message.platform.value,
            message.conversation_id,
            message.thread_id,
        )
        if route is None:
            return None

        match = await self._resolve_target(route.session_id, message.text)
        metadata = {
            "source_platform": message.platform.value,
            "conversation_id": message.conversation_id,
            "thread_id": message.thread_id,
            "sender_external_id": message.sender_external_id,
            "sender_display_name": message.sender_display_name,
        }
        if message.raw:
            metadata["raw"] = message.raw

        if match is None:
            await self._room_port.send_room_message(
                route.session_id,
                message.text,
                source=message.platform.value,
                metadata=metadata,
            )
            return CommunicationDispatchResult(
                session_id=str(route.session_id),
                route_found=True,
            )

        if match.participant.participant_type == "skuld":
            await self._room_port.send_room_message(
                route.session_id,
                match.content,
                source=message.platform.value,
                metadata={
                    **metadata,
                    "target_peer_id": match.participant.peer_id,
                    "target_persona": match.participant.persona,
                },
            )
            return CommunicationDispatchResult(
                session_id=str(route.session_id),
                route_found=True,
            )

        await self._room_port.send_directed_room_message(
            route.session_id,
            match.participant.peer_id,
            match.content,
            source=message.platform.value,
            metadata=metadata,
        )
        return CommunicationDispatchResult(
            session_id=str(route.session_id),
            route_found=True,
            directed=True,
            target_peer_id=match.participant.peer_id,
        )

    async def _resolve_target(
        self,
        session_id,
        text: str,
    ) -> MentionMatch | None:
        match = _TARGET_RE.match(text or "")
        if match is None:
            return None

        target = (match.group(1) or "").strip().lower()
        content = (match.group(2) or "").strip()
        if not target or not content:
            return None

        participants = await self._room_port.list_room_participants(session_id)
        by_key: dict[str, RoomParticipantInfo] = {}
        for participant in participants:
            keys = {
                participant.peer_id.lower(),
                participant.persona.lower(),
            }
            if participant.display_name:
                keys.add(participant.display_name.lower())
            for key in keys:
                by_key.setdefault(key, participant)

        participant = by_key.get(target)
        if participant is None:
            return None
        return MentionMatch(participant=participant, content=content)

"""Session room adapter for forwarding messages into a live Skuld room."""

from __future__ import annotations

from uuid import UUID

import httpx

from volundr.domain.models import (
    CommunicationPlatform,
    CommunicationRouteMode,
    RoomParticipantInfo,
    SessionCommunicationTarget,
)
from volundr.domain.ports import SessionCommunicationPort, SessionRepository, SessionRoomPort


class SkuldRoomAdapter(SessionRoomPort, SessionCommunicationPort):
    """Use the live session proxy to talk to internal Skuld room endpoints."""

    def __init__(
        self,
        session_repository: SessionRepository,
        *,
        timeout: float = 10.0,
    ) -> None:
        self._session_repository = session_repository
        self._timeout = timeout

    async def send_room_message(
        self,
        session_id: UUID,
        text: str,
        *,
        source: str,
        metadata: dict | None = None,
    ) -> None:
        await self._post(
            session_id,
            "/api/room/message",
            {
                "content": text,
                "source": source,
                "metadata": metadata or {},
            },
        )

    async def send_directed_room_message(
        self,
        session_id: UUID,
        target_peer_id: str,
        text: str,
        *,
        source: str,
        metadata: dict | None = None,
    ) -> None:
        await self._post(
            session_id,
            "/api/room/direct",
            {
                "target_peer_id": target_peer_id,
                "content": text,
                "source": source,
                "metadata": metadata or {},
            },
        )

    async def list_room_participants(self, session_id: UUID) -> list[RoomParticipantInfo]:
        payload = await self._get(session_id, "/api/room/participants")
        participants = payload.get("participants", []) if isinstance(payload, dict) else []
        result: list[RoomParticipantInfo] = []
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            result.append(
                RoomParticipantInfo(
                    peer_id=str(participant.get("peer_id") or participant.get("peerId") or ""),
                    persona=str(participant.get("persona") or ""),
                    display_name=str(participant.get("display_name") or ""),
                    participant_type=str(participant.get("participant_type") or "ravn"),
                    status=str(participant.get("status") or "idle"),
                )
        )
        return [participant for participant in result if participant.peer_id]

    async def list_communication_targets(
        self,
        session_id: UUID,
    ) -> list[SessionCommunicationTarget]:
        payload = await self._get(session_id, "/api/communication/routes")
        routes = payload.get("routes", []) if isinstance(payload, dict) else []
        targets: list[SessionCommunicationTarget] = []
        for route in routes:
            if not isinstance(route, dict):
                continue
            platform = route.get("platform")
            conversation_id = route.get("conversation_id") or route.get("conversationId")
            if not platform or not conversation_id:
                continue
            try:
                targets.append(
                    SessionCommunicationTarget(
                        platform=CommunicationPlatform(str(platform)),
                        conversation_id=str(conversation_id),
                        thread_id=_normalize_optional_str(
                            route.get("thread_id", route.get("threadId"))
                        ),
                        mode=CommunicationRouteMode(
                            str(route.get("mode") or CommunicationRouteMode.ROOM.value)
                        ),
                        default_target=_normalize_optional_str(
                            route.get("default_target", route.get("defaultTarget"))
                        ),
                        metadata=dict(route.get("metadata") or {}),
                    )
                )
            except ValueError:
                continue
        return targets

    async def _post(self, session_id: UUID, suffix: str, payload: dict) -> dict:
        base_url = await self._resolve_base_url(session_id)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(f"{base_url}{suffix}", json=payload)
            response.raise_for_status()
            return response.json()

    async def _get(self, session_id: UUID, suffix: str) -> dict:
        base_url = await self._resolve_base_url(session_id)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{base_url}{suffix}")
            response.raise_for_status()
            return response.json()

    async def _resolve_base_url(self, session_id: UUID) -> str:
        session = await self._session_repository.get(session_id)
        if session is None:
            raise LookupError(f"Session not found: {session_id}")
        if not session.chat_endpoint:
            raise ValueError(f"Session {session_id} has no active endpoint")
        base_url = session.chat_endpoint.replace("wss://", "https://").replace("ws://", "http://")
        if base_url.endswith("/session"):
            base_url = base_url[: -len("/session")]
        return base_url


def _normalize_optional_str(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)

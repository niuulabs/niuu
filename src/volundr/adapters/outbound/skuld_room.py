"""Session room adapter for forwarding messages into a live Skuld room."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse
from uuid import UUID

import httpx

from volundr.domain.models import (
    CommunicationPlatform,
    CommunicationRouteMode,
    ConnectedPerson,
    RoomParticipantInfo,
    RoomRoster,
    SessionCommunicationTarget,
)
from volundr.domain.ports import SessionCommunicationPort, SessionRepository, SessionRoomPort

DEFAULT_OPENSHELL_INTERNAL_GATEWAY_URL = "http://openshell.openshell.svc.cluster.local:8080"
OPENSHELL_SERVICE_HOST_SUFFIX = ".openshell.localhost"


@dataclass(frozen=True)
class _SkuldRequestTarget:
    base_url: str
    headers: dict[str, str]


class SkuldRoomAdapter(SessionRoomPort, SessionCommunicationPort):
    """Use the live session proxy to talk to internal Skuld room endpoints."""

    def __init__(
        self,
        session_repository: SessionRepository,
        *,
        timeout: float = 10.0,
        openshell_internal_gateway_url: str | None = None,
        internal_base_url: str = "",
    ) -> None:
        self._session_repository = session_repository
        self._timeout = timeout
        self._internal_base_url = internal_base_url.rstrip("/")
        self._openshell_internal_gateway_url = (
            openshell_internal_gateway_url
            or os.environ.get("OPENSHELL_INTERNAL_GATEWAY_URL")
            or DEFAULT_OPENSHELL_INTERNAL_GATEWAY_URL
        ).rstrip("/")

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
        return _parse_participants(payload)

    async def read_room(self, session_id: UUID) -> RoomRoster:
        payload = await self._get(session_id, "/api/room/participants")
        if not isinstance(payload, dict) or "connected_people" not in payload:
            raise ValueError(
                f"The broker of session {session_id} reported no connected_people; "
                "it is older than this Forge. Restart the session on a current runtime image."
            )
        return RoomRoster(
            participants=_parse_participants(payload),
            connected_people=_parse_connected_people(payload),
        )

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
        target = await self._resolve_request_target(session_id)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{target.base_url}{suffix}",
                json=payload,
                headers=target.headers,
            )
            response.raise_for_status()
            return response.json()

    async def _get(self, session_id: UUID, suffix: str) -> dict:
        target = await self._resolve_request_target(session_id)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(f"{target.base_url}{suffix}", headers=target.headers)
            response.raise_for_status()
            return response.json()

    async def _resolve_request_target(self, session_id: UUID) -> _SkuldRequestTarget:
        session = await self._session_repository.get(session_id)
        if session is None:
            raise LookupError(f"Session not found: {session_id}")
        if not session.chat_endpoint:
            raise ValueError(f"Session {session_id} has no active endpoint")
        base_url = session.chat_endpoint.replace("wss://", "https://").replace("ws://", "http://")
        if base_url.endswith("/session"):
            base_url = base_url[: -len("/session")]
        parsed = urlparse(base_url)
        if _is_openshell_service_host(parsed.hostname):
            gateway = urlparse(self._openshell_internal_gateway_url)
            return _SkuldRequestTarget(
                base_url=urlunparse(
                    (
                        gateway.scheme or "http",
                        gateway.netloc,
                        parsed.path.rstrip("/"),
                        "",
                        "",
                        "",
                    )
                ),
                headers={"Host": parsed.netloc},
            )
        if self._internal_base_url:
            internal = urlparse(self._internal_base_url)
            base_url = urlunparse(
                (internal.scheme or "http", internal.netloc, parsed.path.rstrip("/"), "", "", "")
            )
        return _SkuldRequestTarget(base_url=base_url, headers={})


def _parse_participants(payload: dict) -> list[RoomParticipantInfo]:
    participants = payload.get("participants", []) if isinstance(payload, dict) else []
    result: list[RoomParticipantInfo] = []
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        participant_type = participant.get("participant_type")
        if not participant_type:
            raise ValueError(
                f"Room participant {participant.get('peer_id')!r} reported no "
                "participant_type; the session's broker is older than this Forge"
            )
        result.append(
            RoomParticipantInfo(
                peer_id=str(participant.get("peer_id") or participant.get("peerId") or ""),
                persona=str(participant.get("persona") or ""),
                display_name=str(participant.get("display_name") or ""),
                participant_type=str(participant_type),
                status=str(participant.get("status") or "idle"),
            )
        )
    return [participant for participant in result if participant.peer_id]


def _parse_connected_people(payload: dict) -> list[ConnectedPerson]:
    people: list[ConnectedPerson] = []
    for person in payload["connected_people"]:
        user_id = person.get("user_id") if isinstance(person, dict) else None
        if not user_id:
            raise ValueError(f"A connected person of the room has no user_id: {person!r}")
        people.append(
            ConnectedPerson(
                user_id=str(user_id), display_name=str(person.get("display_name") or "")
            )
        )
    return people


def _normalize_optional_str(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _is_openshell_service_host(hostname: str | None) -> bool:
    return bool(hostname and hostname.endswith(OPENSHELL_SERVICE_HOST_SUFFIX))

"""Copy a voice call's speech into the resident's room transcript.

The call lives in the voice store; its words also belong in the resident's room,
so chat, other channels and the resident itself see what was said. Each finished
utterance is posted to the room's Skuld broker as commentary
(``deliver_to_transport: false``): it is recorded in the transcript and starts no
turn. Agent speech is recorded under the resident's own participant; user speech
under the operator, stamped ``source: voice``.
"""

from __future__ import annotations

from typing import Any

import httpx

_ROOM_MESSAGE_PATH = "/api/room/message"
#: Longest slice of a broker error body quoted in an exception.
_BODY_EXCERPT_LIMIT = 300


class RoomCopyError(RuntimeError):
    """The room broker did not record the utterance."""


class VoiceRoomCopier:
    """Post finished utterances into the room as commentary."""

    def __init__(
        self,
        *,
        broker_url: str,
        participant_id: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not broker_url:
            raise ValueError("Voice room copy needs the room broker URL")
        if not participant_id:
            raise ValueError(
                "Voice room copy needs room_participant_id: the resident's room participant"
            )
        self._url = broker_url.rstrip("/") + _ROOM_MESSAGE_PATH
        self._participant_id = participant_id
        self._client = httpx.AsyncClient(timeout=timeout_seconds, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def copy(self, *, call_id: str, speaker: str, text: str) -> None:
        """Record one utterance; *speaker* is ``user`` or ``agent``."""
        if speaker not in ("user", "agent"):
            raise ValueError(f"Unknown voice speaker: {speaker}")
        body: dict[str, Any] = {
            "content": text,
            "source": "voice",
            "metadata": {"channel": "voice", "call_id": call_id, "speaker": speaker},
            "deliver_to_transport": False,
        }
        if speaker == "agent":
            body["participant_id"] = self._participant_id
        try:
            response = await self._client.post(self._url, json=body)
        except httpx.HTTPError as exc:
            raise RoomCopyError(f"Room broker unreachable for voice copy: {exc}") from exc
        if response.status_code >= 400:
            raise RoomCopyError(
                f"Room broker refused voice copy (HTTP {response.status_code}): "
                f"{response.text[:_BODY_EXCERPT_LIMIT]}"
            )

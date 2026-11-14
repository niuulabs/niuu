"""The broker's hosted room, as a voice call sees it.

:class:`BrokerVoiceRoom` implements :class:`~skuld.voice.room_port.VoiceRoomPort` over a
broker that hosts a routed room and runs no agent. The roster is the team's members as
Forge launched them (``voice.members``: name, persona, lead, voices) joined with who is
seated now; a workflow run's room, which has no such list, is its seated agents. Posts are
the room's kept turns. Saying something goes through the room's own directed delivery,
authored as the person, so each agent records it as that person's message; "everyone" is
one directed message per seated agent, as a team's room delivers an untargeted message.
The broker tells the adapter of every new turn and every question an agent asks.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from niuu.domain.message_author import MessageAuthor
from skuld.conversation_models import ConversationTurn
from skuld.voice.broker_session import VOICE_CALL_REQUEST_PREFIX, VoiceRecordError
from skuld.voice.room_port import (
    PostKind,
    RoomKind,
    RoomMember,
    RoomPost,
    RoomVoiceStatus,
    VoiceRoomPort,
)
from skuld.voice.session_port import SendOutcome, SendStatus

logger = logging.getLogger("skuld.voice")

#: Room message types that are context, never an agent's answer.
_NOTICE_TYPES = frozenset({"notice", "room_notice", "mesh_message", "delegation", "outcome"})


class BrokerVoiceRoom(VoiceRoomPort):
    """The room this broker hosts."""

    def __init__(self, broker: Any) -> None:
        self._broker = broker
        self._listener: Callable[[RoomPost], None] | None = None

    # -- the room ----------------------------------------------------------

    def _seated(self) -> dict[str, Any]:
        """Seated agents by their name in the room."""
        broker = self._broker
        bridge = broker._room_bridge
        if bridge is None:
            return {}
        agents = set(broker._room_agent_peer_ids())
        return {
            _participant_name(p): p for p in bridge.participants.values() if p.peer_id in agents
        }

    def status(self) -> RoomVoiceStatus:
        settings = self._broker._settings
        seated = self._seated()
        members = [
            RoomMember(
                name=name,
                persona=config.persona,
                lead=config.lead,
                present=name in seated,
                voices=dict(config.voices),
            )
            for name, config in settings.voice.members.items()
        ]
        known = {member.name for member in members}
        members += [
            RoomMember(name=name, persona=str(p.persona or ""), present=True)
            for name, p in seated.items()
            if name not in known
        ]
        people = tuple(
            str(person.get("display_name") or person.get("user_id") or "")
            for person in self._broker.connected_people()
        )
        everyone = settings.room.untargeted_delivery == "everyone"
        return RoomVoiceStatus(
            room_id=settings.room.environment_id,
            name=settings.session.name,
            kind=RoomKind.TEAM if everyone else RoomKind.RUN,
            members=tuple(members),
            people_online=tuple(p for p in people if p),
        )

    def posts(self) -> list[RoomPost]:
        posts = [self._post(turn) for turn in self._broker._conversation_turns]
        return [post for post in posts if post is not None]

    def _post(self, turn: ConversationTurn) -> RoomPost | None:
        if turn.visibility != "public" or not (turn.content or "").strip():
            return None
        metadata = turn.metadata or {}
        if str(metadata.get("request_id") or "").startswith(VOICE_CALL_REQUEST_PREFIX):
            # What this call said: the call already knows it.
            return None
        if turn.participant_id:
            meta = turn.participant_meta or {}
            speaker = str(meta.get("display_name") or meta.get("persona") or turn.participant_id)
            return RoomPost(
                post_id=turn.id,
                speaker=speaker,
                kind=_agent_kind(metadata),
                text=turn.content,
                persona=str(meta.get("persona") or ""),
                created_at=turn.created_at,
            )
        if turn.role == "user" and turn.author:
            author = MessageAuthor.from_wire(turn.author)
            return RoomPost(
                post_id=turn.id,
                speaker=author.label if author is not None else "a person",
                kind=PostKind.PERSON,
                text=turn.content,
                created_at=turn.created_at,
            )
        return RoomPost(
            post_id=turn.id,
            speaker="room",
            kind=PostKind.NOTICE,
            text=turn.content,
            created_at=turn.created_at,
        )

    # -- saying ------------------------------------------------------------

    async def say_to(
        self, member: str, text: str, *, author: MessageAuthor | None, model_note: str
    ) -> SendOutcome:
        participant = self._seated().get(member)
        if participant is None:
            return SendOutcome(SendStatus.FAILED, f"{member} is not in the room right now")
        return await self._deliver(participant.peer_id, text, author, model_note)

    async def say_to_everyone(
        self, text: str, *, author: MessageAuthor | None, model_note: str
    ) -> SendOutcome:
        if self._broker._settings.room.untargeted_delivery != "everyone":
            return SendOutcome(
                SendStatus.FAILED, "this room takes messages for one agent at a time"
            )
        names = {p.peer_id: name for name, p in self._seated().items()}
        targets = self._broker._untargeted_room_peer_ids()
        if not targets:
            return SendOutcome(SendStatus.FAILED, "no agent is in the room right now")
        failures: list[str] = []
        for peer_id in targets:
            outcome = await self._deliver(peer_id, text, author, model_note)
            if outcome.status is not SendStatus.DELIVERED:
                failures.append(f"{names.get(peer_id, peer_id)} ({outcome.reason})")
        if not failures:
            return SendOutcome(SendStatus.DELIVERED)
        delivered = len(targets) - len(failures)
        return SendOutcome(
            SendStatus.FAILED,
            f"delivered to {delivered} of {len(targets)} agents; not to " + ", ".join(failures),
        )

    async def _deliver(
        self, peer_id: str, text: str, author: MessageAuthor | None, model_note: str
    ) -> SendOutcome:
        try:
            await self._broker.handle_directed_room_message(
                peer_id,
                text,
                source="voice",
                request_id=f"{VOICE_CALL_REQUEST_PREFIX}{uuid.uuid4().hex}",
                author=author,
                model_note=model_note,
            )
        except (LookupError, RuntimeError, ValueError) as exc:
            logger.error("Voice call could not reach room participant %s: %s", peer_id, exc)
            return SendOutcome(SendStatus.FAILED, str(exc))
        return SendOutcome(SendStatus.DELIVERED)

    # -- hearing -----------------------------------------------------------

    def listen(self, listener: Callable[[RoomPost], None] | None) -> None:
        self._listener = listener

    def observe_turn(self, turn: ConversationTurn) -> None:
        """The broker kept a turn: a new post when it is one."""
        if self._listener is None:
            return
        post = self._post(turn)
        if post is not None:
            self._listener(post)

    def observe_question(self, peer_id: str, frame: dict[str, Any]) -> None:
        """An agent asks the person something (``help_needed``)."""
        if self._listener is None:
            return
        bridge = self._broker._room_bridge
        participant = bridge.participants.get(peer_id) if bridge is not None else None
        text = str(frame.get("data") or frame.get("content") or "").strip()
        if not text:
            return
        self._listener(
            RoomPost(
                post_id=str(frame.get("id") or uuid.uuid4().hex),
                speaker=_participant_name(participant) if participant is not None else peer_id,
                kind=PostKind.QUESTION,
                text=text,
                persona=str(getattr(participant, "persona", "") or ""),
            )
        )

    def record(self, frame: dict) -> None:
        if self._broker._enqueue_event_log(frame) is None:
            raise VoiceRecordError(
                "the room host's durable event log is off, so a voice call cannot be "
                "recorded; voice.enabled needs event_log_enabled and volundr_api_url"
            )


def _participant_name(participant: Any) -> str:
    return str(participant.display_name or participant.persona or participant.peer_id)


def _agent_kind(metadata: dict[str, Any]) -> PostKind:
    message_type = str(metadata.get("messageType") or metadata.get("message_type") or "")
    if metadata.get("error") or message_type == "error":
        return PostKind.ERROR
    if message_type in ("question", "help_needed"):
        return PostKind.QUESTION
    if message_type in _NOTICE_TYPES:
        return PostKind.NOTICE
    return PostKind.ANSWER

"""The broker as the session a voice call is bound to, and the channel's composition.

:class:`BrokerVoiceSession` implements :class:`~skuld.voice.session_port.VoiceSessionPort`
over the broker: the session's state and turns as the broker holds them, a message sent
the way a person's message is (recorded as theirs, delivered with the same bounded
retry and acknowledgement), and call-record frames appended to the durable event log.

:func:`build_voice_channel` composes the channel from the broker's settings. Everything
is configured-or-fatal: a channel that is enabled but has no voice persona stops the
broker with the remedy, rather than answering calls it cannot complete.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from niuu.domain.message_author import MessageAuthor
from skuld.voice.calls import VoiceCallBook
from skuld.voice.channel import SessionVoiceChannel
from skuld.voice.minting import ForgeVoiceMinter
from skuld.voice.persona import VoicePromptComposer, resolve_voice_persona
from skuld.voice.session_binding import SessionVoiceBinding
from skuld.voice.session_port import (
    SendOutcome,
    SendStatus,
    SessionTurn,
    SessionVoiceStatus,
    VoiceSessionPort,
)
from skuld.voice.tools import SessionVoiceTools

logger = logging.getLogger("skuld.voice")

#: Request ids of messages a call sends start with this: the Lexi app shows such a
#: message in the transcript as sent from a voice call.
VOICE_CALL_REQUEST_PREFIX = "voice-call-"


class VoiceRecordError(RuntimeError):
    """A call-record frame could not be written to the session's event log."""


class BrokerVoiceSession(VoiceSessionPort):
    """The broker's own session, as a voice call sees it."""

    def __init__(self, broker: Any, *, send_confirm_seconds: float) -> None:
        self._broker = broker
        self._send_confirm_seconds = send_confirm_seconds

    def status(self) -> SessionVoiceStatus:
        broker = self._broker
        settings = broker._settings
        state = broker._activity_state
        return SessionVoiceStatus(
            session_id=str(broker.session_id),
            name=settings.session.name,
            model=str(broker.model),
            harness=settings.cli_type,
            activity_state=state,
            busy=broker._coarse_bucket(state) == "running",
            state_since=broker._activity_state_since,
            questions=tuple(_questions(broker._pending_ask_user_questions)),
        )

    def turns(self) -> list[SessionTurn]:
        turns: list[SessionTurn] = []
        for turn in self._broker._conversation_turns:
            if turn.role not in ("user", "assistant") or turn.visibility != "public":
                continue
            if not (turn.content or "").strip():
                continue
            author = MessageAuthor.from_wire(turn.author) if turn.author else None
            turns.append(
                SessionTurn(
                    role=turn.role,
                    text=turn.content,
                    created_at=turn.created_at,
                    author=author.label if author is not None else "",
                )
            )
        return turns

    async def send(self, text: str, *, author: MessageAuthor | None, call_id: str) -> SendOutcome:
        broker = self._broker
        msg_id = str(uuid.uuid4())
        request_id = f"{VOICE_CALL_REQUEST_PREFIX}{uuid.uuid4().hex}"
        try:
            delivery = await broker._submit_user_turn(
                text,
                msg_id=msg_id,
                request_id=request_id,
                source="voice",
                extra_metadata={"voice_call_id": call_id} if call_id else None,
                author=author,
            )
        except Exception as exc:
            logger.exception("Voice call could not send a message into the session")
            return SendOutcome(SendStatus.FAILED, f"the session did not accept it ({exc})")
        try:
            await asyncio.wait_for(asyncio.shield(delivery), timeout=self._send_confirm_seconds)
        except TimeoutError:
            if self._steering_state(msg_id) == "active":
                return self._delivered()
            return SendOutcome(SendStatus.QUEUED)
        except Exception as exc:
            logger.exception("Voice call message delivery raised")
            return SendOutcome(SendStatus.FAILED, f"its delivery failed ({exc})")
        if self._steering_state(msg_id) == "failed":
            return SendOutcome(
                SendStatus.FAILED,
                "the session's agent did not take it; the transcript shows it as failed",
            )
        return self._delivered()

    def _delivered(self) -> SendOutcome:
        return SendOutcome(
            SendStatus.DELIVERED,
            pending_questions=len(self._broker._pending_ask_user_questions),
        )

    def _steering_state(self, msg_id: str) -> str:
        for turn in reversed(self._broker._conversation_turns):
            if turn.id == msg_id:
                return str(turn.metadata.get("steering_state") or "")
        return ""

    def record(self, frame: dict[str, Any]) -> None:
        if self._broker._enqueue_event_log(frame) is None:
            raise VoiceRecordError(
                "the session's durable event log is off, so a voice call cannot be recorded; "
                "voice.enabled needs event_log_enabled and volundr_api_url"
            )


def _questions(pending: dict[str, dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for ask in pending.values():
        questions = ask.get("questions")
        if not isinstance(questions, list):
            continue
        for question in questions:
            if isinstance(question, dict) and str(question.get("question") or "").strip():
                texts.append(str(question["question"]).strip())
    return texts


def build_voice_channel(broker: Any) -> SessionVoiceChannel:
    """Compose the session's voice channel; raises with the remedy when it cannot serve."""
    settings = broker._settings
    config = settings.voice
    persona = resolve_voice_persona(
        settings.session.persona, config.default_persona, display_name=config.display_name
    )
    session = BrokerVoiceSession(broker, send_confirm_seconds=config.send_confirm_seconds)
    tools = SessionVoiceTools(
        session,
        tool_timeout_seconds=config.tool_timeout_seconds,
        full_read_max_chars=config.full_read_max_chars,
    )
    composer = VoicePromptComposer(persona, models=config.models, time_zone=config.time_zone)
    # The call is bound to this session. A team call on a room host binds to its room.
    binding = SessionVoiceBinding(
        session,
        tools,
        card_recent_turns=config.card_recent_turns,
        card_turn_max_chars=config.card_turn_max_chars,
    )
    channel = SessionVoiceChannel(
        binding=binding,
        composer=composer,
        calls=VoiceCallBook(session.record),
        minter=ForgeVoiceMinter(
            broker._get_session_http_client,
            session_id=str(broker.session_id),
            timeout_seconds=config.mint_timeout_seconds,
        ),
        owner_id=settings.session.owner_id,
        user_id_header=settings.ws_auth.user_id_header,
        display_name_header=settings.ws_auth.display_name_header,
    )
    logger.info("Voice channel on for session %s (persona %s)", broker.session_id, persona.name)
    return channel

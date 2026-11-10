"""Questions the session's agent puts to one named person, and their answers.

``ask_person`` rides the peer inbox that ``ask`` already uses (see
:mod:`skuld.peer_inbox` and :mod:`niuu.domain.person_question`): the question has a stable
message id, the answer is a peer message in reply to it, a tool call waiting for it holds it
back from the harness, and one nobody waited for is delivered at the next turn boundary. This
mixin adds the two things a person needs that an agent does not:

* the question is shown in the session's room, addressed to the person, as a durable
  conversation turn (``Asked Alice: ...``), once per question id. That turn is also the
  record of who was asked, which is why a restarted broker needs nothing else to know it;
* the answer arrives as a ``person_answer`` message from a browser connection, and is
  authorized here: only the addressee, or anyone holding the approver room role or above,
  may answer. The person who answered is the verified one behind that connection, never a
  name taken from the message.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from niuu.domain.message_author import AUTHOR_KEY, MessageAuthor
from niuu.domain.peer_message import PeerKind, PeerMessage, PeerMessageError
from niuu.domain.person_question import (
    PERSON_ANSWERED_FRAME,
    person_address,
    person_answer_id,
    session_address,
)
from niuu.forge_mcp.models import ForgeApiError
from skuld.control_errors import control_error_frame
from skuld.conversation_models import ConversationTurn
from skuld.forge_mcp.forge_client import HttpForgeClient
from skuld.peer_inbox import AnswerWait

logger = logging.getLogger("skuld.broker")

PERSON_QUESTION_TURN_PREFIX = "pq_"
#: The tool_use part the chat renders for a question put to a person; its input names who.
PERSON_QUESTION_TOOL_NAME = "ask_person"


@dataclass
class PersonAnswerWait(AnswerWait):
    """An ``ask_person`` call waiting for the answer to one question.

    The addressee or an approver may answer, so any person's reply to the question counts;
    who may answer was decided when the answer was accepted.
    """

    def matches(self, envelope: PeerMessage) -> bool:
        return envelope.in_reply_to == self.message_id and envelope.sender.kind is PeerKind.PERSON


@dataclass(frozen=True)
class PersonQuestion:
    """A question put to a person, as the session's conversation records it."""

    question_id: str
    addressee_id: str
    addressee_name: str
    question: str


class PersonQuestionMixin:
    """Broker behavior for questions put to a person."""

    # -- asking

    async def post_person_question(
        self, *, question_id: str, addressee: dict[str, Any], question: str
    ) -> None:
        """Show the question in the room, addressed to its person, once per question id.

        A repeat (the agent asked again with the same ``request_id`` to keep waiting) adds
        nothing: the room already shows it.
        """
        turn_id = f"{PERSON_QUESTION_TURN_PREFIX}{question_id}"
        if any(existing.id == turn_id for existing in self._conversation_turns):
            return
        user_id = str(addressee["user_id"])
        name = str(addressee.get("display_name") or "") or user_id
        turn = ConversationTurn(
            id=turn_id,
            role="assistant",
            content=f"Asked {name}: {question}",
            parts=[
                {
                    "type": "tool_use",
                    "id": question_id,
                    "name": PERSON_QUESTION_TOOL_NAME,
                    "input": {
                        "question_id": question_id,
                        "user_id": user_id,
                        "display_name": str(addressee.get("display_name") or ""),
                        "role": str(addressee.get("role") or ""),
                        "question": question,
                    },
                }
            ],
            metadata={"cost": None, "model": None, "usage": {}, "person_question": question_id},
        )
        # Same durable path as present-file: history + durable log first, then live.
        if self._append_turn(turn) is None:
            raise ForgeApiError("the durable log refused the person question turn", status=503)
        await self._channels.broadcast({"type": "conversation.turn", "turn": asdict(turn)})

    def person_question(self, question_id: str) -> PersonQuestion | None:
        """The question put to a person with this id, from the conversation that records it."""
        turn_id = f"{PERSON_QUESTION_TURN_PREFIX}{question_id}"
        turn = next((t for t in self._conversation_turns if t.id == turn_id), None)
        if turn is None:
            return None
        asked = turn.parts[0]["input"]
        return PersonQuestion(
            question_id=question_id,
            addressee_id=asked["user_id"],
            addressee_name=asked["display_name"] or asked["user_id"],
            question=asked["question"],
        )

    def answered_person_question_frames(self) -> list[dict[str, Any]]:
        """The ``person_answered`` frame of every question in this conversation that has an
        answer, for a browser that attaches after it was given.

        The answer lives in the peer inbox (rebuilt from the durable log on restart) and
        nowhere in the conversation's turns, so without this a late browser shows an
        answered question as still waiting.
        """
        session_id = str(self.session_id)
        frames: list[dict[str, Any]] = []
        for turn in self._conversation_turns:
            if not turn.id.startswith(PERSON_QUESTION_TURN_PREFIX):
                continue
            question = self.person_question(turn.id.removeprefix(PERSON_QUESTION_TURN_PREFIX))
            if question is None:
                continue
            entry = self._peer_inbox.get(person_answer_id(session_id, question.question_id))
            if entry is None:
                continue
            sender = entry.envelope.sender
            frames.append(
                {
                    "type": PERSON_ANSWERED_FRAME,
                    "question_id": question.question_id,
                    "message_id": entry.envelope.message_id,
                    "content": entry.envelope.content,
                    AUTHOR_KEY: MessageAuthor(
                        user_id=sender.id, display_name=sender.name
                    ).to_dict(),
                    "addressee": {
                        "user_id": question.addressee_id,
                        "display_name": question.addressee_name,
                    },
                }
            )
        return frames

    @asynccontextmanager
    async def awaiting_person_answer(self, question_id: str) -> AsyncIterator[PersonAnswerWait]:
        """Hold the answer to ``question_id`` back from the harness while the caller waits.

        Whatever the caller did not take is handed to the harness at the next boundary once
        the wait ends, however it ended.
        """
        wait = PersonAnswerWait(message_id=question_id, from_id="")
        self._peer_answer_waits[question_id] = wait
        try:
            yield wait
        finally:
            self._peer_answer_waits.pop(question_id, None)
            self._spawn_boundary_injection(f"peer-inbox-person-ended-{question_id}")

    # -- answering

    async def handle_person_answer(self, data: dict, sender_ws: Any, *, approver: bool) -> None:
        """A person answers a question put to a person; ``approver`` is the room role check.

        Refused, to the sender and with the reason, when the question is unknown, it is
        already answered, the sender has no verified identity, or is neither the addressee
        nor an approver. Forge is told first (it settles the addressee's notification); the
        answer is accepted only once that is recorded, so a failure at either step leaves
        the question open and the sender can try again.
        """
        question_id = str(data.get("question_id") or "")
        content = str(data.get("content") or "").strip()
        author = self._author_for(sender_ws)
        refusal = self._person_answer_refusal(question_id, content, author, approver)
        if refusal is not None:
            code, message = refusal
            await self._reject_person_answer(sender_ws, data, code, message)
            return
        question = self.person_question(question_id)
        if question is None or author is None:  # _person_answer_refusal ruled both out
            raise RuntimeError("person_answer passed its checks without a question and a person")
        answerer = author
        try:
            await self._settle_person_question(question_id, answerer)
        except ForgeApiError as exc:
            await self._reject_person_answer(
                sender_ws,
                data,
                "forge_unavailable",
                f"Forge could not record the answer ({exc.detail}); nothing was sent to the "
                "agent. Try again.",
            )
            return
        session_id = str(self.session_id)
        envelope = PeerMessage(
            message_id=person_answer_id(session_id, question_id),
            sender=person_address(answerer.user_id, name=answerer.label),
            recipient=session_address(session_id, name=self._settings.session.name),
            sent_at=datetime.now(UTC),
            content=content,
            in_reply_to=question_id,
        )
        try:
            accepted = await self.accept_peer_message(envelope, data.get("request_id"))
        except PeerMessageError as exc:
            await self._reject_person_answer(sender_ws, data, "not_accepted", str(exc))
            return
        if accepted.duplicate:
            await self._reject_person_answer(
                sender_ws, data, "conflict", self._already_answered_message(question)
            )
            return
        await self._emit_broker_frame(
            {
                "type": PERSON_ANSWERED_FRAME,
                "question_id": question_id,
                "message_id": envelope.message_id,
                "content": content,
                AUTHOR_KEY: answerer.to_dict(),
                "addressee": {
                    "user_id": question.addressee_id,
                    "display_name": question.addressee_name,
                },
            }
        )

    def _person_answer_refusal(
        self, question_id: str, content: str, author: MessageAuthor | None, approver: bool
    ) -> tuple[str, str] | None:
        """Why this answer is refused as ``(code, message)``, or ``None`` when it may proceed."""
        if not content:
            return "invalid", "An answer needs content"
        question = self.person_question(question_id)
        if question is None:
            return "not_found", f"No question {question_id!r} was put to a person in this session"
        if author is None:
            return (
                "forbidden",
                "Your identity is not verified on this connection, so the answer cannot be "
                "attributed to you",
            )
        if author.user_id != question.addressee_id and not approver:
            return (
                "forbidden",
                f"This question is for {question.addressee_name}; only they, or someone with "
                "the approver room role or above, may answer it",
            )
        if any(
            entry.envelope.message_id == person_answer_id(str(self.session_id), question_id)
            for entry in self._peer_inbox.values()
        ):
            return "conflict", self._already_answered_message(question)
        return None

    def _already_answered_message(self, question: PersonQuestion) -> str:
        answer = self._peer_inbox.get(person_answer_id(str(self.session_id), question.question_id))
        by = answer.envelope.sender.name or answer.envelope.sender.id if answer else "someone"
        return f"The question to {question.addressee_name} was already answered by {by}"

    async def _reject_person_answer(
        self, sender_ws: Any, data: dict, code: str, message: str
    ) -> None:
        logger.warning("person_answer refused (%s): %s", code, message)
        if sender_ws is None:
            raise ValueError(message)
        await self._send_broker_frame_to(sender_ws, control_error_frame(message, data, code=code))

    async def _settle_person_question(self, question_id: str, answerer: MessageAuthor) -> None:
        """Tell Forge who answered, which settles the addressee's notification."""
        cfg = self._settings.forge_mcp
        client = HttpForgeClient(
            http_client=self._forge_mcp_http_client,
            token=cfg.token,
            timeout_s=cfg.forge_request_timeout_s,
        )
        await client.answer_person_question(
            str(self.session_id),
            question_id,
            answered_by_user_id=answerer.user_id,
            answered_by_name=answerer.label,
        )

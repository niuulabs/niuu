"""A question a session's agent puts to one named person of its project.

``ask_person`` reuses the machinery that ``ask`` already has for a question to another
agent: a message with a stable id, an answer that is the reply to that id, a wait that
holds the answer back from the harness while a tool call is waiting for it, and delivery
of a late answer at the next turn boundary. What differs is the addressee, a person, and
how the answer arrives: a person answers in the session's room, not with a ``reply`` tool.

The question is a :class:`~niuu.domain.peer_message.PeerMessage` to a ``person`` address.
Its id is derived from the asking session, the person and the caller's ``request_id``, so
asking again with the same ``request_id`` is the same question (it is neither posted twice
nor notified twice). The answer is a peer message from the answering person, in reply to
that id, so the broker's inbox, its durable log and its boundary delivery apply unchanged.

Anyone in the room may be told of the question, but only the addressee, or anyone holding
the ``approver`` room role or above, may answer it: the assignment is the agent's judgment
and advisory, so a question must not be stuck on someone who is away.
"""

from __future__ import annotations

from niuu.domain.peer_envelope import derive_message_id
from niuu.domain.peer_message import PeerAddress, PeerKind

#: The broker's durable frame that records a question put to a person (and, with the
#: ``answered`` frame below, lets a restarted broker authorize and close it).
PERSON_QUESTION_FRAME = "person_question"
#: The broker's durable frame that records who answered a question.
PERSON_ANSWERED_FRAME = "person_answered"
#: The browser message through which a person answers a question put to them.
PERSON_ANSWER_MESSAGE = "person_answer"

#: The metadata key on the addressee's notification that names the question's message id.
PERSON_QUESTION_ID_KEY = "person_question_id"
#: The metadata key on the addressee's notification that names the person asked.
ASKED_PERSON_KEY = "asked_person"


def session_address(session_id: str, *, name: str = "") -> PeerAddress:
    return PeerAddress(kind=PeerKind.SESSION, id=session_id, name=name)


def person_address(user_id: str, *, name: str = "") -> PeerAddress:
    return PeerAddress(kind=PeerKind.PERSON, id=user_id, name=name)


def person_question_id(session_id: str, user_id: str, request_id: str) -> str:
    """The question's message id: the same for the same (session, person, request_id)."""
    return derive_message_id(session_address(session_id), person_address(user_id), request_id)


def person_answer_id(session_id: str, question_id: str) -> str:
    """The id of the one answer a question takes, so a second answer is a duplicate."""
    return derive_message_id(
        session_address(session_id), session_address(session_id), f"answer:{question_id}"
    )

"""Resident questions waiting in the ODIN review queue, as Forge sees them.

A resident that needs its owner files an ODIN review item whose
``requested_action`` is ``answer_operator_question``. Forge does not own that queue
(the ``niuu-ravn`` API does); it follows the pending questions so the owner is told,
through the notification feed and the push channel, that a resident is waiting.
This module is the vocabulary of that relay: the question, the notification Forge
records for it, and the keys that make the recording idempotent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from volundr.domain.notifications import (
    FEED_ONLY_KEY,
    ODIN_REVIEW_DECIDE_PATH_KEY,
    ODIN_REVIEW_ID_KEY,
    PUSH_CATEGORY_KEY,
    PUSH_DATA_KEY,
)

#: ``requested_action`` of a review item that asks the operator an open question.
OPERATOR_QUESTION_ACTION = "answer_operator_question"

#: The APNs ``aps.category`` of a question's push. The phone app registers its inline
#: Reply action under this name, so it is part of the Forge/app contract, not a knob.
QUESTION_PUSH_CATEGORY = "NIUU_RESIDENT_QUESTION"

#: ``input_kind`` of a question's notification, the same field a session's attention
#: item uses (``question`` / ``confirmation`` / ``permission``).
QUESTION_INPUT_KIND = "question"

#: Where a review item is decided, on the host serving the ODIN review queue.
DECIDE_PATH_TEMPLATE = "/api/v1/ravn/odin/reviews/{review_id}/decide"


@dataclass(frozen=True)
class OperatorQuestion:
    """One pending ``answer_operator_question`` review item."""

    review_id: str
    valkyrie_id: str
    environment_id: str
    question: str
    requested_at: datetime

    @property
    def decide_path(self) -> str:
        return DECIDE_PATH_TEMPLATE.format(review_id=quote(self.review_id, safe=""))


@dataclass(frozen=True)
class PendingQuestions:
    """The questions the queue holds, and the pending items that could not be read.

    ``malformed`` lists review ids that claim to be questions but carry no readable
    question or timestamp. They are reported, never silently dropped.
    """

    questions: list[OperatorQuestion]
    malformed: list[str]


@dataclass(frozen=True)
class OwnerRelayState:
    """What the relay remembers about one owner between polls and restarts.

    ``watermark_at`` is when the relay first followed the queue for this owner:
    questions requested at or before it are the backlog. ``digest_recorded`` is
    whether that backlog has been announced already.
    """

    watermark_at: datetime
    digest_recorded: bool


def question_dedupe_key(owner_id: str, review_id: str) -> str:
    """One notification per (owner, review item), whichever replica records it."""
    return f"odin-review:{owner_id}:{review_id}"


def digest_dedupe_key(owner_id: str) -> str:
    """The one digest announcing an owner's backlog."""
    return f"odin-review-digest:{owner_id}"


def question_title(question: OperatorQuestion) -> str:
    return f"{question.valkyrie_id} asks"


def question_metadata(question: OperatorQuestion, *, backlog: bool) -> Mapping[str, object]:
    """Notification metadata for a question.

    A backlog question is feed-only (visible, never pushed). Any other pushes with
    the Reply category and the keys the app needs to answer it inline.
    """
    metadata: dict[str, object] = {
        ODIN_REVIEW_ID_KEY: question.review_id,
        ODIN_REVIEW_DECIDE_PATH_KEY: question.decide_path,
        "valkyrie_id": question.valkyrie_id,
        "environment_id": question.environment_id,
        "input_kind": QUESTION_INPUT_KIND,
    }
    if backlog:
        metadata[FEED_ONLY_KEY] = True
        return metadata
    metadata[PUSH_CATEGORY_KEY] = QUESTION_PUSH_CATEGORY
    metadata[PUSH_DATA_KEY] = {
        ODIN_REVIEW_ID_KEY: question.review_id,
        ODIN_REVIEW_DECIDE_PATH_KEY: question.decide_path,
    }
    return metadata


def digest_title(waiting: int) -> str:
    noun = "question" if waiting == 1 else "questions"
    return f"{waiting} resident {noun} waiting"


def digest_body(waiting_by_resident: Mapping[str, int]) -> str:
    """Who is waiting, most waiting first (ties by name)."""
    ranked = sorted(waiting_by_resident.items(), key=lambda entry: (-entry[1], entry[0]))
    return "Waiting on you: " + ", ".join(f"{name} ({count})" for name, count in ranked)

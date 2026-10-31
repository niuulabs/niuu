"""Shared contract for notifications a resident raises for its owner.

A resident (a long-lived Ravn) records what it wants its owner to know in a small
durable outbox of its own: that it is waiting for them (``attention``), that the
wait is over (``resolved``), or a notification its agent sent on purpose
(``notify``). Forge reads that outbox over the connection it already holds to the
resident (the Ravn OpenClaw shim, authenticated by the resident's own shim
token) and projects each entry into the same notification feed, outbox and
delivery rules as Forge sessions' notifications. Ravn writes entries with these
models; Forge reads them back with the same models, so the shape cannot drift.

``channel`` is the resident-side session key the entry is about (the shim's
OpenClaw session key), or ``None`` for the resident's main room. Forge maps it to
the resident session it knows; the resident never needs Forge's ids.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from niuu.domain.notifications import MAX_BODY_CHARS, NotificationDraft

# Protocol limits shared by the resident (writer) and Forge (reader).
MAX_REQUEST_ID_CHARS = 200
MAX_IDEMPOTENCY_KEY_CHARS = 200
MAX_CHANNEL_CHARS = 512
MAX_RESOLVED_REQUESTS = 100


class ResidentNotificationOp(StrEnum):
    """What one outbox entry says."""

    #: The resident is waiting for its owner (a question, confirmation or permission).
    ATTENTION = "attention"
    #: Those waits are over (answered, or no longer needed).
    RESOLVED = "resolved"
    #: The resident's agent notified its owner on purpose.
    NOTIFY = "notify"


class AttentionInputKind(StrEnum):
    """What the resident is waiting for; the same vocabulary as a Forge session's."""

    QUESTION = "question"
    CONFIRMATION = "confirmation"
    PERMISSION = "permission"


class ResidentNotificationEntry(BaseModel):
    """One durable entry of a resident's notification outbox, in write order."""

    model_config = ConfigDict(frozen=True)

    seq: int = Field(ge=1)
    op: ResidentNotificationOp
    channel: str | None = Field(default=None, min_length=1, max_length=MAX_CHANNEL_CHARS)
    #: ``attention``: the one request this wait is. Stable across retries and restarts.
    request_id: str = Field(default="", max_length=MAX_REQUEST_ID_CHARS)
    input_kind: AttentionInputKind | None = None
    prompt: str = Field(default="", max_length=MAX_BODY_CHARS)
    #: ``resolved``: every request it settles.
    resolved_request_ids: list[str] = Field(default_factory=list, max_length=MAX_RESOLVED_REQUESTS)
    #: ``notify``: what the agent sent, and the key its retries share.
    draft: NotificationDraft | None = None
    idempotency_key: str = Field(default="", max_length=MAX_IDEMPOTENCY_KEY_CHARS)
    created_at: datetime

    @model_validator(mode="after")
    def _complete_for_op(self) -> ResidentNotificationEntry:
        if self.op is ResidentNotificationOp.ATTENTION:
            if not self.request_id or self.input_kind is None:
                raise ValueError("An attention entry needs request_id and input_kind")
        elif self.op is ResidentNotificationOp.RESOLVED:
            if not self.resolved_request_ids or not all(self.resolved_request_ids):
                raise ValueError("A resolved entry names the request ids it settles")
            if any(len(item) > MAX_REQUEST_ID_CHARS for item in self.resolved_request_ids):
                raise ValueError(
                    f"Resolved request ids are at most {MAX_REQUEST_ID_CHARS} characters"
                )
        elif self.draft is None or not self.idempotency_key:
            raise ValueError("A notify entry needs a draft and an idempotency_key")
        return self


__all__ = [
    "MAX_CHANNEL_CHARS",
    "MAX_IDEMPOTENCY_KEY_CHARS",
    "MAX_REQUEST_ID_CHARS",
    "MAX_RESOLVED_REQUESTS",
    "AttentionInputKind",
    "ResidentNotificationEntry",
    "ResidentNotificationOp",
]

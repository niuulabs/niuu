"""Per-reader inbox state, independent of session runtime/lifecycle state."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class SessionReadState(BaseModel):
    latest_output_seq: int = Field(default=0, ge=0)
    latest_output_turn_id: str | None = None
    latest_output_at: datetime | None = None
    read_through_seq: int = Field(default=0, ge=0)
    manually_unread: bool = False
    revision: int = Field(default=0, ge=0)

    @computed_field
    @property
    def is_unread(self) -> bool:
        return self.manually_unread or self.latest_output_seq > self.read_through_seq


class SessionReadStateChange(BaseModel):
    state: Literal["read", "unread"]
    expected_revision: int = Field(ge=0)
    # Required even for manual actions: this is the output the caller actually saw.
    through_seq: int = Field(ge=0)


class SessionReadStateConflictError(ValueError):
    """A newer read/unread action won; fetch current state before deciding again."""


def is_final_output(payload: dict) -> bool:
    """Only a public, successful final reply creates inbox attention, never chrome/tools."""
    if payload.get("type") != "conversation.turn":
        return False
    turn = payload.get("turn")
    if not isinstance(turn, dict) or turn.get("role") != "assistant":
        return False
    if turn.get("visibility", "public") != "public":
        return False
    metadata = turn.get("metadata") or {}
    if not isinstance(metadata, dict) or metadata.get("final_output") is not True:
        return False
    if metadata.get("status") in {"error", "interrupted"} or metadata.get("is_error"):
        return False
    content = turn.get("content")
    return bool(isinstance(content, str) and content.strip() and turn.get("id"))

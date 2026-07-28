"""Checkpoint domain model for Ravn task interruption and resume."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ravn.domain.models import Session

# Tool names that trigger an automatic pre-execution named snapshot.
# Defined here (domain layer) so both the agent loop and the checkpoint tools
# can import from a single authoritative location.
DESTRUCTIVE_TOOL_NAMES: frozenset[str] = frozenset({"write_file", "edit_file", "bash", "terminal"})


class InterruptReason(StrEnum):
    """Why the task was interrupted."""

    SIGINT = "sigint"
    SIGTERM = "sigterm"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TING_CANCEL = "ting_cancel"


@dataclass
class Checkpoint:
    """Serialised task state persisted at every tool call boundary.

    Written after every tool call so that resume is safe across crashes —
    not just on explicit interruption.  Fields are kept intentionally flat
    (primitive types and plain dicts) so serialisation is trivial.

    NIU-504 (crash-recovery) checkpoint fields:
        task_id:                Unique ID for this task / checkpoint.
        user_input:             The original prompt that started the task.
        messages:               Full conversation history, Anthropic API format.
        todos:                  Agent todo list (list of serialised TodoItem dicts).
        iteration_budget_consumed: Iterations consumed when checkpoint was written.
        iteration_budget_total:    Configured iteration budget ceiling.
        last_tool_call:         Serialised ToolCall dict from the last iteration,
                                or None if interrupted before any tool was called.
        last_tool_result:       Serialised ToolResult dict from the last iteration,
                                or None.
        partial_response:       LLM text accumulated so far (may be empty).
        interrupted_by:         The signal or condition that stopped the task, or
                                None when the checkpoint was written mid-run
                                (crash-safe checkpoint, not an explicit stop).
        created_at:             UTC timestamp when this checkpoint was written.

    NIU-537 (named snapshot) extensions:
        checkpoint_id:          Globally unique snapshot ID: ``ckpt_{task_id}_{seq}``.
                                Empty string for crash-recovery checkpoints (NIU-504).
        seq:                    Monotonically increasing sequence number within the task.
                                0 for crash-recovery checkpoints.
        label:                  Optional human-readable label, e.g. "after tests pass".
        tags:                   Optional list of free-form tag strings.
        memory_context:         Memory context string captured at snapshot time.
    """

    task_id: str
    user_input: str
    messages: list[dict[str, Any]]
    todos: list[dict[str, Any]]
    iteration_budget_consumed: int
    iteration_budget_total: int
    last_tool_call: dict[str, Any] | None
    last_tool_result: dict[str, Any] | None
    partial_response: str
    interrupted_by: InterruptReason | None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # NIU-537 named-snapshot fields (default to crash-recovery values)
    checkpoint_id: str = ""
    seq: int = 0
    label: str = ""
    tags: list[str] = field(default_factory=list)
    memory_context: str = ""

    @property
    def is_named_snapshot(self) -> bool:
        """True when this is a named snapshot (NIU-537), not a crash-recovery write."""
        return bool(self.checkpoint_id)

    @classmethod
    def make_snapshot_id(cls, task_id: str, seq: int) -> str:
        """Return a canonical checkpoint_id for the given task and sequence."""
        return f"ckpt_{task_id}_{seq}"


def restore_session_from_checkpoint(session: Session, checkpoint: Checkpoint) -> None:
    """Restore session messages and todos in-place from a named checkpoint.

    Mutates *session* directly — clears existing messages and todos then
    repopulates them from the serialised dicts stored in *checkpoint*.
    """
    from ravn.domain.models import Message, TodoItem, TodoStatus

    session.messages.clear()
    for raw in checkpoint.messages:
        session.messages.append(
            Message(
                role=raw["role"],
                content=raw["content"],
                reasoning=raw.get("reasoning", ""),
            )
        )

    session.todos.clear()
    for raw in checkpoint.todos:
        status_raw = raw.get("status", "pending")
        try:
            status = TodoStatus(status_raw)
        except ValueError:
            status = TodoStatus.PENDING
        session.upsert_todo(
            TodoItem(
                id=raw["id"],
                content=raw["content"],
                status=status,
                priority=raw.get("priority", 0),
            )
        )

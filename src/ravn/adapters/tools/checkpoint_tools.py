"""Agent-facing checkpoint tools (NIU-537).

Three tools are available when ``checkpoint.enabled`` is true in config:

* ``checkpoint_save``    — save a named snapshot of the current session.
* ``checkpoint_list``    — list snapshots for the current task.
* ``checkpoint_restore`` — restore session state from a named snapshot.

These tools operate against a :class:`ravn.ports.checkpoint.CheckpointPort`
and a live :class:`ravn.domain.models.Session`.  They are constructed by the
CLI/container and injected alongside the other tools.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from niuu.domain.tool import ToolResult
from niuu.ports.tool import ToolPort
from ravn.domain.checkpoint import (
    Checkpoint,
    restore_session_from_checkpoint,
)
from ravn.domain.models import Session
from ravn.ports.checkpoint import CheckpointPort

logger = logging.getLogger(__name__)

_PERMISSION = "checkpoint:write"
_PERMISSION_READ = "checkpoint:read"


def _format_snapshot_summary(cp: Checkpoint) -> str:
    label_part = f"  label: {cp.label}" if cp.label else ""
    tags_part = f"  tags: {', '.join(cp.tags)}" if cp.tags else ""
    lines = [
        f"  id: {cp.checkpoint_id}",
        f"  seq: {cp.seq}",
        f"  created: {cp.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"  messages: {len(cp.messages)}",
        f"  iterations: {cp.iteration_budget_consumed}/{cp.iteration_budget_total}",
    ]
    if label_part:
        lines.append(label_part)
    if tags_part:
        lines.append(tags_part)
    return "\n".join(lines)


class CheckpointSaveTool(ToolPort):
    """Save a named checkpoint snapshot of the current session.

    Captures the full message history, todo list, iteration budget, and
    memory context.  Returns the assigned checkpoint_id.
    """

    def __init__(
        self,
        checkpoint_port: CheckpointPort,
        session: Session,
        task_id: str,
        iteration_budget_consumed: int = 0,
        iteration_budget_total: int = 0,
    ) -> None:
        self._port = checkpoint_port
        self._session = session
        self._task_id = task_id
        self._budget_consumed = iteration_budget_consumed
        self._budget_total = iteration_budget_total

    @property
    def name(self) -> str:
        return "checkpoint_save"

    @property
    def description(self) -> str:
        return (
            "Save a named checkpoint snapshot of the current session state. "
            "Captures message history, todo list, and iteration budget. "
            "Returns the checkpoint_id to use for restore or rollback."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Optional human-readable label, e.g. 'after tests pass'.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of free-form tag strings.",
                },
            },
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:
        label = str(input.get("label", "")).strip()
        tags = [str(t) for t in input.get("tags", [])]

        messages = [{"role": m.role, "content": m.content} for m in self._session.messages]
        todos = [
            {"id": t.id, "content": t.content, "status": str(t.status), "priority": t.priority}
            for t in self._session.todos
        ]

        checkpoint = Checkpoint(
            task_id=self._task_id,
            user_input="",
            messages=messages,
            todos=todos,
            iteration_budget_consumed=self._budget_consumed,
            iteration_budget_total=self._budget_total,
            last_tool_call=None,
            last_tool_result=None,
            partial_response="",
            interrupted_by=None,
            created_at=datetime.now(UTC),
            label=label,
            tags=tags,
        )

        try:
            checkpoint_id = await self._port.save_snapshot(checkpoint)
        except Exception as exc:
            logger.warning("checkpoint_save failed: %s", exc)
            return ToolResult(
                tool_call_id="",
                content=f"Checkpoint save failed: {exc}",
                is_error=True,
            )

        label_note = f" (label: {label!r})" if label else ""
        return ToolResult(
            tool_call_id="",
            content=f"Checkpoint saved{label_note}.\ncheckpoint_id: {checkpoint_id}",
        )


class CheckpointListTool(ToolPort):
    """List named checkpoint snapshots for the current task."""

    def __init__(self, checkpoint_port: CheckpointPort, task_id: str) -> None:
        self._port = checkpoint_port
        self._task_id = task_id

    @property
    def name(self) -> str:
        return "checkpoint_list"

    @property
    def description(self) -> str:
        return (
            "List all named checkpoint snapshots for the current task, newest first. "
            "Returns checkpoint_id, label, and creation time for each snapshot."
        )

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        try:
            snapshots = await self._port.list_for_task(self._task_id)
        except Exception as exc:
            logger.warning("checkpoint_list failed: %s", exc)
            return ToolResult(
                tool_call_id="",
                content=f"Checkpoint list failed: {exc}",
                is_error=True,
            )

        if not snapshots:
            return ToolResult(tool_call_id="", content="No checkpoints found for this task.")

        lines = [f"Checkpoints for task {self._task_id!r} ({len(snapshots)} total):", ""]
        for cp in snapshots:
            lines.append(_format_snapshot_summary(cp))
            lines.append("")
        return ToolResult(tool_call_id="", content="\n".join(lines).rstrip())


class CheckpointRestoreTool(ToolPort):
    """Restore session state from a named checkpoint snapshot.

    Returns a summary of the restored state.  The caller (agent loop) is
    responsible for replacing the active session with the restored one.
    """

    def __init__(
        self,
        checkpoint_port: CheckpointPort,
        session: Session,
    ) -> None:
        self._port = checkpoint_port
        self._session = session

    @property
    def name(self) -> str:
        return "checkpoint_restore"

    @property
    def description(self) -> str:
        return (
            "Restore session state from a named checkpoint snapshot. "
            "Replaces the current message history and todo list with the snapshot's state. "
            "Use checkpoint_list to find available checkpoint_ids."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "checkpoint_id": {
                    "type": "string",
                    "description": "The checkpoint_id to restore (e.g. ckpt_my_task_3).",
                },
            },
            "required": ["checkpoint_id"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:
        checkpoint_id = str(input.get("checkpoint_id", "")).strip()
        if not checkpoint_id:
            return ToolResult(
                tool_call_id="",
                content="'checkpoint_id' is required.",
                is_error=True,
            )

        try:
            cp = await self._port.load_snapshot(checkpoint_id)
        except Exception as exc:
            logger.warning("checkpoint_restore failed: %s", exc)
            return ToolResult(
                tool_call_id="",
                content=f"Checkpoint restore failed: {exc}",
                is_error=True,
            )

        if cp is None:
            return ToolResult(
                tool_call_id="",
                content=f"Checkpoint {checkpoint_id!r} not found.",
                is_error=True,
            )

        restore_session_from_checkpoint(self._session, cp)

        return ToolResult(
            tool_call_id="",
            content=(
                f"Restored from checkpoint {checkpoint_id!r}.\n"
                f"  Messages restored: {len(self._session.messages)}\n"
                f"  Todos restored: {len(self._session.todos)}\n"
                f"  Snapshot created: {cp.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                f"  Label: {cp.label or '(none)'}\n"
                f"  Iterations at snapshot: "
                f"{cp.iteration_budget_consumed}/{cp.iteration_budget_total}"
            ),
        )

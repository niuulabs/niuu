"""Todo list tools for Ravn agents.

Provides two tools that share session-scoped todo state:

* ``todo_write`` — create, update, or delete todo items.
* ``todo_read``  — read the current todo list.

Todo state lives in ``Session.todos`` so it persists across turns within a
task and is automatically discarded when the session ends.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from ravn.domain.models import Session, TodoItem, TodoStatus, ToolResult
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)

_PERMISSION = "todo:write"
_PERMISSION_READ = "todo:read"

_VALID_STATUSES = {s.value for s in TodoStatus}

_STATUS_SYMBOLS = {
    TodoStatus.PENDING: "○",
    TodoStatus.IN_PROGRESS: "◉",
    TodoStatus.DONE: "✓",
    TodoStatus.CANCELLED: "✗",
}


def _format_todos(todos: list[TodoItem]) -> str:
    if not todos:
        return "(no todos)"
    lines: list[str] = []
    for item in sorted(todos, key=lambda t: (-t.priority, t.id)):
        symbol = _STATUS_SYMBOLS.get(item.status, "?")
        lines.append(f"[{symbol}] {item.id}: {item.content}  ({item.status})")
    return "\n".join(lines)


class TodoWriteTool(ToolPort):
    """Create, update, or delete items in the session todo list.

    Operations
    ----------
    * ``create`` — add a new item (``content`` required; ``id`` auto-generated
      unless provided; ``priority`` defaults to 0).
    * ``update`` — change ``content``, ``status``, and/or ``priority`` of an
      existing item (``id`` required).
    * ``delete`` — remove an item by ``id``.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def name(self) -> str:
        return "todo_write"

    @property
    def description(self) -> str:
        return (
            "Manage the in-session todo list. "
            "Use operation='create' to add items, 'update' to change status/content/priority, "
            "and 'delete' to remove an item by id. "
            "Statuses: pending | in_progress | done | cancelled."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["create", "update", "delete"],
                    "description": "The operation to perform.",
                },
                "id": {
                    "type": "string",
                    "description": (
                        "Item id. Required for 'update' and 'delete'. "
                        "Optional for 'create' (auto-generated if omitted)."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "Text description of the todo item.",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "done", "cancelled"],
                    "description": "Item status (default: pending for create).",
                },
                "priority": {
                    "type": "integer",
                    "description": "Higher numbers = higher priority (default: 0).",
                },
            },
            "required": ["operation"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:
        operation = input.get("operation", "")

        match operation:
            case "create":
                return self._create(input)
            case "update":
                return self._update(input)
            case "delete":
                return self._delete(input)
            case _:
                return ToolResult(
                    tool_call_id="",
                    content=f"Unknown operation: '{operation}'. Use create, update, or delete.",
                    is_error=True,
                )
        raise AssertionError("Unreachable execute fallthrough")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _create(self, input: dict) -> ToolResult:
        content = input.get("content", "").strip()
        if not content:
            return ToolResult(
                tool_call_id="",
                content="'content' is required for create.",
                is_error=True,
            )

        status_str = input.get("status", TodoStatus.PENDING.value)
        if status_str not in _VALID_STATUSES:
            return ToolResult(
                tool_call_id="",
                content=f"Invalid status '{status_str}'. Valid: {sorted(_VALID_STATUSES)}",
                is_error=True,
            )

        item_id = input.get("id") or uuid4().hex[:8]
        priority = int(input.get("priority", 0))

        item = TodoItem(
            id=item_id,
            content=content,
            status=TodoStatus(status_str),
            priority=priority,
        )
        self._session.upsert_todo(item)
        logger.debug("todo_write: created %s", item_id)

        return ToolResult(
            tool_call_id="",
            content=f"Created todo {item_id}.\n\n{_format_todos(self._session.todos)}",
        )

    def _update(self, input: dict) -> ToolResult:
        item_id = input.get("id", "").strip()
        if not item_id:
            return ToolResult(
                tool_call_id="",
                content="'id' is required for update.",
                is_error=True,
            )

        existing = next((t for t in self._session.todos if t.id == item_id), None)
        if existing is None:
            return ToolResult(
                tool_call_id="",
                content=f"Todo '{item_id}' not found.",
                is_error=True,
            )

        content = input.get("content", existing.content).strip() or existing.content
        status_str = input.get("status", existing.status.value)
        if status_str not in _VALID_STATUSES:
            return ToolResult(
                tool_call_id="",
                content=f"Invalid status '{status_str}'. Valid: {sorted(_VALID_STATUSES)}",
                is_error=True,
            )

        priority = int(input.get("priority", existing.priority))

        updated = TodoItem(
            id=item_id,
            content=content,
            status=TodoStatus(status_str),
            priority=priority,
        )
        self._session.upsert_todo(updated)
        logger.debug("todo_write: updated %s -> %s", item_id, status_str)

        return ToolResult(
            tool_call_id="",
            content=f"Updated todo {item_id}.\n\n{_format_todos(self._session.todos)}",
        )

    def _delete(self, input: dict) -> ToolResult:
        item_id = input.get("id", "").strip()
        if not item_id:
            return ToolResult(
                tool_call_id="",
                content="'id' is required for delete.",
                is_error=True,
            )

        removed = self._session.remove_todo(item_id)
        if not removed:
            return ToolResult(
                tool_call_id="",
                content=f"Todo '{item_id}' not found.",
                is_error=True,
            )

        logger.debug("todo_write: deleted %s", item_id)
        return ToolResult(
            tool_call_id="",
            content=f"Deleted todo {item_id}.\n\n{_format_todos(self._session.todos)}",
        )


class TodoReadTool(ToolPort):
    """Read the current todo list from the session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def name(self) -> str:
        return "todo_read"

    @property
    def description(self) -> str:
        return (
            "Read the current todo list. "
            "Returns all items with their id, content, status, and priority."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "done", "cancelled"],
                    "description": "Filter by status (omit to return all items).",
                },
            },
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        status_filter = input.get("status")

        if status_filter is not None and status_filter not in _VALID_STATUSES:
            return ToolResult(
                tool_call_id="",
                content=f"Invalid status '{status_filter}'. Valid: {sorted(_VALID_STATUSES)}",
                is_error=True,
            )

        todos = self._session.todos
        if status_filter:
            todos = [t for t in todos if t.status.value == status_filter]

        return ToolResult(tool_call_id="", content=_format_todos(todos))

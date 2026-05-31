"""CheckpointsView — checkpoint list and resume."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import DataTable

_REASON_STYLE: dict[str, str] = {
    "manual": "#06b6d4",
    "interrupt": "#f59e0b",
    "error": "#ef4444",
    "auto": "#71717a",
}


class CheckpointsView(Widget):
    """All checkpoints from CheckpointPort.

    Per checkpoint: task_id, label, tags, created_at, iteration count,
    interrupted_by reason.
    """

    DEFAULT_CSS = """
    CheckpointsView {
        height: 1fr;
        width: 1fr;
        background: #09090b;
    }
    CheckpointsView DataTable {
        height: 1fr;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("r", "resume", "Resume"),
        Binding("d", "delete_cp", "Delete"),
    ]

    def __init__(self, connection: Any | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._connection = connection
        self._checkpoints: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        table = DataTable(id="ck-table", cursor_type="row")
        table.add_columns("Task ID", "Label", "Tags", "Created", "Iter", "Reason")
        yield table

    def on_mount(self) -> None:
        asyncio.create_task(self._load(), name="cp-load")

    async def _load(self) -> None:
        if not self._connection:
            self._render()
            return
        with suppress(Exception):
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._connection.base_url}/checkpoints")
                if resp.status_code == 200:
                    self._checkpoints = resp.json()
        self._render()

    def _render(self) -> None:
        try:
            table = self.query_one("#ck-table", DataTable)
        except Exception:
            return
        table.clear()
        if not self._checkpoints:
            table.add_row("(no checkpoints)", "—", "—", "—", "—", "—")
            return
        for cp in self._checkpoints:
            task_id = str(cp.get("task_id", ""))[:8]
            label = cp.get("label", "—")
            tags = ", ".join(cp.get("tags", []))
            created = cp.get("created_at", "—")
            itr = str(cp.get("iteration", "—"))
            reason = cp.get("interrupted_by", "—")
            style = _REASON_STYLE.get(reason, "#a1a1aa")
            table.add_row(task_id, label, tags, created, itr, f"[{style}]{reason}[/]")

    def load_checkpoints(self, checkpoints: list[dict[str, Any]]) -> None:
        """Load checkpoint data directly (for testing or programmatic use)."""
        self._checkpoints = list(checkpoints)
        self._render()

    def action_resume(self) -> None:
        pass

    def action_delete_cp(self) -> None:
        pass

"""TaskBoardView — cascade task board with CaptureChannel progress."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import DataTable

from ravn.tui.utils import iter_bar

_STATUS_STYLE: dict[str, str] = {
    "running": "#f59e0b",
    "queued": "#06b6d4",
    "complete": "#10b981",
    "error": "#ef4444",
    "stopped": "#71717a",
}


class TaskBoardView(Widget):
    """Shows all running and queued cascade tasks across the Flokk."""

    DEFAULT_CSS = """
    TaskBoardView {
        height: 1fr;
        width: 1fr;
        background: #09090b;
    }
    TaskBoardView DataTable {
        height: 1fr;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("s", "stop_task", "Stop"),
        Binding("c", "collect", "Collect"),
        Binding("n", "new_task", "New"),
        Binding("enter", "expand_task", "Expand"),
    ]

    def __init__(self, flokk: Any | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._flokk = flokk
        self._tasks: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        table = DataTable(id="tb-table", cursor_type="row")
        table.add_columns("Task ID", "Title", "Ravn", "Status", "Progress", "Elapsed")
        yield table

    def on_mount(self) -> None:
        if self._flokk:
            self._flokk.on_event(self._on_event)
        self.set_interval(2.0, self._refresh)

    def _on_event(self, conn: Any, event: dict[str, Any]) -> None:
        self.call_after_refresh(self._refresh)

    def _refresh(self) -> None:
        try:
            table = self.query_one("#tb-table", DataTable)
        except Exception:
            return
        table.clear()
        if not self._flokk:
            table.add_row("—", "no flokk connection", "—", "—", "—", "—")
            return
        conns = self._flokk.connections()
        if not conns:
            table.add_row("—", "no ravens connected", "—", "—", "—", "—")
            return
        for conn in conns:
            info = conn.ravn_info or {}
            state = info.get("state", conn.status)
            style = _STATUS_STYLE.get(state, "#a1a1aa")
            persona = str(info.get("persona", "—"))[:30]
            cur = info.get("iteration")
            mx = info.get("max_iterations")
            progress = iter_bar(cur, mx) if cur is not None and mx is not None else "—"
            uptime = str(info.get("uptime", "—"))
            table.add_row(
                conn.name[:12],
                persona,
                conn.name[:12],
                f"[{style}]{state}[/]",
                progress,
                uptime,
            )

    def load_tasks(self, tasks: list[dict[str, Any]]) -> None:
        """Load task data directly (for testing or programmatic use)."""
        self._tasks = list(tasks)
        self._refresh()

    def action_stop_task(self) -> None:
        pass  # Wired up by app

    def action_collect(self) -> None:
        pass

    def action_new_task(self) -> None:
        pass

    def action_expand_task(self) -> None:
        pass

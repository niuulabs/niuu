"""CronView — scheduled task manager."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import DataTable

_STATUS_STYLE = {
    "enabled": "#10b981",
    "disabled": "#71717a",
    "running": "#f59e0b",
    "error": "#ef4444",
}


class CronView(Widget):
    """Table of all cron jobs with toggle, trigger, delete, and create."""

    DEFAULT_CSS = """
    CronView {
        height: 1fr;
        width: 1fr;
        background: #09090b;
    }
    CronView DataTable {
        height: 1fr;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("space", "toggle_job", "Toggle"),
        Binding("r", "run_now", "Run now"),
        Binding("d", "delete_job", "Delete"),
        Binding("n", "new_job", "New"),
    ]

    def __init__(self, connection: Any | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._connection = connection
        self._jobs: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        table = DataTable(id="cr-table", cursor_type="row")
        table.add_columns("Name", "Schedule", "Last run", "Next run", "Status")
        yield table

    def on_mount(self) -> None:
        asyncio.create_task(self._load_jobs(), name="cron-load")

    async def _load_jobs(self) -> None:
        if not self._connection:
            self._render()
            return
        with suppress(Exception):
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._connection.base_url}/cron")
                if resp.status_code == 200:
                    self._jobs = resp.json()
        self._render()

    def _render(self) -> None:
        try:
            table = self.query_one("#cr-table", DataTable)
        except Exception:
            return
        table.clear()
        if not self._jobs:
            table.add_row("(no jobs)", "—", "—", "—", "—")
            return
        for job in self._jobs:
            name = job.get("name", "?")
            schedule = job.get("schedule", "?")
            last_run = job.get("last_run", "—")
            next_run = job.get("next_run", "—")
            status = job.get("status", "enabled")
            style = _STATUS_STYLE.get(status, "#a1a1aa")
            table.add_row(name, schedule, last_run, next_run, f"[{style}]{status}[/]")

    def load_jobs(self, jobs: list[dict[str, Any]]) -> None:
        """Load job data directly (for testing or programmatic use)."""
        self._jobs = list(jobs)
        self._render()

    def action_toggle_job(self) -> None:
        pass

    def action_run_now(self) -> None:
        pass

    def action_delete_job(self) -> None:
        pass

    def action_new_job(self) -> None:
        pass

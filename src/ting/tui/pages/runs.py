"""Runs TUI page — list runs with status, confidence, and actions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static

from cli.tui.theme import (
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_EMERALD,
    ACCENT_INDIGO,
    ACCENT_ORANGE,
    ACCENT_PURPLE,
    ACCENT_RED,
    BG_SECONDARY,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from cli.tui.widgets.metric_card import MetricCard, MetricRow
from cli.tui.widgets.tabs import NiuuTabs
from ting.tui._helpers import format_confidence, format_confidence_history

if TYPE_CHECKING:
    from niuu.cli_api_client import CLIAPIClient

# Filter tabs matching RunStatus values.
_RUN_TABS = ["All", "Pending", "Queued", "Running", "Review", "Escalated"]

# RunStatus → display color.
_RUN_STATUS_COLORS: dict[str, str] = {
    "PENDING": ACCENT_PURPLE,
    "QUEUED": ACCENT_AMBER,
    "RUNNING": ACCENT_EMERALD,
    "REVIEW": ACCENT_CYAN,
    "ESCALATED": ACCENT_ORANGE,
    "MERGED": ACCENT_INDIGO,
    "FAILED": ACCENT_RED,
}


class RunRow(Widget):
    """A single run entry in the list."""

    DEFAULT_CSS = """
    RunRow {
        height: auto;
        padding: 1 2;
        border-bottom: solid #27272a;
        background: #18181b;
    }
    """

    def __init__(self, run: dict[str, Any], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._run = run

    @property
    def run(self) -> dict[str, Any]:
        return self._run

    def compose(self) -> ComposeResult:
        run = self._run
        name = run.get("name", "Unknown")
        status = run.get("status", "PENDING")
        confidence = run.get("confidence", 0.0)
        session_id = run.get("session_id") or "—"
        run_id = str(run.get("id", ""))[:8]
        retry_count = run.get("retry_count", 0)

        color = _RUN_STATUS_COLORS.get(status, TEXT_MUTED)
        conf_pct = format_confidence(confidence)
        history_str = format_confidence_history(
            run.get("confidence_history", []),
            TEXT_MUTED,
        )

        retry_str = f"  [{TEXT_MUTED}]retries: {retry_count}[/]" if retry_count else ""

        yield Static(
            f"[bold {TEXT_PRIMARY}]{name}[/]  "
            f"[{TEXT_MUTED}]{run_id}[/]  "
            f"[{color}]{status}[/]  "
            f"[{ACCENT_AMBER}]{conf_pct}[/]  "
            f"[{TEXT_SECONDARY}]session: {session_id}[/]"
            f"{retry_str}{history_str}",
            id="run-row-content",
        )


class RunsPage(Widget):
    """TUI page for viewing and managing runs."""

    DEFAULT_CSS = f"""
    RunsPage {{
        width: 1fr;
        height: 1fr;
        background: {BG_SECONDARY};
    }}
    RunsPage #runs-search {{
        margin: 0 2;
        height: 3;
    }}
    RunsPage #runs-list {{
        height: 1fr;
    }}
    """

    class ApproveRequested(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class RejectRequested(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class RetryRequested(Message):
        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    def __init__(
        self,
        client: CLIAPIClient | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._runs: list[dict[str, Any]] = []
        self._filter_status: str = "All"
        self._search_query: str = ""

    @property
    def runs(self) -> list[dict[str, Any]]:
        return list(self._runs)

    @property
    def filtered_runs(self) -> list[dict[str, Any]]:
        result = self._runs
        if self._filter_status != "All":
            target = self._filter_status.upper()
            result = [r for r in result if r.get("status", "").upper() == target]
        if self._search_query:
            q = self._search_query.lower()
            result = [r for r in result if q in r.get("name", "").lower()]
        return result

    def compose(self) -> ComposeResult:
        yield NiuuTabs(items=_RUN_TABS, id="runs-tabs")
        yield MetricRow(id="runs-metrics")
        yield Input(placeholder="Search runs...", id="runs-search")
        yield VerticalScroll(id="runs-list")

    def on_mount(self) -> None:
        self._load_runs()

    def load_data(self, runs: list[dict[str, Any]]) -> None:
        """Load run data directly (for testing or programmatic use)."""
        self._runs = runs
        self._update_metrics()
        self._render_list()

    def _load_runs(self) -> None:
        if not self._client:
            return
        try:
            resp = self._client.get("/api/v1/ting/runs/active")
            resp.raise_for_status()
            self._runs = resp.json()
        except Exception:
            self._runs = []
        self._update_metrics()
        self._render_list()

    def _update_metrics(self) -> None:
        try:
            row = self.query_one("#runs-metrics", MetricRow)
        except Exception:
            return
        row.remove_children()

        total = len(self._runs)
        running = sum(1 for r in self._runs if r.get("status") == "RUNNING")
        review = sum(1 for r in self._runs if r.get("status") == "REVIEW")
        failed = sum(1 for r in self._runs if r.get("status") == "FAILED")

        row.mount(
            MetricCard(
                label="Total",
                value=str(total),
                icon="⚔",
                id="metric-total",
            )
        )
        row.mount(
            MetricCard(
                label="Running",
                value=str(running),
                icon="▶",
                color=ACCENT_EMERALD,
                id="metric-running",
            )
        )
        row.mount(
            MetricCard(
                label="Review",
                value=str(review),
                icon="👁",
                color=ACCENT_CYAN,
                id="metric-review",
            )
        )
        row.mount(
            MetricCard(
                label="Failed",
                value=str(failed),
                icon="✗",
                color=ACCENT_RED,
                id="metric-failed",
            )
        )

    def _render_list(self) -> None:
        try:
            container = self.query_one("#runs-list", VerticalScroll)
        except Exception:
            return
        container.remove_children()

        filtered = self.filtered_runs
        if not filtered:
            container.mount(
                Static(
                    f"[{TEXT_MUTED}]No runs found.[/]",
                )
            )
            return

        for run in filtered:
            container.mount(RunRow(run))

    def on_niuu_tabs_tab_selected(self, message: NiuuTabs.TabSelected) -> None:
        self._filter_status = message.label
        self._render_list()

    def on_input_changed(self, message: Input.Changed) -> None:
        if message.input.id == "runs-search":
            self._search_query = message.value
            self._render_list()

    def approve_run(self, run_id: str) -> bool:
        """Approve a run via API. Returns True on success."""
        if not self._client:
            return False
        try:
            resp = self._client.post(f"/api/v1/ting/runs/{run_id}/approve")
            resp.raise_for_status()
            return True
        except Exception:
            return False

    def reject_run(self, run_id: str) -> bool:
        """Reject a run via API. Returns True on success."""
        if not self._client:
            return False
        try:
            resp = self._client.post(f"/api/v1/ting/runs/{run_id}/reject")
            resp.raise_for_status()
            return True
        except Exception:
            return False

    def retry_run(self, run_id: str) -> bool:
        """Retry a run via API. Returns True on success."""
        if not self._client:
            return False
        try:
            resp = self._client.post(f"/api/v1/ting/runs/{run_id}/retry")
            resp.raise_for_status()
            return True
        except Exception:
            return False

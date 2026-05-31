"""Dispatch TUI page — queue view with bulk dispatch and activity log."""

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
    ACCENT_PURPLE,
    ACCENT_RED,
    BG_SECONDARY,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from cli.tui.widgets.metric_card import MetricCard, MetricRow
from cli.tui.widgets.tabs import NiuuTabs
from ting.tui._helpers import format_confidence

if TYPE_CHECKING:
    from niuu.cli_api_client import CLIAPIClient

_DISPATCH_TABS = ["Queue", "Activity"]


class QueueItem(Widget):
    """A pending run ready for dispatch."""

    DEFAULT_CSS = """
    QueueItem {
        height: auto;
        padding: 1 2;
        border-bottom: solid #27272a;
        background: #18181b;
    }
    """

    def __init__(self, run: dict[str, Any], selected: bool = False, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._run = run
        self._selected = selected

    @property
    def run(self) -> dict[str, Any]:
        return self._run

    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._refresh_display()

    def compose(self) -> ComposeResult:
        yield Static(self._render_content(), id="queue-item-content")

    def _render_content(self) -> str:
        run = self._run
        name = run.get("name", "Unknown")
        run_id = str(run.get("id", ""))[:8]
        confidence = run.get("confidence", 0.0)
        conf_pct = format_confidence(confidence)

        check = "☑" if self._selected else "☐"
        check_color = ACCENT_EMERALD if self._selected else TEXT_MUTED

        return (
            f"[{check_color}]{check}[/]  "
            f"[bold {TEXT_PRIMARY}]{name}[/]  "
            f"[{TEXT_MUTED}]{run_id}[/]  "
            f"[{ACCENT_AMBER}]{conf_pct}[/]"
        )

    def _refresh_display(self) -> None:
        try:
            label = self.query_one("#queue-item-content", Static)
        except Exception:
            return
        label.update(self._render_content())

    def _on_click(self) -> None:
        self._selected = not self._selected
        self._refresh_display()


class ActivityEntry(Widget):
    """A single entry in the dispatch activity log."""

    DEFAULT_CSS = """
    ActivityEntry {
        height: 1;
        padding: 0 2;
    }
    """

    def __init__(self, entry: dict[str, Any], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._entry = entry

    def compose(self) -> ComposeResult:
        entry = self._entry
        action = entry.get("action", "dispatch")
        name = entry.get("name", "")
        timestamp = entry.get("timestamp", "")
        status = entry.get("status", "")

        if status == "success":
            color = ACCENT_EMERALD
        elif status == "failed":
            color = ACCENT_RED
        else:
            color = TEXT_MUTED

        yield Static(
            f"[{TEXT_MUTED}]{timestamp}[/]  [{color}]{action}[/]  [{TEXT_PRIMARY}]{name}[/]"
        )


class DispatchPage(Widget):
    """TUI page for dispatch queue and activity log."""

    DEFAULT_CSS = f"""
    DispatchPage {{
        width: 1fr;
        height: 1fr;
        background: {BG_SECONDARY};
    }}
    DispatchPage #dispatch-content {{
        height: 1fr;
    }}
    DispatchPage #dispatch-config {{
        height: auto;
        padding: 1 2;
    }}
    """

    class BulkDispatchRequested(Message):
        """Fired when bulk dispatch is requested."""

        def __init__(self, run_ids: list[str]) -> None:
            super().__init__()
            self.run_ids = run_ids

    def __init__(
        self,
        client: CLIAPIClient | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._pending_runs: list[dict[str, Any]] = []
        self._activity_log: list[dict[str, Any]] = []
        self._selected_ids: set[str] = set()
        self._active_tab: str = "Queue"
        self._search_query: str = ""
        self._dispatch_config: dict[str, Any] = {
            "max_concurrent": 3,
            "threshold": 0.7,
        }

    @property
    def pending_runs(self) -> list[dict[str, Any]]:
        return list(self._pending_runs)

    @property
    def activity_log(self) -> list[dict[str, Any]]:
        return list(self._activity_log)

    @property
    def selected_ids(self) -> set[str]:
        return set(self._selected_ids)

    @property
    def dispatch_config(self) -> dict[str, Any]:
        return dict(self._dispatch_config)

    @property
    def filtered_pending_runs(self) -> list[dict[str, Any]]:
        """Return pending runs filtered by search query."""
        if not self._search_query:
            return self._pending_runs
        q = self._search_query.lower()
        return [r for r in self._pending_runs if q in r.get("name", "").lower()]

    def compose(self) -> ComposeResult:
        yield NiuuTabs(items=_DISPATCH_TABS, id="dispatch-tabs")
        yield MetricRow(id="dispatch-metrics")
        yield Input(placeholder="Search queue...", id="dispatch-search")
        yield Static(self._render_config(), id="dispatch-config")
        yield VerticalScroll(id="dispatch-content")

    def on_mount(self) -> None:
        self._load_data()

    def load_data(
        self,
        pending: list[dict[str, Any]] | None = None,
        activity: list[dict[str, Any]] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Load data directly (for testing or programmatic use)."""
        if pending is not None:
            self._pending_runs = pending
        if activity is not None:
            self._activity_log = activity
        if config is not None:
            self._dispatch_config.update(config)
        self._update_metrics()
        self._render_content()
        self._update_config_display()

    def _load_data(self) -> None:
        if not self._client:
            return
        try:
            resp = self._client.get("/api/v1/ting/runs/active")
            resp.raise_for_status()
            all_runs = resp.json()
            self._pending_runs = [r for r in all_runs if r.get("status") == "PENDING"]
        except Exception:
            self._pending_runs = []
        self._update_metrics()
        self._render_content()

    def _update_metrics(self) -> None:
        try:
            row = self.query_one("#dispatch-metrics", MetricRow)
        except Exception:
            return
        row.remove_children()

        queued = len(self._pending_runs)
        selected = len(self._selected_ids)
        dispatched = len(self._activity_log)
        max_conc = self._dispatch_config.get("max_concurrent", 3)

        row.mount(
            MetricCard(
                label="Queued",
                value=str(queued),
                icon="📋",
                id="metric-queued",
            )
        )
        row.mount(
            MetricCard(
                label="Selected",
                value=str(selected),
                icon="☑",
                color=ACCENT_EMERALD,
                id="metric-selected",
            )
        )
        row.mount(
            MetricCard(
                label="Dispatched",
                value=str(dispatched),
                icon="🚀",
                color=ACCENT_CYAN,
                id="metric-dispatched",
            )
        )
        row.mount(
            MetricCard(
                label="Max Concurrent",
                value=str(max_conc),
                icon="⚙",
                color=ACCENT_PURPLE,
                id="metric-max-concurrent",
            )
        )

    def _render_config(self) -> str:
        max_conc = self._dispatch_config.get("max_concurrent", 3)
        threshold = self._dispatch_config.get("threshold", 0.7)
        return (
            f"[{TEXT_MUTED}]Config:[/]  "
            f"[{TEXT_SECONDARY}]max concurrent: [{ACCENT_CYAN}]{max_conc}[/][/]  "
            f"[{TEXT_SECONDARY}]threshold: [{ACCENT_AMBER}]{threshold:.0%}[/][/]"
        )

    def _update_config_display(self) -> None:
        try:
            config = self.query_one("#dispatch-config", Static)
        except Exception:
            return
        config.update(self._render_config())

    def _render_content(self) -> None:
        try:
            container = self.query_one("#dispatch-content", VerticalScroll)
        except Exception:
            return
        container.remove_children()

        if self._active_tab == "Queue":
            self._render_queue(container)
        else:
            self._render_activity(container)

    def on_input_changed(self, message: Input.Changed) -> None:
        if message.input.id == "dispatch-search":
            self._search_query = message.value
            self._render_content()

    def _render_queue(self, container: VerticalScroll) -> None:
        filtered = self.filtered_pending_runs
        if not filtered:
            container.mount(
                Static(
                    f"[{TEXT_MUTED}]No pending runs in queue.[/]",
                )
            )
            return

        for run in filtered:
            run_id = str(run.get("id", ""))
            selected = run_id in self._selected_ids
            container.mount(QueueItem(run, selected=selected))

    def _render_activity(self, container: VerticalScroll) -> None:
        if not self._activity_log:
            container.mount(
                Static(
                    f"[{TEXT_MUTED}]No recent activity.[/]",
                )
            )
            return

        for entry in self._activity_log:
            container.mount(ActivityEntry(entry))

    def on_niuu_tabs_tab_selected(self, message: NiuuTabs.TabSelected) -> None:
        self._active_tab = message.label
        self._render_content()

    def toggle_selection(self, run_id: str) -> None:
        """Toggle selection of a run for bulk dispatch."""
        if run_id in self._selected_ids:
            self._selected_ids.discard(run_id)
        else:
            self._selected_ids.add(run_id)
        self._update_metrics()

    def select_all(self) -> None:
        """Select all pending runs."""
        self._selected_ids = {str(r.get("id", "")) for r in self._pending_runs}
        self._update_metrics()
        self._render_content()

    def clear_selection(self) -> None:
        """Clear all selections."""
        self._selected_ids.clear()
        self._update_metrics()
        self._render_content()

    def dispatch_selected(self) -> list[str]:
        """Dispatch all selected runs. Returns list of successfully dispatched IDs."""
        if not self._client:
            return []

        dispatched: list[str] = []
        for run_id in list(self._selected_ids):
            try:
                resp = self._client.post(
                    f"/api/v1/ting/runs/{run_id}/dispatch",
                )
                resp.raise_for_status()
                dispatched.append(run_id)
            except Exception:
                pass

        self._selected_ids -= set(dispatched)
        return dispatched

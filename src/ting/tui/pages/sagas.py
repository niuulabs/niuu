"""Sagas TUI page — list sagas with status, phases, and run counts."""

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

# Status filter tabs.
_SAGA_TABS = ["All", "Active", "Complete", "Failed"]

# Status → color for progress bars.
_STATUS_COLORS: dict[str, str] = {
    "ACTIVE": ACCENT_EMERALD,
    "COMPLETE": ACCENT_CYAN,
    "FAILED": ACCENT_RED,
}


class SagaRow(Widget):
    """A single saga entry in the list."""

    DEFAULT_CSS = """
    SagaRow {
        height: auto;
        padding: 1 2;
        border-bottom: solid #27272a;
        background: #18181b;
    }
    SagaRow:hover {
        background: #27272a;
    }
    """

    class Selected(Message):
        """Fired when a saga row is clicked."""

        def __init__(self, saga_id: str) -> None:
            super().__init__()
            self.saga_id = saga_id

    def __init__(self, saga: dict[str, Any], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._saga = saga

    @property
    def saga(self) -> dict[str, Any]:
        return self._saga

    def compose(self) -> ComposeResult:
        saga = self._saga
        name = saga.get("name", "Unknown")
        status = saga.get("status", "ACTIVE")
        run_count = saga.get("run_count", 0)
        progress = saga.get("progress", "0/0")
        confidence = saga.get("confidence", 0.0)
        saga_id = str(saga.get("id", ""))[:8]

        color = _STATUS_COLORS.get(status, TEXT_MUTED)
        conf_pct = format_confidence(confidence)

        yield Static(
            f"[bold {TEXT_PRIMARY}]{name}[/]  "
            f"[{TEXT_MUTED}]{saga_id}[/]  "
            f"[{color}]{status}[/]  "
            f"[{TEXT_SECONDARY}]Runs: {run_count}[/]  "
            f"[{TEXT_SECONDARY}]Progress: {progress}[/]  "
            f"[{ACCENT_AMBER}]{conf_pct}[/]",
            id="saga-row-content",
        )

    def _on_click(self) -> None:
        self.post_message(self.Selected(str(self._saga.get("id", ""))))


class SagasPage(Widget):
    """TUI page for viewing and managing sagas."""

    DEFAULT_CSS = f"""
    SagasPage {{
        width: 1fr;
        height: 1fr;
        background: {BG_SECONDARY};
    }}
    SagasPage #sagas-search {{
        margin: 0 2;
        height: 3;
    }}
    SagasPage #sagas-list {{
        height: 1fr;
    }}
    SagasPage #sagas-empty {{
        margin: 2 2;
        color: {TEXT_MUTED};
    }}
    """

    class DispatchRequested(Message):
        """Fired when user requests dispatch on a saga."""

        def __init__(self, saga_id: str) -> None:
            super().__init__()
            self.saga_id = saga_id

    class DeleteRequested(Message):
        """Fired when user requests saga deletion."""

        def __init__(self, saga_id: str) -> None:
            super().__init__()
            self.saga_id = saga_id

    def __init__(
        self,
        client: CLIAPIClient | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._sagas: list[dict[str, Any]] = []
        self._filter_status: str = "All"
        self._search_query: str = ""

    @property
    def sagas(self) -> list[dict[str, Any]]:
        return list(self._sagas)

    @property
    def filtered_sagas(self) -> list[dict[str, Any]]:
        """Return sagas filtered by status tab and search query."""
        result = self._sagas
        if self._filter_status != "All":
            target = self._filter_status.upper()
            result = [s for s in result if s.get("status", "").upper() == target]
        if self._search_query:
            q = self._search_query.lower()
            result = [s for s in result if q in s.get("name", "").lower()]
        return result

    def compose(self) -> ComposeResult:
        yield NiuuTabs(items=_SAGA_TABS, id="sagas-tabs")
        yield MetricRow(id="sagas-metrics")
        yield Input(placeholder="Search sagas...", id="sagas-search")
        yield VerticalScroll(id="sagas-list")

    def on_mount(self) -> None:
        self._load_sagas()

    def load_data(self, sagas: list[dict[str, Any]]) -> None:
        """Load saga data directly (for testing or programmatic use)."""
        self._sagas = sagas
        self._update_metrics()
        self._render_list()

    def _load_sagas(self) -> None:
        if not self._client:
            return
        try:
            resp = self._client.get("/api/v1/ting/sagas")
            resp.raise_for_status()
            self._sagas = resp.json()
        except Exception:
            self._sagas = []
        self._update_metrics()
        self._render_list()

    def _update_metrics(self) -> None:
        try:
            row = self.query_one("#sagas-metrics", MetricRow)
        except Exception:
            return
        row.remove_children()

        total = len(self._sagas)
        active = sum(1 for s in self._sagas if s.get("status") == "ACTIVE")
        complete = sum(1 for s in self._sagas if s.get("status") == "COMPLETE")
        failed = sum(1 for s in self._sagas if s.get("status") == "FAILED")

        row.mount(
            MetricCard(
                label="Total",
                value=str(total),
                icon="⚡",
                id="metric-total",
            )
        )
        row.mount(
            MetricCard(
                label="Active",
                value=str(active),
                icon="▶",
                color=ACCENT_EMERALD,
                id="metric-active",
            )
        )
        row.mount(
            MetricCard(
                label="Complete",
                value=str(complete),
                icon="✓",
                color=ACCENT_CYAN,
                id="metric-complete",
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
            container = self.query_one("#sagas-list", VerticalScroll)
        except Exception:
            return
        container.remove_children()

        filtered = self.filtered_sagas
        if not filtered:
            container.mount(
                Static(
                    f"[{TEXT_MUTED}]No sagas found.[/]",
                )
            )
            return

        for saga in filtered:
            container.mount(SagaRow(saga))

    def on_niuu_tabs_tab_selected(self, message: NiuuTabs.TabSelected) -> None:
        self._filter_status = message.label
        self._render_list()

    def on_input_changed(self, message: Input.Changed) -> None:
        if message.input.id == "sagas-search":
            self._search_query = message.value
            self._render_list()

    def dispatch_saga(self, saga_id: str) -> bool:
        """Dispatch a saga via the API. Returns True on success."""
        if not self._client:
            return False
        try:
            resp = self._client.post(
                "/api/v1/ting/dispatch/approve",
                json_body={"saga_id": saga_id},
            )
            resp.raise_for_status()
            return True
        except Exception:
            return False

    def delete_saga(self, saga_id: str) -> bool:
        """Delete a saga via the API. Returns True on success."""
        if not self._client:
            return False
        try:
            resp = self._client.delete(f"/api/v1/ting/sagas/{saga_id}")
            resp.raise_for_status()
            return True
        except Exception:
            return False

"""Review TUI page — live review dashboard for runs in REVIEW state."""

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
from ting.tui._helpers import format_confidence, format_confidence_history

if TYPE_CHECKING:
    from niuu.cli_api_client import CLIAPIClient

_REVIEW_TABS = ["All", "In Review", "Auto-approved", "Escalated"]

# Confidence color thresholds.
_CONFIDENCE_HIGH = 0.8
_CONFIDENCE_MED = 0.5


class ReviewRow(Widget):
    """A single run in review state."""

    DEFAULT_CSS = """
    ReviewRow {
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
        run_id = str(run.get("id", ""))[:8]
        confidence = run.get("confidence", 0.0)
        reviewer_session = run.get("reviewer_session_id") or "—"
        review_round = run.get("review_round", 0)
        status = run.get("status", "REVIEW")
        auto_approved = run.get("auto_approved", False)

        conf_pct = format_confidence(confidence)

        # Confidence color based on threshold.
        if confidence >= _CONFIDENCE_HIGH:
            conf_color = ACCENT_EMERALD
        elif confidence >= _CONFIDENCE_MED:
            conf_color = ACCENT_AMBER
        else:
            conf_color = ACCENT_RED

        status_label = "auto-approved" if auto_approved else status.lower()
        if auto_approved:
            status_color = ACCENT_EMERALD
        elif status == "REVIEW":
            status_color = ACCENT_CYAN
        else:
            status_color = ACCENT_PURPLE

        history_str = format_confidence_history(
            run.get("confidence_history", []),
            TEXT_MUTED,
        )

        yield Static(
            f"[bold {TEXT_PRIMARY}]{name}[/]  "
            f"[{TEXT_MUTED}]{run_id}[/]  "
            f"[{status_color}]{status_label}[/]  "
            f"[{conf_color}]{conf_pct}[/]  "
            f"[{TEXT_SECONDARY}]reviewer: {reviewer_session}[/]  "
            f"[{TEXT_MUTED}]round: {review_round}[/]"
            f"{history_str}",
            id="review-row-content",
        )


class ReviewPage(Widget):
    """TUI page for live review dashboard."""

    DEFAULT_CSS = f"""
    ReviewPage {{
        width: 1fr;
        height: 1fr;
        background: {BG_SECONDARY};
    }}
    ReviewPage #review-list {{
        height: 1fr;
    }}
    """

    class OpenReviewerSession(Message):
        """Fired when user wants to open a reviewer session in Chat."""

        def __init__(self, session_id: str) -> None:
            super().__init__()
            self.session_id = session_id

    def __init__(
        self,
        client: CLIAPIClient | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._runs: list[dict[str, Any]] = []
        self._filter_tab: str = "All"
        self._search_query: str = ""

    @property
    def runs(self) -> list[dict[str, Any]]:
        return list(self._runs)

    @property
    def filtered_runs(self) -> list[dict[str, Any]]:
        result = self._runs
        match self._filter_tab:
            case "In Review":
                result = [
                    r
                    for r in result
                    if r.get("status") == "REVIEW" and not r.get("auto_approved", False)
                ]
            case "Auto-approved":
                result = [r for r in result if r.get("auto_approved", False)]
            case "Escalated":
                result = [r for r in result if r.get("status") == "ESCALATED"]
            case _:
                pass
        if self._search_query:
            q = self._search_query.lower()
            result = [r for r in result if q in r.get("name", "").lower()]
        return result

    def compose(self) -> ComposeResult:
        yield NiuuTabs(items=_REVIEW_TABS, id="review-tabs")
        yield MetricRow(id="review-metrics")
        yield Input(placeholder="Search reviews...", id="review-search")
        yield VerticalScroll(id="review-list")

    def on_mount(self) -> None:
        self._load_data()

    def load_data(self, runs: list[dict[str, Any]]) -> None:
        """Load run data directly (for testing or programmatic use)."""
        self._runs = runs
        self._update_metrics()
        self._render_list()

    def _load_data(self) -> None:
        if not self._client:
            return
        try:
            resp = self._client.get("/api/v1/ting/runs/active")
            resp.raise_for_status()
            all_runs = resp.json()
            self._runs = [
                r
                for r in all_runs
                if r.get("status") in ("REVIEW", "ESCALATED") or r.get("auto_approved", False)
            ]
        except Exception:
            self._runs = []
        self._update_metrics()
        self._render_list()

    def _update_metrics(self) -> None:
        try:
            row = self.query_one("#review-metrics", MetricRow)
        except Exception:
            return
        row.remove_children()

        total = len(self._runs)
        in_review = sum(
            1
            for r in self._runs
            if r.get("status") == "REVIEW" and not r.get("auto_approved", False)
        )
        auto_approved = sum(1 for r in self._runs if r.get("auto_approved", False))
        escalated = sum(1 for r in self._runs if r.get("status") == "ESCALATED")

        row.mount(
            MetricCard(
                label="Total",
                value=str(total),
                icon="👁",
                id="metric-total",
            )
        )
        row.mount(
            MetricCard(
                label="In Review",
                value=str(in_review),
                icon="🔍",
                color=ACCENT_CYAN,
                id="metric-in-review",
            )
        )
        row.mount(
            MetricCard(
                label="Auto-approved",
                value=str(auto_approved),
                icon="✓",
                color=ACCENT_EMERALD,
                id="metric-auto-approved",
            )
        )
        row.mount(
            MetricCard(
                label="Escalated",
                value=str(escalated),
                icon="⚠",
                color=ACCENT_AMBER,
                id="metric-escalated",
            )
        )

    def _render_list(self) -> None:
        try:
            container = self.query_one("#review-list", VerticalScroll)
        except Exception:
            return
        container.remove_children()

        filtered = self.filtered_runs
        if not filtered:
            container.mount(
                Static(
                    f"[{TEXT_MUTED}]No runs in review.[/]",
                )
            )
            return

        for run in filtered:
            container.mount(ReviewRow(run))

    def on_niuu_tabs_tab_selected(self, message: NiuuTabs.TabSelected) -> None:
        self._filter_tab = message.label
        self._render_list()

    def on_input_changed(self, message: Input.Changed) -> None:
        if message.input.id == "review-search":
            self._search_query = message.value
            self._render_list()

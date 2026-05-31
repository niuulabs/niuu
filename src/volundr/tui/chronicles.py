"""Chronicles page — timeline events with filter tabs and search."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static

from cli.tui.theme import (
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_EMERALD,
    ACCENT_INDIGO,
    ACCENT_PURPLE,
    ACCENT_RED,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from cli.tui.widgets.metric_card import MetricCard, MetricRow
from cli.tui.widgets.tabs import NiuuTabs
from volundr.tui._utils import format_count

CHRONICLE_FILTERS = ("All", "Session", "Message", "File", "Git", "Terminal", "Error")
FILTER_TYPE_MAP = {
    "All": None,
    "Session": "session",
    "Message": "message",
    "File": "file",
    "Git": "git",
    "Terminal": "terminal",
    "Error": "error",
}

EVENT_STYLES: dict[str, tuple[str, str]] = {
    "session": ("◉", ACCENT_CYAN),
    "message": ("◈", ACCENT_PURPLE),
    "file": ("▶", ACCENT_EMERALD),
    "git": ("⊕", ACCENT_INDIGO),
    "terminal": ("▸", ACCENT_AMBER),
    "error": ("✗", ACCENT_RED),
}
DEFAULT_EVENT_STYLE = ("○", TEXT_MUTED)


def _format_elapsed(seconds: int) -> str:
    """Format seconds into human-friendly string (e.g. 5s, 2m30s, 1h05m)."""

    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


@dataclass
class ChronicleEvent:
    """A single chronicle timeline event."""

    event_type: str = "message"  # session, message, file, git, terminal, error
    label: str = ""
    action: str = ""
    insertions: int = 0
    deletions: int = 0
    git_hash: str = ""
    elapsed: int = 0  # seconds since session start
    tokens: int = 0


class TimelineEntry(Widget):
    """Renders a single timeline event with connector."""

    DEFAULT_CSS = """
    TimelineEntry { height: auto; padding: 0 0 0 2; }
    TimelineEntry.selected { background: #27272a; }
    TimelineEntry .te-main { height: 1; }
    TimelineEntry .te-meta { height: 1; }
    """

    def __init__(self, event: ChronicleEvent, *, selected: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._event = event
        if selected:
            self.add_class("selected")

    def compose(self) -> ComposeResult:
        e = self._event
        icon, color = EVENT_STYLES.get(e.event_type, DEFAULT_EVENT_STYLE)
        elapsed = _format_elapsed(e.elapsed)

        main_line = (
            f"[{TEXT_MUTED}]{elapsed:>10}[/]  "
            f"[bold {color}]{icon}[/]  "
            f"[bold {TEXT_PRIMARY}]{e.label}[/]  "
            f"[bold {color}]{e.event_type}[/]"
        )
        yield Static(main_line, classes="te-main")

        # Metadata line
        meta_parts: list[str] = []
        if e.tokens > 0:
            meta_parts.append(f"◈ {format_count(e.tokens)} tokens")
        if e.action:
            meta_parts.append(e.action)
        if e.insertions > 0 or e.deletions > 0:
            meta_parts.append(f"+{e.insertions}/-{e.deletions}")
        if e.git_hash:
            display = e.git_hash[:8] if len(e.git_hash) > 8 else e.git_hash
            meta_parts.append(display)

        connector = f"[{ACCENT_CYAN}]│[/]"
        meta_text = f"{'':>10}  {connector}  [{TEXT_MUTED}]{'  '.join(meta_parts)}[/]"
        yield Static(meta_text, classes="te-meta")


class ChroniclesPage(Widget):
    """Chronicles timeline page with filter tabs and search.

    Keybindings:
        j/k         navigate events
        tab         cycle filter
        /           search
        G/g         jump to last/first
        r           refresh
    """

    DEFAULT_CSS = """
    ChroniclesPage { width: 1fr; height: 1fr; }
    ChroniclesPage #chron-metrics { height: auto; }
    ChroniclesPage #chron-tabs { height: auto; }
    ChroniclesPage #chron-search { height: auto; display: none; }
    ChroniclesPage #chron-search.visible { display: block; }
    ChroniclesPage #chron-timeline { height: 1fr; overflow-y: auto; }
    ChroniclesPage #chron-empty { color: #71717a; padding: 2; }
    """

    cursor: reactive[int] = reactive(0)
    filter_index: reactive[int] = reactive(0)
    searching: reactive[bool] = reactive(False)

    def __init__(self, events: list[ChronicleEvent] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._all_events: list[ChronicleEvent] = events or []
        self._filtered: list[ChronicleEvent] = list(self._all_events)
        self._search_term = ""
        self._mounted = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield MetricRow(id="chron-metrics")
            yield NiuuTabs(list(CHRONICLE_FILTERS), id="chron-tabs")
            with Horizontal(id="chron-search"):
                yield Input(placeholder="Search events…", id="chron-search-input")
            yield VerticalScroll(id="chron-timeline")

    def on_mount(self) -> None:
        self._mounted = True
        self._rebuild_metrics()
        self._apply_filter()

    # ── Data ────────────────────────────────────────────────

    def set_events(self, events: list[ChronicleEvent]) -> None:
        self._all_events = list(events)
        self._apply_filter()
        self._rebuild_metrics()

    # ── Filter ──────────────────────────────────────────────

    def _apply_filter(self) -> None:
        type_filter = FILTER_TYPE_MAP[CHRONICLE_FILTERS[self.filter_index]]
        search = self._search_term.lower()
        result: list[ChronicleEvent] = []
        for e in self._all_events:
            if type_filter and e.event_type != type_filter:
                continue
            if search and not _event_matches(e, search):
                continue
            result.append(e)
        self._filtered = result
        if self.cursor >= len(self._filtered):
            self.cursor = max(0, len(self._filtered) - 1)
        self._rebuild_timeline()

    def _rebuild_timeline(self) -> None:
        if not self._mounted:
            return
        try:
            container = self.query_one("#chron-timeline", VerticalScroll)
        except Exception:
            return
        container.remove_children()
        if not self._filtered:
            msg = f"[{TEXT_MUTED}]  No events match the current filter[/]"
            container.mount(Static(msg))
            return
        for i, event in enumerate(self._filtered):
            container.mount(TimelineEntry(event, selected=(i == self.cursor)))

    def _rebuild_metrics(self) -> None:
        if not self._mounted:
            return
        try:
            row = self.query_one("#chron-metrics", MetricRow)
        except Exception:
            return
        row.remove_children()
        counts = _count_by_type(self._all_events)
        row.mount(MetricCard("Total", str(len(self._all_events)), icon="◷", color=ACCENT_AMBER))
        row.mount(
            MetricCard(
                "Messages",
                str(counts.get("message", 0)),
                icon="◈",
                color=ACCENT_PURPLE,
            )
        )
        row.mount(MetricCard("Files", str(counts.get("file", 0)), icon="▶", color=ACCENT_EMERALD))
        row.mount(MetricCard("Git", str(counts.get("git", 0)), icon="⊕", color=ACCENT_INDIGO))

    # ── Tab selection ────────────────────────────────────────

    def on_niuu_tabs_tab_selected(self, event: NiuuTabs.TabSelected) -> None:
        self.filter_index = event.index
        self._apply_filter()

    # ── Search ──────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "chron-search-input":
            self._search_term = event.value
            self._apply_filter()

    def action_toggle_search(self) -> None:
        self.searching = not self.searching

    def watch_searching(self, value: bool) -> None:
        try:
            box = self.query_one("#chron-search", Horizontal)
        except Exception:
            return
        if value:
            box.add_class("visible")
            with suppress(Exception):
                self.query_one("#chron-search-input", Input).focus()
        else:
            box.remove_class("visible")

    # ── Cursor ──────────────────────────────────────────────

    def watch_cursor(self) -> None:
        self._rebuild_timeline()

    def action_cursor_up(self) -> None:
        if self.cursor > 0:
            self.cursor -= 1

    def action_cursor_down(self) -> None:
        if self.cursor < len(self._filtered) - 1:
            self.cursor += 1

    def action_cursor_top(self) -> None:
        self.cursor = 0

    def action_cursor_bottom(self) -> None:
        self.cursor = max(0, len(self._filtered) - 1)

    def action_refresh(self) -> None:
        self._apply_filter()
        self._rebuild_metrics()


def _event_matches(e: ChronicleEvent, search: str) -> bool:
    return (
        search in e.label.lower()
        or search in e.action.lower()
        or search in e.event_type.lower()
        or search in e.git_hash.lower()
    )


def _count_by_type(events: list[ChronicleEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        counts[e.event_type] = counts.get(e.event_type, 0) + 1
    return counts

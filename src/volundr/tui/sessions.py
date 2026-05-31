"""Sessions page — DataTable with status badges, filters, search, and actions."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static

from cli.tui.theme import (
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_EMERALD,
    ACCENT_PURPLE,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from cli.tui.widgets.metric_card import MetricCard, MetricRow
from cli.tui.widgets.status_badge import _DEFAULT_INDICATOR, _STATUS_MAP
from cli.tui.widgets.tabs import NiuuTabs
from volundr.tui._utils import format_count

SESSION_FILTERS = ("All", "Running", "Stopped", "Error")
FILTER_STATUS_MAP = {"All": None, "Running": "running", "Stopped": "stopped", "Error": "error"}


@dataclass
class SessionData:
    """Lightweight session data for the TUI."""

    id: str = ""
    name: str = ""
    status: str = "stopped"
    model: str = ""
    repo: str = ""
    branch: str = ""
    tokens_used: int = 0
    context_key: str = ""
    context_name: str = ""
    error: str = ""


def _demo_sessions() -> list[SessionData]:
    """Placeholder sessions for when no API is available."""
    return [
        SessionData(
            id="a1b2c3d4",
            name="feat/auth-flow",
            status="running",
            model="claude-sonnet-4",
            repo="niuu/volundr",
            branch="feat/auth-flow",
            tokens_used=128_450,
            context_key="demo",
        ),
        SessionData(
            id="b2c3d4e5",
            name="fix/ws-reconnect",
            status="running",
            model="claude-sonnet-4",
            repo="niuu/volundr",
            branch="fix/ws-reconnect",
            tokens_used=67_200,
            context_key="demo",
        ),
        SessionData(
            id="c3d4e5f6",
            name="refactor/api-client",
            status="stopped",
            model="claude-opus-4",
            repo="niuu/hlidskjalf",
            branch="refactor/api",
            tokens_used=342_100,
            context_key="demo",
        ),
        SessionData(
            id="d4e5f6a7",
            name="feat/tui-client",
            status="running",
            model="claude-opus-4",
            repo="niuu/volundr",
            branch="feat/niu-130-go-tui",
            tokens_used=891_200,
            context_key="demo",
        ),
        SessionData(
            id="e5f6a7b8",
            name="docs/api-reference",
            status="completed",
            model="claude-haiku-3.5",
            repo="niuu/docs",
            branch="docs/api",
            tokens_used=15_800,
            context_key="demo",
        ),
        SessionData(
            id="f6a7b8c9",
            name="fix/migration-lock",
            status="error",
            model="claude-sonnet-4",
            repo="niuu/volundr",
            branch="fix/migration",
            tokens_used=23_400,
            context_key="demo",
            error="Pod OOMKilled after 4.2GB memory usage",
        ),
    ]


class SessionRow(Widget):
    """A single session row with status badge."""

    DEFAULT_CSS = """
    SessionRow { height: 3; padding: 0 1; }
    SessionRow.selected { background: #27272a; }
    SessionRow .sr-line1 { height: 1; }
    SessionRow .sr-line2 { height: 1; }
    """

    def __init__(self, session: SessionData, *, selected: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._session = session
        if selected:
            self.add_class("selected")

    def compose(self) -> ComposeResult:
        s = self._session
        badge_dot, badge_color = _status_dot(s.status)
        model_part = f"[{ACCENT_PURPLE}]{s.model}[/]" if s.model else ""
        line1 = (
            f"  [{badge_color}]{badge_dot}[/] [{badge_color}]{s.status}[/]  "
            f"[bold {TEXT_PRIMARY}]{s.name}[/]  {model_part}"
        )
        repo_part = f"[{ACCENT_CYAN}]{s.repo}[/]" if s.repo else ""
        branch_part = f"[{TEXT_MUTED}]{s.branch}[/]" if s.branch else ""
        tokens_part = f"[{TEXT_MUTED}]{format_count(s.tokens_used)} tokens[/]"
        line2 = f"     {repo_part}  {branch_part}  {tokens_part}"
        yield Static(line1, classes="sr-line1")
        yield Static(line2, classes="sr-line2")


def _status_dot(status: str) -> tuple[str, str]:
    """Return (dot char, color hex) for a status string."""
    return _STATUS_MAP.get(status, _DEFAULT_INDICATOR)


class SessionsPage(Widget):
    """Sessions list page with filter tabs, search, metric cards, and actions.

    Keybindings (NORMAL mode):
        j/k     navigate
        /       search
        tab     cycle filter
        s       start session
        x       stop session
        d       delete session
        r       refresh
        c       cycle context filter
    """

    DEFAULT_CSS = """
    SessionsPage { width: 1fr; height: 1fr; }
    SessionsPage #sessions-metrics { height: auto; }
    SessionsPage #sessions-tabs { height: auto; }
    SessionsPage #sessions-search { height: auto; display: none; }
    SessionsPage #sessions-search.visible { display: block; }
    SessionsPage #sessions-list { height: 1fr; overflow-y: auto; }
    SessionsPage #sessions-empty { color: #71717a; padding: 2 0; }
    SessionsPage #sessions-search-input { width: 100%; }
    """

    cursor: reactive[int] = reactive(0)
    filter_index: reactive[int] = reactive(0)
    searching: reactive[bool] = reactive(False)

    class SessionAction(Message):
        """Emitted when a session action is requested."""

        def __init__(self, action: str, session_id: str) -> None:
            super().__init__()
            self.action = action
            self.session_id = session_id

    def __init__(
        self,
        sessions: list[SessionData] | None = None,
        api_client: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._all_sessions: list[SessionData] = (
            sessions if sessions is not None else _demo_sessions()
        )
        self._filtered: list[SessionData] = list(self._all_sessions)
        self._api_client = api_client
        self._search_term = ""
        self._context_filter = ""
        self._mounted = False

    # ── Compose ──────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical():
            yield MetricRow(id="sessions-metrics")
            yield NiuuTabs(list(SESSION_FILTERS), id="sessions-tabs")
            with Horizontal(id="sessions-search"):
                yield Input(placeholder="Search sessions…", id="sessions-search-input")
            yield Vertical(id="sessions-list")

    def on_mount(self) -> None:
        self._mounted = True
        self._rebuild_metrics()
        self._apply_filter()

    # ── Data setters ────────────────────────────────────────

    def set_sessions(self, sessions: list[SessionData]) -> None:
        """Replace all sessions and refresh the view."""
        self._all_sessions = list(sessions)
        self._apply_filter()
        self._rebuild_metrics()

    # ── Filter / search ─────────────────────────────────────

    def _apply_filter(self) -> None:
        status_filter = FILTER_STATUS_MAP[SESSION_FILTERS[self.filter_index]]
        search = self._search_term.lower()
        result: list[SessionData] = []
        for s in self._all_sessions:
            if status_filter and s.status != status_filter:
                continue
            if self._context_filter and s.context_key != self._context_filter:
                continue
            if search and not _session_matches_search(s, search):
                continue
            result.append(s)
        self._filtered = result
        if self.cursor >= len(self._filtered):
            self.cursor = max(0, len(self._filtered) - 1)
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        if not self._mounted:
            return
        try:
            container = self.query_one("#sessions-list", Vertical)
        except Exception:
            return
        container.remove_children()
        if not self._filtered:
            container.mount(Static("[#71717a]  No sessions found[/]"))
            return
        for i, sess in enumerate(self._filtered):
            container.mount(SessionRow(sess, selected=(i == self.cursor)))

    def _rebuild_metrics(self) -> None:
        if not self._mounted:
            return
        try:
            row = self.query_one("#sessions-metrics", MetricRow)
        except Exception:
            return
        row.remove_children()
        running = sum(1 for s in self._all_sessions if s.status == "running")
        stopped = sum(1 for s in self._all_sessions if s.status == "stopped")
        total_tokens = sum(s.tokens_used for s in self._all_sessions)
        row.mount(MetricCard("Total", str(len(self._all_sessions)), icon="◉", color=ACCENT_AMBER))
        row.mount(MetricCard("Running", str(running), icon="▶", color=ACCENT_EMERALD))
        row.mount(MetricCard("Stopped", str(stopped), icon="■", color=TEXT_MUTED))
        row.mount(MetricCard("Tokens", format_count(total_tokens), icon="◈", color=ACCENT_AMBER))

    # ── Tab selection ────────────────────────────────────────

    def on_niuu_tabs_tab_selected(self, event: NiuuTabs.TabSelected) -> None:
        self.filter_index = event.index
        self._apply_filter()

    # ── Search ──────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "sessions-search-input":
            self._search_term = event.value
            self._apply_filter()

    # ── Cursor movement ──────────────────────────────────────

    def watch_cursor(self) -> None:
        self._rebuild_list()

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

    # ── Actions ──────────────────────────────────────────────

    def _selected_session(self) -> SessionData | None:
        if not self._filtered or self.cursor >= len(self._filtered):
            return None
        return self._filtered[self.cursor]

    def action_start_session(self) -> None:
        sess = self._selected_session()
        if sess:
            self.post_message(self.SessionAction("start", sess.id))

    def action_stop_session(self) -> None:
        sess = self._selected_session()
        if sess:
            self.post_message(self.SessionAction("stop", sess.id))

    def action_delete_session(self) -> None:
        sess = self._selected_session()
        if sess:
            self.post_message(self.SessionAction("delete", sess.id))

    def action_toggle_search(self) -> None:
        self.searching = not self.searching

    def watch_searching(self, value: bool) -> None:
        try:
            box = self.query_one("#sessions-search", Horizontal)
        except Exception:
            return
        if value:
            box.add_class("visible")
            with suppress(Exception):
                self.query_one("#sessions-search-input", Input).focus()
        else:
            box.remove_class("visible")

    def action_refresh(self) -> None:
        """Trigger a session reload via the API client."""
        self._apply_filter()
        self._rebuild_metrics()

    def action_cycle_filter(self) -> None:
        idx = (self.filter_index + 1) % len(SESSION_FILTERS)
        try:
            self.query_one("#sessions-tabs", NiuuTabs).select(idx)
        except Exception:
            self.filter_index = idx
            self._apply_filter()

    def action_cycle_context(self) -> None:
        """Cycle through available context filters."""
        contexts = sorted({s.context_key for s in self._all_sessions if s.context_key})
        if not contexts:
            return
        if self._context_filter == "" or self._context_filter not in contexts:
            self._context_filter = contexts[0]
        else:
            idx = contexts.index(self._context_filter)
            if idx + 1 < len(contexts):
                self._context_filter = contexts[idx + 1]
            else:
                self._context_filter = ""
        self._apply_filter()


def _session_matches_search(s: SessionData, search: str) -> bool:
    """Case-insensitive substring match on name/repo/model/context."""
    return (
        search in s.name.lower()
        or search in s.repo.lower()
        or search in s.model.lower()
        or search in s.context_key.lower()
    )

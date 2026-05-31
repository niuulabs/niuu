"""Terminal page — PTY over WebSocket with multi-tab, scrollback, Insert/Normal modes."""

from __future__ import annotations
from contextlib import suppress

from dataclasses import dataclass, field
from typing import Any

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from cli.tui.theme import (
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_EMERALD,
    TEXT_MUTED,
)

DEFAULT_TERM_COLS = 80
DEFAULT_TERM_ROWS = 24
MAX_SCROLLBACK_LINES = 10_000


@dataclass
class TerminalTab:
    """State for a single terminal tab."""

    label: str = "shell"
    terminal_id: str = ""
    session_id: str = ""
    ws_url: str = ""
    conn_state: str = "disconnected"  # disconnected, connecting, connected
    conn_error: str = ""
    lines: list[str] = field(default_factory=list)
    cursor_row: int = 0
    cursor_col: int = 0


class TerminalTabBar(Widget):
    """Horizontal tab bar for terminal tabs."""

    DEFAULT_CSS = """
    TerminalTabBar {
        height: 1;
        background: #18181b;
        border-bottom: solid #27272a;
    }
    """

    def __init__(self, tabs: list[TerminalTab], active: int = 0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tabs = tabs
        self._active = active

    def compose(self) -> ComposeResult:
        yield Static(self._render_bar(), id="term-tab-bar-text")

    def update_tabs(self, tabs: list[TerminalTab], active: int) -> None:
        self._tabs = tabs
        self._active = active
        with suppress(Exception):
            self.query_one("#term-tab-bar-text", Static).update(self._render_bar())

    def _render_bar(self) -> str:
        parts: list[str] = []
        for i, tab in enumerate(self._tabs):
            conn_dot = _conn_dot(tab.conn_state)
            if i == self._active:
                parts.append(f"[bold {ACCENT_AMBER}] {conn_dot} {tab.label} [/]")
            else:
                parts.append(f"[{TEXT_MUTED}] {conn_dot} {tab.label} [/]")
        return " ".join(parts)


def _conn_dot(state: str) -> str:
    match state:
        case "connected":
            return f"[{ACCENT_EMERALD}]●[/]"
        case "connecting":
            return f"[{ACCENT_AMBER}]◐[/]"
        case _:
            return f"[{TEXT_MUTED}]○[/]"
    raise AssertionError("Unreachable _conn_dot fallthrough")


class TerminalView(Widget):
    """The terminal display area showing PTY output lines."""

    DEFAULT_CSS = """
    TerminalView {
        width: 1fr;
        height: 1fr;
        background: #09090b;
        border: round #27272a;
        padding: 0 1;
        overflow-y: auto;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lines: list[str] = []
        self._scroll_offset: int = 0

    def compose(self) -> ComposeResult:
        yield Static("", id="term-output")

    def set_lines(self, lines: list[str], scroll_offset: int = 0) -> None:
        self._lines = lines
        self._scroll_offset = scroll_offset
        self._refresh_display()

    def _refresh_display(self) -> None:
        try:
            output = self.query_one("#term-output", Static)
        except Exception:
            return
        if not self._lines:
            output.update(f"[{TEXT_MUTED}]No terminal output yet[/]")
            return
        # Show lines accounting for scroll offset.
        end = max(0, len(self._lines) - self._scroll_offset)
        start = max(0, end - DEFAULT_TERM_ROWS)
        visible = self._lines[start:end]
        output.update("\n".join(visible) if visible else f"[{TEXT_MUTED}]…[/]")


class TerminalPage(Widget):
    """Full terminal page with multi-tab PTY, Insert/Normal modes, and scrollback.

    Keybindings:
        Ctrl+t      new tab
        Ctrl+w      close tab
        Tab         switch to next tab
        Shift+Tab   switch to previous tab
        i / Insert  enter insert mode
        Esc         exit insert mode
        j/k         scroll in normal mode
        G/g         jump to bottom/top
    """

    DEFAULT_CSS = """
    TerminalPage { width: 1fr; height: 1fr; }
    TerminalPage #term-mode-bar {
        height: 1; padding: 0 2; background: #18181b;
    }
    """

    insert_mode: reactive[bool] = reactive(False)
    active_tab: reactive[int] = reactive(0)

    class TabCreated(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    class TabClosed(Message):
        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    class KeystrokeSent(Message):
        """Fired when a keystroke should be forwarded to the PTY."""

        def __init__(self, data: str, tab_index: int) -> None:
            super().__init__()
            self.data = data
            self.tab_index = tab_index

    def __init__(self, tabs: list[TerminalTab] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tabs: list[TerminalTab] = tabs if tabs is not None else [TerminalTab(label="shell-1")]
        self._scroll_offset: int = 0
        self._mounted = False

    @property
    def tabs(self) -> list[TerminalTab]:
        return list(self._tabs)

    @property
    def current_tab(self) -> TerminalTab | None:
        if not self._tabs or self.active_tab >= len(self._tabs):
            return None
        return self._tabs[self.active_tab]

    def compose(self) -> ComposeResult:
        yield TerminalTabBar(self._tabs, self.active_tab, id="term-tab-bar")
        yield TerminalView(id="term-view")
        yield Static(self._mode_text(), id="term-mode-bar")

    def on_mount(self) -> None:
        self._mounted = True
        self._refresh_view()

    # ── Tab management ──────────────────────────────────────

    def action_new_tab(self) -> None:
        idx = len(self._tabs)
        tab = TerminalTab(label=f"shell-{idx + 1}")
        self._tabs.append(tab)
        self.active_tab = idx
        self.post_message(self.TabCreated(idx))
        self._refresh_tab_bar()
        self._refresh_view()

    def action_close_tab(self) -> None:
        if len(self._tabs) <= 1:
            return
        closed = self.active_tab
        self._tabs.pop(closed)
        if self.active_tab >= len(self._tabs):
            self.active_tab = len(self._tabs) - 1
        self.post_message(self.TabClosed(closed))
        self._refresh_tab_bar()
        self._refresh_view()

    def action_next_tab(self) -> None:
        if self._tabs:
            self.active_tab = (self.active_tab + 1) % len(self._tabs)
            self._scroll_offset = 0
            self._refresh_tab_bar()
            self._refresh_view()

    def action_prev_tab(self) -> None:
        if self._tabs:
            self.active_tab = (self.active_tab - 1) % len(self._tabs)
            self._scroll_offset = 0
            self._refresh_tab_bar()
            self._refresh_view()

    # ── Mode management ─────────────────────────────────────

    def action_enter_insert(self) -> None:
        self.insert_mode = True
        self._update_mode_bar()

    def action_exit_insert(self) -> None:
        self.insert_mode = False
        self._update_mode_bar()

    # ── Output management ────────────────────────────────────

    def append_output(self, text: str, tab_index: int | None = None) -> None:
        """Append PTY output text to a tab's line buffer."""
        idx = tab_index if tab_index is not None else self.active_tab
        if idx < 0 or idx >= len(self._tabs):
            return
        tab = self._tabs[idx]
        new_lines = text.split("\n")
        if tab.lines and new_lines:
            tab.lines[-1] += new_lines[0]
            tab.lines.extend(new_lines[1:])
        else:
            tab.lines.extend(new_lines)
        # Enforce scrollback limit.
        if len(tab.lines) > MAX_SCROLLBACK_LINES:
            tab.lines = tab.lines[-MAX_SCROLLBACK_LINES:]
        if idx == self.active_tab:
            self._refresh_view()

    def set_tab_state(self, tab_index: int, conn_state: str, error: str = "") -> None:
        """Update a tab's connection state."""
        if tab_index < 0 or tab_index >= len(self._tabs):
            return
        self._tabs[tab_index].conn_state = conn_state
        self._tabs[tab_index].conn_error = error
        self._refresh_tab_bar()

    # ── Scroll ──────────────────────────────────────────────

    def action_scroll_up(self) -> None:
        tab = self.current_tab
        if tab and self._scroll_offset < len(tab.lines) - 1:
            self._scroll_offset += 1
            self._refresh_view()

    def action_scroll_down(self) -> None:
        if self._scroll_offset > 0:
            self._scroll_offset -= 1
            self._refresh_view()

    def action_scroll_top(self) -> None:
        tab = self.current_tab
        if tab:
            self._scroll_offset = max(0, len(tab.lines) - 1)
            self._refresh_view()

    def action_scroll_bottom(self) -> None:
        self._scroll_offset = 0
        self._refresh_view()

    # ── Refresh helpers ──────────────────────────────────────

    def _refresh_view(self) -> None:
        if not self._mounted:
            return
        tab = self.current_tab
        try:
            view = self.query_one("#term-view", TerminalView)
        except Exception:
            return
        if tab is None:
            view.set_lines([])
            return
        view.set_lines(tab.lines, self._scroll_offset)

    def _refresh_tab_bar(self) -> None:
        if not self._mounted:
            return
        try:
            bar = self.query_one("#term-tab-bar", TerminalTabBar)
        except Exception:
            return
        bar.update_tabs(self._tabs, self.active_tab)

    def _mode_text(self) -> str:
        if self.insert_mode:
            return (
                f"[bold {ACCENT_EMERALD}]-- INSERT --[/]"
                f"  [{TEXT_MUTED}]Esc: normal  Ctrl+t: new tab[/]"
            )
        return (
            f"[bold {ACCENT_CYAN}]-- NORMAL --[/]"
            f"  [{TEXT_MUTED}]i: insert  Ctrl+t: new tab"
            f"  j/k: scroll[/]"
        )

    def _update_mode_bar(self) -> None:
        with suppress(Exception):
            self.query_one("#term-mode-bar", Static).update(self._mode_text())

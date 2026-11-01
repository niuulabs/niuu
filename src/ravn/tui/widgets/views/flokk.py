"""FlokkView — Flokk sidebar list with status dots, progress bars, and section dividers."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from ravn.tui.utils import iter_bar

# Status dot markup per ravn state
_STATE_DOT: dict[str, str] = {
    "running": "[bold #f59e0b]●[/]",
    "thinking": "[bold #06b6d4]●[/]",
    "idle": "[#3f3f46]●[/]",
    "error": "[bold #ef4444]●[/]",
    "connecting": "[#f59e0b]◌[/]",
    "disconnected": "[#3f3f46]○[/]",
}

# Iter bar color per state
_ITER_COLOR: dict[str, str] = {
    "running": "#f59e0b",
    "thinking": "#06b6d4",
}


class FlokkView(Widget):
    """Scrollable Flokk sidebar: ravens with status dots, progress bars, section dividers.

    Keyboard navigation: j/k move selection, Enter opens chat for selected ravn.
    Clicking a row also selects it.
    """

    class RavnSelected(Message):
        """Posted when the user selects a different ravn (keyboard or click)."""

        def __init__(self, conn: Any) -> None:
            super().__init__()
            self.conn = conn

    class _RowClicked(Message):
        """Internal — emitted by _RavnRow so FlokkView can update selection."""

        def __init__(self, idx: int) -> None:
            super().__init__()
            self.idx = idx

    DEFAULT_CSS = """
    FlokkView {
        height: 1fr;
        width: 1fr;
        background: #09090b;
    }
    FlokkView VerticalScroll {
        background: #09090b;
        scrollbar-size: 1 1;
        scrollbar-color: #27272a;
        scrollbar-color-hover: #3f3f46;
    }
    FlokkView .fv-section {
        height: 1;
        padding: 0 1;
        color: #52525b;
        text-style: bold;
    }
    FlokkView ._ravn-row {
        height: auto;
        padding: 1 1 0 1;
    }
    FlokkView ._ravn-row:hover {
        background: #111113;
    }
    FlokkView ._ravn-row--selected {
        background: #111113;
    }
    FlokkView .fv-empty {
        height: 1;
        padding: 0 2;
        color: #3f3f46;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("j", "select_next", "Next", show=False),
        Binding("k", "select_prev", "Prev", show=False),
        Binding("enter", "open_chat", "Chat", show=False),
        Binding("g", "ghost_mode", "Ghost"),
    ]

    _selected_idx: reactive[int] = reactive(0)

    def __init__(self, flokk: Any | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._flokk = flokk

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="fv-scroll"):
            yield Static("[#3f3f46]  loading…[/]", classes="fv-empty")

    def on_mount(self) -> None:
        self.set_interval(2.0, self._rebuild)
        if self._flokk:
            self._flokk.on_event(lambda *_: self.call_after_refresh(self._rebuild))
        self._rebuild()

    # ------------------------------------------------------------------
    # Rebuilding the list
    # ------------------------------------------------------------------

    def _rebuild(self) -> None:
        try:
            scroll = self.query_one("#fv-scroll", VerticalScroll)
        except Exception:
            return

        conns = self._flokk.connections() if self._flokk else []

        if conns and self._selected_idx >= len(conns):
            self._selected_idx = len(conns) - 1

        # Derive the flokk group name from the common host suffix
        flokk_name = _derive_flokk_name(conns)

        widgets: list[Widget] = []

        # --- Ravens section ---
        widgets.append(Static(f"  {flokk_name.upper()}", classes="fv-section"))
        if conns:
            for i, conn in enumerate(conns):
                widgets.append(_RavnRow(conn, selected=(i == self._selected_idx), idx=i))
        else:
            widgets.append(
                Static(
                    "  [#3f3f46]no ravens connected[/]\n"
                    "\n"
                    "  [#52525b]connect a ravn:[/]\n"
                    "  [#3f3f46]:[/][#71717a]connect host:7477[/]\n"
                    "\n"
                    "  [#3f3f46]j/k[/] [#52525b]navigate[/]  "
                    "[#3f3f46]↵[/] [#52525b]open chat[/]\n"
                    "  [#3f3f46]^w v[/] [#52525b]vsplit[/]  "
                    "[#3f3f46]^w s[/] [#52525b]hsplit[/]",
                    classes="fv-empty",
                )
            )

        scroll.remove_children()
        scroll.mount(*widgets)

    # ------------------------------------------------------------------
    # Row click handler
    # ------------------------------------------------------------------

    def on_flokk_view__row_clicked(self, msg: _RowClicked) -> None:
        self._selected_idx = msg.idx
        self._rebuild()
        self._emit_selected()

    # ------------------------------------------------------------------
    # Keyboard actions
    # ------------------------------------------------------------------

    def action_select_next(self) -> None:
        conns = self._flokk.connections() if self._flokk else []
        if not conns:
            return
        self._selected_idx = (self._selected_idx + 1) % len(conns)
        self._rebuild()
        self._emit_selected()

    def action_select_prev(self) -> None:
        conns = self._flokk.connections() if self._flokk else []
        if not conns:
            return
        self._selected_idx = (self._selected_idx - 1) % len(conns)
        self._rebuild()
        self._emit_selected()

    def action_open_chat(self) -> None:
        conn = self.get_selected_connection()
        if conn:
            self._emit_selected()

    def action_ghost_mode(self) -> None:
        conn = self.get_selected_connection()
        if conn and hasattr(self.app, "GhostMode"):
            self.app.post_message(self.app.GhostMode(conn.host, conn.port))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_selected_connection(self) -> Any | None:
        if not self._flokk:
            return None
        conns = self._flokk.connections()
        if not conns or self._selected_idx >= len(conns):
            return None
        return conns[self._selected_idx]

    def _emit_selected(self) -> None:
        conn = self.get_selected_connection()
        if conn:
            self.post_message(self.RavnSelected(conn))


# ---------------------------------------------------------------------------
# Helper widgets
# ---------------------------------------------------------------------------


class _RavnRow(Static):
    """Multi-line Static representing one ravn in the sidebar."""

    def __init__(self, conn: Any, selected: bool, idx: int, **kwargs: object) -> None:
        markup = _build_ravn_markup(conn, selected)
        classes = "_ravn-row" + (" _ravn-row--selected" if selected else "")
        super().__init__(markup, classes=classes, **kwargs)
        self._idx = idx

    def on_click(self) -> None:
        self.post_message(FlokkView._RowClicked(self._idx))


# ---------------------------------------------------------------------------
# Markup builders
# ---------------------------------------------------------------------------


def _build_ravn_markup(conn: Any, selected: bool) -> str:
    info: dict[str, Any] = conn.ravn_info or {}
    state = _resolve_state(conn, info)

    dot = _STATE_DOT.get(state, "[#3f3f46]●[/]")
    ghost_mark = " [#52525b]⊙[/]" if getattr(conn, "ghost", False) else ""

    # Amber left-accent for selected row, plain space otherwise
    accent = "[bold #f59e0b]▌[/]" if selected else " "

    # Profile identity — use rune/name/location from profile when available.
    profile: dict[str, Any] = info.get("profile") or {}
    rune: str = profile.get("rune") or "ᚱ"
    display_name: str = profile.get("name") or conn.name
    location: str = profile.get("location") or ""
    specialisations: list[str] = list(profile.get("specialisations") or [])

    location_mark = f" [#52525b]{location}[/]" if location else ""

    # Line 1 — rune · dot · name · location
    name_line = (
        f"{accent}[bold]{rune}[/] {dot} [#fafafa]{display_name}{ghost_mark}[/]{location_mark}"
    )

    # Line 2 — iter progress bar or idle/uptime
    cur = info.get("iteration")
    mx = info.get("max_iterations")
    uptime: str = info.get("uptime", "")

    if cur is not None and mx is not None:
        bar = iter_bar(cur, mx)
        color = _ITER_COLOR.get(state, "#f59e0b")
        state_line = f"  [{color}]{bar}[/]"
    else:
        idle_text = f"idle · {uptime}" if uptime else "idle"
        state_line = f"  [#3f3f46]{idle_text}[/]"

    # Line 3 — specialisations > capabilities > persona (first available wins)
    caps: list[str] = list(info.get("capabilities", []) or [])
    persona: str = info.get("persona", "")
    if specialisations:
        detail = " · ".join(str(s) for s in specialisations[:4])
        return f"{name_line}\n{state_line}\n  [#52525b]{detail}[/]"
    if caps:
        detail = " · ".join(str(c) for c in caps[:4])
        return f"{name_line}\n{state_line}\n  [#52525b]{detail}[/]"
    if persona:
        return f"{name_line}\n{state_line}\n  [#52525b]{persona}[/]"
    return f"{name_line}\n{state_line}"


def _resolve_state(conn: Any, info: dict[str, Any]) -> str:
    if conn.status == "error":
        return "error"
    if conn.status in ("connecting",):
        return "connecting"
    if conn.status == "disconnected":
        return "disconnected"
    return info.get("state", "idle")


def _derive_flokk_name(conns: list[Any]) -> str:
    if not conns:
        return "local"
    host = conns[0].host
    parts = host.rsplit(".", 1)
    if len(parts) == 2 and parts[1]:
        return parts[1]
    return "local"

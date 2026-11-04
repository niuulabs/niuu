"""EventStreamView — live SSE event stream with colour-coded type badges."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import RichLog

from ravn.tui.widgets.pane import PaneMetaUpdate

# Fixed-width badge markup per event type
_BADGE: dict[str, str] = {
    "thought": "[#78909d on #131313] THOUGHT  [/]",
    "tool_start": "[#06b6d4 on #071a1e] TOOL_START[/]",
    "tool_result": "[#76bfa7 on #071a12] TOOL_RES  [/]",
    "response": "[#86d0e8 on #1a1200] RESPONSE  [/]",
    "task_start": "[#a855f7 on #130d1f] TASK_START[/]",
    "task_complete": "[#a855f7 on #130d1f] TASK_DONE [/]",
    "task_error": "[#e78d94 on #1a0909] TASK_ERR  [/]",
    "heartbeat": "[#648090 on #111111] HEARTBEAT [/]",
    "decision": "[#86d0e8 on #1a1200] DECISION  [/]",
}

_EVENT_CYCLE = ["all", "thought", "tool", "response", "task", "heartbeat"]
_MAX_ROWS = 500

_SOURCE_COLORS = ["#86d0e8", "#06b6d4", "#76bfa7", "#a855f7", "#f97316", "#6366f1"]


def _source_color(name: str) -> str:
    """Assign a stable accent color to a source name via hash."""

    return _SOURCE_COLORS[abs(hash(name)) % len(_SOURCE_COLORS)]


class EventStreamView(Widget):
    """SSE subscription to /events.

    Each event renders as a single line:
        [time] [BADGE] [source]  [detail]

    ``f`` cycles filter, ``l`` locks auto-scroll, ``g/G`` scroll top/bottom.
    """

    DEFAULT_CSS = """
    EventStreamView {
        height: 1fr;
        width: 1fr;
        background: #071019;
    }
    EventStreamView RichLog {
        height: 1fr;
        background: #071019;
        scrollbar-size: 1 1;
        scrollbar-color: #182b38;
        scrollbar-color-hover: #2b4351;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("f", "cycle_filter", "Filter"),
        Binding("G", "scroll_bottom", "Bottom"),
        Binding("l", "lock_scroll", "Lock"),
        Binding("g", "scroll_top", "Top"),
    ]

    _filter_idx: reactive[int] = reactive(0)
    _locked: reactive[bool] = reactive(False)

    def __init__(
        self,
        flokk: Any | None = None,
        target: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._flokk = flokk
        self._target = target
        self._row_count = 0

    def compose(self) -> ComposeResult:
        yield RichLog(id="ev-log", markup=True, highlight=False, wrap=False)

    def on_mount(self) -> None:
        if self._flokk:
            self._flokk.on_event(self._on_event)
        self._push_meta()

    def _on_event(self, conn: Any, event: dict[str, Any]) -> None:
        if self._target and conn.name != self._target:
            return
        self._row_count += 1
        if self._row_count > _MAX_ROWS:
            # Clear and restart to avoid unbounded growth
            with suppress(Exception):
                self.query_one("#ev-log", RichLog).clear()
            self._row_count = 0
        self.call_after_refresh(self._append_line, conn, event)

    def _append_line(self, conn: Any, event: dict[str, Any]) -> None:
        current_filter = _EVENT_CYCLE[self._filter_idx]
        data_field = event.get("data", {})
        fallback = data_field.get("type", "?") if isinstance(data_field, dict) else "?"
        event_type = str(event.get("event", fallback))

        if current_filter != "all" and not event_type.startswith(current_filter):
            return

        try:
            log = self.query_one("#ev-log", RichLog)
        except Exception:
            return

        badge = _BADGE.get(event_type, f"[#a5b7c1 on #10212d] {event_type[:10]:<10}[/]")
        ts = datetime.now().strftime("%H:%M:%S")
        data = event.get("data", {})
        detail = _summarise(data) if isinstance(data, dict) else ""
        source = (conn.name if hasattr(conn, "name") else str(conn))[:12]

        src_color = _source_color(source)
        line = f"[#2b4351]{ts}[/] {badge} [{src_color}]{source}[/]" + (
            f"  [#78909d]{detail}[/]" if detail else ""
        )
        log.write(line)

        if not self._locked:
            log.scroll_end(animate=False)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_cycle_filter(self) -> None:
        self._filter_idx = (self._filter_idx + 1) % len(_EVENT_CYCLE)
        self._push_meta()

    def action_scroll_bottom(self) -> None:
        self._locked = False
        with suppress(Exception):
            self.query_one("#ev-log", RichLog).scroll_end(animate=False)
        self._push_meta()

    def action_scroll_top(self) -> None:
        with suppress(Exception):
            self.query_one("#ev-log", RichLog).scroll_home(animate=False)

    def action_lock_scroll(self) -> None:
        self._locked = not self._locked
        self._push_meta()

    # ------------------------------------------------------------------
    # Pane header metadata
    # ------------------------------------------------------------------

    def _push_meta(self) -> None:
        current = _EVENT_CYCLE[self._filter_idx]
        lock = " ⏸" if self._locked else ""
        self.post_message(PaneMetaUpdate(f"{current}{lock}"))


def _summarise(data: dict[str, Any]) -> str:
    for key in ("text", "message", "title", "tool_name"):
        if key in data:
            val = str(data[key])
            return val[:60] + "…" if len(val) > 60 else val
    return str(data)[:60]

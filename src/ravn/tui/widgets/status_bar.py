"""StatusBar — top bar: ᚱ RAVN · flokk tag · active ravn · clock."""

from __future__ import annotations
from contextlib import suppress

from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Static

_URGENCY_THRESHOLD = 0.8


class StatusBar(Widget):
    """Top status bar matching the HTML prototype.

    Layout: [ᚱ RAVN] │ [flokk:name · N ravens · N tasks] │ [→ active-ravn] · · · [clock]
    """

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: #161618;
        layout: horizontal;
        padding: 0 1;
    }
    StatusBar #sb-logo {
        width: auto;
        padding: 0 1 0 0;
    }
    StatusBar #sb-mode {
        width: auto;
        padding: 0 1;
        color: #3f3f46;
    }
    StatusBar #sb-sep1 {
        width: auto;
        padding: 0 1;
        color: #3f3f46;
    }
    StatusBar #sb-flokk-tag {
        width: auto;
        padding: 0 1;
    }
    StatusBar #sb-sep2 {
        width: auto;
        padding: 0 1;
        color: #3f3f46;
    }
    StatusBar #sb-active {
        width: auto;
        padding: 0 1;
        color: #52525b;
    }
    StatusBar #sb-notification {
        width: 1fr;
        padding: 0 1;
    }
    StatusBar #sb-clock {
        width: auto;
        color: #52525b;
        dock: right;
    }
    """

    def __init__(self, flokk: Any | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._flokk = flokk
        self._active_ravn: str = "—"
        self._flokk_name: str = "local"

    def compose(self) -> ComposeResult:
        yield Static("[bold #f59e0b]ᚱ RAVN[/]", id="sb-logo")
        yield Static("[#3f3f46] NORMAL [/]", id="sb-mode")
        yield Static("[#3f3f46]│[/]", id="sb-sep1")
        yield Label("[#f59e0b]flokk:local · 0 ravens · 0 tasks[/]", id="sb-flokk-tag")
        yield Static("[#3f3f46]│[/]", id="sb-sep2")
        yield Label("[#52525b]→ —[/]", id="sb-active")
        yield Static("", id="sb-notification")
        yield Label("", id="sb-clock")

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick)
        if self._flokk:
            self._flokk.on_event(self._on_event)

    def _tick(self) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        with suppress(Exception):
            self.query_one("#sb-clock", Label).update(now)
        self._update_flokk_tag()

    def _on_event(self, conn: Any, event: dict[str, Any]) -> None:
        data = event.get("data", {})
        if not isinstance(data, dict):
            return
        urgency = data.get("urgency", 0.0)
        if urgency >= _URGENCY_THRESHOLD:
            text = str(data.get("payload", {}).get("text", ""))
            if text:
                self._flash_notification(text)

    def _flash_notification(self, text: str) -> None:
        try:
            notif = self.query_one("#sb-notification", Static)
            notif.update(f"[bold #f59e0b]⚡ {text}[/]")
        except Exception:
            return
        self.set_timer(5.0, self._clear_notification)

    def _clear_notification(self) -> None:
        with suppress(Exception):
            self.query_one("#sb-notification", Static).update("")

    def _update_flokk_tag(self) -> None:
        if not self._flokk:
            return
        conns = self._flokk.connections()
        count = len(conns)
        # Derive flokk group name from the common host suffix of connected ravens
        if conns:
            host = conns[0].host
            parts = host.rsplit(".", 1)
            if len(parts) == 2 and parts[1]:
                self._flokk_name = parts[1]
        # Count ravens with an active task
        running = sum(1 for c in conns if c.ravn_info and c.ravn_info.get("state") == "running")
        tag = f"flokk:{self._flokk_name} · {count} ravens · {running} tasks"
        with suppress(Exception):
            self.query_one("#sb-flokk-tag", Label).update(f"[#f59e0b]{tag}[/]")

    def set_active_ravn(self, name: str) -> None:
        self._active_ravn = name
        with suppress(Exception):
            self.query_one("#sb-active", Label).update(f"[#52525b]→ {name}[/]")

    def set_task_count(self, count: int) -> None:
        # task count is now derived live in _update_flokk_tag; kept for compat
        pass

    def set_mode(self, mode: str) -> None:
        """Update the vim mode indicator (NORMAL / INSERT)."""
        if mode == "INSERT":
            markup = "[bold #f59e0b] INSERT [/]"
        else:
            markup = "[#3f3f46] NORMAL [/]"
        with suppress(Exception):
            self.query_one("#sb-mode", Static).update(markup)

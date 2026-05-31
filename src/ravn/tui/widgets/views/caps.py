"""CapsView — capability matrix across Ravens."""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable

_KNOWN_CAPABILITIES = [
    "bash",
    "file_read",
    "file_write",
    "web_search",
    "web_fetch",
    "browser",
    "git",
    "mcp",
    "cascade",
    "checkpoint",
    "cron",
    "mimir",
]


class CapsView(Widget):
    """Grid of Ravens × capabilities.

    Cell shows ✓ (enabled), – (disabled), or ● (active right now).
    """

    DEFAULT_CSS = """
    CapsView {
        height: 1fr;
        width: 1fr;
        background: #09090b;
    }
    CapsView DataTable {
        height: 1fr;
    }
    """

    def __init__(self, flokka: Any | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._flokka = flokka

    def compose(self) -> ComposeResult:
        table = DataTable(id="caps-table", cursor_type="row")
        table.add_column("Ravn")
        for cap in _KNOWN_CAPABILITIES:
            table.add_column(cap[:6])
        yield table

    def on_mount(self) -> None:
        self.set_interval(10.0, self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        try:
            table = self.query_one("#caps-table", DataTable)
        except Exception:
            return
        table.clear()
        if not self._flokka:
            return
        for conn in self._flokka.connections():
            caps = conn.ravn_info.get("capabilities", {})
            active_caps = conn.ravn_info.get("active_capabilities", [])
            row: list[str] = [conn.name]
            for cap in _KNOWN_CAPABILITIES:
                if cap in active_caps:
                    row.append("[bold #f59e0b]●[/]")
                elif caps.get(cap, False):
                    row.append("[#10b981]✓[/]")
                else:
                    row.append("[#3f3f46]–[/]")
            table.add_row(*row)

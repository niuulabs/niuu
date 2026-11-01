"""Tabs widget — horizontal tab bar with separators."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from cli.tui.theme import ACCENT_AMBER, BORDER, TEXT_MUTED


class NiuuTabs(Widget):
    """Horizontal tab bar. Active tab = amber + bold + underline."""

    DEFAULT_CSS = """
    NiuuTabs {
        height: 1;
        background: #18181b;
        border-bottom: solid #27272a;
    }
    NiuuTabs #tabs-bar {
        width: 1fr;
        height: 1;
    }
    """

    active_tab: reactive[int] = reactive(0)

    class TabSelected(Message):
        """Fired when a tab is selected."""

        def __init__(self, index: int, label: str) -> None:
            super().__init__()
            self.index = index
            self.label = label

    def __init__(
        self,
        items: list[str] | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._items: list[str] = items or []

    @property
    def items(self) -> list[str]:
        return list(self._items)

    def compose(self) -> ComposeResult:
        yield Static(self._render_tabs(), id="tabs-bar")

    def set_items(self, items: list[str]) -> None:
        self._items = list(items)
        self._refresh()

    def select(self, index: int) -> None:
        if 0 <= index < len(self._items):
            self.active_tab = index
            self.post_message(self.TabSelected(index, self._items[index]))

    def watch_active_tab(self) -> None:
        self._refresh()

    def _render_tabs(self) -> str:
        parts: list[str] = []
        for i, label in enumerate(self._items):
            if i == self.active_tab:
                parts.append(f"[bold underline {ACCENT_AMBER}] {label} [/]")
            else:
                parts.append(f"[{TEXT_MUTED}] {label} [/]")
        return f" [{BORDER}]│[/] ".join(parts)

    def _refresh(self) -> None:
        try:
            bar = self.query_one("#tabs-bar", Static)
        except Exception:
            return
        bar.update(self._render_tabs())

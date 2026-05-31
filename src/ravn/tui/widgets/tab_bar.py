"""TabBar — layout preset tab switcher between StatusBar and the split container."""

from __future__ import annotations
from contextlib import suppress

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

_TABS: list[tuple[str, str]] = [
    ("ᚠ Flokk", "flokk"),
    ("⑄ Cascade", "cascade"),
    ("ᛗ Mímir", "mimir"),
    ("⑃ Broadcast", "broadcast"),
]


class TabBar(Widget):
    """Horizontal tab bar for switching between layout presets.

    Emits :class:`TabChanged` when a tab is activated via click or
    :meth:`set_active`.
    """

    class TabChanged(Message):
        def __init__(self, tab_id: str) -> None:
            super().__init__()
            self.tab_id = tab_id

    DEFAULT_CSS = """
    TabBar {
        height: 1;
        background: #111113;
        layout: horizontal;
        padding: 0 1;
    }
    TabBar ._tab {
        width: auto;
        padding: 0 1;
        color: #52525b;
        background: transparent;
    }
    TabBar ._tab:hover {
        color: #a1a1aa;
        background: #1c1c1e;
    }
    TabBar ._tab--active {
        color: #f59e0b;
        background: #1a1400;
    }
    """

    _active: reactive[str] = reactive("flokk")

    def compose(self) -> ComposeResult:
        for label, tab_id in _TABS:
            classes = "_tab _tab--active" if tab_id == self._active else "_tab"
            yield _TabItem(label, tab_id=tab_id, classes=classes)

    def set_active(self, tab_id: str) -> None:
        if self._active == tab_id:
            return
        self._active = tab_id
        for _, tid in _TABS:
            with suppress(Exception):
                item = self.query_one(f"#_tbtab_{tid}", _TabItem)
                if tid == tab_id:
                    item.add_class("_tab--active")
                    item.remove_class("_tab")
                else:
                    item.remove_class("_tab--active")
                    item.add_class("_tab")


class _TabItem(Static):
    """A single clickable tab label."""

    def __init__(self, label: str, tab_id: str, **kwargs: object) -> None:
        super().__init__(label, id=f"_tbtab_{tab_id}", **kwargs)
        self._tab_id = tab_id

    def on_click(self) -> None:
        self.post_message(TabBar.TabChanged(self._tab_id))

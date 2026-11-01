"""Sidebar widget — page navigation with icons, names, and number-key hints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

if TYPE_CHECKING:
    from collections.abc import Sequence


SIDEBAR_EXPANDED_WIDTH = 24
SIDEBAR_COLLAPSED_WIDTH = 5


@dataclass(frozen=True)
class SidebarPage:
    """A page entry rendered in the sidebar."""

    name: str
    icon: str
    key: str  # e.g. "1", "2"


class NiuuSidebar(Widget):
    """Navigation sidebar: 24ch expanded, 5ch collapsed."""

    DEFAULT_CSS = """
    NiuuSidebar {
        dock: left;
        width: 24;
        background: #18181b;
        border-right: solid #27272a;
        padding: 1 0;
    }
    NiuuSidebar.collapsed {
        width: 5;
    }
    NiuuSidebar .sidebar-item {
        padding: 0 1;
        color: #a1a1aa;
        height: 1;
    }
    NiuuSidebar .sidebar-item.active {
        color: #f59e0b;
        background: #27272a;
        text-style: bold;
    }
    NiuuSidebar .sidebar-hint {
        color: #71717a;
        padding: 0 1;
        height: 1;
    }
    """

    collapsed: reactive[bool] = reactive(False)
    active_index: reactive[int] = reactive(0)

    class PageSelected(Message):
        """Fired when a sidebar page is clicked or navigated to."""

        def __init__(self, index: int, page: SidebarPage) -> None:
            super().__init__()
            self.index = index
            self.page = page

    def __init__(
        self,
        pages: Sequence[SidebarPage] = (),
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._pages: list[SidebarPage] = list(pages)
        self._mounted = False

    @property
    def pages(self) -> list[SidebarPage]:
        return list(self._pages)

    def set_pages(self, pages: Sequence[SidebarPage]) -> None:
        self._pages = list(pages)
        if self._mounted:
            self._rebuild()

    def compose(self) -> ComposeResult:
        yield from self._build_items()

    def on_mount(self) -> None:
        self._mounted = True

    def _build_items(self) -> list[Static]:
        items: list[Static] = []
        for i, page in enumerate(self._pages):
            if self.collapsed:
                label = page.icon
            else:
                label = f"{page.icon} {page.name:<14}{page.key}"
            classes = "sidebar-item active" if i == self.active_index else "sidebar-item"
            items.append(Static(label, classes=classes))

        if not self.collapsed:
            items.append(Static("? help  q quit", classes="sidebar-hint"))
        return items

    def watch_collapsed(self) -> None:
        if self.collapsed:
            self.add_class("collapsed")
        else:
            self.remove_class("collapsed")
        if self._mounted:
            self._rebuild()

    def watch_active_index(self) -> None:
        if self._mounted:
            self._rebuild()

    def _rebuild(self) -> None:
        existing = self.query("Static")
        if existing:
            existing.remove()
        for widget in self._build_items():
            self.mount(widget)

    def toggle(self) -> None:
        self.collapsed = not self.collapsed

    def select_page(self, index: int) -> None:
        if 0 <= index < len(self._pages):
            self.active_index = index
            self.post_message(self.PageSelected(index, self._pages[index]))

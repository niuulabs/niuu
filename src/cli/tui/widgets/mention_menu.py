"""MentionMenu widget — autocomplete dropdown for @files, /commands, !issues."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from cli.tui.theme import TEXT_MUTED

MAX_VISIBLE_ITEMS = 10


@dataclass(frozen=True)
class MentionItem:
    """A single autocomplete suggestion."""

    label: str
    value: str
    detail: str = ""
    icon: str = ""
    category: str = ""


def _fuzzy_contains(query: str, text: str) -> bool:
    """Case-insensitive substring match."""
    return query.lower() in text.lower()


class MentionMenu(Widget):
    """Autocomplete dropdown showing up to 10 items with wrap-around selection."""

    DEFAULT_CSS = """
    MentionMenu {
        display: none;
        width: 50;
        max-height: 14;
        background: #18181b;
        border: round #3f3f46;
        padding: 0 1;
        layer: autocomplete;
    }
    MentionMenu.active {
        display: block;
    }
    MentionMenu .mention-item {
        height: 1;
        padding: 0 1;
    }
    MentionMenu .mention-item.selected {
        background: #27272a;
        color: #f59e0b;
    }
    MentionMenu .mention-more {
        color: #71717a;
        height: 1;
    }
    """

    active: reactive[bool] = reactive(False)
    selected: reactive[int] = reactive(0)

    class ItemChosen(Message):
        """Fired when a mention item is selected."""

        def __init__(self, item: MentionItem) -> None:
            super().__init__()
            self.item = item

    def __init__(
        self,
        *,
        trigger: str = "@",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._trigger = trigger
        self._items: list[MentionItem] = []
        self._query = ""

    @property
    def trigger(self) -> str:
        return self._trigger

    @property
    def items(self) -> list[MentionItem]:
        return list(self._items)

    @property
    def filtered(self) -> list[MentionItem]:
        if not self._query:
            return list(self._items)
        return [i for i in self._items if _fuzzy_contains(self._query, i.label)]

    def compose(self) -> ComposeResult:
        yield Vertical(id="mention-list")

    def open(self, items: list[MentionItem] | None = None, query: str = "") -> None:
        if items is not None:
            self._items = list(items)
        self._query = query
        self.selected = 0
        self.active = True
        self._rebuild()

    def close(self) -> None:
        self.active = False
        self._query = ""

    def set_query(self, query: str) -> None:
        self._query = query
        self.selected = 0
        self._rebuild()

    def watch_active(self, value: bool) -> None:
        if value:
            self.add_class("active")
        else:
            self.remove_class("active")

    def move_up(self) -> None:
        items = self.filtered
        if not items:
            return
        self.selected = (self.selected - 1) % len(items)
        self._rebuild()

    def move_down(self) -> None:
        items = self.filtered
        if not items:
            return
        self.selected = (self.selected + 1) % len(items)
        self._rebuild()

    def select_current(self) -> MentionItem | None:
        items = self.filtered
        if not items:
            return None
        item = items[self.selected]
        self.post_message(self.ItemChosen(item))
        self.close()
        return item

    def _rebuild(self) -> None:
        try:
            container = self.query_one("#mention-list", Vertical)
        except Exception:
            return
        container.query("Static").remove()

        items = self.filtered
        if not items:
            container.mount(Static("[{TEXT_MUTED}]No matches[/]", classes="mention-more"))
            return

        # Window around selected item.
        total = len(items)
        start = max(0, self.selected - MAX_VISIBLE_ITEMS // 2)
        end = min(total, start + MAX_VISIBLE_ITEMS)
        if end - start < MAX_VISIBLE_ITEMS:
            start = max(0, end - MAX_VISIBLE_ITEMS)

        if start > 0:
            container.mount(Static(f"  …{start} more above", classes="mention-more"))

        for i in range(start, end):
            item = items[i]
            icon = f"{item.icon} " if item.icon else ""
            detail = f" [{TEXT_MUTED}]{item.detail}[/]" if item.detail else ""
            classes = "mention-item selected" if i == self.selected else "mention-item"
            container.mount(Static(f"{icon}{item.label}{detail}", classes=classes))

        remaining = total - end
        if remaining > 0:
            container.mount(Static(f"  …{remaining} more below", classes="mention-more"))

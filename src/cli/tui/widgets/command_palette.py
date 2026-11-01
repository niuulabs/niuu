"""CommandPalette — modal screen with Ctrl+K fuzzy-search command launcher."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from cli.tui.theme import ACCENT_AMBER, ACCENT_CYAN, ACCENT_EMERALD, TEXT_MUTED

MAX_VISIBLE_RESULTS = 10


class PaletteItemType(StrEnum):
    """Category of a command palette item."""

    SESSION = "session"
    PAGE = "page"
    ACTION = "action"


_TYPE_COLORS: dict[PaletteItemType, str] = {
    PaletteItemType.SESSION: ACCENT_EMERALD,
    PaletteItemType.PAGE: ACCENT_CYAN,
    PaletteItemType.ACTION: ACCENT_AMBER,
}


@dataclass(frozen=True)
class PaletteItem:
    """A single entry in the command palette."""

    label: str
    description: str = ""
    item_type: PaletteItemType = PaletteItemType.ACTION
    icon: str = ""
    action_id: str = ""


def _fuzzy_match(query: str, text: str) -> bool:
    """Case-insensitive subsequence match."""
    query = query.lower()
    text = text.lower()
    qi = 0
    for char in text:
        if qi < len(query) and char == query[qi]:
            qi += 1
    return qi == len(query)


class CommandPalette(ModalScreen[PaletteItem | None]):
    """Ctrl+K command palette with fuzzy search."""

    DEFAULT_CSS = """
    CommandPalette {
        align: center top;
        padding-top: 3;
    }
    CommandPalette #palette-container {
        width: 60;
        max-height: 70%;
        background: #18181b;
        border: round #a855f7;
        padding: 1 2;
    }
    CommandPalette #palette-input {
        margin-bottom: 1;
    }
    CommandPalette .palette-section-header {
        color: #71717a;
        text-style: bold;
        height: 1;
        margin-top: 1;
    }
    CommandPalette .palette-item {
        height: 1;
        padding: 0 1;
    }
    CommandPalette .palette-item.selected {
        background: #27272a;
    }
    CommandPalette #palette-more {
        color: #71717a;
        height: 1;
    }
    """

    BINDINGS = [
        ("escape", "dismiss_palette", "Close"),
    ]

    class ItemSelected(Message):
        """Fired when a palette item is selected."""

        def __init__(self, item: PaletteItem) -> None:
            super().__init__()
            self.item = item

    def __init__(
        self,
        items: list[PaletteItem] | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._all_items: list[PaletteItem] = items or []
        self._matched: list[PaletteItem] = list(self._all_items)
        self._cursor: int = 0

    @property
    def matched(self) -> list[PaletteItem]:
        return list(self._matched)

    @property
    def cursor(self) -> int:
        return self._cursor

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="palette-container"):
                yield Input(placeholder="Type to search…", id="palette-input")
                yield Vertical(id="palette-results")

    def on_mount(self) -> None:
        self._filter("")
        self.query_one("#palette-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._filter(event.value)

    def _filter(self, query: str) -> None:
        if not query:
            self._matched = list(self._all_items)
        else:
            self._matched = [item for item in self._all_items if _fuzzy_match(query, item.label)]
        self._cursor = 0
        self._rebuild_results()

    def _rebuild_results(self) -> None:
        try:
            container = self.query_one("#palette-results", Vertical)
        except Exception:
            return
        container.query("Static").remove()

        visible = self._matched[:MAX_VISIBLE_RESULTS]
        current_type: PaletteItemType | None = None

        for i, item in enumerate(visible):
            if item.item_type != current_type:
                current_type = item.item_type
                color = _TYPE_COLORS[item.item_type]
                container.mount(
                    Static(
                        f"[{color}]{item.item_type.value.title()}s[/]",
                        classes="palette-section-header",
                    )
                )
            icon = f"{item.icon} " if item.icon else ""
            color = _TYPE_COLORS[item.item_type]
            desc = f" [{TEXT_MUTED}]{item.description}[/]" if item.description else ""
            classes = "palette-item selected" if i == self._cursor else "palette-item"
            container.mount(Static(f"[{color}]{icon}{item.label}[/]{desc}", classes=classes))

        remaining = len(self._matched) - MAX_VISIBLE_RESULTS
        if remaining > 0:
            container.mount(Static(f"  …{remaining} more", id="palette-more"))

    def on_key(self, event: object) -> None:
        from textual.events import Key

        if not isinstance(event, Key):
            return
        if event.key in ("down", "j"):
            self._move_cursor(1)
            event.prevent_default()
        elif event.key in ("up", "k"):
            self._move_cursor(-1)
            event.prevent_default()
        elif event.key == "enter":
            self._select_current()
            event.prevent_default()

    def _move_cursor(self, delta: int) -> None:
        if not self._matched:
            return
        self._cursor = (self._cursor + delta) % len(self._matched)
        self._rebuild_results()

    def _select_current(self) -> None:
        if not self._matched:
            return
        item = self._matched[self._cursor]
        self.post_message(self.ItemSelected(item))
        self.dismiss(item)

    def action_dismiss_palette(self) -> None:
        self.dismiss(None)

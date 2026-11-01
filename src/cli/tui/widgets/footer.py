"""Footer widget — context-sensitive key hints per page and mode."""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from cli.tui.mode import InputMode
from cli.tui.theme import ACCENT_AMBER, TEXT_MUTED


@dataclass(frozen=True)
class KeyHint:
    """A single key hint shown in the footer."""

    key: str
    description: str


# Default hints for NORMAL mode when no page overrides.
DEFAULT_HINTS: list[KeyHint] = [
    KeyHint("1-7", "pages"),
    KeyHint("?", "help"),
    KeyHint("[", "sidebar"),
    KeyHint("/", "search"),
    KeyHint("Ctrl+K", "command"),
    KeyHint("q", "quit"),
]

INSERT_HINTS: list[KeyHint] = [
    KeyHint("Esc", "normal mode"),
    KeyHint("Alt+1-7", "pages"),
]

SEARCH_HINTS: list[KeyHint] = [
    KeyHint("Esc", "cancel"),
    KeyHint("Enter", "confirm"),
    KeyHint("Tab", "next filter"),
]

COMMAND_HINTS: list[KeyHint] = [
    KeyHint("Esc", "close"),
    KeyHint("j/k", "navigate"),
    KeyHint("Enter", "select"),
]

_MODE_HINTS: dict[InputMode, list[KeyHint]] = {
    InputMode.NORMAL: DEFAULT_HINTS,
    InputMode.INSERT: INSERT_HINTS,
    InputMode.SEARCH: SEARCH_HINTS,
    InputMode.COMMAND: COMMAND_HINTS,
}


class NiuuFooter(Widget):
    """Bottom bar showing context-sensitive keybinding hints."""

    DEFAULT_CSS = """
    NiuuFooter {
        dock: bottom;
        height: 1;
        background: #18181b;
        color: #71717a;
    }
    NiuuFooter #footer-hints {
        width: 1fr;
        height: 1;
    }
    """

    mode: reactive[InputMode] = reactive(InputMode.NORMAL)

    def __init__(
        self,
        hints: list[KeyHint] | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._page_hints: list[KeyHint] | None = hints

    def compose(self) -> ComposeResult:
        yield Static(self._render_hints(), id="footer-hints")

    def set_hints(self, hints: list[KeyHint]) -> None:
        self._page_hints = hints
        self._refresh()

    def watch_mode(self) -> None:
        self._refresh()

    def _active_hints(self) -> list[KeyHint]:
        if self.mode != InputMode.NORMAL:
            return _MODE_HINTS[self.mode]
        if self._page_hints is not None:
            return self._page_hints
        return DEFAULT_HINTS

    def _render_hints(self) -> str:
        parts: list[str] = []
        for hint in self._active_hints():
            parts.append(f" [bold {ACCENT_AMBER}]{hint.key}[/] {hint.description} ")
        return f"[{TEXT_MUTED}]│[/]".join(parts)

    def _refresh(self) -> None:
        try:
            bar = self.query_one("#footer-hints", Static)
        except Exception:
            return
        bar.update(self._render_hints())

"""Modal widget — generic centered dialog with title and content."""

from __future__ import annotations

from contextlib import suppress

from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class NiuuModal(Widget):
    """Generic centered modal dialog.

    Set ``visible`` reactive to show/hide.
    """

    DEFAULT_CSS = """
    NiuuModal {
        align: center middle;
        display: none;
    }
    NiuuModal.visible {
        display: block;
        layer: modal;
    }
    NiuuModal #modal-container {
        width: 60;
        max-height: 20;
        background: #18181b;
        border: round #f59e0b;
        padding: 1 2;
    }
    NiuuModal #modal-title {
        text-align: center;
        text-style: bold;
        color: #f59e0b;
        height: 1;
        margin-bottom: 1;
    }
    NiuuModal #modal-body {
        height: auto;
        color: #fafafa;
    }
    """

    is_visible: reactive[bool] = reactive(False)

    def __init__(
        self,
        title: str = "",
        content: str = "",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._title = title
        self._content = content

    @property
    def title(self) -> str:
        return self._title

    @property
    def content(self) -> str:
        return self._content

    def compose(self) -> ComposeResult:
        with Center():
            with Vertical(id="modal-container"):
                yield Static(self._title, id="modal-title")
                yield Static(self._content, id="modal-body")

    def watch_is_visible(self, value: bool) -> None:
        if value:
            self.add_class("visible")
        else:
            self.remove_class("visible")

    def show(self, title: str | None = None, content: str | None = None) -> None:
        if title is not None:
            self._title = title
            with suppress(Exception):
                self.query_one("#modal-title", Static).update(title)
        if content is not None:
            self._content = content
            with suppress(Exception):
                self.query_one("#modal-body", Static).update(content)
        self.is_visible = True

    def hide(self) -> None:
        self.is_visible = False

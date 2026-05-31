"""PaneWidget — leaf node of the split tree with a unified pane header bar."""

from __future__ import annotations
from contextlib import suppress

from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

if TYPE_CHECKING:
    pass

_VIEW_LABELS: dict[str, str] = {
    "flokk": "FLOKK",
    "chat": "CHAT",
    "events": "EVENTS",
    "tasks": "CASCADE",
    "mimir": "MÍMIR",
    "cron": "CRON",
    "checkpoints": "CHECKPOINTS",
    "caps": "CAPS",
    "log": "LOG",
}


class PaneMetaUpdate(Message):
    """Sent by a view widget to update its parent pane's header metadata text."""

    def __init__(self, meta: str) -> None:
        super().__init__()
        self.meta = meta


class PaneHeader(Widget):
    """1-line header bar rendered at the top of every PaneWidget.

    Left side: view label (uppercase) + optional target name.
    Right side: metadata string supplied by the hosted view via
    :class:`PaneMetaUpdate`.

    Active pane title is amber; inactive is zinc-500.
    """

    DEFAULT_CSS = """
    PaneHeader {
        height: 1;
        background: #111113;
        layout: horizontal;
        padding: 0 1;
    }
    PaneHeader #ph-title {
        width: 1fr;
        color: #71717a;
    }
    PaneHeader #ph-meta {
        width: auto;
        color: #52525b;
    }
    """

    def __init__(
        self,
        view_type: str,
        target: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._view_type = view_type
        self._target = target
        self._active = False

    def compose(self) -> ComposeResult:
        yield Static(self._title_markup(active=False), id="ph-title")
        yield Static("", id="ph-meta")

    def _title_text(self) -> str:
        label = _VIEW_LABELS.get(self._view_type, self._view_type.upper())
        if self._target:
            return f"{label} · {self._target}"
        return label

    def _title_markup(self, *, active: bool) -> str:
        text = self._title_text()
        if active:
            return f"[bold #f59e0b]{text}[/]"
        return f"[#71717a]{text}[/]"

    def set_active(self, active: bool) -> None:
        self._active = active
        with suppress(Exception):
            self.query_one("#ph-title", Static).update(self._title_markup(active=active))

    def update_meta(self, meta: str) -> None:
        with suppress(Exception):
            self.query_one("#ph-meta", Static).update(f"[#52525b]{meta}[/]")

    def update_view(self, view_type: str, target: str | None = None) -> None:
        self._view_type = view_type
        self._target = target
        with suppress(Exception):
            self.query_one("#ph-title", Static).update(self._title_markup(active=self._active))


class PaneWidget(Widget):
    """A single pane in the split layout.

    Renders a :class:`PaneHeader` on top, then the assigned view below.
    Focused panes get an amber border and their header title turns amber.
    Views can push metadata to the header via :class:`PaneMetaUpdate`.
    """

    DEFAULT_CSS = """
    PaneWidget {
        layout: vertical;
        border: solid #27272a;
        height: 1fr;
        width: 1fr;
    }
    PaneWidget.-focused {
        border: solid #f59e0b;
    }
    """

    focused_pane: reactive[bool] = reactive(False)

    def __init__(
        self,
        pane_id: str,
        view_type: str = "flokk",
        target: str | None = None,
        flokk: Any | None = None,
        mimir_urls: list[tuple[str, str]] | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self.pane_id = pane_id
        self._view_type = view_type
        self._target = target
        self._flokk: Any | None = flokk
        self._mimir_urls: list[tuple[str, str]] = mimir_urls or []

    def compose(self) -> ComposeResult:
        yield PaneHeader(
            view_type=self._view_type,
            target=self._target,
            id=f"ph_{self.pane_id.replace('-', '_')}",
        )
        yield self._build_view()

    def _build_view(self) -> Widget:
        from ravn.tui.widgets.views.caps import CapsView
        from ravn.tui.widgets.views.chat import ChatView
        from ravn.tui.widgets.views.checkpoints import CheckpointsView
        from ravn.tui.widgets.views.cron import CronView
        from ravn.tui.widgets.views.events import EventStreamView
        from ravn.tui.widgets.views.flokk import FlokkView
        from ravn.tui.widgets.views.mimir import MimirView
        from ravn.tui.widgets.views.tasks import TaskBoardView

        conn = None
        if self._flokk and self._target:
            conn = self._flokk.get(self._target)

        match self._view_type:
            case "chat":
                return ChatView(connection=conn)
            case "events":
                return EventStreamView(flokk=self._flokk, target=self._target)
            case "tasks":
                return TaskBoardView(flokk=self._flokk)
            case "mimir":
                return MimirView(connection=conn, mimir_urls=self._mimir_urls)
            case "cron":
                return CronView(connection=conn)
            case "checkpoints":
                return CheckpointsView(connection=conn)
            case "caps":
                return CapsView(flokk=self._flokk)
            case _:
                return FlokkView(flokk=self._flokk)
        raise AssertionError("Unreachable _build_view fallthrough")

    def assign_view(
        self,
        view_type: str,
        target: str | None = None,
        flokk: Any | None = None,
    ) -> None:
        """Swap the view hosted by this pane without touching the PaneHeader."""
        self._view_type = view_type
        self._target = target
        if flokk is not None:
            self._flokk = flokk

        # Update header title
        with suppress(Exception):
            self.query_one(PaneHeader).update_view(view_type, target)

        # Remove the old view but keep the header
        for child in list(self.children):
            if not isinstance(child, PaneHeader):
                child.remove()
        self.mount(self._build_view())

    def on_pane_meta_update(self, msg: PaneMetaUpdate) -> None:
        """Route metadata from a hosted view up to the PaneHeader."""
        with suppress(Exception):
            self.query_one(PaneHeader).update_meta(msg.meta)

    def watch_focused_pane(self, focused: bool) -> None:
        self.set_class(focused, "-focused")
        with suppress(Exception):
            self.query_one(PaneHeader).set_active(focused)

    def on_focus(self) -> None:
        self.focused_pane = True

    def on_blur(self) -> None:
        self.focused_pane = False

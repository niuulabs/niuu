"""BroadcastOverlay — select ravens and send them a message."""

from __future__ import annotations

import asyncio
from typing import Any

import textual.screen as textual_screen
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, RichLog, Static

ModalScreen = textual_screen.ModalScreen


class BroadcastOverlay(ModalScreen[None]):
    """Multi-select ravens and send them a broadcast message.

    j/k navigate list, space toggles selection, tab jumps to message
    input, enter sends, esc cancels.
    """

    DEFAULT_CSS = """
    BroadcastOverlay {
        align: center middle;
    }
    BroadcastOverlay #bo-panel {
        width: 60;
        background: #18181b;
        border: solid #3f3f46;
        layout: vertical;
        height: auto;
    }
    BroadcastOverlay #bo-title {
        height: 1;
        padding: 0 1;
        background: #111113;
        color: #f59e0b;
    }
    BroadcastOverlay #bo-list {
        height: 10;
        background: #18181b;
        padding: 0;
    }
    BroadcastOverlay #bo-input {
        background: #18181b;
        border: none;
        border-top: solid #3f3f46;
        color: #fafafa;
        height: 3;
    }
    BroadcastOverlay #bo-hint {
        height: 1;
        padding: 0 1;
        background: #111113;
        color: #52525b;
    }
    """

    def __init__(self, flokk: Any) -> None:
        super().__init__()
        self._flokk = flokk
        self._ravns: list[Any] = []
        self._selected: set[str] = set()
        self._list_idx: int = 0

    def compose(self) -> ComposeResult:
        with Container(id="bo-panel"):
            yield Static("[bold #f59e0b]  Broadcast[/]", id="bo-title")
            yield RichLog(id="bo-list", markup=True, highlight=False, wrap=False)
            yield Input(
                placeholder="  message to send to selected ravens…",
                id="bo-input",
            )
            yield Static(
                "[#3f3f46]  j/k navigate   space toggle   tab↔input   enter send   esc cancel[/]",
                id="bo-hint",
            )

    def on_mount(self) -> None:
        conns = self._flokk.connections() if self._flokk else []
        self._ravns = [c for c in conns if not getattr(c, "ghost", False)]
        self._selected = {c.name for c in self._ravns}
        self._rebuild()
        self.query_one("#bo-input", Input).focus()

    def on_key(self, event: Any) -> None:
        from textual.widgets import Input as _Input

        in_input = isinstance(self.focused, _Input)
        key = event.key

        if key == "escape":
            self.dismiss(None)
            event.stop()
            return

        if in_input:
            # Tab moves focus to list area
            if key == "tab":
                self.query_one("#bo-list", RichLog).focus()
                event.prevent_default()
                event.stop()
            return

        # List navigation (focus not in input)
        match key:
            case "j" | "down":
                if self._ravns:
                    self._list_idx = (self._list_idx + 1) % len(self._ravns)
                    self._rebuild()
                event.stop()
            case "k" | "up":
                if self._ravns:
                    self._list_idx = (self._list_idx - 1) % len(self._ravns)
                    self._rebuild()
                event.stop()
            case "space":
                if self._ravns:
                    name = self._ravns[self._list_idx].name
                    if name in self._selected:
                        self._selected.discard(name)
                    else:
                        self._selected.add(name)
                    self._rebuild()
                event.stop()
            case "tab":
                self.query_one("#bo-input", Input).focus()
                event.stop()
            case "a":
                # Toggle all
                if len(self._selected) == len(self._ravns):
                    self._selected.clear()
                else:
                    self._selected = {c.name for c in self._ravns}
                self._rebuild()
                event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        message = event.value.strip()
        if not message:
            return
        if not self._selected:
            self.app.notify("No ravens selected", severity="warning")
            return
        asyncio.create_task(self._do_send(list(self._selected), message))
        self.dismiss(None)

    async def _do_send(self, names: list[str], message: str) -> None:
        try:
            results = await self._flokk.broadcast_to(names, message)
            self.app.notify(f"Broadcast sent to {len(results)} ravn(s)")
        except Exception as exc:
            self.app.notify(f"Broadcast failed: {exc}", severity="error")

    def _rebuild(self) -> None:
        try:
            log = self.query_one("#bo-list", RichLog)
        except Exception:
            return
        log.clear()
        if not self._ravns:
            log.write("[#52525b]  no ravens connected[/]")
            return
        for i, conn in enumerate(self._ravns):
            sel = i == self._list_idx
            checked = conn.name in self._selected
            accent = "[bold #f59e0b]▌[/]" if sel else " "
            check = "[bold #10b981]✓[/]" if checked else "[#52525b]·[/]"
            info = conn.ravn_info or {}
            state = info.get("state", conn.status)
            log.write(f"{accent}{check} [#fafafa]{conn.name}[/]  [#3f3f46]{state}[/]")
        n_sel = len(self._selected)
        n_tot = len(self._ravns)
        log.write(f"\n[#52525b]  {n_sel}/{n_tot} selected  [#3f3f46]a[/] to toggle all[/]")

"""CommandPaletteScreen — fuzzy-search overlay for all TUI commands and keybindings."""

from __future__ import annotations

from typing import Any

import textual.screen as textual_screen
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, RichLog, Static

ModalScreen = textual_screen.ModalScreen

# (label, command_text)
# command_text = ":cmd" to execute/pre-fill; "" for keybinding reference
_ITEMS: list[tuple[str, str]] = [
    # Commands
    ("connect <host:port>", ":connect "),
    ("disconnect <name>", ":disconnect "),
    ("view flokk", ":view flokk"),
    ("view events", ":view events"),
    ("view mimir", ":view mimir"),
    ("view tasks", ":view tasks"),
    ("view chat", ":view chat "),
    ("broadcast <message>", ":broadcast "),
    ("layout save <name>", ":layout save "),
    ("layout load <name>", ":layout load "),
    ("layout list", ":layout list"),
    ("ingest <path>", ":ingest "),
    ("keybindings reload", ":keybindings reload"),
    ("keybindings show", ":keybindings show"),
    ("quit", ":q"),
    # Keybindings (informational)
    ("^w v   split vertical", ""),
    ("^w s   split horizontal", ""),
    ("^w q   close pane", ""),
    ("^w w   next pane", ""),
    ("^w =   equalise panes", ""),
    ("^w z   zoom/unzoom", ""),
    ("f      flokk view", ""),
    ("e      events view", ""),
    ("m      mímir view", ""),
    ("t      tasks view", ""),
    ("i      insert mode", ""),
    ("b      broadcast", ""),
    ("?      command palette", ""),
]


class CommandPaletteScreen(ModalScreen[str | None]):
    """Fuzzy-search overlay.  Dismisses with the selected command text, or None."""

    DEFAULT_CSS = """
    CommandPaletteScreen {
        align: center top;
        padding-top: 4;
    }
    CommandPaletteScreen #cp-panel {
        width: 68;
        background: #10212d;
        border: solid #2b4351;
        layout: vertical;
        height: auto;
    }
    CommandPaletteScreen #cp-header {
        height: 1;
        padding: 0 1;
        background: #111113;
        color: #86d0e8;
    }
    CommandPaletteScreen #cp-filter {
        background: #10212d;
        border: none;
        border-bottom: solid #182b38;
        color: #edf4f7;
        height: 3;
    }
    CommandPaletteScreen #cp-list {
        height: 15;
        background: #10212d;
    }
    CommandPaletteScreen #cp-hint {
        height: 1;
        padding: 0 1;
        background: #111113;
        color: #648090;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._filtered: list[tuple[str, str]] = list(_ITEMS)
        self._idx: int = 0

    def compose(self) -> ComposeResult:
        with Container(id="cp-panel"):
            yield Static("[bold #86d0e8]  Command Palette[/]", id="cp-header")
            yield Input(placeholder="  filter…", id="cp-filter")
            yield RichLog(id="cp-list", markup=True, highlight=False, wrap=False)
            yield Static(
                "[#2b4351]  ↑/↓ navigate   enter select   esc close[/]",
                id="cp-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#cp-filter", Input).focus()
        self._rebuild()

    def on_key(self, event: Any) -> None:
        match event.key:
            case "up":
                if self._filtered:
                    self._idx = (self._idx - 1) % len(self._filtered)
                    self._rebuild()
                event.prevent_default()
                event.stop()
            case "down":
                if self._filtered:
                    self._idx = (self._idx + 1) % len(self._filtered)
                    self._rebuild()
                event.prevent_default()
                event.stop()
            case "escape":
                self.dismiss(None)
                event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if not self._filtered:
            self.dismiss(None)
            return
        _, cmd = self._filtered[self._idx]
        self.dismiss(cmd or None)

    def on_input_changed(self, event: Input.Changed) -> None:
        q = event.value.lower().strip()
        if q:
            self._filtered = [(label, c) for label, c in _ITEMS if q in label.lower()]
        else:
            self._filtered = list(_ITEMS)
        self._idx = 0
        self._rebuild()

    def _rebuild(self) -> None:
        try:
            log = self.query_one("#cp-list", RichLog)
        except Exception:
            return
        log.clear()
        if not self._filtered:
            log.write("[#648090]  no matches[/]")
            return
        for i, (label, cmd) in enumerate(self._filtered):
            sel = i == self._idx
            accent = "[bold #86d0e8]▌[/]" if sel else " "
            if cmd:
                # Command entry
                style = "[#edf4f7]" if sel else "[#a5b7c1]"
                prefix = "[#2b4351]:[/] " if cmd.startswith(":") else "  "
            else:
                # Keybinding reference
                style = "[#78909d]" if sel else "[#648090]"
                prefix = "  "
            log.write(f"{accent}{prefix}{style}{label}[/]")

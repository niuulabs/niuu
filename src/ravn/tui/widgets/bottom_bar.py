"""BottomBar — contextual keybinding hints with kbd-style key boxes."""

from __future__ import annotations

from contextlib import suppress

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


def _kbd(key: str) -> str:
    """Render a key as a subtle pill: key on dark background."""

    return f"[#c4c4c4 on #232326] {key} [/]"


def _lbl(text: str) -> str:
    """Render a hint label in muted text."""
    return f"[#78909d]{text}[/]"


# Global hints — always shown, matches HTML prototype layout
_GLOBAL = (
    f"{_kbd('tab')} {_lbl('pane')}  "
    f"{_kbd('hjkl')} {_lbl('nav')}  "
    f"{_kbd('f')} {_lbl('flokk')}  "
    f"{_kbd('e')} {_lbl('events')}  "
    f"{_kbd('m')} {_lbl('mímir')}  "
    f"{_kbd('t')} {_lbl('tasks')}  "
    f"{_kbd('b')} {_lbl('broadcast')}  "
    f"{_kbd('z')} {_lbl('zoom')}  "
    f"{_kbd(':')} {_lbl('command')}  "
    f"{_kbd('q')} {_lbl('quit')}"
)

# Contextual hints shown above global when a specific view is focused
_VIEW_HINTS: dict[str, str] = {
    "flokk": (
        f"{_kbd('j/k')} {_lbl('select')}  "
        f"{_kbd('↵')} {_lbl('chat')}  "
        f"{_kbd('g')} {_lbl('ghost')}  "
        f"{_kbd('^w v')} {_lbl('vsplit')}  "
        f"{_kbd('^w s')} {_lbl('hsplit')}  "
        f"{_kbd('^w q')} {_lbl('close')}  "
        f"[#2b4351]│[/]  "
    ),
    "chat": (f"{_kbd('↵')} {_lbl('send')}  {_kbd('g/G')} {_lbl('scroll')}  [#2b4351]│[/]  "),
    "events": (
        f"{_kbd('f')} {_lbl('filter')}  "
        f"{_kbd('G')} {_lbl('bottom')}  "
        f"{_kbd('l')} {_lbl('lock')}  "
        f"[#2b4351]│[/]  "
    ),
    "tasks": (
        f"{_kbd('n')} {_lbl('new')}  "
        f"{_kbd('s')} {_lbl('stop')}  "
        f"{_kbd('↵')} {_lbl('expand')}  "
        f"[#2b4351]│[/]  "
    ),
    "mimir": (
        f"{_kbd('/')} {_lbl('search')}  "
        f"{_kbd('↵')} {_lbl('open')}  "
        f"{_kbd('⌫')} {_lbl('back')}  "
        f"[#2b4351]│[/]  "
    ),
    "cron": (
        f"{_kbd('spc')} {_lbl('toggle')}  "
        f"{_kbd('r')} {_lbl('run')}  "
        f"{_kbd('d')} {_lbl('delete')}  "
        f"[#2b4351]│[/]  "
    ),
    "checkpoints": (f"{_kbd('r')} {_lbl('resume')}  {_kbd('d')} {_lbl('delete')}  [#2b4351]│[/]  "),
}


class BottomBar(Widget):
    """Bottom bar showing contextual keybinding hints.

    Hints update when the focused view type changes via :meth:`set_context`.
    """

    DEFAULT_CSS = """
    BottomBar {
        height: 1;
        background: #111113;
        layout: horizontal;
    }
    BottomBar #bb-hints {
        width: 1fr;
        padding: 0 1;
        color: #648090;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(_GLOBAL, id="bb-hints")

    def set_context(self, view_type: str) -> None:
        """Update hint text for the currently focused view type."""
        extra = _VIEW_HINTS.get(view_type, "")
        text = extra + _GLOBAL if extra else _GLOBAL
        with suppress(Exception):
            self.query_one("#bb-hints", Static).update(text)

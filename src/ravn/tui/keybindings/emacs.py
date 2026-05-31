"""Emacs keybinding parser.

Supports:
- ``~/.emacs``
- ``~/.emacs.d/init.el``
- ``~/.config/emacs/init.el``

Patterns recognised::

    (global-set-key (kbd "C-h") 'backward-char)
    (define-key evil-normal-state-map (kbd "C-h") 'evil-window-left)
    (evil-define-key 'normal evil-normal-state-map (kbd "C-h") 'evil-window-left)

Evil-mode window commands are mapped to TUI actions.  Vanilla emacs
navigation commands (``forward-char``, ``backward-char``) are also
mapped where they correspond to TUI directional actions.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ravn.tui.keybindings.model import KeybindingMap, emacs_kbd_to_textual

logger = logging.getLogger(__name__)

_EMACS_PATHS: list[Path] = [
    Path.home() / ".emacs",
    Path.home() / ".emacs.d" / "init.el",
    Path.home() / ".config" / "emacs" / "init.el",
]

# evil-mode commands → TUI actions
_EVIL_CMD_TO_ACTION: dict[str, str] = {
    "evil-window-left": "focus_left",
    "evil-window-right": "focus_right",
    "evil-window-up": "focus_up",
    "evil-window-down": "focus_down",
    "evil-window-vsplit": "split_vert",
    "evil-window-split": "split_horiz",
    "evil-window-delete": "close_pane",
    "evil-window-next": "focus_next",
    "evil-window-rotate-upwards": "rotate_pane",
    "evil-window-move-far-left": "move_far_left",
    "evil-window-move-far-right": "move_far_right",
    "evil-window-move-very-top": "move_far_up",
    "evil-window-move-very-bottom": "move_far_down",
    # evil navigation
    "evil-next-line": "scroll_down",
    "evil-previous-line": "scroll_up",
    "evil-goto-line": "scroll_bottom",
    "evil-goto-first-line": "scroll_top",
    "evil-search-forward": "search",
    "evil-ex": "command_mode",
    "evil-quit": "quit",
}

# Vanilla emacs commands → TUI actions (limited mapping)
_EMACS_CMD_TO_ACTION: dict[str, str] = {
    "next-line": "scroll_down",
    "previous-line": "scroll_up",
    "end-of-buffer": "scroll_bottom",
    "beginning-of-buffer": "scroll_top",
    "isearch-forward": "search",
    "execute-extended-command": "command_mode",
}

_ALL_COMMANDS = {**_EVIL_CMD_TO_ACTION, **_EMACS_CMD_TO_ACTION}

# Both 'x and (quote x) are valid elisp; handle both
_QUOTE = r"(?:'|(?:\(quote\s+))"

# (global-set-key (kbd "...") 'command)  or  (global-set-key (kbd "...") (quote command))
_GLOBAL_SET_RE = re.compile(
    r"""\(\s*global-set-key\s+\(kbd\s+["']([^"']+)["']\)\s+""" + _QUOTE + r"""([\w-]+)""",
)

# (define-key <map> (kbd "...") 'command)
_DEFINE_KEY_RE = re.compile(
    r"""\(\s*define-key\s+\S+\s+\(kbd\s+["']([^"']+)["']\)\s+""" + _QUOTE + r"""([\w-]+)""",
)

# (evil-define-key 'normal <map> (kbd "...") 'command)
# Also handles (evil-define-key (quote normal) ...)
_EVIL_DEFINE_RE = re.compile(
    r"""\(\s*evil-define-key\s+(?:'normal|\(quote\s+normal\))\s+\S+\s+\(kbd\s+["']([^"']+)["']\)\s+"""
    + _QUOTE
    + r"""([\w-]+)""",
)


def find_emacs_config() -> Path | None:
    """Return the first existing emacs config path."""
    for p in _EMACS_PATHS:
        if p.exists():
            return p
    return None


class EmacsParser:
    """Parse an Emacs init file for relevant keybindings."""

    def parse_file(self, path: Path) -> dict[str, str]:
        """Return ``{emacs_kbd_string: emacs_command}`` from *path*."""
        try:
            content = path.read_text(errors="replace")
        except OSError as exc:
            logger.debug("cannot read emacs config %s: %s", path, exc)
            return {}
        return self.parse(content)

    def parse(self, content: str) -> dict[str, str]:
        """Extract ``{kbd_string: command}`` from emacs elisp content."""
        remaps: dict[str, str] = {}
        for pattern in (_EVIL_DEFINE_RE, _DEFINE_KEY_RE, _GLOBAL_SET_RE):
            for m in pattern.finditer(content):
                kbd_str, cmd = m.group(1), m.group(2)
                remaps[kbd_str] = cmd
        return remaps

    def apply_to_map(self, path: Path, kb: KeybindingMap) -> int:
        """Parse *path* and add recognised remaps to *kb*.

        Returns the number of bindings added.
        """
        remaps = self.parse_file(path)
        added = 0
        for kbd_str, cmd in remaps.items():
            action = _ALL_COMMANDS.get(cmd)
            if action is None:
                continue

            textual_seq = emacs_kbd_to_textual(kbd_str)
            if textual_seq is None:
                kb.warn(f"emacs: cannot convert kbd {kbd_str!r} to Textual key")
                continue

            kb.register(textual_seq, action)
            logger.debug("emacs: %s → %s → %s", kbd_str, cmd, action)
            added += 1

        return added

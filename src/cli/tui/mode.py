"""Input-mode system for the Niuu TUI.

Four modes control which keybindings are active:

* NORMAL  — navigation (j/k, 1-7, ?, q, etc.)
* INSERT  — text input captured; only Alt+N and Esc pass through
* SEARCH  — filter/search active; / activates, Esc exits
* COMMAND — command palette open; Ctrl+K activates
"""

from __future__ import annotations

from enum import StrEnum


class InputMode(StrEnum):
    """TUI input mode."""

    NORMAL = "NORMAL"
    INSERT = "INSERT"
    SEARCH = "SEARCH"
    COMMAND = "COMMAND"

    def __str__(self) -> str:  # noqa: D105
        return self.value


# Mode → accent color (Textual CSS variable name).
MODE_COLORS: dict[InputMode, str] = {
    InputMode.NORMAL: "$accent-cyan",
    InputMode.INSERT: "$accent-emerald",
    InputMode.SEARCH: "$accent-amber",
    InputMode.COMMAND: "$accent-purple",
}

# Raw hex fallback used in Rich markup inside widgets.
MODE_COLORS_HEX: dict[InputMode, str] = {
    InputMode.NORMAL: "#06b6d4",
    InputMode.INSERT: "#10b981",
    InputMode.SEARCH: "#f59e0b",
    InputMode.COMMAND: "#a855f7",
}

"""Command mode parser and handler for Ravn TUI.

Parses vim-style colon commands and dispatches them to handlers.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

# All known view types for tab completion
VIEW_TYPES = [
    "flokk",
    "chat",
    "events",
    "tasks",
    "mimir",
    "cron",
    "checkpoints",
    "caps",
    "log",
]

# All known command names
COMMAND_NAMES = [
    "connect",
    "disconnect",
    "broadcast",
    "layout",
    "view",
    "ingest",
    "checkpoint",
    "resume",
    "quit",
    "q",
]

_LAYOUT_SUBCOMMANDS = ["save", "load", "list", "delete"]


@dataclass(frozen=True)
class Command:
    """A parsed command from the command bar."""

    name: str
    args: list[str]
    raw: str


class CommandParseError(ValueError):
    """Raised when a command cannot be parsed."""


def parse_command(raw: str) -> Command:
    """Parse a colon command string into a Command.

    Input is the text after the leading colon.
    """
    text = raw.strip()
    if not text:
        raise CommandParseError("empty command")
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        raise CommandParseError(f"malformed command: {exc}") from exc
    name = parts[0].lower()
    args = parts[1:]
    return Command(name=name, args=args, raw=raw)


def complete_command(partial: str) -> list[str]:
    """Return tab-completion candidates for a partial command string."""
    text = partial.lstrip(":")
    parts = text.split()

    if not parts:
        return [f":{c}" for c in COMMAND_NAMES]

    if len(parts) == 1 and not text.endswith(" "):
        # Completing the command name
        word = parts[0].lower()
        return [f":{c}" for c in COMMAND_NAMES if c.startswith(word)]

    name = parts[0].lower()

    if name == "view" and len(parts) <= 2:
        prefix = parts[1] if len(parts) > 1 else ""
        return [v for v in VIEW_TYPES if v.startswith(prefix)]

    if name == "layout" and len(parts) <= 2:
        prefix = parts[1] if len(parts) > 1 else ""
        return [s for s in _LAYOUT_SUBCOMMANDS if s.startswith(prefix)]

    return []


class CommandDispatcher:
    """Routes parsed commands to handler callables registered by the app."""

    def __init__(self) -> None:
        self._handlers: dict[str, Any] = {}

    def register(self, name: str, handler: Any) -> None:
        """Register *handler* for command *name*."""
        self._handlers[name] = handler

    async def dispatch(self, cmd: Command) -> str | None:
        """Dispatch *cmd* to its handler.

        Returns a status message string, or None on success.
        Raises CommandParseError if the command is unknown.
        """
        handler = self._handlers.get(cmd.name)
        if handler is None:
            raise CommandParseError(f"unknown command: {cmd.name!r}")
        result = handler(*cmd.args)
        if hasattr(result, "__await__"):
            result = await result
        return result

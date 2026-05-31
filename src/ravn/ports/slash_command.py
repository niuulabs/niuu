"""Slash command port — interface for agent slash command handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ravn.adapters.slash_commands import SlashCommandContext


class SlashCommandPort(ABC):
    """Abstract interface for a slash command handler.

    Slash commands are invoked by the user with a leading ``/``, e.g.
    ``/help`` or ``/status``.  Each command receives the trailing argument
    string and an ambient :class:`~ravn.adapters.slash_commands.SlashCommandContext`
    and returns a formatted string to be printed.

    To implement a custom slash command::

        from ravn.ports.slash_command import SlashCommandPort
        from ravn.adapters.slash_commands import SlashCommandContext

        class VersionCommand(SlashCommandPort):
            @property
            def name(self) -> str:
                return "/version"

            @property
            def description(self) -> str:
                return "show the running Ravn version"

            def handle(self, args: str, ctx: SlashCommandContext) -> str:
                from importlib.metadata import version
                return f"Ravn {version('ravn')}"

    Register it in ``ravn.yaml``::

        slash_commands:
          - adapter: mypackage.commands.VersionCommand
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """The slash command token including the leading slash, e.g. ``/help``.

        Must be lower-case and unique within the registry.
        """
        raise NotImplementedError

    @property
    def aliases(self) -> list[str]:
        """Optional aliases, e.g. ``["/h"]``. Default: no aliases."""
        return []

    @property
    def description(self) -> str:
        """One-line description shown in /help output. Default: empty string."""
        return ""

    @abstractmethod
    def handle(self, args: str, ctx: SlashCommandContext) -> str:
        """Execute the command and return the output string.

        Args:
            args: Everything after the command token, stripped of whitespace.
                  Empty string when the command was invoked with no arguments.
            ctx: Ambient agent state — session, tools, budget, etc.

        Returns:
            A formatted string to be printed to the user.
        """
        raise NotImplementedError

"""Shared CLI transport contract for agent executors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

EventCallback = Callable[[dict], Awaitable[None]]


@dataclass(frozen=True)
class TransportCapabilities:
    """Declares what a CLI transport supports."""

    send_message: bool = True
    cli_websocket: bool = False
    session_resume: bool = False
    interrupt: bool = False
    steer: bool = False
    steering_mode: str = "none"
    set_model: bool = False
    set_thinking_tokens: bool = False
    set_permission_mode: bool = False
    rewind_files: bool = False
    mcp_set_servers: bool = False
    permission_requests: bool = False
    slash_commands: bool = False
    skills: bool = False
    terminal_output: bool = False
    terminal_input: bool = False
    terminal_keys: bool = False
    terminal_resize: bool = False
    terminal_panes: bool = False


class CLITransport(ABC):
    """Abstract transport for communicating with coding CLIs."""

    def __init__(self) -> None:
        self._event_callback: EventCallback | None = None

    def on_event(self, callback: EventCallback | None) -> None:
        """Register a callback for CLI events."""
        self._event_callback = callback

    @property
    def event_callback(self) -> EventCallback | None:
        """Return the currently registered event callback."""
        return self._event_callback

    async def _emit(self, data: dict) -> None:
        """Fire the event callback if registered."""
        if self._event_callback is None:
            return
        await self._event_callback(data)

    @abstractmethod
    async def start(self) -> None:
        """Initialize the transport."""

    @abstractmethod
    async def stop(self) -> None:
        """Shut down the transport and clean up."""

    @abstractmethod
    async def send_message(
        self, content: str, *, msg_id: str | None = None, request_id: str | None = None
    ) -> None:
        """Send a user message to the CLI.

        ``msg_id``/``request_id`` (when supplied by the broker) let a transport correlate the
        eventual "the agent consumed this message" signal back to the originating steer, so the
        client can flip that message from pending → active. Keyword-only with defaults: transports
        that don't emit such a signal simply ignore them, and existing ``send_message(content)``
        call sites keep working unchanged.
        """

    async def interrupt(self) -> None:
        """Cancel the in-flight turn, if any. Default no-op."""
        return None

    async def send_control_response(self, request_id: str, response: dict) -> None:
        """Respond to a CLI-initiated control request."""

    async def send_control(self, subtype: str, **kwargs: object) -> None:
        """Send a server-initiated control message."""

    async def discover_slash_commands(self, *, refresh: bool = False) -> list[dict]:
        """Return slash commands available in this transport, if discoverable."""
        return []

    @property
    @abstractmethod
    def session_id(self) -> str | None:
        """The CLI session ID, if the transport supports resume."""

    @property
    @abstractmethod
    def last_result(self) -> dict | None:
        """The most recent result event."""

    @property
    @abstractmethod
    def is_alive(self) -> bool:
        """Whether the transport is connected and operational."""

    @property
    def is_turn_active(self) -> bool:
        """Whether the transport is currently executing a turn."""
        return False

    @property
    def capabilities(self) -> TransportCapabilities:
        """Declare what this transport supports."""
        return TransportCapabilities()

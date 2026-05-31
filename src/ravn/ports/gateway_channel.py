"""Gateway channel port — bidirectional messaging interface for external platforms.

Each messaging platform adapter (Discord, Slack, Matrix, WhatsApp) implements
this ABC so the gateway orchestrator can start/stop adapters uniformly and route
inbound messages to :class:`~ravn.adapters.channels.gateway.RavnGateway`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

# Called when an inbound message arrives: (chat_id, text) → None.
# chat_id is platform-specific (see subclass docs).
MessageHandler = Callable[[str, str], Awaitable[None]]


class GatewayChannelPort(ABC):
    """Abstract interface for a bidirectional gateway platform adapter.

    Implementations connect to an external messaging platform and bridge
    messages to/from :class:`~ravn.adapters.channels.gateway.RavnGateway`.

    Lifecycle::

        adapter.on_message(handler)    # register inbound handler once
        await adapter.start()           # connect + begin receiving
        await adapter.send_text(chat_id, "hello")
        await adapter.stop()            # disconnect gracefully

    ``chat_id`` is platform-specific:

    * Discord  — ``"guild_id/channel_id"``
    * Slack    — Slack channel ID (e.g. ``"C0123ABCDE"``)
    * Matrix   — full room ID (e.g. ``"!abc:matrix.example.com"``)
    * WhatsApp — E.164 phone number or group JID
    """

    @abstractmethod
    async def start(self) -> None:
        """Connect to the platform and begin receiving messages."""
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect from the platform and release all resources."""
        raise NotImplementedError

    @abstractmethod
    async def send_text(self, chat_id: str, text: str) -> None:
        """Send *text* to *chat_id*."""
        raise NotImplementedError

    @abstractmethod
    async def send_image(self, chat_id: str, image: bytes, caption: str = "") -> None:
        """Send *image* bytes with optional *caption* to *chat_id*."""
        raise NotImplementedError

    @abstractmethod
    async def send_audio(self, chat_id: str, audio: bytes) -> None:
        """Send *audio* bytes to *chat_id*."""
        raise NotImplementedError

    @abstractmethod
    def on_message(self, handler: MessageHandler) -> None:
        """Register *handler* to be called for every inbound user message.

        Must be called before :meth:`start`.  Only one handler is supported;
        a second call replaces the first.
        """
        raise NotImplementedError

"""Event translator port — translates RavnEvents into wire-format dicts."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.domain.events import RavnEvent


class EventTranslatorPort(ABC):
    """Translates :class:`RavnEvent` objects into wire-format dicts for a
    specific protocol (e.g. Claude CLI stream-json, plain JSON, etc.).

    Implementations are stateful per-turn: call :meth:`reset` before each new
    agent turn to clear accumulated block indices and tracking state.
    """

    @abstractmethod
    def translate(self, event: RavnEvent) -> list[dict]:
        """Return zero or more wire-format event dicts for *event*."""
        raise NotImplementedError

    @abstractmethod
    def reset(self) -> None:
        """Reset per-turn state (block indices, flags, etc.)."""
        raise NotImplementedError

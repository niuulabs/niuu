"""AnswerModel — the model calls the memory gateway makes (contract C8 on the Bifröst side).

``memory_ask`` makes one answer call, charged to the session's owner: it is made
with the session's own credential, which Bifröst confirms with Forge. The context
pack is sized from the model catalog's ``context_window``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelReply:
    """One model answer, in the shape :func:`niuu.cap_telemetry.require_complete` reads."""

    content: str
    stop_reason: str
    reasoning: str = ""
    tool_calls: tuple[Any, ...] = ()


class AnswerModelError(Exception):
    """The model call failed or answered something unusable. The message names why."""


class ModelCatalogError(Exception):
    """The catalog could not say what is needed about a model. The message names the fix."""


class AnswerModel(ABC):
    @abstractmethod
    async def answer(self, *, model: str, system: str, prompt: str, credential: str) -> ModelReply:
        """One answer from *model*, with no output cap sent, made as *credential*'s holder."""

    @abstractmethod
    async def context_window(self, model: str) -> int:
        """The model's input context window in tokens.

        Raises:
            ModelCatalogError: the model is unknown, or the catalog records no window.
        """

    async def close(self) -> None:  # noqa: B027 — optional hook
        """Release connections."""

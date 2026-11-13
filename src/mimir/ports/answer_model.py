"""AnswerModel — the model calls the memory gateway makes (contract C8 on the Bifröst side).

``memory_ask`` makes one answer call, charged to whoever asked: a session's call is
made with the session's own credential, which Bifröst confirms with Forge, and charged
to its owner; a person's call (the people ask route) is made with their own login and
charged to them.
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


@dataclass(frozen=True)
class ModelCaller:
    """Who an answer call is made as, and so charged to.

    ``authorization`` is the ``Authorization`` header presented to Bifröst: a session's
    ``forge_session`` credential, or a person's own login (empty in local mini mode, where
    nothing verifies callers). ``user_id`` and ``tenant_id`` name a person for a Bifröst
    that takes attribution from headers (``auth_mode: open``); one that verifies callers
    charges the verified credential and ignores them.
    """

    authorization: str
    user_id: str = ""
    tenant_id: str = ""

    @classmethod
    def session(cls, credential: str) -> ModelCaller:
        if not credential.strip():
            raise ValueError("a session's answer call needs the session's credential")
        return cls(authorization=f"Bearer {credential.strip()}")

    @classmethod
    def person(cls, *, authorization: str, user_id: str, tenant_id: str) -> ModelCaller:
        if not user_id.strip() or not tenant_id.strip():
            raise ValueError("a person's answer call names the person and their org")
        return cls(authorization=authorization, user_id=user_id, tenant_id=tenant_id)


class AnswerModelError(Exception):
    """The model call failed or answered something unusable. The message names why."""


class AnswerModel(ABC):
    @abstractmethod
    async def answer(
        self, *, model: str, system: str, prompt: str, caller: ModelCaller
    ) -> ModelReply:
        """One answer from *model*, with no output cap sent, made as *caller*."""

    async def close(self) -> None:  # noqa: B027 — optional hook
        """Release connections."""

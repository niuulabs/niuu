"""Value types shared by every Forge MCP transport (broker stdio today, Forge HTTP later)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ForgeMcpGrant(StrEnum):
    """Operator grants that unlock tools acting on *other* sessions.

    Reading sessions and notifying are always available; steering another agent
    or changing its lifecycle needs an explicit grant (launch spec, session
    create or the scoped session token).
    """

    MESSAGE = "message"
    LIFECYCLE = "lifecycle"


class ForgeMcpNotifyMode(StrEnum):
    """How ``notify`` records a notification.

    ``transcript``: the broker appends a notification turn to its own session's
    durable log (inline in the conversation, then projected into the feed).
    ``feed``: a direct submit to Forge's feed (``POST /sessions/{id}/notifications``),
    used by the Forge-hosted HTTP endpoint, which has no session log to append to.
    """

    TRANSCRIPT = "transcript"
    FEED = "feed"


@dataclass(frozen=True)
class ForgeMcpLimits:
    """Output bounds. They come from configuration so a deployment can tune them."""

    list_default_limit: int
    list_max_limit: int
    transcript_default_turns: int
    transcript_max_turns: int
    transcript_turn_max_chars: int
    output_max_chars: int

    def clamp_list(self, requested: int | None) -> int:
        if requested is None:
            return min(self.list_default_limit, self.list_max_limit)
        return max(1, min(requested, self.list_max_limit))

    def clamp_turns(self, requested: int | None) -> int:
        if requested is None:
            return min(self.transcript_default_turns, self.transcript_max_turns)
        return max(1, min(requested, self.transcript_max_turns))


class ForgeApiError(Exception):
    """A Forge REST call failed. ``status`` is ``None`` when Forge was unreachable."""

    def __init__(self, detail: str, *, status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


class FlockError(Exception):
    """A flock tool could not hand work to, or hear from, the brokered flock."""


@dataclass(frozen=True)
class ToolResult:
    """One MCP ``tools/call`` result, before transport encoding."""

    text: str
    structured: dict[str, Any] | None = None
    is_error: bool = False

    @classmethod
    def error(cls, message: str) -> ToolResult:
        return cls(text=message, is_error=True)

    @classmethod
    def of(cls, payload: dict[str, Any], *, max_chars: int) -> ToolResult:
        """A success whose text is the JSON payload (bounded) and whose structure is the payload."""
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n… (truncated; see structuredContent)"
        return cls(text=text, structured=payload)

    def to_mcp(self) -> dict[str, Any]:
        """The MCP ``CallToolResult`` shape."""
        result: dict[str, Any] = {
            "content": [{"type": "text", "text": self.text}],
            "isError": self.is_error,
        }
        if self.structured is not None:
            result["structuredContent"] = self.structured
        return result

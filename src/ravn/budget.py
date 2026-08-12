"""Iteration budget and token estimation for Ravn.

IterationBudget tracks how many LLM iterations have been consumed across an
agent turn (or across a cascade of parent + sub-ravn agents sharing the same
budget object).

TokenEstimator provides lightweight token count estimates before API calls
so the agent can decide whether to compress context.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from ravn.domain.models import Message, TokenUsage

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

_CHARS_PER_TOKEN = 4  # rough heuristic: 4 chars ≈ 1 token


class TokenEstimator:
    """Lightweight token counting for pre-request projection."""

    @staticmethod
    def rough(text: str) -> int:
        """Estimate tokens from a plain string (4 chars ≈ 1 token)."""
        return len(text) // _CHARS_PER_TOKEN

    @staticmethod
    def rough_messages(messages: list[Message]) -> int:
        """Estimate tokens for a list of Message objects."""
        total = 0
        for msg in messages:
            total += TokenEstimator._message_chars(msg)
        return total // _CHARS_PER_TOKEN

    @staticmethod
    def rough_api_messages(messages: list[dict]) -> int:
        """Estimate tokens for a list of raw API message dicts."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        for v in block.values():
                            if isinstance(v, str):
                                total += len(v)
        return total // _CHARS_PER_TOKEN

    @staticmethod
    def rough_blocks(blocks: list[dict]) -> int:
        """Estimate tokens for Anthropic-format system prompt blocks."""
        total = sum(len(b.get("text", "")) for b in blocks if isinstance(b, dict))
        return total // _CHARS_PER_TOKEN

    @staticmethod
    def rough_structured(value: Any) -> int:
        """Estimate a complete structured payload, including nested values."""
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return len(rendered) // _CHARS_PER_TOKEN

    @staticmethod
    def conservative(tokens: int, safety_factor: float) -> int:
        """Apply a configured safety factor to a rough token estimate."""
        return math.ceil(max(0, tokens) * max(1.0, safety_factor))

    @staticmethod
    def chars_for_tokens(tokens: int, safety_factor: float = 1.0) -> int:
        """Invert :meth:`rough`: the char count that estimates to *tokens*.

        The inverse of ``conservative(rough(text))``, for callers that hold a
        token allowance and need to cut text to fit it.
        """
        if tokens <= 0:
            return 0
        return int(tokens / max(1.0, safety_factor)) * _CHARS_PER_TOKEN

    @staticmethod
    def from_usage(usage: TokenUsage) -> int:
        """Return the accurate total from an API usage record."""
        return usage.total_tokens

    @staticmethod
    def _message_chars(msg: Message) -> int:
        content = msg.content
        if isinstance(content, str):
            return len(content)
        return len(
            json.dumps(
                content,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )


# ---------------------------------------------------------------------------
# Iteration budget
# ---------------------------------------------------------------------------


@dataclass
class IterationBudget:
    """Tracks LLM iteration consumption across a session or cascade.

    Pass the *same* IterationBudget instance to a parent agent and all its
    sub-ravn children so that every ``consume()`` call is reflected globally.

    ``task_ceiling`` caps the effective limit for this particular budget view.
    A sub-ravn can receive the shared budget with a lower ceiling to prevent a
    single task from monopolising the global pool:

        # In the parent agent:
        sub_budget = IterationBudget(
            total=parent.total,
            consumed=parent.consumed,
            task_ceiling=30,
        )
        sub_agent = RavnAgent(..., iteration_budget=sub_budget)

    Warning injection: call ``warning_suffix()`` after each iteration and, when
    non-None, append the returned string to the tool-result content so the
    model is informed about remaining budget without inserting extra messages.
    """

    total: int = 90
    consumed: int = 0
    task_ceiling: int | None = None
    near_limit_threshold: float = 0.8
    _task_consumed: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        # _task_consumed starts at 0 regardless of initial consumed value so
        # that task_ceiling correctly reflects *this task's* consumption.
        object.__setattr__(self, "_task_consumed", 0)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def remaining(self) -> int:
        """Iterations remaining, respecting both total and task_ceiling."""
        global_remaining = max(0, self.total - self.consumed)
        if self.task_ceiling is None:
            return global_remaining
        task_remaining = max(0, self.task_ceiling - self._task_consumed)
        return min(global_remaining, task_remaining)

    @property
    def near_limit(self) -> bool:
        """True when consumed fraction crosses near_limit_threshold."""
        if self.task_ceiling is not None:
            effective_total = self.task_ceiling
            effective_consumed = self._task_consumed
        else:
            effective_total = self.total
            effective_consumed = self.consumed
        if effective_total == 0:
            return True
        return effective_consumed / effective_total >= self.near_limit_threshold

    @property
    def exhausted(self) -> bool:
        """True when no iterations remain."""
        return self.remaining <= 0

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def consume(self, n: int = 1) -> None:
        """Record that *n* iterations have been consumed."""
        self.consumed += n
        self._task_consumed += n

    # ------------------------------------------------------------------
    # Warning text
    # ------------------------------------------------------------------

    def warning_suffix(self) -> str | None:
        """Return a warning string to append to tool results, or None.

        Returns a non-empty string when the budget is near its limit or
        exhausted.  The caller should append this to tool result content so
        the model is informed without inserting an extra message.
        """
        if self.exhausted:
            return (
                f"\n\n[Budget exhausted: {self.consumed}/{self.total} iterations used — stopping.]"
            )
        if self.near_limit:
            return (
                f"\n\n[Budget warning: {self.consumed}/{self.total} iterations "
                f"used, {self.remaining} remaining.]"
            )
        return None

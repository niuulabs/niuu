"""Token usage as each wire protocol reports it, mapped to :class:`TokenUsage`.

One mapping per protocol, shared by the canonical translation and the native
passthrough path so a request is metered and costed the same on either.
"""

from __future__ import annotations

from typing import Any

from bifrost.domain.models import TokenUsage


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) else 0


def responses_usage(raw: dict[str, Any] | None) -> TokenUsage:
    """A Responses API ``usage`` object as :class:`TokenUsage`.

    The one mapping both paths use for the ChatGPT backend: the canonical
    translation reports these same numbers in its Anthropic events.
    """
    raw = raw or {}
    output_details = raw.get("output_tokens_details") or {}
    return TokenUsage(
        input_tokens=_int(raw.get("input_tokens")),
        output_tokens=_int(raw.get("output_tokens")),
        reasoning_tokens=_int(output_details.get("reasoning_tokens")),
    )


def chat_completions_usage(raw: dict[str, Any] | None) -> TokenUsage:
    """A Chat Completions ``usage`` object as :class:`TokenUsage`."""
    raw = raw or {}
    completion_details = raw.get("completion_tokens_details") or {}
    return TokenUsage(
        input_tokens=_int(raw.get("prompt_tokens")),
        output_tokens=_int(raw.get("completion_tokens")),
        reasoning_tokens=_int(completion_details.get("reasoning_tokens")),
    )


def add_usage(usage: TokenUsage, other: TokenUsage) -> None:
    """Add *other* into *usage* in place."""
    usage.input_tokens += other.input_tokens
    usage.output_tokens += other.output_tokens
    usage.cache_creation_input_tokens += other.cache_creation_input_tokens
    usage.cache_read_input_tokens += other.cache_read_input_tokens
    usage.reasoning_tokens += other.reasoning_tokens


def anthropic_compaction_usage(raw: dict[str, Any] | None) -> TokenUsage:
    """Tokens Anthropic spent compacting, from ``usage.iterations``.

    With the compaction beta the top-level ``input_tokens``/``output_tokens``
    exclude the compaction iterations; what is billed is the sum over every
    iteration, so the compaction entries are added on top.
    """
    total = TokenUsage()
    for iteration in (raw or {}).get("iterations") or []:
        if isinstance(iteration, dict) and iteration.get("type") == "compaction":
            total.input_tokens += _int(iteration.get("input_tokens"))
            total.output_tokens += _int(iteration.get("output_tokens"))
    return total

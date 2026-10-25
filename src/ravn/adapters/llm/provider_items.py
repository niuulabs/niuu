"""Shared handling of a message's verbatim provider items.

An assistant :class:`~ravn.domain.models.Message` may carry the turn exactly as
its provider returned it (``provider_items``, in ``provider_protocol``). Only an
adapter of that protocol can replay it; every other adapter must leave it out
of its request — and must refuse a history whose context lives in another
protocol's compaction item, because dropping that item would silently lose the
conversation it summarises.
"""

from __future__ import annotations

from typing import Any

from ravn.domain.exceptions import LLMError
from ravn.domain.models import PROVIDER_ITEMS_KEY, PROVIDER_PROTOCOL_KEY

OPENAI_RESPONSES = "openai_responses"
ANTHROPIC_MESSAGES = "anthropic_messages"

# The item (Responses) / content block (Anthropic) type of a provider's
# compaction state. Both protocols use the same name.
COMPACTION_TYPE = "compaction"

PROVIDER_KEYS = frozenset({PROVIDER_ITEMS_KEY, PROVIDER_PROTOCOL_KEY})


def provider_items_of(message: dict[str, Any], protocol: str) -> list[dict[str, Any]]:
    """The message's provider items when they are in *protocol*, else ``[]``."""
    if message.get(PROVIDER_PROTOCOL_KEY) != protocol:
        return []
    items = message.get(PROVIDER_ITEMS_KEY) or []
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise LLMError(f"message provider_items in {protocol} must be a list of objects")
    return items


def holds_compaction(items: list[dict[str, Any]]) -> bool:
    return any(item.get("type") == COMPACTION_TYPE for item in items)


def without_provider_keys(message: dict[str, Any]) -> dict[str, Any]:
    """*message* without the provider-item keys, for any wire format."""
    return {key: value for key, value in message.items() if key not in PROVIDER_KEYS}


def refuse_foreign_compaction(messages: list[dict[str, Any]], protocol: str, adapter: str) -> None:
    """Raise when the history's context lives in another protocol's compaction item.

    Raises:
        LLMError: A message carries a compaction item from a protocol other than
            *protocol*; this adapter could only drop it.
    """
    for message in messages:
        other = message.get(PROVIDER_PROTOCOL_KEY)
        if not other or other == protocol:
            continue
        if holds_compaction(message.get(PROVIDER_ITEMS_KEY) or []):
            raise LLMError(
                f"{adapter} cannot continue this conversation: its earlier context was "
                f"compacted natively by a {other} provider into an opaque item that only "
                f"a {other} adapter can send back. Switch the model back to that provider, "
                "or start a new session"
            )


def latest_compaction_index(messages: list[dict[str, Any]], protocol: str) -> int:
    """Index of the last message holding a *protocol* compaction item, or ``0``."""
    for index in range(len(messages) - 1, -1, -1):
        if holds_compaction(provider_items_of(messages[index], protocol)):
            return index
    return 0

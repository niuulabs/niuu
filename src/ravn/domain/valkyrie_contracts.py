"""Resident Valkyrie outcome events, and the outcome fields the runtime owns."""

from __future__ import annotations

VALKYRIE_JUDGMENT_PROPOSED = "valkyrie.judgment.proposed"
VALKYRIE_STATE_UPDATED = "valkyrie.state.updated"
VALKYRIE_ACTION_PROPOSED = "valkyrie.action.proposed"
VALKYRIE_ACTION_EXECUTED = "valkyrie.action.executed"
VALKYRIE_ACTION_FAILED = "valkyrie.action.failed"

VALKYRIE_OUTCOME_EVENTS = frozenset(
    {
        VALKYRIE_JUDGMENT_PROPOSED,
        VALKYRIE_STATE_UPDATED,
        VALKYRIE_ACTION_PROPOSED,
        VALKYRIE_ACTION_EXECUTED,
        VALKYRIE_ACTION_FAILED,
    }
)

#: Fields the runtime stamps on a resident outcome; a persona's model never writes them.
VALKYRIE_RUNTIME_OWNED_FIELDS = frozenset(
    {"environment_id", "environment_type", "valkyrie_id", "correlation_ids"}
)


def is_valkyrie_outcome_event(event_type: str) -> bool:
    """Return whether *event_type* is a resident Valkyrie outcome contract."""
    return event_type in VALKYRIE_OUTCOME_EVENTS
